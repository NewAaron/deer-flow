"""CVCI account identities provision without asserting ownership of a mailbox."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.cvci import CvciGrant
from app.gateway.auth.cvci_provisioning import get_or_provision_cvci_user
from app.gateway.auth.local_provider import LocalAuthProvider
from app.gateway.auth.models import User
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from deerflow.config.auth_config import OIDCProviderConfig
from deerflow.persistence.user.model import UserRow

_EMAIL = "cvci-t3-u42@cvci.identity.example.com"
_SUBJECT = "cvci:3:42"


def _grant(tenant="3", user="42"):
    return CvciGrant(grantId="mock-grant-id-000000001", tenantId=tenant, userId=user, deerFlowUserId="")


def _config(**overrides):
    return OIDCProviderConfig(display_name="CVCI", issuer="https://cvci.example.com", client_id="deerflow", **overrides)


def _provider(existing=None):
    async def create(**values):
        return User(**values)

    return SimpleNamespace(
        get_user_by_oauth=AsyncMock(return_value=existing),
        get_user_by_email=AsyncMock(),
        create_oauth_user=AsyncMock(side_effect=create),
    )


@pytest.mark.asyncio
async def test_new_account_uses_stable_internal_identity_without_email_rules_or_admin_mapping():
    provider = _provider()
    config = _config(require_verified_email=True, allowed_email_domains=["another.example.com"], admin_emails=[_EMAIL])

    result = await get_or_provision_cvci_user(_grant(), config, provider)

    user = result["user"]
    assert result["created"] is True
    assert user.email == _EMAIL
    assert user.system_role == "user"
    assert user.oauth_provider == "cvci" and user.oauth_id == _SUBJECT
    assert user.password_hash is None and user.needs_setup is False
    provider.create_oauth_user.assert_awaited_once_with(email=_EMAIL, oauth_provider="cvci", oauth_id=_SUBJECT, system_role="user")
    provider.get_user_by_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_account_keeps_all_local_identity_and_lifecycle_fields():
    existing = User(email="legacy@example.com", password_hash="legacy-hash", oauth_provider="cvci", oauth_id=_SUBJECT, system_role="admin", token_version=7)
    before = existing.model_dump()
    provider = _provider(existing)

    result = await get_or_provision_cvci_user(_grant(), _config(auto_create_users=False), provider)

    assert result == {"user": existing, "created": False}
    assert existing.model_dump() == before
    provider.create_oauth_user.assert_not_awaited()
    provider.get_user_by_email.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["disabled", "needs_setup"])
@pytest.mark.parametrize("after_race", [False, True])
async def test_bound_account_lifecycle_is_enforced_before_return_including_race(flag, after_race):
    existing = User(email="legacy@example.com", oauth_provider="cvci", oauth_id=_SUBJECT, **{flag: True})
    provider = _provider(existing)
    if after_race:
        provider.get_user_by_oauth.side_effect = [None, existing]
        provider.create_oauth_user.side_effect = ValueError("mock uniqueness race")

    with pytest.raises(HTTPException) as raised:
        await get_or_provision_cvci_user(_grant(), _config(), provider)

    assert raised.value.status_code == 403
    assert getattr(existing, flag) is True
    assert provider.create_oauth_user.await_count == int(after_race)


@pytest.mark.asyncio
async def test_auto_create_gate_preserved():
    provider = _provider()
    with pytest.raises(HTTPException) as raised:
        await get_or_provision_cvci_user(_grant(), _config(auto_create_users=False), provider)
    assert raised.value.status_code == 403
    provider.create_oauth_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_callback_reuses_only_the_same_provider_subject():
    winner = User(email=_EMAIL, oauth_provider="cvci", oauth_id=_SUBJECT)
    provider = _provider()
    provider.get_user_by_oauth.side_effect = [None, winner]
    provider.create_oauth_user.side_effect = ValueError("mock uniqueness race")

    assert await get_or_provision_cvci_user(_grant(), _config(), provider) == {"user": winner, "created": False}
    assert provider.get_user_by_oauth.await_args_list[0] == provider.get_user_by_oauth.await_args_list[1]
    provider.get_user_by_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_uniqueness_collision_fails_without_linking_or_exposing_details():
    provider = _provider()
    provider.create_oauth_user.side_effect = ValueError("private database collision detail")
    with pytest.raises(HTTPException) as raised:
        await get_or_provision_cvci_user(_grant(), _config(), provider)
    assert raised.value.status_code == 409
    assert "private database" not in raised.value.detail
    provider.get_user_by_email.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant,user", [("0", "42"), ("03", "42"), ("3", "../42"), ("3", str(2**63))])
async def test_invalid_noncanonical_identity_cannot_allocate_a_row(tenant, user):
    provider = _provider()
    with pytest.raises(ValueError):
        await get_or_provision_cvci_user(_grant(tenant, user), _config(), provider)
    provider.create_oauth_user.assert_not_awaited()


@pytest_asyncio.fixture
async def local_provider(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(UserRow.__table__.create)
    repository = SQLiteUserRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield LocalAuthProvider(repository)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_database_round_trip_preserves_binding_and_isolates_tenants(local_provider):
    first = await get_or_provision_cvci_user(_grant(), _config(), local_provider)
    repeat = await get_or_provision_cvci_user(_grant(), _config(), local_provider)
    another_tenant = await get_or_provision_cvci_user(_grant("4"), _config(), local_provider)
    assert repeat["created"] is False
    assert repeat["user"].id == first["user"].id
    assert repeat["user"].email == _EMAIL
    assert another_tenant["user"].id != first["user"].id
    assert another_tenant["user"].email == "cvci-t4-u42@cvci.identity.example.com"
    assert await local_provider.count_users() == 2


@pytest.mark.asyncio
async def test_reserved_alias_collision_never_takes_over_a_local_password_account(local_provider):
    local = await local_provider.create_user(_EMAIL, "mock-local-password", "admin")
    with pytest.raises(HTTPException) as raised:
        await get_or_provision_cvci_user(_grant(), _config(), local_provider)
    assert raised.value.status_code == 409
    stored = await local_provider.get_user(str(local.id))
    assert stored.oauth_provider is None and stored.oauth_id is None
    assert stored.system_role == "admin" and stored.password_hash == local.password_hash
    assert await local_provider.count_users() == 1


@pytest.mark.asyncio
async def test_real_concurrent_callbacks_create_one_bound_account(local_provider, monkeypatch):
    original_lookup = local_provider.get_user_by_oauth
    both_checked = asyncio.Event()
    initial_checks = 0

    async def lookup(provider, subject):
        nonlocal initial_checks
        result = await original_lookup(provider, subject)
        if initial_checks < 2:
            initial_checks += 1
            if initial_checks == 2:
                both_checked.set()
            await both_checked.wait()
        return result

    monkeypatch.setattr(local_provider, "get_user_by_oauth", lookup)
    results = await asyncio.wait_for(
        asyncio.gather(
            get_or_provision_cvci_user(_grant(), _config(), local_provider),
            get_or_provision_cvci_user(_grant(), _config(), local_provider),
        ),
        timeout=10,
    )
    assert results[0]["user"].id == results[1]["user"].id
    assert {result["created"] for result in results} == {False, True}
    assert await local_provider.count_users() == 1


@pytest.mark.asyncio
async def test_unbound_local_contact_account_is_not_auto_linked(local_provider):
    local = await local_provider.create_user("contact@example.com", "mock-local-password", "admin")
    result = await get_or_provision_cvci_user(_grant(), _config(), local_provider)
    assert result["user"].id != local.id
    assert result["user"].email == _EMAIL
    stored = await local_provider.get_user(str(local.id))
    assert stored.oauth_provider is None and stored.oauth_id is None
    assert stored.email == local.email and stored.system_role == local.system_role
    assert await local_provider.count_users() == 2
