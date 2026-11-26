"""Session MCP reads use this HTTP request's authority, never an owner lookup."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deerflow_extension_api import McpReadTool
from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
from deerflow_extension_api.host_capabilities import HostCapabilityError
from deerflow_extension_api.plugins import ActionContext, BackendAction, PluginContribution
from fastapi import HTTPException
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from starlette.requests import Request

from app.gateway import session_mcp_reader as readers
from app.gateway.auth.cvci import CvciAuthorizationError
from app.gateway.auth.jwt import create_access_token
from app.gateway.routers.plugins import invoke_plugin_action
from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig

OWNER = "dc0bfa07-a74e-4c78-947d-813015d7003b"
SID = "mock-session-id-00001"
SERVER_URL = "http://cvci-mcp:48083/mcp"
TOOL = "cvci_search_archives"
BINDINGS = (McpReadTool("cvci", TOOL),)


def _request(source="session", *, owner=OWNER, sid=SID):
    token = create_access_token(owner, session_id=sid)
    service = SimpleNamespace(ticket=AsyncMock(return_value="dfmcp_test_ticket_00000001"))
    return Request({"type": "http", "method": "POST", "app": SimpleNamespace(state=SimpleNamespace(cvci_authorization=service)), "state": {"auth_source": source}, "headers": [(b"cookie", f"access_token={token}".encode())]})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    monkeypatch.setenv("DEER_FLOW_CVCI_MCP_URL", SERVER_URL)
    config = ExtensionsConfig(mcp_servers={"cvci": McpServerConfig(enabled=True, type="http", url=SERVER_URL, headers={"authorization": "Bearer discovery-fixture-secret"})})
    monkeypatch.setattr(readers, "get_extensions_config", lambda: config)
    from app.gateway import deps

    monkeypatch.setattr(deps, "get_local_provider", lambda: SimpleNamespace(get_user=AsyncMock(return_value=SimpleNamespace(id=OWNER, oauth_provider="cvci"))))
    monkeypatch.setattr(deps, "validate_resolved_session_user", lambda user, payload: None)
    return config


def _transport(monkeypatch, *, read_only=True, destructive=False, result=None, failure=None):
    session = SimpleNamespace(
        initialize=AsyncMock(),
        list_tools=AsyncMock(return_value=ListToolsResult(tools=[Tool(name=TOOL, inputSchema={"type": "object"}, annotations=ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive))])),
        call_tool=AsyncMock(return_value=result or CallToolResult(content=[TextContent(type="text", text="ok")], structuredContent={"data": {"items": []}}, isError=False), side_effect=failure),
    )
    connections = []

    @asynccontextmanager
    async def create(connection):
        connections.append(connection)
        yield session

    monkeypatch.setattr(readers, "_create_session", create)
    return session, connections


def test_public_contract_is_additive_and_declarations_are_immutable():
    async def handler(payload, context):
        return {}

    assert ActionContext(ExtensionPrincipal(OWNER), {}).mcp_reader is None
    assert BackendAction("plain", handler).mcp_reads == ()
    mutable = list(BINDINGS)
    action = BackendAction("search", handler, mcp_reads=mutable)
    mutable.clear()
    assert action.mcp_reads == BINDINGS
    with pytest.raises(ValueError):
        McpReadTool("https://attacker.invalid/", TOOL)


@pytest.mark.asyncio
async def test_current_sid_and_owner_issue_a_new_ticket_per_search(configured, monkeypatch):
    session, connections = _transport(monkeypatch)
    request = _request()
    with readers.session_mcp_reader_scope(request, ExtensionPrincipal(OWNER), BINDINGS) as reader:
        result = await reader.call("cvci", TOOL, {"kind": "VEHICLE", "keyword": "A"})
        await reader.call("cvci", TOOL, {"kind": "SITE", "keyword": "B"})
    assert result["structuredContent"] == {"data": {"items": []}}
    assert request.app.state.cvci_authorization.ticket.await_args_list[0].args == (SID, OWNER)
    assert request.app.state.cvci_authorization.ticket.await_count == 2
    assert session.call_tool.await_count == 2
    assert all(connection["url"] == SERVER_URL for connection in connections)
    assert all(connection["headers"] == {"authorization": "Bearer dfmcp_test_ticket_00000001"} for connection in connections)
    assert "dfmcp_" not in str(result) and "discovery-fixture-secret" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["pat", "internal", None])
async def test_non_session_callers_never_receive_a_reader(configured, source):
    request = _request(source)
    with readers.session_mcp_reader_scope(request, ExtensionPrincipal(OWNER), BINDINGS) as reader:
        assert reader is None
    request.app.state.cvci_authorization.ticket.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_sessions_and_wrong_owners_cannot_borrow_a_grant(configured, monkeypatch):
    session, connections = _transport(monkeypatch)
    for request, principal in [(_request(sid=None), ExtensionPrincipal(OWNER)), (_request(), ExtensionPrincipal("other-owner"))]:
        with readers.session_mcp_reader_scope(request, principal, BINDINGS) as reader:
            with pytest.raises(HostCapabilityError, match="mcp_read_session_unavailable"):
                await reader.call("cvci", TOOL, {})
        request.app.state.cvci_authorization.ticket.assert_not_awaited()
    assert connections == []
    session.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_grant_revocation_fails_without_transport_or_credentials(configured, monkeypatch):
    _, connections = _transport(monkeypatch)
    request = _request()
    request.app.state.cvci_authorization.ticket.side_effect = CvciAuthorizationError("secret upstream response")
    with readers.session_mcp_reader_scope(request, ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError) as error:
            await reader.call("cvci", TOOL, {})
    assert error.value.code == "mcp_read_session_unavailable"
    assert "secret" not in str(error.value)
    assert connections == []


@pytest.mark.asyncio
async def test_declared_allowlist_precedes_ticket_and_transport(configured, monkeypatch):
    _, connections = _transport(monkeypatch)
    request = _request()
    with readers.session_mcp_reader_scope(request, ExtensionPrincipal(OWNER), BINDINGS) as reader:
        for server, tool in [("cvci", "cvci_query_relations"), ("attacker", TOOL)]:
            with pytest.raises(HostCapabilityError, match="mcp_read_not_declared"):
                await reader.call(server, tool, {})
    request.app.state.cvci_authorization.ticket.assert_not_awaited()
    assert connections == []


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only,destructive", [(False, False), (True, True)])
async def test_read_only_hint_and_non_destructive_metadata_are_required(configured, monkeypatch, read_only, destructive):
    session, _ = _transport(monkeypatch, read_only=read_only, destructive=destructive)
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError, match="mcp_read_not_readonly"):
            await reader.call("cvci", TOOL, {})
    session.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_deployment_url_fails_closed(configured, monkeypatch):
    configured.mcp_servers["cvci"].url = "https://attacker.invalid/mcp"
    _, connections = _transport(monkeypatch)
    request = _request()
    with readers.session_mcp_reader_scope(request, ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError, match="mcp_read_unavailable"):
            await reader.call("cvci", TOOL, {})
    request.app.state.cvci_authorization.ticket.assert_not_awaited()
    assert connections == []


@pytest.mark.asyncio
async def test_captured_reader_and_inherited_task_expire_at_action_end(configured, monkeypatch):
    _, connections = _transport(monkeypatch)
    gate = asyncio.Event()
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:

        async def later():
            await gate.wait()
            return await reader.call("cvci", TOOL, {})

        task = asyncio.create_task(later())
    gate.set()
    with pytest.raises(HostCapabilityError, match="mcp_read_expired"):
        await task
    assert connections == []


@pytest.mark.asyncio
async def test_transport_failures_and_credential_echoes_are_not_returned(configured, monkeypatch):
    _transport(monkeypatch, failure=RuntimeError("Authorization: Bearer dfmcp_test_ticket_00000001"))
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError) as error:
            await reader.call("cvci", TOOL, {})
    assert error.value.code == "mcp_read_failed"
    assert "dfmcp" not in str(error.value)
    _transport(monkeypatch, result=CallToolResult(content=[TextContent(type="text", text="dfmcp_test_ticket_00000001")]))
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError, match="mcp_read_failed"):
            await reader.call("cvci", TOOL, {})


@pytest.mark.asyncio
async def test_arguments_and_results_are_bounded(configured, monkeypatch):
    session, _ = _transport(monkeypatch)
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError, match="mcp_read_invalid_input"):
            await reader.call("cvci", TOOL, {"query": "x" * 40000})
        session.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text="x" * 300000)])
        with pytest.raises(HostCapabilityError, match="mcp_read_result_too_large"):
            await reader.call("cvci", TOOL, {})


def _action_request(handler, *, source="session", declarations=BINDINGS, enabled=True):
    request = _request(source)
    plugin = PluginContribution(namespace="example.read", title="Read", enabled=enabled, backend=(BackendAction("search", handler, mcp_reads=declarations),))
    request.app.state.extensions = SimpleNamespace(plugins=(("fixture", plugin),))
    setattr(request.app.state, EXTENSION_PRINCIPAL_RESOLVER_KEY, lambda incoming: ExtensionPrincipal(OWNER))

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request._receive = receive
    return request


@pytest.mark.asyncio
async def test_action_route_supplies_the_declared_reader_and_expires_it(configured, monkeypatch):
    from app.gateway import authz

    monkeypatch.setattr(authz, "authorize_plugin_action_for_request", AsyncMock())
    _transport(monkeypatch)
    contexts = []

    async def search(payload, context):
        contexts.append(context)
        assert context.interaction == "session"
        return await context.mcp_reader.call("cvci", TOOL, {"kind": "SITE"})

    result = await invoke_plugin_action(_action_request(search), "example.read", "search")
    assert result["structuredContent"]["data"]["items"] == []
    with pytest.raises(HostCapabilityError, match="mcp_read_expired"):
        await contexts[0].mcp_reader.call("cvci", TOOL, {})


@pytest.mark.asyncio
async def test_unavailable_grant_is_a_safe_action_error_not_a_global_401(configured, monkeypatch):
    from app.gateway import authz

    monkeypatch.setattr(authz, "authorize_plugin_action_for_request", AsyncMock())

    async def search(payload, context):
        return await context.mcp_reader.call("cvci", TOOL, {})

    request = _action_request(search)
    request.app.state.cvci_authorization.ticket.side_effect = CvciAuthorizationError("private-token-data")
    with pytest.raises(HTTPException) as error:
        await invoke_plugin_action(request, "example.read", "search")
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "mcp_read_session_unavailable"
    assert "private-token-data" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_router_expires_the_reader_when_the_handler_fails(configured, monkeypatch):
    from app.gateway import authz

    monkeypatch.setattr(authz, "authorize_plugin_action_for_request", AsyncMock())
    readers_seen = []

    async def search(payload, context):
        readers_seen.append(context.mcp_reader)
        raise ValueError("unsafe handler details")

    with pytest.raises(HTTPException) as error:
        await invoke_plugin_action(_action_request(search), "example.read", "search")
    assert error.value.status_code == 422
    with pytest.raises(HostCapabilityError, match="mcp_read_expired"):
        await readers_seen[0].call("cvci", TOOL, {})


@pytest.mark.asyncio
async def test_disabled_or_denied_action_cannot_prepare_a_ticket(configured, monkeypatch):
    from app.gateway import authz

    permission = AsyncMock(side_effect=HTTPException(403, "denied"))
    monkeypatch.setattr(authz, "authorize_plugin_action_for_request", permission)
    prepare = AsyncMock()
    monkeypatch.setattr(readers, "prepare_cvci_ticket_provider", prepare)
    handler = AsyncMock()
    for enabled in (False, True):
        with pytest.raises(HTTPException) as error:
            await invoke_plugin_action(_action_request(handler, enabled=enabled), "example.read", "search")
        assert error.value.status_code == 403
    prepare.assert_not_called()
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_actions_without_declarations_keep_their_context(configured, monkeypatch):
    from app.gateway import authz

    monkeypatch.setattr(authz, "authorize_plugin_action_for_request", AsyncMock())
    prepare = AsyncMock()
    monkeypatch.setattr(readers, "prepare_cvci_ticket_provider", prepare)

    async def original(payload, context):
        assert context.mcp_reader is None
        assert context.principal.user_id == OWNER
        assert context.interaction == "session"
        return {"original": True}

    assert await invoke_plugin_action(_action_request(original, declarations=()), "example.read", "search") == {"original": True}
    prepare.assert_not_called()


@pytest.mark.asyncio
async def test_action_end_cancels_inflight_read_and_prevents_returning_results(configured, monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    session, _ = _transport(monkeypatch)

    async def pending(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    session.call_tool.side_effect = pending
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:
        task = asyncio.create_task(reader.call("cvci", TOOL, {}))
        await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(HostCapabilityError, match="mcp_read_expired"):
        await asyncio.wait_for(task, 1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_timeout_has_a_safe_machine_readable_error(configured, monkeypatch):
    monkeypatch.setattr(readers, "_CALL_TIMEOUT", 0.03)
    session, _ = _transport(monkeypatch)
    session.call_tool.side_effect = lambda *args: None

    async def pending(*args):
        await asyncio.Event().wait()

    session.call_tool.side_effect = pending
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER), BINDINGS) as reader:
        with pytest.raises(HostCapabilityError, match="mcp_read_timeout"):
            await reader.call("cvci", TOOL, {})


@pytest.mark.asyncio
async def test_internal_principal_cannot_claim_session_reader(configured):
    with readers.session_mcp_reader_scope(_request(), ExtensionPrincipal(OWNER, is_internal=True), BINDINGS) as reader:
        assert reader is None


def test_unknown_capability_details_are_not_exposed():
    assert readers.mcp_read_http_error("private-host-path") == (502, {"code": "plugin_capability_unavailable", "message": "Plugin capability unavailable."})


@pytest.mark.asyncio
async def test_explicit_business_access_denial_is_a_fixed_safe_action_403(configured, monkeypatch):
    from app.gateway import authz

    monkeypatch.setattr(authz, "authorize_plugin_action_for_request", AsyncMock())

    async def search(payload, context):
        raise HostCapabilityError("mcp_read_access_denied", "private upstream response, credential, and host path")

    with pytest.raises(HTTPException) as error:
        await invoke_plugin_action(_action_request(search), "example.read", "search")
    assert error.value.status_code == 403
    assert error.value.detail == {"code": "mcp_read_access_denied", "message": "You do not have permission to search these business records. Contact your CVCI administrator."}
