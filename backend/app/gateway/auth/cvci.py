"""Server-side CVCI grant association for DeerFlow OIDC sessions."""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import Request
from langchain_core.tools import ToolException
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.gateway.auth.errors import TokenError
from app.gateway.auth.jwt import TokenPayload, decode_token
from app.gateway.auth.oidc import OIDCIdentity
from app.gateway.auth_disabled import AUTH_SOURCE_SESSION
from deerflow.mcp.headers import illegal_header_value_reason
from deerflow.mcp.trusted_auth import TicketProvider, cvci_httpx_client_factory, cvci_oidc_enabled

logger = logging.getLogger(__name__)

CVCI_PROVIDER = "cvci"
CVCI_SESSION_DURATION = timedelta(hours=8)
_REFERENCE_PATTERN = re.compile(r"[a-zA-Z0-9_-]{16,128}")
_NUMERIC_ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")
_TICKET_PATTERN = re.compile(r"dfmcp_[a-zA-Z0-9_-]{16,256}")
_SESSION_KEY_PREFIX = "deerflow:cvci:oidc-session:"


class CvciAuthorizationError(Exception):
    """A safe verdict without credentials or remote response details."""


class CvciSessionInvalid(CvciAuthorizationError):
    """An authoritative rejection, distinct from a temporary authority failure."""


def cvci_subject(tenant_id: str, user_id: str) -> str:
    if any(not _NUMERIC_ID_PATTERN.fullmatch(value) or int(value) > 2**63 - 1 for value in (tenant_id, user_id)):
        raise ValueError("CVCI tenant and user IDs must be positive decimal 64-bit integers")
    return f"cvci:{tenant_id}:{user_id}"


class CvciGrant(BaseModel):
    grantId: str
    tenantId: str
    userId: str
    deerFlowUserId: str

    @classmethod
    def from_identity(cls, identity: OIDCIdentity, client_id: str, owner: str) -> CvciGrant:
        claims = identity.signed_claims
        grant_id, tenant_id, user_id = (claims.get(key) for key in ("cvci_grant_id", "cvci_tenant_id", "cvci_user_id"))
        if identity.provider != CVCI_PROVIDER or claims.get("aud") != client_id or not all(isinstance(value, str) for value in (grant_id, tenant_id, user_id)) or not _REFERENCE_PATTERN.fullmatch(grant_id):
            raise CvciAuthorizationError("Invalid CVCI identity claims")
        try:
            subject = cvci_subject(tenant_id, user_id)
        except ValueError:
            raise CvciAuthorizationError("Invalid CVCI identity claims") from None
        if claims.get("sub") != subject or identity.subject != subject:
            raise CvciAuthorizationError("Invalid CVCI identity claims")
        return cls(grantId=grant_id, tenantId=tenant_id, userId=user_id, deerFlowUserId=owner)

    def principal(self) -> dict[str, str]:
        return {"grantId": self.grantId, "deerFlowUserId": self.deerFlowUserId}


def _bridge_key() -> str:
    direct = os.environ.get("DEER_FLOW_CVCI_BRIDGE_KEY")
    key_file = os.environ.get("DEER_FLOW_CVCI_BRIDGE_KEY_FILE")
    if bool(direct) == bool(key_file):
        raise RuntimeError("Set exactly one CVCI bridge key source")
    if key_file:
        try:
            direct = Path(key_file).read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeError):
            raise RuntimeError("The CVCI bridge key file could not be read") from None
    if not direct or len(direct) < 32 or illegal_header_value_reason(direct):
        raise RuntimeError("The CVCI bridge key is invalid")
    return direct


class CvciAuthorization:
    def __init__(self, redis: Redis, client: httpx.AsyncClient, bridge_url: str, bridge_key: str) -> None:
        self._redis = redis
        self._client = client
        self._bridge_url = bridge_url
        self._bridge_key = bridge_key

    @classmethod
    def from_env(cls) -> CvciAuthorization:
        raw_url = os.environ.get("DEER_FLOW_CVCI_BRIDGE_BASE_URL", "")
        try:
            url = httpx.URL(raw_url)
        except (ValueError, httpx.InvalidURL):
            raise RuntimeError("The CVCI bridge URL is invalid") from None
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment or url.path.rstrip("/") != "/admin-api/cvci/internal/deerflow":
            raise RuntimeError("Set the CVCI bridge URL to the internal DeerFlow API prefix")
        redis_url = os.environ.get("DEER_FLOW_CVCI_SESSION_REDIS_URL", "")
        if not redis_url.startswith(("redis://", "rediss://")) or not os.environ.get("DEER_FLOW_CVCI_MCP_URL"):
            raise RuntimeError("Configure the CVCI session Redis URL and MCP deployment URL")
        key = _bridge_key()
        return cls(
            Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True),
            cvci_httpx_client_factory(timeout=httpx.Timeout(3, connect=2)),
            str(url).rstrip("/"),
            key,
        )

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        finally:
            await self._redis.aclose()

    async def _post(self, operation: str, data: dict[str, str]) -> object:
        try:
            response = await self._client.post(f"{self._bridge_url}/{operation}", json=data, headers={"X-CVCI-DeerFlow-Key": self._bridge_key})
            if response.status_code in {401, 403}:
                raise CvciSessionInvalid("CVCI authorization was rejected")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise CvciAuthorizationError("CVCI authorization service is unavailable") from None
        if isinstance(payload, dict) and type(payload.get("code")) is int and payload["code"] in {401, 403}:
            raise CvciSessionInvalid("CVCI authorization was rejected")
        if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
            raise CvciAuthorizationError("CVCI authorization was rejected")
        return payload.get("data")

    async def bind(self, grant: CvciGrant) -> str:
        session_id = secrets.token_urlsafe(32)
        await self._post("grant/bind", grant.model_dump())
        try:
            stored = await self._redis.set(f"{_SESSION_KEY_PREFIX}{session_id}", grant.model_dump_json(), ex=int(CVCI_SESSION_DURATION.total_seconds()), nx=True)
            if stored:
                return session_id
        except RedisError:
            pass
        await self._revoke_upstream(grant)
        raise CvciAuthorizationError("CVCI session storage is unavailable")

    async def _lookup(self, session_id: str, owner: str, *, revoke: bool = False) -> CvciGrant | None:
        if not _REFERENCE_PATTERN.fullmatch(session_id):
            return None
        key = f"{_SESSION_KEY_PREFIX}{session_id}"
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            grant = CvciGrant.model_validate_json(raw)
            if grant.deerFlowUserId != owner:
                return None
            # The association is immutable and this key has a unique random
            # name. GETDEL lets just one concurrent logout revoke upstream.
            if revoke and await self._redis.getdel(key) is None:
                return None
        except (RedisError, ValidationError):
            raise CvciAuthorizationError("CVCI session storage is unavailable") from None
        return grant

    async def _revoke_upstream(self, grant: CvciGrant) -> None:
        try:
            await self._post("grant/revoke", grant.principal())
        except CvciAuthorizationError:
            logger.warning("CVCI grant revocation could not be confirmed")

    async def logout(self, session_id: str, owner: str) -> None:
        grant = await self._lookup(session_id, owner, revoke=True)
        if grant is not None:
            await self._revoke_upstream(grant)

    async def check(self, session_id: str, owner: str) -> None:
        grant = await self._lookup(session_id, owner)
        if grant is None:
            raise CvciSessionInvalid("CVCI session expired")
        if await self._post("grant/check", grant.principal()) is not True:
            raise CvciAuthorizationError("Invalid CVCI authorization result")

    async def logout_all(self, session_id: str, owner: str) -> None:
        grant = await self._lookup(session_id, owner)
        receipt_key = f"{_SESSION_KEY_PREFIX}logout-completed:{session_id}"
        if grant is None:
            try:
                if await self._redis.get(receipt_key) == owner:
                    return
            except RedisError:
                raise CvciAuthorizationError("CVCI session storage is unavailable") from None
            raise CvciSessionInvalid("CVCI session expired")
        data = await self._post("grant/logout-all", grant.principal())
        if not isinstance(data, dict) or data.get("status") not in {"completed", "already_completed"}:
            raise CvciAuthorizationError("CVCI global sign-out was not confirmed")
        # Keep the original association until upstream confirms. A failed call
        # can then retry with A's proof even if this browser now displays B.
        script = """
        local raw = redis.call('GET', KEYS[1])
        if raw and raw ~= ARGV[1] then return 0 end
        local receipt = redis.call('GET', KEYS[2])
        if receipt and receipt ~= ARGV[2] then return 0 end
        redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
        redis.call('DEL', KEYS[1])
        return 1
        """
        try:
            saved = await self._redis.eval(script, 2, f"{_SESSION_KEY_PREFIX}{session_id}", receipt_key, grant.model_dump_json(), owner, int(CVCI_SESSION_DURATION.total_seconds()))
        except RedisError:
            raise CvciAuthorizationError("CVCI sign-out receipt could not be saved; retry sign-out.") from None
        if saved != 1:
            raise CvciAuthorizationError("CVCI sign-out receipt could not be saved; retry sign-out.")

    async def ticket(self, session_id: str, owner: str) -> str:
        grant = await self._lookup(session_id, owner)
        if grant is None:
            raise CvciAuthorizationError("CVCI session expired")
        data = await self._post("ticket/issue", grant.principal())
        if not isinstance(data, dict):
            raise CvciAuthorizationError("Invalid CVCI MCP ticket")
        ticket, lifetime = data.get("ticket"), data.get("expiresIn")
        if not isinstance(ticket, str) or not _TICKET_PATTERN.fullmatch(ticket) or type(lifetime) is not int or not 0 < lifetime <= 60:
            raise CvciAuthorizationError("Invalid CVCI MCP ticket")
        if await self._lookup(session_id, owner) != grant:
            raise CvciAuthorizationError("CVCI session expired")
        return ticket


def get_cvci_authorization(request: Request) -> CvciAuthorization | None:
    if not cvci_oidc_enabled():
        return None
    return getattr(request.app.state, "cvci_authorization", None)


def read_session_reference(request: Request) -> TokenPayload | None:
    from app.gateway.auth.browser_session import read_browser_token

    token = read_browser_token(request.cookies)
    if not token:
        return None
    payload = decode_token(token)
    if isinstance(payload, TokenError) or not payload.sid or not _REFERENCE_PATTERN.fullmatch(payload.sid):
        return None
    return payload


def prepare_cvci_ticket_provider(request: Request, owner: str | None) -> TicketProvider | None:
    if not owner or getattr(request.state, "auth_source", None) != AUTH_SOURCE_SESSION:
        return None
    service = get_cvci_authorization(request)
    if service is None:
        return None
    payload = read_session_reference(request)
    if payload is None or payload.sub != owner:
        return None
    session_id = payload.sid
    assert session_id is not None

    async def issue_ticket() -> str:
        from app.gateway.deps import get_local_provider, validate_resolved_session_user

        try:
            user = await get_local_provider().get_user(owner)
            if user is None or user.oauth_provider != CVCI_PROVIDER or validate_resolved_session_user(user, payload):
                raise CvciAuthorizationError("CVCI session expired")
            return await service.ticket(session_id, owner)
        except Exception:
            raise ToolException("CVCI authorization is unavailable. Sign in through CVCI again.") from None

    return issue_ticket
