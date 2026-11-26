from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.gateway.auth.cvci import CvciAuthorization, CvciAuthorizationError, CvciGrant
from app.gateway.auth.jwt import create_access_token
from deerflow.config.auth_config import AuthAppConfig, LocalAuthConfig


def _request(cookies: str = "") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"cookie", cookies.encode())], "scheme": "http", "server": ("localhost", 2026)})


def test_browser_policy_defaults_preserve_generic_auth():
    assert LocalAuthConfig().enabled is True
    assert AuthAppConfig().required_oidc_provider is None
    assert LocalAuthConfig(enabled=False).enabled is False


def test_required_provider_cannot_silently_fall_back_to_passwords():
    with pytest.raises(ValidationError):
        AuthAppConfig(required_oidc_provider="cvci")


def test_generation_cookie_cannot_fall_back_to_a_legacy_admin_cookie():
    from app.gateway.auth.browser_session import ACTIVE_SESSION_COOKIE, SESSION_COOKIE_PREFIX, read_browser_token

    sid = "a" * 43
    assert read_browser_token({"access_token": "legacy"}, strict=True) is None
    assert read_browser_token({"access_token": "legacy"}, strict=False) == "legacy"
    assert read_browser_token({ACTIVE_SESSION_COOKIE: sid, SESSION_COOKIE_PREFIX + sid: "current", "access_token": "legacy"}, strict=True) == "current"
    assert read_browser_token({ACTIVE_SESSION_COOKIE: "../invalid", "access_token": "legacy"}, strict=True) is None


def test_clearing_old_cookie_does_not_delete_the_new_session_selector():
    from fastapi import Response

    from app.gateway.auth.browser_session import ACTIVE_SESSION_COOKIE, SESSION_COOKIE_PREFIX, clear_browser_session

    sid = "a" * 43
    response = Response()
    clear_browser_session(response, _request(), sid)
    cookies = response.headers.getlist("set-cookie")
    assert any(item.startswith(SESSION_COOKIE_PREFIX + sid + "=") and "Max-Age=0" in item for item in cookies)
    assert not any(item.startswith(ACTIVE_SESSION_COOKIE + "=") or item.startswith("csrf_token=") or item.startswith("access_token=") for item in cookies)


def test_session_fence_changes_with_owner_and_session_and_is_not_a_bearer():
    from app.gateway.auth.browser_session import browser_session_fence

    first = browser_session_fence("user-a", "a" * 43)
    assert len(first) == 64
    assert first != browser_session_fence("user-b", "a" * 43)
    assert first != browser_session_fence("user-a", "b" * 43)
    assert first != "a" * 43


@pytest.mark.asyncio
async def test_live_grant_check_is_read_only_and_uses_bound_owner():
    grant = CvciGrant(grantId="grant-identifier-0001", tenantId="3", userId="42", deerFlowUserId="owner-a")
    redis = SimpleNamespace(get=AsyncMock(return_value=grant.model_dump_json()))
    calls = []

    def reply(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"code": 0, "data": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = CvciAuthorization(redis, client, "http://server/admin-api/cvci/internal/deerflow", "test-key")
        await service.check("a" * 43, "owner-a")
    assert calls == [("/admin-api/cvci/internal/deerflow/grant/check", grant.principal())]


@pytest.mark.asyncio
async def test_failed_global_logout_retains_the_original_subject_for_retry():
    grant = CvciGrant(grantId="grant-identifier-0001", tenantId="3", userId="42", deerFlowUserId="owner-a")
    redis = SimpleNamespace(get=AsyncMock(return_value=grant.model_dump_json()), getdel=AsyncMock(), delete=AsyncMock(), eval=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        service = CvciAuthorization(redis, client, "http://server/admin-api/cvci/internal/deerflow", "test-key")
        with pytest.raises(CvciAuthorizationError):
            await service.logout_all("a" * 43, "owner-a")
    redis.getdel.assert_not_awaited()
    redis.delete.assert_not_awaited()
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_logout_clears_only_after_confirmed_upstream_receipt():
    grant = CvciGrant(grantId="grant-identifier-0001", tenantId="3", userId="42", deerFlowUserId="owner-a")
    order = []

    async def finish(*args):
        order.append("local-receipt")
        return 1

    def reply(request):
        order.append(request.url.path.rsplit("/", 1)[-1])
        assert json.loads(request.content) == grant.principal()
        return httpx.Response(200, json={"code": 0, "data": {"status": "completed"}})

    redis = SimpleNamespace(get=AsyncMock(return_value=grant.model_dump_json()), eval=finish)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = CvciAuthorization(redis, client, "http://server/admin-api/cvci/internal/deerflow", "test-key")
        await service.logout_all("a" * 43, "owner-a")
    assert order == ["logout-all", "local-receipt"]


@pytest.fixture
def strict_client(monkeypatch):
    from uuid import UUID

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway import auth_middleware, deps
    from app.gateway.auth import config as jwt_config
    from app.gateway.auth.browser_session import BrowserAuthPolicy
    from app.gateway.auth.models import User
    from app.gateway.routers import auth

    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    monkeypatch.setattr(jwt_config, "_auth_config", jwt_config.AuthConfig(jwt_secret="unified-tests-private-secret-not-production"))
    first = User(id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), email="a@example.com", oauth_provider="cvci", system_role="user")
    second = User(id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), email="b@example.com", oauth_provider="cvci", system_role="admin")
    users = {str(user.id): user for user in (first, second)}
    provider = SimpleNamespace(get_user=AsyncMock(side_effect=users.get), authenticate=AsyncMock(), count_admin_users=AsyncMock())
    authority = SimpleNamespace(check=AsyncMock(), logout_all=AsyncMock())
    monkeypatch.setattr(deps, "get_local_provider", lambda: provider)
    monkeypatch.setattr(auth, "get_local_provider", lambda: provider)
    monkeypatch.setattr(auth_middleware, "resolve_route_permissions", AsyncMock(return_value=[]))
    app = FastAPI()
    app.state.browser_auth_policy = BrowserAuthPolicy(False, "cvci")
    app.state.cvci_authorization = authority
    app.include_router(auth.router)
    app.add_middleware(auth_middleware.AuthMiddleware)

    @app.get("/protected")
    async def protected(request: Request):
        return {"id": str(request.state.user.id), "role": request.state.user.system_role}

    def cookies(user=first, sid="a" * 43):
        from app.gateway.auth.browser_session import ACTIVE_SESSION_COOKIE, SESSION_COOKIE_PREFIX

        return {ACTIVE_SESSION_COOKIE: sid, SESSION_COOKIE_PREFIX + sid: create_access_token(str(user.id), session_id=sid)}

    with TestClient(app) as client:
        yield client, first, second, provider, authority, cookies


def test_old_admin_cookie_is_rejected_even_when_its_user_is_linked_to_cvci(strict_client):
    client, _, admin, _, authority, _ = strict_client
    response = client.get("/protected", cookies={"access_token": create_access_token(str(admin.id))})
    assert response.status_code == 401
    authority.check.assert_not_awaited()


def test_current_session_has_online_check_and_never_uses_admin_fallback(strict_client):
    client, first, admin, _, authority, cookies = strict_client
    response = client.get("/protected", cookies={**cookies(), "access_token": create_access_token(str(admin.id))})
    assert response.status_code == 200
    assert response.json() == {"id": str(first.id), "role": "user"}
    authority.check.assert_awaited_once_with("a" * 43, str(first.id))


@pytest.mark.parametrize("unavailable", [False, True])
def test_rejected_or_unavailable_authority_never_admits_cookie(strict_client, unavailable):
    from app.gateway.auth.cvci import CvciSessionInvalid

    client, _, _, _, authority, cookies = strict_client
    authority.check.side_effect = CvciAuthorizationError("unavailable") if unavailable else CvciSessionInvalid("revoked")
    response = client.get("/protected", cookies=cookies())
    assert response.status_code == (503 if unavailable else 401)


def test_local_auth_endpoints_reject_without_authentication_or_creation(strict_client):
    client, _, _, provider, _, cookies = strict_client
    response = client.post("/api/v1/auth/login/local", data={"username": "admin@example.com", "password": "irrelevant"})
    assert response.status_code == 403
    provider.authenticate.assert_not_awaited()
    for route in ("register", "initialize"):
        response = client.post("/api/v1/auth/" + route, json={"email": "new@example.com", "password": "Random-safe-password-912!"})
        assert response.status_code == 403
    provider.count_admin_users.assert_not_awaited()
    response = client.post("/api/v1/auth/change-password", cookies=cookies(), json={"current_password": "old", "new_password": "Random-safe-password-912!"})
    assert response.status_code == 403


def test_global_logout_keeps_proof_on_failure_and_rejects_stale_browser_action(strict_client):
    from app.gateway.auth.browser_session import ACTIVE_SESSION_COOKIE, browser_session_fence

    client, first, second, _, authority, cookies = strict_client
    proof = browser_session_fence(str(first.id), "a" * 43)
    authority.logout_all.side_effect = CvciAuthorizationError("unavailable")
    response = client.post("/api/v1/auth/logout", cookies=cookies(), json={"expected_session": proof})
    assert response.status_code == 503
    assert not response.headers.get_list("set-cookie")
    authority.logout_all.assert_awaited_once_with("a" * 43, str(first.id))
    authority.logout_all.reset_mock()
    response = client.post("/api/v1/auth/logout", cookies=cookies(second, "b" * 43), json={"expected_session": proof})
    assert response.status_code == 409
    authority.logout_all.assert_not_awaited()
    assert not any(header.startswith(ACTIVE_SESSION_COOKIE + "=") for header in response.headers.get_list("set-cookie"))


def test_late_logout_cookie_response_cannot_clear_a_new_login(strict_client):
    from app.gateway.auth.browser_session import ACTIVE_SESSION_COOKIE, SESSION_COOKIE_PREFIX, browser_session_fence

    client, first, second, _, _, cookies = strict_client
    response = client.post("/api/v1/auth/logout", cookies=cookies(), json={"expected_session": browser_session_fence(str(first.id), "a" * 43)})
    assert response.status_code == 200
    jar = httpx.Cookies()
    for key, value in cookies(second, "b" * 43).items():
        jar.set(key, value, domain="testserver.local", path="/")
    jar.extract_cookies(httpx.Response(200, headers=response.headers, request=httpx.Request("POST", "http://testserver/api/v1/auth/logout")))
    assert jar.get(ACTIVE_SESSION_COOKIE) == "b" * 43
    assert jar.get(SESSION_COOKIE_PREFIX + "b" * 43)
    assert not any(item.startswith(ACTIVE_SESSION_COOKIE + "=") for item in response.headers.get_list("set-cookie"))


def test_cleanup_never_revokes_a_live_new_identity(strict_client):
    from app.gateway.auth.browser_session import browser_session_fence

    client, first, second, _, authority, cookies = strict_client
    response = client.post("/api/v1/auth/logout/cleanup", cookies=cookies(second, "b" * 43), json={"expected_session": browser_session_fence(str(first.id), "a" * 43)})
    assert response.status_code == 200
    assert response.json() == {"status": "unchanged"}
    assert not response.headers.get_list("set-cookie")
    authority.logout_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_sse_is_closed_when_the_original_authority_is_revoked():
    from fastapi import HTTPException

    from app.gateway.auth.browser_session import guard_browser_events

    closed = []

    async def events():
        try:
            yield b"first"
            import asyncio

            await asyncio.sleep(30)
            yield b"must-not-leak"
        finally:
            closed.append(True)

    check = AsyncMock(side_effect=[None, HTTPException(401, "revoked")])
    result = [chunk async for chunk in guard_browser_events(events(), check, interval=0.005)]
    assert result == [b"first"]
    assert closed == [True]
