"""Unified, optional browser/backend contributions from one trusted package.

Backend actions run in the Gateway process. The host supplies current settings
and an authenticated principal for each admitted call; this is not a sandbox.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from deerflow_extension_api.agent_runs import AgentRuns
from deerflow_extension_api.auth import ExtensionPrincipal
from deerflow_extension_api.host_capabilities import HostCapabilityError
from deerflow_extension_api.settings import FrontendBinding, SettingsContribution, SettingsField, SettingValue


@dataclass(frozen=True)
class McpReadTool:
    """An exact deployment tool a browser action may ask the host to read."""

    server: str
    tool: str

    def __post_init__(self) -> None:
        if not isinstance(self.server, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", self.server):
            raise ValueError("Invalid MCP read server")
        if not isinstance(self.tool, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.tool):
            raise ValueError("Invalid MCP read tool")


class SessionMcpReader(Protocol):
    """Action-lifetime, host-authorized reads. Credentials never cross this API.

    Returns a bounded JSON CallToolResult envelope with content,
    structuredContent and isError. Raises HostCapabilityError on refusal.
    The declaration is an allowlist, not proof of the current user's authority.
    """

    async def call(self, server: str, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raise HostCapabilityError("mcp_read_unavailable", "This host does not provide session MCP reads.")


@dataclass(frozen=True)
class ActionContext:
    principal: ExtensionPrincipal
    settings: Mapping[str, SettingValue]
    agent_runs: AgentRuns | None = field(default=None, kw_only=True)
    # Host-stamped browser session; model tools and other callers default closed.
    interaction: Literal["session", "other"] = field(default="other", kw_only=True)
    mcp_reader: SessionMcpReader | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class BackendAction:
    name: str
    handler: Callable[[Mapping[str, Any], ActionContext], Awaitable[Any]]
    mcp_reads: tuple[McpReadTool, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        bindings = tuple(self.mcp_reads)
        if any(not isinstance(item, McpReadTool) for item in bindings):
            raise ValueError("MCP reads must declare exact deployment tools")
        object.__setattr__(self, "mcp_reads", bindings)


@dataclass(frozen=True)
class ToolContext(ActionContext):
    """Host-bound identity for a model tool call.

    Resource ownership remains the plugin provider's responsibility,
    using principal.user_id.
    """

    thread_id: str | None


@dataclass(frozen=True)
class PluginToolResult:
    """Model-visible text plus optional JSON artifact for an opted-in tool."""

    content: str
    artifact: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ModelTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[[Mapping[str, Any], ToolContext], Awaitable[Any]]
    group: str = "extensions"
    response_format: Literal["content", "content_and_artifact"] = field(default="content", kw_only=True)


@dataclass(frozen=True)
class BrowserModule:
    """Self-contained browser module; use BrowserAssets for relative resources."""

    module: str
    code: str
    public_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_fields", tuple(self.public_fields))


@dataclass(frozen=True)
class BrowserAssets:
    """Versioned manifest and static files inside a trusted installed package.

    The host validates and snapshots the allowlisted files during registration.
    Relative paths in the manifest are resolved against root, never a request.
    """

    module: str
    root: str | Path
    manifest: str = "ui_manifest.json"
    public_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_fields", tuple(self.public_fields))


@dataclass(frozen=True)
class PluginContribution:
    """One identity, one enabled switch, optional settings and implementations.

    The host owns the boolean ``enabled`` field. Other fields are non-secret
    settings, private to the backend unless explicitly projected by its browser declaration.
    Supply at least one browser module, backend action, or model tool. Backend implementations
    are installed through the existing operator-controlled Python loader.
    """

    namespace: str
    title: str
    description: str = ""
    enabled: bool = False
    fields: tuple[SettingsField, ...] = ()
    frontend: BrowserModule | BrowserAssets | None = None
    backend: tuple[BackendAction, ...] = ()
    api_version: int = 1
    tools: tuple[ModelTool, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "backend", tuple(self.backend))
        object.__setattr__(self, "tools", tuple(self.tools))

    def settings_contribution(self) -> SettingsContribution:
        return SettingsContribution(
            namespace=self.namespace,
            title=self.title,
            description=self.description,
            fields=(SettingsField("enabled", "启用 / Enabled", "boolean", self.enabled), *self.fields),
            applies="request-and-page-load" if (self.backend or self.tools) and self.frontend else "next-request" if self.backend or self.tools else "page-load",
            frontend=FrontendBinding(self.frontend.module, ("enabled", *self.frontend.public_fields)) if self.frontend else None,
        )
