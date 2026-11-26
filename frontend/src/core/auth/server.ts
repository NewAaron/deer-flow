import { cookies } from "next/headers";

import { isStaticWebsiteOnly } from "../static-mode";

import { AUTH_DISABLED_USER, isAuthDisabledMode } from "./auth-disabled-user";
import { AUTH_REQUEST_TIMEOUT_MS } from "./constants";
import { getGatewayConfig } from "./gateway-config";
import { STATIC_WEBSITE_USER } from "./static-user";
import { type AuthResult, parseAuthError, userSchema } from "./types";

const SESSION_COOKIE_NAME = /^deerflow_session_[A-Za-z0-9_-]{16,128}$/;
const SESSION_ID = /^[A-Za-z0-9_-]{16,128}$/;
const SAFE_COOKIE_VALUE =
  /^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{1,8192}$/;

/**
 * Fetch the authenticated user from the gateway using the request's cookies.
 * Returns a tagged AuthResult — callers use exhaustive switch, no try/catch.
 */
export async function getServerSideUser(): Promise<AuthResult> {
  if (isStaticWebsiteOnly()) {
    return {
      tag: "authenticated",
      user: STATIC_WEBSITE_USER,
    };
  }

  if (isAuthDisabledMode()) {
    return {
      tag: "authenticated",
      user: AUTH_DISABLED_USER,
    };
  }

  const cookieStore = await cookies();
  const sessionCookie = cookieStore.get("access_token");
  const namedCookies = cookieStore
    .getAll()
    .filter((cookie) => SESSION_COOKIE_NAME.test(cookie.name));
  // Preserve every bounded candidate: only Gateway may verify signatures,
  // choose the newest generation and check its current online authorization.
  if (
    namedCookies.length > 32 ||
    namedCookies.some((cookie) => !SAFE_COOKIE_VALUE.test(cookie.value)) ||
    (sessionCookie && !SAFE_COOKIE_VALUE.test(sessionCookie.value))
  ) {
    return { tag: "unauthenticated" };
  }
  const authCookies = namedCookies.map(
    (cookie) => `${cookie.name}=${cookie.value}`,
  );
  if (sessionCookie) authCookies.unshift(`access_token=${sessionCookie.value}`);
  const selector = cookieStore.get("deerflow_active_session");
  if (authCookies.length > 0 && selector && SESSION_ID.test(selector.value)) {
    authCookies.push(`deerflow_active_session=${selector.value}`);
  }

  let internalGatewayUrl: string;
  try {
    internalGatewayUrl = getGatewayConfig().internalGatewayUrl;
  } catch (err) {
    return { tag: "config_error", message: String(err) };
  }

  if (authCookies.length === 0) {
    // No session — check whether the system has been initialised yet.
    const setupController = new AbortController();
    const setupTimeout = setTimeout(
      () => setupController.abort(),
      AUTH_REQUEST_TIMEOUT_MS,
    );
    try {
      const setupRes = await fetch(
        `${internalGatewayUrl}/api/v1/auth/setup-status`,
        {
          cache: "no-store",
          signal: setupController.signal,
        },
      );
      clearTimeout(setupTimeout);
      if (setupRes.ok) {
        const setupData = (await setupRes.json()) as { needs_setup?: boolean };
        if (setupData.needs_setup) {
          return { tag: "system_setup_required" };
        }
      }
    } catch {
      clearTimeout(setupTimeout);
      // If setup-status is unreachable/times out, fall through to unauthenticated.
    }
    return { tag: "unauthenticated" };
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(`${internalGatewayUrl}/api/v1/auth/me`, {
      headers: { Cookie: authCookies.join("; ") },
      cache: "no-store",
      signal: controller.signal,
    });
    clearTimeout(timeout); // Clear immediately — covers all response branches

    if (res.ok) {
      const parsed = userSchema.safeParse(await res.json());
      if (!parsed.success) {
        console.error("[SSR auth] Malformed /auth/me response:", parsed.error);
        return { tag: "gateway_unavailable" };
      }
      if (parsed.data.needs_setup) {
        return { tag: "needs_setup", user: parsed.data };
      }
      return { tag: "authenticated", user: parsed.data };
    }
    if (res.status === 401 || res.status === 403) {
      // A disabled account gets a distinct code so the login redirect can
      // say why (#4063 gap 3); every other rejected session (expired,
      // invalid, revoked) stays a plain unauthenticated bounce.
      const code = await res
        .json()
        .then((data) => parseAuthError(data).code)
        .catch(() => null);
      if (code === "account_disabled") {
        return { tag: "account_disabled" };
      }
      return { tag: "unauthenticated" };
    }
    console.error(`[SSR auth] /api/v1/auth/me responded ${res.status}`);
    return { tag: "gateway_unavailable" };
  } catch {
    clearTimeout(timeout);
    console.error("[SSR auth] Failed to reach gateway");
    return { tag: "gateway_unavailable" };
  }
}
