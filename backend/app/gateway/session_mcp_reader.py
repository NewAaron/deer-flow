"""Request-bound MCP reads for explicitly declared browser plugin actions.

The public capability is generic; the current host adapter deliberately supports
only the existing pinned CVCI HTTP/session grant chain. It never reconstructs
authority from a user ID, a plugin payload, or a past run/checkpoint.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from deerflow_extension_api.auth import ExtensionPrincipal
from deerflow_extension_api.host_capabilities import HostCapabilityError
from deerflow_extension_api.plugins import McpReadTool, SessionMcpReader
from fastapi import Request

from app.gateway.auth.cvci import prepare_cvci_ticket_provider
from app.gateway.auth_disabled import AUTH_SOURCE_SESSION
from deerflow.config.extensions_config import get_extensions_config
from deerflow.mcp.client import build_server_params
from deerflow.mcp.headers import apply_header_overrides, illegal_header_value_reason
from deerflow.mcp.trusted_auth import CVCI_SERVER, require_trusted_cvci_server

_INPUT_LIMIT = 16 * 1024
_RESULT_LIMIT = 256 * 1024
_CALL_TIMEOUT = 10

_HTTP_ERRORS = {
    "mcp_read_session_unavailable": (403, "Sign in through CVCI again to search business records."),
    "mcp_read_access_denied": (403, "You do not have permission to search these business records. Contact your CVCI administrator."),
    "mcp_read_not_declared": (403, "This search action cannot call that tool."),
    "mcp_read_not_readonly": (403, "The declared tool is not an available read-only operation."),
    "mcp_read_unavailable": (503, "The business search connection is unavailable."),
    "mcp_read_expired": (409, "This search action has ended. Open the selector again."),
    "mcp_read_invalid_input": (422, "Search input is invalid or too large."),
    "mcp_read_result_too_large": (422, "The search result is too large; narrow the search."),
    "mcp_read_failed": (502, "Business search failed. Try again or sign in through CVCI again."),
    "mcp_read_timeout": (504, "Business search timed out. Try again."),
}


def mcp_read_http_error(code: str) -> tuple[int, dict[str, str]]:
    # Only host-owned, fixed details cross the action route, never arbitrary
    # extension exception strings (which may include credentials or paths).
    if not isinstance(code, str) or code not in _HTTP_ERRORS:
        return 502, {"code": "plugin_capability_unavailable", "message": "Plugin capability unavailable."}
    status, message = _HTTP_ERRORS[code]
    return status, {"code": code, "message": message}


def _create_session(connection):
    from langchain_mcp_adapters.sessions import create_session

    return create_session(connection)


def _detached_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    try:
        if not isinstance(arguments, Mapping):
            raise ValueError
        encoded = json.dumps(dict(arguments), allow_nan=False, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > _INPUT_LIMIT:
            raise ValueError
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise HostCapabilityError("mcp_read_invalid_input", "Search input is invalid or too large.") from None


def _connection(server: str) -> dict[str, Any]:
    config = get_extensions_config()
    definition = config.get_enabled_mcp_servers().get(server)
    if server != CVCI_SERVER or definition is None or not require_trusted_cvci_server(server, definition):
        raise HostCapabilityError("mcp_read_unavailable", "This deployment does not provide the requested session read.")
    return build_server_params(server, definition)


async def _read_tool(connection: dict[str, Any], tool: str, arguments: dict[str, Any]):
    result = None
    failure = None
    async with _create_session(connection) as session:
        try:
            await session.initialize()
            discovered = await session.list_tools()
            matches = [item for item in discovered.tools if item.name == tool]
            annotations = matches[0].annotations if len(matches) == 1 else None
            if annotations is None or annotations.readOnlyHint is not True or annotations.destructiveHint is not False:
                raise HostCapabilityError("mcp_read_not_readonly", "The declared tool is not an available read-only operation.")
            result = await session.call_tool(tool, arguments)
        except Exception as exc:
            # MCP context managers can suppress an exception during disconnect;
            # re-raise after cleanup so that cannot turn a refusal into success.
            failure = exc
    if failure is not None:
        raise failure
    if result is None:
        raise HostCapabilityError("mcp_read_failed", "The search did not return a result.")
    return result


class _SessionMcpReader:
    def __init__(self, request: Request, principal: ExtensionPrincipal, declarations: tuple[McpReadTool, ...]) -> None:
        self._allowed = frozenset((item.server, item.tool) for item in declarations)
        self._active = True
        self._pending: set[asyncio.Task] = set()
        try:
            self._provider = prepare_cvci_ticket_provider(request, principal.user_id)
        except Exception:
            self._provider = None

    def _check_active(self) -> None:
        if not self._active:
            raise HostCapabilityError("mcp_read_expired", "This search action has ended. Open the selector again.")

    def close(self) -> None:
        self._active = False
        self._provider = None
        for task in tuple(self._pending):
            task.cancel()

    async def call(self, server: str, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self._check_active()
        if not isinstance(server, str) or not isinstance(tool, str) or (server, tool) not in self._allowed:
            raise HostCapabilityError("mcp_read_not_declared", "This search action cannot call that tool.")
        detached = _detached_arguments(arguments)
        if self._provider is None:
            raise HostCapabilityError("mcp_read_session_unavailable", "Sign in through CVCI again to search business records.")
        task = asyncio.create_task(self._execute(server, tool, detached))
        self._pending.add(task)
        try:
            result = await task
            self._check_active()
            return result
        except TimeoutError:
            raise HostCapabilityError("mcp_read_timeout", "Business search timed out. Try again.") from None
        except asyncio.CancelledError:
            self._check_active()
            raise
        finally:
            self._pending.discard(task)

    async def _execute(self, server: str, tool: str, arguments: dict[str, Any]) -> Mapping[str, Any]:
        async with asyncio.timeout(_CALL_TIMEOUT):
            try:
                connection = await asyncio.to_thread(_connection, server)
            except HostCapabilityError:
                raise
            except Exception:
                raise HostCapabilityError("mcp_read_unavailable", "The business search connection is unavailable.") from None
            self._check_active()
            provider = self._provider
            if provider is None:
                raise HostCapabilityError("mcp_read_session_unavailable", "Sign in through CVCI again to search business records.")
            try:
                ticket = await provider()
                if not isinstance(ticket, str) or not ticket or illegal_header_value_reason(ticket):
                    raise ValueError
            except Exception:
                raise HostCapabilityError("mcp_read_session_unavailable", "Sign in through CVCI again to search business records.") from None
            self._check_active()
            original_headers = connection.get("headers") or {}
            connection["headers"] = apply_header_overrides(original_headers, {"Authorization": f"Bearer {ticket}"})
            try:
                raw = await _read_tool(connection, tool, arguments)
                self._check_active()
                envelope = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
                result = {key: envelope[key] for key in ("content", "structuredContent", "isError") if key in envelope}
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > _RESULT_LIMIT:
                    raise HostCapabilityError("mcp_read_result_too_large", "The search result is too large; narrow the search.")
                secrets = {ticket}
                for value in original_headers.values():
                    if isinstance(value, str) and len(value) >= 8:
                        secrets.add(value)
                        if value.lower().startswith("bearer "):
                            secrets.add(value[7:])
                if any(secret in encoded for secret in secrets):
                    raise ValueError
                return json.loads(encoded)
            except HostCapabilityError:
                raise
            except Exception:
                raise HostCapabilityError("mcp_read_failed", "Business search failed. Try again or sign in through CVCI again.") from None


@contextmanager
def session_mcp_reader_scope(request: Request, principal: ExtensionPrincipal, declarations: tuple[McpReadTool, ...]) -> Iterator[SessionMcpReader | None]:
    reader = None
    if declarations and getattr(request.state, "auth_source", None) == AUTH_SOURCE_SESSION and not principal.is_internal:
        reader = _SessionMcpReader(request, principal, declarations)
    try:
        yield reader
    finally:
        if reader is not None:
            reader.close()
