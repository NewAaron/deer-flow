# Authentication contracts

Generic browser cookies use `session_cookie`: `remember_me` stores no password;
persist HttpOnly `access_token` only on HTTPS/trusted HTTPS, direct localhost or
explicit opt-in. Other HTTP uses session cookies. Creators stamp final max_age
on request.state; CSRF mirrors it. An HttpOnly preference retains the choice;
generic logout deletes cookies and suppresses CSRF reissue.

`auth.local.enabled` defaults true; false blocks local login, register, initialize
and password changes before mutation. Optional `required_oidc_provider: cvci`
also requires OIDC and rejects auth-disabled mode. Load policy off-loop and cache
at startup. Require a signed SID and live bound grant on HTTP, WS, SSE and
standalone LangGraph; local/no-SID/demo identities cannot substitute.

Strict cookies are `deerflow_session_<sid>`. Bound verification to 32 cookies;
verify signatures and cookie-name/SID agreement before ordering the signed
`browser_issued_at` on this host. The shared selector is only a hint. An expired
or revoked latest credential fails without reviving an older one. OAuth state
binds its initiating browser generation; changed generations fail before bind,
provision or revoke. Global logout uses the signed owner plus server grant and
UI fence; retain proof until upstream confirmation. Cleanup never revokes;
delete only the original named JWT, preserving newer cookies and CSRF.

OIDC retains authorization-endpoint query bytes (RFC6749 3.1); malformed IdP
bodies raise OIDCError (502/sso_failed). Generic loopback CVCI may select browser
host; strict CVCI uses its fixed canonical endpoint. Backchannel endpoints verify
the configured issuer; MCP tickets bind the current session and CVCI tenant/user.
Tests: test_oidc_auth.py, test_cvci_unified_sessions.py and auth_surfaces.

PATs run as their owner. Invalid Bearer is 401 without cookie fallback, making
the CSRF Bearer skip safe; auth-origin checks remain. Scopes narrow only the
allowlisted threads/runs/projects routes (including thread move); every other
route rejects PATs, including admins. PAT management and password change require
interactive sessions. Store only SHA256 digests (migration0017).
