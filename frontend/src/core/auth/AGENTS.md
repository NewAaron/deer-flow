# Auth state and recovery

`AuthProvider` publishes ID, logout (`null`) or unknown (`undefined`) outside
`QueryClientProvider`. Mount-local clients remount consumers, cancel/clear
old queries on owner changes; logout unmounts immediately. Public/unknown routes
use an independent client. Stale refreshes cannot restore owners.

Unknown transport failures allow 60s visible checks and immediate visible `online`
revalidation; connectivity never authenticates. Anonymous/logout/demos never probe.
Wakeups are single-flight; unmount removes listeners. Only current requests release
guards; confirmed user/logout supersedes them. Unknown pending refresh coalesces
reconnects once; success/401/logout/replacement/unmount discard them. Hidden tabs
retain reconnect until visible, bypassing throttle once. Effect deps include
`isLoading` to drain wakes after unchanged failed auth.

SSR sends bounded named SID/selector/legacy access_token cookies to configured
Gateway `/me` for signature/generation/online checks. Selector alone cannot
authenticate; no candidate truncation or credential logging.

Tests: `AuthProvider-recovery.dom.test.tsx`, `auth-network-recovery.spec.ts`,
query-owner and artifact-draft isolation. Trim emails, never passwords.

Read public `/auth/providers` before local forms or signed-in `/login` redirects.
Required provider starts fresh OAuth; errors/disabled pages require explicit retry.
Generic policy defaults local.

Strict CVCI requires `oauth_provider === "cvci"` and server `session_fence`. Capture
it before logout; unmount/lock consumers but retain owner/proof. POST
`expected_session` with CSRF. Only confirmed global success clears auth/drafts;
failure retains proof, 401/409 stops changed-identity retries. Superseding applyUser
fences late responses. Other providers, unfenced CVCI and demos retain behavior.

Public `/auth/cvci-logout-complete` only POSTs cleanup (`cleared`/`unchanged`);
unknown/503 retries. `/auth/cvci-logout` uses server-only `CVCI_LOGIN_ORIGIN`:
pure HTTPS or explicit-port HTTP localhost/127.0.0.1, fixed `/logout/complete`,
no browser redirect params. Loopbacks alone align strict loopback Host (optional
valid port); URL fallback requires absent Host. Ignore forwarded hosts; preserve
configured scheme/port, no-store/no-referrer, safe error HTML.
See `docs/cvci-workspace.md` and login/logout regressions.
