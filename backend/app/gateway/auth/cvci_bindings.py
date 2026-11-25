"""Operator CLI for linking existing DeerFlow UUIDs to verified CVCI identities."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator
from sqlalchemy import URL, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.gateway.auth.cvci import CVCI_PROVIDER, cvci_subject
from deerflow.config import get_app_config
from deerflow.persistence.postgres_schema import build_asyncpg_connect_args
from deerflow.persistence.user.model import UserRow


class BindingConflict(Exception):
    """The plan does not match the current account state."""


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tenantId: str
    userId: str
    deerFlowUserId: str

    @model_validator(mode="after")
    def validate_ids(self) -> Binding:
        cvci_subject(self.tenantId, self.userId)
        if str(UUID(self.deerFlowUserId)) != self.deerFlowUserId:
            raise ValueError("DeerFlow user must be a canonical UUID")
        return self

    @property
    def subject(self) -> str:
        return cvci_subject(self.tenantId, self.userId)


BindingMode = Literal["dry-run", "apply", "undo"]
_MANIFEST = TypeAdapter(list[Binding])


def _read_manifest(path: Path) -> list[Binding]:
    bindings = _MANIFEST.validate_json(path.read_bytes())
    if len({item.deerFlowUserId for item in bindings}) != len(bindings) or len({item.subject for item in bindings}) != len(bindings):
        raise BindingConflict("The manifest repeats a DeerFlow or CVCI identity")
    return sorted(bindings, key=lambda item: item.deerFlowUserId)


def _write_undo_manifest(path: Path, changes: list[Binding]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump([item.model_dump() for item in changes], stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


async def _plan(session: AsyncSession, mode: BindingMode, bindings: list[Binding]) -> list[Binding]:
    changes = []
    for item in bindings:
        select_user = select(UserRow).where(UserRow.id == item.deerFlowUserId)
        if mode != "dry-run":
            select_user = select_user.with_for_update()
        user = await session.scalar(select_user)
        if user is None or (mode != "undo" and (user.disabled or user.needs_setup)):
            raise BindingConflict(f"DeerFlow account {item.deerFlowUserId} is missing, disabled, or needs setup")

        match (user.oauth_provider, user.oauth_id):
            case (None, None):
                if mode == "undo":
                    continue
            case ("cvci", subject) if subject == item.subject:
                if mode == "undo":
                    changes.append(item)
                continue
            case _:
                raise BindingConflict(f"DeerFlow account {item.deerFlowUserId} already has a different OIDC identity")

        existing = await session.scalar(select(UserRow.id).where(UserRow.oauth_provider == CVCI_PROVIDER, UserRow.oauth_id == item.subject))
        if existing is not None:
            raise BindingConflict(f"CVCI identity is already assigned to another DeerFlow account for {item.deerFlowUserId}")
        changes.append(item)
    return changes


async def _apply(session: AsyncSession, mode: BindingMode, changes: list[Binding]) -> None:
    for item in changes:
        row = update(UserRow).where(UserRow.id == item.deerFlowUserId)
        if mode == "undo":
            row = row.where(UserRow.oauth_provider == CVCI_PROVIDER, UserRow.oauth_id == item.subject).values(
                oauth_provider=None,
                oauth_id=None,
                token_version=UserRow.token_version + 1,
            )
        else:
            row = row.where(UserRow.oauth_provider.is_(None), UserRow.oauth_id.is_(None), UserRow.disabled.is_(False), UserRow.needs_setup.is_(False)).values(
                oauth_provider=CVCI_PROVIDER,
                oauth_id=item.subject,
            )
        changed = await session.scalar(row.returning(UserRow.id))
        if changed is None:
            raise BindingConflict(f"DeerFlow account {item.deerFlowUserId} changed during the operation")


async def run_bindings(mode: BindingMode, manifest: Path, undo_file: Path | None = None) -> int:
    bindings = _read_manifest(manifest)
    config = get_app_config().database
    if config.backend == "sqlite":
        # URI mode forbids creating a file when the target is absent, including
        # an operator accidentally pointing to a new or wrong data directory.
        uri = Path(config.sqlite_path).resolve().as_uri()
        url = URL.create("sqlite+aiosqlite", database=uri, query={"uri": "true", "mode": "ro" if mode == "dry-run" else "rw"})
        engine = create_async_engine(url)
    elif config.backend == "postgres":
        engine = create_async_engine(config.app_sqlalchemy_url, connect_args=build_asyncpg_connect_args(config.postgres_schema))
    else:
        raise BindingConflict("An existing SQLite or PostgreSQL DeerFlow database is required")

    undo_written = False
    try:
        session_factory = async_sessionmaker(engine)
        async with session_factory.begin() as session:
            changes = await _plan(session, mode, bindings)
            if mode == "apply":
                if undo_file is None:
                    raise BindingConflict("Apply requires a new undo manifest path")
                _write_undo_manifest(undo_file, changes)
                undo_written = True
            if mode != "dry-run":
                await _apply(session, mode, changes)
    except BindingConflict:
        # Keep the undo manifest for ambiguous database commit failures. This
        # branch only represents a known compare-and-set conflict before commit.
        if undo_written and undo_file is not None:
            undo_file.unlink(missing_ok=True)
        raise
    finally:
        await engine.dispose()

    return len(changes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage verified CVCI-to-DeerFlow account bindings")
    actions = parser.add_subparsers(dest="mode", required=True)
    for mode in ("dry-run", "apply", "undo"):
        action = actions.add_parser(mode)
        action.add_argument("manifest", type=Path)
        if mode == "apply":
            action.add_argument("--undo-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        changes = asyncio.run(run_bindings(args.mode, args.manifest, getattr(args, "undo_file", None)))
    except BindingConflict as exc:
        print(f"Binding rejected: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValidationError, SQLAlchemyError):
        print("Unable to complete the operation. Check the manifest, database, and undo file path before retrying.", file=sys.stderr)
        return 1
    print(f"{args.mode}: {changes} account binding(s) {'would change' if args.mode == 'dry-run' else 'changed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
