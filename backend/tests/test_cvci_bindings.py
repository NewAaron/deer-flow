from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth import cvci_bindings
from deerflow.persistence.user.model import UserRow


@pytest.mark.asyncio
async def test_link_dry_run_apply_idempotence_and_undo_keep_deerflow_uuid(tmp_path, monkeypatch):
    database_path = tmp_path / "deerflow.db"
    monkeypatch.setattr(cvci_bindings, "get_app_config", lambda: SimpleNamespace(database=SimpleNamespace(backend="sqlite", sqlite_path=str(database_path))))
    existing_user, another_user = str(uuid4()), str(uuid4())
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(UserRow.__table__.create)
        user_session = async_sessionmaker(engine, expire_on_commit=False)
        async with user_session.begin() as session:
            session.add_all(
                (
                    UserRow(id=existing_user, email="owner@example.test", password_hash="existing-hash", system_role="admin", token_version=3),
                    UserRow(id=another_user, email="other@example.test"),
                )
            )

        async def fetch_user(user_id):
            async with user_session() as session:
                return await session.get(UserRow, user_id)

        binding = {"tenantId": "3", "userId": "12", "deerFlowUserId": existing_user}
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps([binding]), encoding="utf-8")
        undo = tmp_path / "undo.json"
        assert await cvci_bindings.run_bindings("dry-run", manifest) == 1
        assert (await fetch_user(existing_user)).oauth_provider is None
        assert await cvci_bindings.run_bindings("apply", manifest, undo) == 1
        assert json.loads(undo.read_text(encoding="utf-8")) == [binding]
        linked = await fetch_user(existing_user)
        assert linked.oauth_provider == "cvci" and linked.oauth_id == "cvci:3:12"
        assert linked.id == existing_user and linked.system_role == "admin" and linked.password_hash == "existing-hash" and linked.token_version == 3

        repeat_undo = tmp_path / "repeat-undo.json"
        assert await cvci_bindings.run_bindings("apply", manifest, repeat_undo) == 0
        assert json.loads(repeat_undo.read_text(encoding="utf-8")) == []
        assert await cvci_bindings.run_bindings("undo", repeat_undo) == 0
        assert (await fetch_user(existing_user)).oauth_id == "cvci:3:12"

        conflict = tmp_path / "conflict.json"
        conflict.write_text(json.dumps([{**binding, "deerFlowUserId": another_user}]), encoding="utf-8")
        with pytest.raises(cvci_bindings.BindingConflict, match="already assigned"):
            await cvci_bindings.run_bindings("apply", conflict, tmp_path / "conflict-undo.json")
        assert (await fetch_user(another_user)).oauth_provider is None

        assert await cvci_bindings.run_bindings("undo", undo) == 1
        assert await cvci_bindings.run_bindings("undo", undo) == 0
        reverted = await fetch_user(existing_user)
        assert reverted.id == existing_user and reverted.oauth_provider is None and reverted.oauth_id is None
        assert reverted.system_role == "admin" and reverted.password_hash == "existing-hash" and reverted.token_version == 4
    finally:
        await engine.dispose()
