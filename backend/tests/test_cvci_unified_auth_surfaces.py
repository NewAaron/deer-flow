"""Independent, synthetic checks of unified auth across every browser surface."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langgraph_sdk import Auth
from starlette.websockets import WebSocket

from app.gateway import auth_middleware, deps, langgraph_auth
from app.gateway.auth import config as jwt_config
from app.gateway.auth.browser_session import ACTIVE_SESSION_COOKIE, SESSION_COOKIE_PREFIX, BrowserAuthPolicy, browser_session_fence
from app.gateway.auth.cvci import CvciAuthorization, CvciAuthorizationError, CvciSessionInvalid
from app.gateway.auth.jwt import create_access_token
from app.gateway.auth.models import User
from app.gateway.auth_disabled import AUTH_DISABLED_USER_ID
from app.gateway.routers import auth, browser

SID_A = "a" * 43
SID_B = "b" * 43


@pytest.fixture
def surfaces(monkeypatch):
    from app.gateway.auth import browser_session

    monkeypatch.delenv("DEER_FLOW_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DEER_FLOW_CVCI_OIDC_ENABLED", "1")
    monkeypatch.setattr(jwt_config, "_auth_config", jwt_config.AuthConfig(jwt_secret="independent-surfaces-synthetic-secret-only"))
    first = User(id=UUID("11111111-1111-4111-8111-111111111111"), email="a@example.com", oauth_provider="cvci", system_role="user")
    second = User(id=UUID("22222222-2222-4222-8222-222222222222"), email="b@example.com", oauth_provider="cvci", system_role="user")
    admin = User(id=UUID("33333333-3333-4333-8333-333333333333"), email="admin@example.com", password_hash="synthetic", system_role="admin")
    users = {str(user.id): user for user in (first, second, admin)}
    provider = SimpleNamespace(get_user=AsyncMock(side_effect=users.get))
    authority = SimpleNamespace(check=AsyncMock(), logout_all=AsyncMock(), aclose=AsyncMock())
    app = FastAPI()
    app.state.browser_auth_policy = BrowserAuthPolicy(False, "cvci")
    app.state.cvci_authorization = authority
    monkeypatch.setattr(browser_session, "_load_browser_policy", lambda: app.state.browser_auth_policy)
    monkeypatch.setattr(deps, "get_local_provider", lambda: provider)
    monkeypatch.setattr(auth, "get_local_provider", lambda: provider)
    monkeypatch.setattr(langgraph_auth, "get_local_provider", lambda: provider)
    monkeypatch.setattr(CvciAuthorization, "from_env", classmethod(lambda cls: authority))
    monkeypatch.setattr(auth_middleware, "resolve_route_permissions", AsyncMock(return_value=[]))
    app.include_router(auth.router)
    app.add_middleware(auth_middleware.AuthMiddleware)

    @app.get("/protected-surface")
    async def protected(request: Request):
        return {"id": str(request.state.user.id), "role": request.state.user.system_role}

    def cookies(user=first, sid=SID_A, token_sid="same"):
        return {ACTIVE_SESSION_COOKIE: sid, SESSION_COOKIE_PREFIX + sid: create_access_token(str(user.id), session_id=sid if token_sid == "same" else token_sid)}

    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, first=first, second=second, admin=admin, authority=authority, provider=provider, cookies=cookies)


def _ws(surface, cookies):
    return WebSocket(
        {"type": "websocket", "path": "/browser/ws", "scheme": "ws", "server": ("localhost", 2026), "app": surface.app, "headers": [(b"cookie", "; ".join(f"{key}={value}" for key, value in cookies.items()).encode())], "query_string": b""},
        receive=AsyncMock(),
        send=AsyncMock(),
    )


def _langgraph_request(surface, cookies):
    return SimpleNamespace(app=surface.app, cookies=cookies, method="GET", headers={})


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket", "langgraph"])
async def test_live_strict_session_checks_original_authority_on_each_protocol(surfaces, protocol):
    f = surfaces
    cookies = f.cookies()
    if protocol == "http":
        response = f.client.get("/protected-surface", cookies=cookies)
        assert response.status_code == 200
        assert response.json() == {"id": str(f.first.id), "role": "user"}
    elif protocol == "websocket":
        assert await browser._authenticate_ws(_ws(f, cookies)) == f.first
    else:
        assert await langgraph_auth.authenticate(_langgraph_request(f, cookies)) == str(f.first.id)
        f.authority.aclose.assert_awaited_once()
    f.authority.check.assert_awaited_once_with(SID_A, str(f.first.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket", "langgraph"])
@pytest.mark.parametrize("credential", ["missing", "legacy-admin", "named-local-admin", "missing-sid", "wrong-sid"])
async def test_strict_identity_never_falls_back_to_old_or_mismatched_credentials(surfaces, protocol, credential):
    f = surfaces
    cookies = {
        "missing": {},
        "legacy-admin": {"access_token": create_access_token(str(f.admin.id))},
        "named-local-admin": f.cookies(f.admin),
        "missing-sid": f.cookies(token_sid=None),
        "wrong-sid": f.cookies(token_sid=SID_B),
    }[credential]
    if protocol == "http":
        assert f.client.get("/protected-surface", cookies=cookies).status_code == 401
    elif protocol == "websocket":
        assert await browser._authenticate_ws(_ws(f, cookies)) is None
    else:
        with pytest.raises(Auth.exceptions.HTTPException) as error:
            await langgraph_auth.authenticate(_langgraph_request(f, cookies))
        assert error.value.status_code == 401
    f.authority.check.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket", "langgraph"])
@pytest.mark.parametrize("failure", ["revoked", "unavailable", "missing-service"])
async def test_unavailable_or_revoked_authority_never_admits_a_browser(surfaces, monkeypatch, protocol, failure):
    f = surfaces
    cookies = {**f.cookies(), "access_token": create_access_token(str(f.admin.id))}
    status = 401 if failure == "revoked" else 503
    if failure == "missing-service":
        f.app.state.cvci_authorization = None

        def unavailable(cls):
            raise RuntimeError("synthetic private transport configuration")

        monkeypatch.setattr(CvciAuthorization, "from_env", classmethod(unavailable))
    else:
        f.authority.check.side_effect = CvciSessionInvalid("synthetic revoked") if failure == "revoked" else CvciAuthorizationError("synthetic private network error")
    if protocol == "http":
        response = f.client.get("/protected-surface", cookies=cookies)
        assert response.status_code == status
        assert "synthetic private" not in response.text
    elif protocol == "websocket":
        assert await browser._authenticate_ws(_ws(f, cookies)) is None
    else:
        with pytest.raises(Auth.exceptions.HTTPException) as error:
            await langgraph_auth.authenticate(_langgraph_request(f, cookies))
        assert error.value.status_code == status
        assert "synthetic private" not in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket", "langgraph"])
async def test_required_policy_cannot_be_bypassed_by_auth_disabled_fallback(surfaces, monkeypatch, protocol):
    f = surfaces
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
    if protocol == "http":
        assert f.client.get("/protected-surface").status_code in (401, 503)
    elif protocol == "websocket":
        assert await browser._authenticate_ws(_ws(f, {})) is None
    else:
        with pytest.raises(Auth.exceptions.HTTPException) as error:
            await langgraph_auth.authenticate(_langgraph_request(f, {}))
        assert error.value.status_code in (401, 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket", "langgraph"])
async def test_generic_auth_disabled_control_still_uses_original_demo_identity(surfaces, monkeypatch, protocol):
    f = surfaces
    f.app.state.browser_auth_policy = BrowserAuthPolicy()
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")
    if protocol == "http":
        response = f.client.get("/protected-surface")
        assert response.status_code == 200
        assert response.json()["id"] == AUTH_DISABLED_USER_ID
    elif protocol == "websocket":
        assert (await browser._authenticate_ws(_ws(f, {}))).id == AUTH_DISABLED_USER_ID
    else:
        assert await langgraph_auth.authenticate(_langgraph_request(f, {})) == AUTH_DISABLED_USER_ID
    f.authority.check.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket", "langgraph"])
async def test_generic_local_jwt_control_stays_available_without_live_cvci(surfaces, protocol):
    f = surfaces
    f.app.state.browser_auth_policy = BrowserAuthPolicy()
    f.authority.check.side_effect = CvciAuthorizationError("unavailable")
    cookies = {"access_token": create_access_token(str(f.admin.id))}
    if protocol == "http":
        response = f.client.get("/protected-surface", cookies=cookies)
        assert response.status_code == 200
        assert response.json() == {"id": str(f.admin.id), "role": "admin"}
    elif protocol == "websocket":
        assert await browser._authenticate_ws(_ws(f, cookies)) == f.admin
    else:
        assert await langgraph_auth.authenticate(_langgraph_request(f, cookies)) == str(f.admin.id)
    f.authority.check.assert_not_awaited()


def test_public_provider_policy_exposes_only_safe_required_provider_metadata(surfaces, monkeypatch):
    f = surfaces
    providers = {"cvci": SimpleNamespace(display_name="CVCI", client_secret="synthetic-private-secret"), "other": SimpleNamespace(display_name="Other")}
    monkeypatch.setattr(auth, "_oidc_config", AsyncMock(return_value=SimpleNamespace(enabled=True, providers=providers)))
    response = f.client.get("/api/v1/auth/providers")
    assert response.status_code == 200
    assert response.json() == {"local_login_enabled": False, "required_oidc_provider": "cvci", "providers": [{"id": "cvci", "display_name": "CVCI", "type": "oidc"}]}
    assert "synthetic-private-secret" not in response.text


@pytest.mark.parametrize("strict", [True, False])
def test_oauth_host_selector_cannot_leave_canonical_origin_in_strict_mode(surfaces, monkeypatch, strict):
    from deerflow.config.auth_config import OIDCProviderConfig

    f = surfaces
    if not strict:
        f.app.state.browser_auth_policy = BrowserAuthPolicy()
    provider = OIDCProviderConfig(
        display_name="CVCI",
        issuer="http://localhost:18080/sso/oidc",
        client_id="synthetic-client",
        client_secret="synthetic-secret",
        redirect_uri="http://localhost:2026/api/v1/auth/callback/cvci",
        allow_loopback_http=True,
        authorization_endpoint="http://localhost:18080/sso/oidc/oauth2/authorize",
    )
    service = SimpleNamespace(discover=AsyncMock(return_value={}), build_authorization_url=Mock(return_value="http://localhost:18080/sso/oidc/oauth2/authorize"))
    monkeypatch.setattr(auth, "_oidc_config", AsyncMock(return_value=SimpleNamespace(enabled=True, providers={"cvci": provider})))
    monkeypatch.setattr(auth, "_get_oidc_service", lambda: service)
    response = f.client.get("/api/v1/auth/oauth/cvci?cvci_browser_host=127.0.0.1", follow_redirects=False)
    assert response.status_code == 302
    override = service.discover.call_args.args[1]["authorization_endpoint"]
    assert override == f"http://{'localhost' if strict else '127.0.0.1'}:18080/sso/oidc/oauth2/authorize"


def test_me_fence_is_current_but_never_a_replacement_credential(surfaces):
    f = surfaces
    response = f.client.get("/api/v1/auth/me", cookies=f.cookies())
    assert response.status_code == 200
    fence_a = response.json()["session_fence"]
    assert fence_a == browser_session_fence(str(f.first.id), SID_A)
    response_b = f.client.get("/api/v1/auth/me", cookies=f.cookies(f.second, SID_B))
    assert response_b.json()["session_fence"] != fence_a
    f.client.cookies.clear()
    assert f.client.get("/protected-surface", cookies={ACTIVE_SESSION_COOKIE: SID_A, SESSION_COOKIE_PREFIX + SID_A: fence_a}).status_code == 401


def test_cleanup_of_revoked_a_expires_only_a_named_cookie(surfaces):
    f = surfaces
    f.authority.check.side_effect = CvciSessionInvalid("revoked")
    response = f.client.post("/api/v1/auth/logout/cleanup", cookies=f.cookies())
    assert response.status_code == 200
    assert response.json() == {"status": "cleared"}
    cleared = response.headers.get_list("set-cookie")
    assert len(cleared) == 1
    assert cleared[0].startswith(SESSION_COOKIE_PREFIX + SID_A + "=")
    assert "Max-Age=0" in cleared[0]
    assert not any(header.startswith(ACTIVE_SESSION_COOKIE + "=") or header.startswith(SESSION_COOKIE_PREFIX + SID_B + "=") for header in cleared)
    f.authority.logout_all.assert_not_awaited()


def test_cleanup_unknown_authority_preserves_all_browser_proofs(surfaces):
    f = surfaces
    f.authority.check.side_effect = CvciAuthorizationError("unavailable")
    response = f.client.post("/api/v1/auth/logout/cleanup", cookies=f.cookies())
    assert response.status_code == 503
    assert not response.headers.get_list("set-cookie")
    f.authority.logout_all.assert_not_awaited()


@pytest.mark.parametrize(("required", "auth_disabled", "expected"), [(True, True, 403), (True, False, 403), (False, True, 200)])
def test_strict_live_http_post_cannot_lose_csrf_protection_to_demo_flag(surfaces, monkeypatch, required, auth_disabled, expected):
    from app.gateway.csrf_middleware import CSRFMiddleware

    f = surfaces
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1" if auth_disabled else "0")
    app = FastAPI()
    app.state.browser_auth_policy = BrowserAuthPolicy(False, "cvci") if required else BrowserAuthPolicy()
    app.state.cvci_authorization = f.authority
    calls = []

    @app.post("/api/independent-write-probe")
    async def probe():
        calls.append(True)
        return {"ok": True}

    app.add_middleware(auth_middleware.AuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    with TestClient(app) as client:
        response = client.post("/api/independent-write-probe", cookies=f.cookies() if required else {})
    assert response.status_code == expected
    assert calls == ([] if expected == 403 else [True])


def test_strict_logout_retries_same_original_proof_before_any_cookie_is_erased(surfaces):
    f = surfaces
    f.authority.logout_all.side_effect = [CvciAuthorizationError("unavailable"), None]
    proof = {"expected_session": browser_session_fence(str(f.first.id), SID_A)}
    first = f.client.post("/api/v1/auth/logout", cookies=f.cookies(), json=proof)
    assert first.status_code == 503
    assert not first.headers.get_list("set-cookie")
    second = f.client.post("/api/v1/auth/logout", cookies=f.cookies(), json=proof)
    assert second.status_code == 200
    assert f.authority.logout_all.await_count == 2
    assert all(call.args == (SID_A, str(f.first.id)) for call in f.authority.logout_all.await_args_list)
    assert all(header.startswith(SESSION_COOKIE_PREFIX + SID_A + "=") for header in second.headers.get_list("set-cookie"))


@pytest.mark.parametrize("initial_account", ["a", "anonymous"])
def test_oauth_callback_cannot_replace_or_globally_revoke_a_later_b_login(surfaces, monkeypatch, initial_account):
    _configure_callback(surfaces, monkeypatch)
    f = surfaces
    initial_cookies = f.cookies() if initial_account == "a" else {}
    started = f.client.get("/api/v1/auth/oauth/cvci", cookies=initial_cookies, follow_redirects=False)
    assert started.status_code == 302
    state = _started_state(f)
    response = f.client.get("/api/v1/auth/callback/cvci", params={"code": "synthetic-valid-code", "state": state}, cookies=f.cookies(f.second, SID_B), follow_redirects=False)
    f.authority.logout_all.assert_not_awaited()
    f.authority.bind.assert_not_awaited()
    assert not any(header.startswith(SESSION_COOKIE_PREFIX + "c" * 43 + "=") or header.startswith(ACTIVE_SESSION_COOKIE + "=") for header in response.headers.get_list("set-cookie"))


@pytest.mark.parametrize("initial_account", ["a", "anonymous"])
def test_oauth_original_browser_generation_control_can_complete_a_valid_login(surfaces, monkeypatch, initial_account):
    _, selected = _configure_callback(surfaces, monkeypatch)
    f = surfaces
    cookies = f.cookies() if initial_account == "a" else {}
    assert f.client.get("/api/v1/auth/oauth/cvci", cookies=cookies, follow_redirects=False).status_code == 302
    response = f.client.get("/api/v1/auth/callback/cvci", params={"code": "synthetic-valid-code", "state": _started_state(f)}, cookies=cookies, follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/callback?next=" in response.headers["location"]
    f.authority.bind.assert_awaited_once()
    f.authority.check.assert_awaited_with("c" * 43, str(selected.id))
    if initial_account == "a":
        f.authority.logout_all.assert_awaited_once_with(SID_A, str(f.first.id))
    else:
        f.authority.logout_all.assert_not_awaited()


def _started_state(surface):
    from app.gateway.auth.oidc_state import _verify_state_signed

    state = _verify_state_signed(surface.client.cookies["df_oidc_state_cvci"])
    assert state is not None
    return state.state


def _configure_callback(surface, monkeypatch, *, selected=None, session_id="c" * 43):
    from app.gateway.auth.oidc import OIDCIdentity
    from deerflow.config.auth_config import OIDCProviderConfig

    selected = selected or User(id=UUID("44444444-4444-4444-8444-444444444444"), email="c@example.com", oauth_provider="cvci", system_role="user")
    provider = OIDCProviderConfig(
        display_name="CVCI",
        issuer="http://localhost:18080/sso/oidc",
        client_id="synthetic-client",
        client_secret="synthetic-secret",
        redirect_uri="http://localhost:2026/api/v1/auth/callback/cvci",
        allow_loopback_http=True,
        authorization_endpoint="http://localhost:18080/sso/oidc/oauth2/authorize",
    )
    native_user_id = "42" if selected.id == surface.first.id else "43" if selected.id == surface.second.id else "77"
    subject = f"cvci:3:{native_user_id}"
    signed = {"aud": "synthetic-client", "sub": subject, "cvci_grant_id": "synthetic-grant-" + session_id, "cvci_tenant_id": "3", "cvci_user_id": native_user_id}
    identity = OIDCIdentity(provider="cvci", subject=subject, email="", email_verified=False, name="Synthetic selected user", claims={}, signed_claims=signed)
    service = SimpleNamespace(discover=AsyncMock(return_value={}), build_authorization_url=Mock(return_value="http://localhost:18080/sso/oidc/oauth2/authorize"), authenticate_callback=AsyncMock(return_value=identity))
    surface.authority.bind = AsyncMock(return_value=session_id)
    surface.authority.logout = AsyncMock()
    monkeypatch.setattr(auth, "_oidc_config", AsyncMock(return_value=SimpleNamespace(enabled=True, providers={"cvci": provider}, frontend_base_url="http://localhost:2026")))
    monkeypatch.setattr(auth, "_get_oidc_service", lambda: service)
    monkeypatch.setattr(auth, "get_or_provision_cvci_user", AsyncMock(return_value={"user": selected}))
    return service, selected


def _finish_anonymous_login(surface, monkeypatch, user, sid):
    # Generate real signed state and real callback Set-Cookie headers, but keep
    # identity-provider transport and business provisioning entirely synthetic.
    surface.client.cookies.clear()
    _configure_callback(surface, monkeypatch, selected=user, session_id=sid)
    started = surface.client.get("/api/v1/auth/oauth/cvci", follow_redirects=False)
    assert started.status_code == 302
    result = surface.client.get("/api/v1/auth/callback/cvci", params={"code": "synthetic-valid-code", "state": _started_state(surface)}, follow_redirects=False)
    assert result.status_code == 302
    assert "/auth/callback?next=" in result.headers["location"]
    return result


def _assert_b_is_still_selected(surface):
    surface.authority.check.reset_mock()
    result = surface.client.get("/api/v1/auth/me")
    assert result.status_code == 200
    assert result.json()["id"] == str(surface.second.id)
    assert result.json()["session_fence"] == browser_session_fence(str(surface.second.id), SID_B)
    surface.authority.check.assert_awaited_once_with(SID_B, str(surface.second.id))


def test_already_generated_a_callback_headers_cannot_replace_later_b_in_real_cookie_jar(surfaces, monkeypatch):
    f = surfaces
    late_a = _finish_anonymous_login(f, monkeypatch, f.first, SID_A)
    # A's response exists but has not reached the browser. B signs in first.
    _finish_anonymous_login(f, monkeypatch, f.second, SID_B)
    _assert_b_is_still_selected(f)
    # Deliver the previously generated A response after B, exactly as a late
    # network response would apply its HttpOnly cookies to the same cookie jar.
    f.client.cookies.extract_cookies(late_a)
    _assert_b_is_still_selected(f)


def test_revoked_latest_b_never_resurrects_an_older_a_cookie(surfaces, monkeypatch):
    f = surfaces
    late_a = _finish_anonymous_login(f, monkeypatch, f.first, SID_A)
    _finish_anonymous_login(f, monkeypatch, f.second, SID_B)
    f.client.cookies.extract_cookies(late_a)

    async def current_authority(sid, owner):
        if sid == SID_B:
            raise CvciSessionInvalid("latest B was revoked")

    f.authority.check.reset_mock()
    f.authority.check.side_effect = current_authority
    result = f.client.get("/api/v1/auth/me")
    assert result.status_code == 401
    f.authority.check.assert_awaited_once_with(SID_B, str(f.second.id))


def test_expired_latest_b_never_resurrects_a_valid_older_a_cookie(surfaces, monkeypatch):
    import time

    import jwt

    f = surfaces
    late_a = _finish_anonymous_login(f, monkeypatch, f.first, SID_A)
    original_create = auth.create_access_token

    def expire_b(user_id, *args, **kwargs):
        token = original_create(user_id, *args, **kwargs)
        if user_id != str(f.second.id):
            return token
        secret = jwt_config.get_auth_config().jwt_secret
        claims = jwt.decode(token, secret, algorithms=["HS256"])
        claims["exp"] = int(time.time()) - 1
        return jwt.encode(claims, secret, algorithm="HS256")

    monkeypatch.setattr(auth, "create_access_token", expire_b)
    _finish_anonymous_login(f, monkeypatch, f.second, SID_B)
    f.client.cookies.extract_cookies(late_a)
    f.authority.check.reset_mock()
    result = f.client.get("/api/v1/auth/me")
    assert result.status_code == 401
    f.authority.check.assert_not_awaited()


@pytest.mark.parametrize("poison", ["bad-signature-future", "signed-wrong-cookie-name", "expired-signed-future"])
def test_untrusted_or_misnamed_future_generation_cannot_outvote_real_b_cookie(surfaces, monkeypatch, poison):
    import time

    import jwt

    f = surfaces
    _finish_anonymous_login(f, monkeypatch, f.second, SID_B)
    sid = "z" * 43
    payload = {
        "sub": str(f.first.id),
        "sid": SID_A if poison == "signed-wrong-cookie-name" else sid,
        "ver": 0,
        "iat": int(time.time()),
        "exp": int(time.time()) + (-1 if poison == "expired-signed-future" else 3600),
        "browser_issued_at": time.time_ns() + 10**15,
    }
    secret = "synthetic-attacker-secret-not-the-server-secret" if poison == "bad-signature-future" else jwt_config.get_auth_config().jwt_secret
    forged = jwt.encode(payload, secret, algorithm="HS256")
    f.client.cookies.set(SESSION_COOKIE_PREFIX + sid, forged, domain="testserver.local", path="/")
    f.client.cookies.set(ACTIVE_SESSION_COOKIE, sid, domain="testserver.local", path="/")
    _assert_b_is_still_selected(f)


@pytest.mark.asyncio
async def test_uncached_policy_config_io_runs_off_event_loop_under_strict_blocking_gate(tmp_path, monkeypatch):
    import json
    import threading

    from support.detectors.blocking_io_runtime import detect_blocking_io_strict

    from app.gateway.auth import browser_session
    from deerflow import config

    source = tmp_path / "synthetic-browser-policy.json"
    source.write_text('{"local": false, "provider": "cvci"}', encoding="utf-8")
    read_threads = []

    def load_config():
        read_threads.append(threading.get_ident())
        data = json.loads(source.read_text(encoding="utf-8"))
        return SimpleNamespace(auth=SimpleNamespace(local=SimpleNamespace(enabled=data["local"]), required_oidc_provider=data["provider"]))

    monkeypatch.setattr(config, "get_app_config", load_config)
    loop_thread = threading.get_ident()
    with detect_blocking_io_strict():
        policy = await browser_session.browser_auth_policy()
    assert policy == BrowserAuthPolicy(False, "cvci")
    assert read_threads and all(thread != loop_thread for thread in read_threads)


@pytest.mark.asyncio
async def test_cached_policy_never_reopens_config_under_strict_blocking_gate(monkeypatch):
    from support.detectors.blocking_io_runtime import detect_blocking_io_strict

    from app.gateway.auth import browser_session

    reader = Mock(side_effect=AssertionError("cached request policy must not reopen configuration"))
    monkeypatch.setattr(browser_session, "_load_browser_policy", reader)
    policy = BrowserAuthPolicy(False, "cvci")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(browser_auth_policy=policy)))
    with detect_blocking_io_strict():
        assert await browser_session.browser_auth_policy(request) is policy
    reader.assert_not_called()
