from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.tools import ToolException
from starlette.requests import Request

from app.gateway.auth import cvci
from app.gateway.auth.cvci import CvciAuthorization, CvciAuthorizationError, CvciGrant
from app.gateway.auth.jwt import create_access_token, decode_token
from app.gateway.auth.oidc import OIDCIdentity
from app.gateway.auth.oidc_state import OIDCStatePayload
from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL, AUTH_SOURCE_PAT, AUTH_SOURCE_SESSION
from app.gateway.routers import auth
from deerflow.config.auth_config import OIDCAuthConfig, OIDCProviderConfig
from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig
from deerflow.mcp.client import build_server_params
from deerflow.mcp.trusted_auth import build_trusted_cvci_interceptor, trusted_mcp_ticket_scope

_GRANT = "test-grant-id-000000001"
_MCP_URL = "http://cvci-mcp:48083/mcp"
_BRIDGE = "http://cvci-server:48080/admin-api/cvci/internal/deerflow"
_OWNER = "dc0bfa07-a74e-4c78-947d-813015d7003b"


def _identity() -> OIDCIdentity:
    signed = {"aud": "deerflow", "sub": "cvci:3:12", "cvci_grant_id": _GRANT, "cvci_tenant_id": "3", "cvci_user_id": "12"}
    unsigned_override = {**signed, "cvci_grant_id": "untrusted-grant", "cvci_tenant_id": "4"}
    return OIDCIdentity("cvci", "cvci:3:12", "user@example.test", True, None, unsigned_override, signed)


def test_grant_only_uses_signed_identity_claims():
    identity = _identity()
    assert CvciGrant.from_identity(identity, "deerflow", _OWNER).model_dump() == {
        "grantId": _GRANT,
        "tenantId": "3",
        "userId": "12",
        "deerFlowUserId": _OWNER,
    }
    for claims in ({}, {**identity.signed_claims, "cvci_user_id": 12}, {**identity.signed_claims, "cvci_tenant_id": "4"}, {**identity.signed_claims, "aud": ["deerflow", "another"]}):
        with pytest.raises(CvciAuthorizationError):
            CvciGrant.from_identity(replace(identity, signed_claims=claims), "deerflow", _OWNER)


@pytest.mark.asyncio
async def test_ticket_per_call_and_logout_is_session_specific():
    entries: dict[str, str] = {}
    calls: list[tuple[str, dict]] = []

    async def set_entry(key, value, *, ex, nx):
        assert ex == 8 * 60 * 60 and nx is True
        entries[key] = value
        return True

    def endpoint(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-CVCI-DeerFlow-Key"] == "mock-deerflow-bridge-key"
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("/ticket/issue"):
            return httpx.Response(200, json={"code": 0, "data": {"ticket": f"dfmcp_mock_ticket_000000{len(calls)}", "expiresIn": 30}})
        return httpx.Response(200, json={"code": 0})

    redis = SimpleNamespace(set=set_entry, get=AsyncMock(side_effect=entries.get), getdel=AsyncMock(side_effect=entries.pop))
    grant = CvciGrant.from_identity(_identity(), "deerflow", _OWNER)
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        service = CvciAuthorization(redis, client, _BRIDGE, "mock-deerflow-bridge-key")
        session = await service.bind(grant)
        other_grant = grant.model_copy(update={"grantId": "test-grant-id-000000002"})
        other_session = await service.bind(other_grant)
        assert session != other_session and session != grant.grantId
        with pytest.raises(CvciAuthorizationError):
            await service.ticket(session, "a-different-owner")
        assert await service.ticket(session, _OWNER) != await service.ticket(session, _OWNER)
        await service.logout(session, _OWNER)
        with pytest.raises(CvciAuthorizationError):
            await service.ticket(session, _OWNER)
        await service.ticket(other_session, _OWNER)

    assert [path.rsplit("/", 1)[-1] for path, _ in calls] == ["bind", "bind", "issue", "issue", "revoke", "issue"]
    assert calls[4][1] == grant.principal()
    assert calls[5][1] == other_grant.principal()


@dataclass
class _ToolRequest:
    server_name: str
    headers: dict[str, str]
    runtime: object | None = None

    def override(self, **changes):
        return replace(self, **changes)


@pytest.mark.asyncio
async def test_trusted_interceptor_pins_endpoint_and_ignores_runtime_secrets(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    monkeypatch.setenv("DEER_FLOW_CVCI_MCP_URL", _MCP_URL)
    server = McpServerConfig(enabled=True, type="http", url=_MCP_URL, headers={"authorization": "Bearer discovery"})
    config = ExtensionsConfig(mcp_servers={"cvci": server})
    interceptor = build_trusted_cvci_interceptor(config)
    assert interceptor is not None
    assert "httpx_client_factory" in build_server_params("cvci", server)
    with pytest.raises(ValueError, match="deployment HTTP URL"):
        build_server_params("cvci", server.model_copy(update={"url": "https://untrusted.example.test/mcp"}))
    assert "httpx_client_factory" not in build_server_params("personal_mock", server)

    request = _ToolRequest("cvci", {}, SimpleNamespace(context={"secrets": {"Authorization": "fake-ticket"}}))
    handler = AsyncMock()
    with pytest.raises(ToolException, match="Sign in through CVCI"):
        await interceptor(request, handler)
    handler.assert_not_awaited()

    issue = AsyncMock(side_effect=["dfmcp_mock_ticket_0000001", "dfmcp_mock_ticket_0000002"])
    with trusted_mcp_ticket_scope(issue):
        await interceptor(request, handler)
        await interceptor(request, handler)
    assert issue.await_count == 2
    assert [call.args[0].headers for call in handler.await_args_list] == [
        {"authorization": "Bearer dfmcp_mock_ticket_0000001"},
        {"authorization": "Bearer dfmcp_mock_ticket_0000002"},
    ]
    with pytest.raises(ToolException):
        await interceptor(request, handler)

    release = asyncio.Event()

    async def delayed_call():
        await release.wait()
        await interceptor(request, handler)

    with trusted_mcp_ticket_scope(issue):
        background = asyncio.create_task(delayed_call())
    release.set()
    with pytest.raises(ToolException):
        await background
    assert issue.await_count == 2


def test_only_an_authenticated_browser_session_can_prepare_ticket_provider(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    app = FastAPI()
    app.state.cvci_authorization = object()
    token = create_access_token(_OWNER, session_id="mock-session-id-00001")

    def request(auth_source, cookie):
        return Request({"type": "http", "app": app, "state": {"auth_source": auth_source}, "headers": [(b"cookie", f"access_token={cookie}".encode())]})

    assert cvci.prepare_cvci_ticket_provider(request(AUTH_SOURCE_SESSION, token), _OWNER) is not None
    assert cvci.prepare_cvci_ticket_provider(request(AUTH_SOURCE_SESSION, create_access_token(_OWNER)), _OWNER) is None
    assert cvci.prepare_cvci_ticket_provider(request(AUTH_SOURCE_PAT, token), _OWNER) is None
    assert cvci.prepare_cvci_ticket_provider(request(AUTH_SOURCE_INTERNAL, token), _OWNER) is None
    assert cvci.prepare_cvci_ticket_provider(request(AUTH_SOURCE_SESSION, token), "other-user") is None


def test_callback_preserves_uuid_and_failure_has_its_own_page(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    app = FastAPI()
    app.include_router(auth.router)
    bridge = SimpleNamespace(bind=AsyncMock(return_value="new-mock-session-00001"), logout=AsyncMock())
    app.state.cvci_authorization = bridge
    internal_discovery = "http://cvci-sso:48084/sso/oidc/.well-known/openid-configuration"
    cfg = OIDCAuthConfig(
        enabled=True,
        providers={"cvci": OIDCProviderConfig(display_name="CVCI", issuer="https://cvci.example.test", client_id="deerflow", client_secret="test-secret", discovery_endpoint=internal_discovery)},
    )
    monkeypatch.setattr(auth, "_oidc_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(auth, "get_state_cookie", lambda *_: OIDCStatePayload(provider="cvci", state="test-state", nonce="test-nonce", code_verifier="test-verifier"))
    monkeypatch.setattr(auth, "get_local_provider", lambda: object())
    oidc = SimpleNamespace(discover=AsyncMock(), authenticate_callback=AsyncMock(return_value=_identity()))
    monkeypatch.setattr(auth, "_get_oidc_service", lambda: oidc)
    provision = AsyncMock(return_value={"user": SimpleNamespace(id=_OWNER, token_version=7)})
    monkeypatch.setattr(auth, "get_or_provision_oidc_user", provision)

    old_token = create_access_token("old-owner", session_id="old-mock-session-00001")
    with TestClient(app, base_url="https://deerflow.example.test") as client:
        client.cookies.set("access_token", old_token)
        response = client.get("/api/v1/auth/callback/cvci?code=mock-code&state=test-state", follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"].startswith("/auth/callback?")
        assert response.headers["cache-control"] == "no-store"
        payload = decode_token(response.cookies["access_token"])
        assert payload.sub == _OWNER and payload.sid == "new-mock-session-00001" and payload.ver == 7
        bridge.logout.assert_awaited_once_with("old-mock-session-00001", "old-owner")
        assert bridge.bind.await_args.args[0].deerFlowUserId == _OWNER
        assert oidc.discover.await_args.args[1]["discovery_endpoint"] == internal_discovery

        oidc.authenticate_callback.return_value = replace(_identity(), signed_claims={})
        failure = client.get("/api/v1/auth/callback/cvci?code=other-code&state=test-state", follow_redirects=False)
        assert failure.status_code == 302 and failure.headers["location"] == "/auth/cvci-failed"
        assert "access_token=" not in "".join(failure.headers.get_list("set-cookie"))
        assert provision.await_count == 1
