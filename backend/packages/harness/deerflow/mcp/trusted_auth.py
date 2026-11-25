"""MCP credentials provided by the Gateway for a single admitted run."""

from __future__ import annotations

import os
import ssl
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
from langchain_core.tools import ToolException

from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig
from deerflow.mcp.headers import apply_header_overrides, header_spellings, illegal_header_value_reason

CVCI_SERVER = "cvci"
TicketProvider = Callable[[], Awaitable[str]]


@dataclass(slots=True)
class _TicketScope:
    provider: TicketProvider | None


_ticket_scope: ContextVar[_TicketScope | None] = ContextVar("trusted_mcp_ticket_scope", default=None)


def cvci_oidc_enabled() -> bool:
    return os.environ.get("DEER_FLOW_CVCI_OIDC_ENABLED") == "1"


@contextmanager
def trusted_mcp_ticket_scope(provider: TicketProvider | None) -> Iterator[None]:
    """Keep host authority outside graph context, inputs and checkpoints."""
    scope = _TicketScope(provider)
    token = _ticket_scope.set(scope)
    try:
        yield
    finally:
        # A task may have inherited this ContextVar before the run ended.
        # Invalidate its shared scope as well as resetting our own context.
        scope.provider = None
        _ticket_scope.reset(token)


def require_trusted_cvci_server(name: str, config: McpServerConfig) -> bool:
    if name != CVCI_SERVER or not cvci_oidc_enabled():
        return False
    expected_url = os.environ.get("DEER_FLOW_CVCI_MCP_URL")
    if not expected_url or config.url != expected_url or config.type != "http":
        raise ValueError("The CVCI MCP server must match its deployment HTTP URL")
    try:
        url = httpx.URL(expected_url)
    except (httpx.InvalidURL, ValueError):
        raise ValueError("The CVCI MCP deployment URL is invalid") from None
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
        raise ValueError("The CVCI MCP deployment URL is invalid")
    if config.oauth or config.user_auth or config.headers_from_context or config.task_toolsets or (config.model_extra or {}).get("personal_public_network"):
        raise ValueError("The CVCI MCP server only supports its deployment authentication and discovery headers")
    return True


def cvci_httpx_client_factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    # A ticket may only reach the pinned deployment endpoint. Ignore proxy
    # variables and refuse all redirects, even same-origin path redirects.
    verify = _tls_context(os.environ.get("SSL_CERT_FILE"))
    return httpx.AsyncClient(headers=headers, timeout=timeout if timeout is not None else 30.0, auth=auth, verify=verify, follow_redirects=False, trust_env=False)


@lru_cache(maxsize=2)
def _tls_context(cafile: str | None) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=cafile)


def build_trusted_cvci_interceptor(config: ExtensionsConfig) -> Any | None:
    server = config.get_enabled_mcp_servers().get(CVCI_SERVER)
    if server is None or not require_trusted_cvci_server(CVCI_SERVER, server):
        return None
    spellings = header_spellings(server.headers)

    async def intercept(request: Any, handler: Any) -> Any:
        if request.server_name != CVCI_SERVER:
            return await handler(request)
        scope = _ticket_scope.get()
        provider = scope.provider if scope is not None else None
        if provider is None:
            raise ToolException("Sign in through CVCI and start a new chat request to use CVCI tools.")
        ticket = await provider()
        if scope.provider is not provider or not ticket or illegal_header_value_reason(ticket):
            raise ToolException("CVCI authorization is unavailable. Sign in through CVCI again.")
        headers = apply_header_overrides(request.headers, {"Authorization": f"Bearer {ticket}"}, spellings=spellings)
        try:
            return await handler(request.override(headers=headers))
        except Exception:
            # Keep transport errors that might mention authorization headers
            # out of the agent transcript and tracing.
            raise ToolException("The CVCI tool request failed. Try again or sign in through CVCI again.") from None

    return intercept
