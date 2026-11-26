"""Browser policy and generation-scoped cookies; a late sign-out cannot erase a new login."""

import asyncio
import contextlib
import hashlib
import hmac
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import HTTPException, Response

from app.gateway.auth.config import get_auth_config

ACTIVE_SESSION_COOKIE = "deerflow_active_session"
SESSION_COOKIE_PREFIX = "deerflow_session_"
_SID = re.compile(r"[A-Za-z0-9_-]{16,128}")


@dataclass(frozen=True)
class BrowserAuthPolicy:
    local_login_enabled: bool = True
    required_oidc_provider: str | None = None


def _load_browser_policy() -> BrowserAuthPolicy:
    from deerflow.config import get_app_config

    try:
        auth = getattr(get_app_config(), "auth", None)
    except FileNotFoundError:
        return BrowserAuthPolicy()
    return BrowserAuthPolicy(getattr(getattr(auth, "local", None), "enabled", True), getattr(auth, "required_oidc_provider", None))


async def browser_auth_policy(connection=None) -> BrowserAuthPolicy:
    scope = getattr(connection, "scope", None)
    app = scope.get("app") if isinstance(scope, dict) else getattr(connection, "app", None) if connection is not None else None
    cached = getattr(getattr(app, "state", None), "browser_auth_policy", None)
    return cached if isinstance(cached, BrowserAuthPolicy) else await asyncio.to_thread(_load_browser_policy)


def _newest_browser_session(cookies: Mapping[str, str]) -> tuple[str, str] | None:
    """Order signed cookies, never authorize them or fall back from a revoked winner."""
    import jwt

    from app.gateway.auth.jwt import TokenPayload

    candidates = [(key.removeprefix(SESSION_COOKIE_PREFIX), value) for key, value in cookies.items() if key.startswith(SESSION_COOKIE_PREFIX)]
    if len(candidates) > 32:
        return "", ""  # Bound verification work; an oversized jar cannot authenticate.
    newest = None
    newest_generation = 0
    for sid, token in candidates:
        if not _SID.fullmatch(sid) or not isinstance(token, str) or len(token) > 8192:
            continue
        try:
            # Keep the newest *signed* expired cookie selected too: its normal
            # authorization must fail, rather than revive an older identity.
            claims = jwt.decode(token, get_auth_config().jwt_secret, algorithms=["HS256"], options={"verify_exp": False, "verify_iat": False})
            payload = TokenPayload.model_validate(claims)
            generation = payload.browser_issued_at
            if payload.sid != sid or generation is None or payload.iat is None or abs(generation // 1_000_000_000 - int(payload.iat.timestamp())) > 1:
                continue
        except (jwt.PyJWTError, ValueError):
            continue
        if generation > newest_generation:
            newest, newest_generation = (sid, token), generation
        elif generation == newest_generation and newest != (sid, token):
            return "", ""
    return newest


def read_browser_token(cookies: Mapping[str, str], *, strict: bool = False) -> str | None:
    newest = _newest_browser_session(cookies)
    if newest is not None:
        return newest[1] or None
    sid = cookies.get(ACTIVE_SESSION_COOKIE)
    if isinstance(sid, str) and _SID.fullmatch(sid):
        token = cookies.get(SESSION_COOKIE_PREFIX + sid)
        if token:
            return token
    return None if strict else cookies.get("access_token")


def read_session_id(cookies: Mapping[str, str]) -> str | None:
    newest = _newest_browser_session(cookies)
    if newest is not None:
        return newest[0] or None
    value = cookies.get(ACTIVE_SESSION_COOKIE)
    return value if isinstance(value, str) and _SID.fullmatch(value) else None


def browser_session_fence(owner: str, session_id: str) -> str:
    value = f"deerflow-browser-fence-v1:{owner}:{session_id}".encode()
    return hmac.new(get_auth_config().jwt_secret.encode(), value, hashlib.sha256).hexdigest()


def browser_login_fence(cookies: Mapping[str, str]) -> str:
    """Bind an OAuth flow to its initiating browser generation, including anonymous."""
    sid = read_session_id(cookies) or ""
    token = read_browser_token(cookies, strict=True) or ""
    value = f"deerflow-login-fence-v1:{sid}:{token}".encode()
    return hmac.new(get_auth_config().jwt_secret.encode(), value, hashlib.sha256).hexdigest()


def set_browser_session(response: Response, request, token: str, session_id: str) -> None:
    from app.gateway.auth.session_cookie_state import SESSION_COOKIE_ISSUED_STATE_ATTR, SESSION_COOKIE_MAX_AGE_STATE_ATTR, SESSION_COOKIE_SECURE_STATE_ATTR
    from app.gateway.csrf_middleware import is_secure_request

    if not _SID.fullmatch(session_id):
        raise ValueError("Invalid browser session identifier")
    secure = is_secure_request(request)
    for key, value in ((SESSION_COOKIE_PREFIX + session_id, token), (ACTIVE_SESSION_COOKIE, session_id)):
        response.set_cookie(key, value, httponly=True, secure=secure, samesite="lax")
    setattr(request.state, SESSION_COOKIE_MAX_AGE_STATE_ATTR, None)
    setattr(request.state, SESSION_COOKIE_SECURE_STATE_ATTR, secure)
    setattr(request.state, SESSION_COOKIE_ISSUED_STATE_ATTR, True)


def clear_browser_session(response: Response, request, session_id: str) -> None:
    from app.gateway.auth.session_cookie_state import SKIP_AUTH_CSRF_COOKIE_STATE_ATTR
    from app.gateway.csrf_middleware import is_secure_request

    if not _SID.fullmatch(session_id):
        raise ValueError("Invalid browser session identifier")
    # Never clear the shared selector or CSRF cookie: a newer login may already
    # have replaced them while this response was in flight. The selector alone
    # cannot authenticate without its corresponding signed, live session cookie.
    response.delete_cookie(SESSION_COOKIE_PREFIX + session_id, httponly=True, secure=is_secure_request(request), samesite="lax")
    setattr(request.state, SKIP_AUTH_CSRF_COOKIE_STATE_ATTR, True)


async def validate_browser_authorization(connection, user, payload, *, standalone: bool = False) -> None:
    from app.gateway.auth.cvci import CvciAuthorization, CvciAuthorizationError, CvciSessionInvalid, get_cvci_authorization

    policy = await browser_auth_policy(connection)
    provider = getattr(user, "oauth_provider", None)
    if not policy.local_login_enabled and not provider:
        raise HTTPException(401, detail={"code": "token_invalid", "message": "Sign in through the configured identity provider."})
    if policy.required_oidc_provider is None:
        return
    sid = getattr(payload, "sid", None)
    if provider != policy.required_oidc_provider or not sid or read_session_id(connection.cookies) != sid:
        raise HTTPException(401, detail={"code": "token_invalid", "message": "Sign in through CVCI again."})
    service = None if standalone else get_cvci_authorization(connection)
    owned_service = False
    try:
        if standalone:
            service = await asyncio.to_thread(CvciAuthorization.from_env)
            owned_service = True
        if service is None:
            raise CvciAuthorizationError("CVCI authorization is unavailable")
        await service.check(sid, str(user.id))
    except CvciSessionInvalid:
        raise HTTPException(401, detail={"code": "token_invalid", "message": "Sign in through CVCI again."}) from None
    except (CvciAuthorizationError, RuntimeError):
        raise HTTPException(503, detail={"code": "identity_unavailable", "message": "CVCI authorization is temporarily unavailable."}) from None
    finally:
        if owned_service and service is not None:
            await service.aclose()


async def guard_browser_events(source, check, *, interval: float = 2.0):
    """Stop an already-open SSE subscription when its original authority ends."""
    iterator = source.__aiter__()
    pending = None
    next_check = 0.0
    try:
        while True:
            if time.monotonic() >= next_check:
                await check()
                next_check = time.monotonic() + interval
            pending = asyncio.create_task(anext(iterator))
            while not pending.done():
                done, _ = await asyncio.wait({pending}, timeout=max(0.001, next_check - time.monotonic()))
                if not done:
                    await check()
                    next_check = time.monotonic() + interval
            chunk = pending.result()
            pending = None
            yield chunk
    except (StopAsyncIteration, HTTPException):
        return
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
