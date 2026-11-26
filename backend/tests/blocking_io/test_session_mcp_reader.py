"""Plugin search performs deployment-config filesystem reads off the event loop."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deerflow_extension_api import McpReadTool
from deerflow_extension_api.auth import ExtensionPrincipal
from mcp.types import CallToolResult, ListToolsResult, Tool, ToolAnnotations
from starlette.requests import Request

from app.gateway import session_mcp_reader as readers
from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig


@pytest.mark.asyncio
async def test_session_read_offloads_the_real_config_resolution(monkeypatch, tmp_path):
    probe = tmp_path / "deployment-probe"
    probe.write_text("configured", encoding="utf-8")
    loaded = []
    config = ExtensionsConfig(mcp_servers={"cvci": McpServerConfig(enabled=True, type="http", url="http://cvci-mcp:48083/mcp")})

    def load():
        loaded.append(probe.read_text(encoding="utf-8"))
        return config

    monkeypatch.setattr(readers, "get_extensions_config", load)
    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    monkeypatch.setenv("DEER_FLOW_CVCI_MCP_URL", "http://cvci-mcp:48083/mcp")
    monkeypatch.setattr(readers, "prepare_cvci_ticket_provider", lambda request, owner: AsyncMock(return_value="dfmcp_fixture_ticket_00001"))
    session = SimpleNamespace(
        initialize=AsyncMock(),
        list_tools=AsyncMock(return_value=ListToolsResult(tools=[Tool(name="cvci_search_archives", inputSchema={"type": "object"}, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))])),
        call_tool=AsyncMock(return_value=CallToolResult(content=[], structuredContent={"ok": True})),
    )

    @asynccontextmanager
    async def create(connection):
        yield session

    monkeypatch.setattr(readers, "_create_session", create)
    request = Request({"type": "http", "headers": [], "state": {"auth_source": "session"}})
    with readers.session_mcp_reader_scope(request, ExtensionPrincipal("owner"), (McpReadTool("cvci", "cvci_search_archives"),)) as reader:
        result = await reader.call("cvci", "cvci_search_archives", {"kind": "SITE"})
    assert result["structuredContent"] == {"ok": True}
    assert loaded == ["configured"]
    session.call_tool.assert_awaited_once_with("cvci_search_archives", {"kind": "SITE"})
