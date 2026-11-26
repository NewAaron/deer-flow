# CVCI workspace ownership

The local product name is MSAI. Reuse `public/cvci/emblem.png` for product marks,
favicon and notification icons; decorative auth/landing backgrounds use the
emblem as an image, not a silhouette mask. Keep upstream promotional links out
of product navigation and About. Documentation/blog bodies, license notices,
internal protocol names and persisted data remain unchanged.

The local CVCI deployment presentation uses `styles/workspace-brand.css` and the
`data-cvci-workspace` marker in `WorkspaceContent`. Workspace-scoped body tokens
also reach Radix/Sonner portals; do not replace global auth/docs/landing tokens.

`BusinessWelcome` remains presentation. Its four cards open the installed
`cvci-entities` extension's `composer-picker` surfaces, hosted by `ComposerPicker`
in a desktop Dialog or mobile Sheet. The host validates selection references,
appends them to the canonical draft and submits the native form only when that
draft had no text, files, quotes or references. Existing drafts remain intact
for manual sending. Surface cancellation, composer lifetime and picker generation
fence late callbacks. Picker surfaces never enter page navigation; CVCI search,
entity types and question text belong to the packaged extension. Keep native
model/mode resolution, message grouping and disclosure defaults. Generic starter
suggestions stay on the default welcome screen. The composer paperclip opens the
native file chooser directly, and the `@` button opens the reference picker.

CVCI account profiles show a localized unified-management notice instead of an
email row. Use the authenticated `oauth_provider === "cvci"` identity, never an
email-domain heuristic: internal identity aliases are not contact addresses.
Other OIDC and local accounts keep their existing email presentation and password
flows. Preserve the SSO password-management explanation for CVCI accounts.

The deployment's public login policy disables local password login and requires
CVCI. `/login` waits for policy before exposing forms or reusing any existing
DeerFlow identity, then starts fresh OAuth. An OAuth error does not auto-loop.
Canonical browser addresses are localhost:18080 and localhost:2026; no localStorage
or user identity is migrated from the numeric alias.

Strict CVCI logout captures the server-issued session fence, locks the workspace
without discarding the original proof, and clears auth/cache/drafts only after
global success. Failure stays locked for retry; changed sessions stop. Successful
logout in either app enters `/auth/cvci-logout-complete`, whose only action is
cleanup; `cleared` and `unchanged` reach the fixed `/auth/cvci-logout` GET route.
That route resolves server-only runtime `CVCI_LOGIN_ORIGIN` and constructs only
`/logout/complete`. CVCI's completion page never performs global logout: a valid
different account remains intact, confirmed invalid credentials clear only while
the captured identity still matches, and unknown state offers retry. It finishes
at `/login?recovery=1`. Neither completion page restarts OAuth automatically;
request queries never supply a destination. Accept only a pure HTTPS origin or
HTTP `localhost`/`127.0.0.1` with an explicit port. Align these two loopback hosts
with a strict loopback `Host` authority (optional valid port) only when the
configured origin is also loopback, preserving its scheme/port. Only an absent
`Host` falls back to the request URL; forwarded-host headers are ignored.
Responses are uncached and omit referrers. Missing/invalid
configuration shows a fixed safe retry page, never OAuth or the old assistant.
Other providers, static demos and generic CVCI sessions without strict fences
retain their existing logout behavior. Global revocation is a server guarantee,
separate from best-effort browser cleanup; no identity or proof travels in a URL.
