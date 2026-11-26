import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
  rs,
} from "@rstest/core";

import { AUTH_DISABLED_USER } from "@/core/auth/auth-disabled-user";
import { STATIC_WEBSITE_USER } from "@/core/auth/static-user";
import { userSchema } from "@/core/auth/types";

rs.mock("next/headers", () => ({
  cookies: rs.fn(() => {
    throw new Error("cookies should not be read in static website mode");
  }),
}));

const ENV_KEYS = [
  "DEER_FLOW_AUTH_DISABLED",
  "DEER_FLOW_ENV",
  "ENVIRONMENT",
  "NEXT_PUBLIC_STATIC_WEBSITE_ONLY",
] as const;

type EnvSnapshot = Partial<
  Record<(typeof ENV_KEYS)[number], string | undefined>
>;

function snapshotEnv(): EnvSnapshot {
  const snapshot: EnvSnapshot = {};
  for (const key of ENV_KEYS) {
    snapshot[key] = process.env[key];
  }
  return snapshot;
}

function setEnv(key: (typeof ENV_KEYS)[number], value: string | undefined) {
  const env = process.env as Record<string, string | undefined>;
  if (value === undefined) {
    delete env[key];
  } else {
    env[key] = value;
  }
}

function restoreEnv(snapshot: EnvSnapshot) {
  for (const key of ENV_KEYS) {
    setEnv(key, snapshot[key]);
  }
}

async function loadFreshServerAuth() {
  rs.resetModules();
  return await import("@/core/auth/server");
}

describe("getServerSideUser — generation-scoped browser cookies", () => {
  let saved: EnvSnapshot;
  const sidA = "a".repeat(43);
  const sidB = "b".repeat(43);
  const user = {
    id: "cvci-user",
    email: "cvci-t3-u42@cvci.identity.example.com",
    system_role: "user",
    oauth_provider: "cvci",
    session_fence: "server-fence",
  };

  beforeEach(() => {
    saved = snapshotEnv();
    for (const key of ENV_KEYS) setEnv(key, undefined);
  });

  afterEach(() => {
    restoreEnv(saved);
    rs.unstubAllGlobals();
    rs.doUnmock("next/headers");
  });

  function mockCookies(values: Record<string, string>, status = 200) {
    rs.doMock("next/headers", () => ({
      cookies: rs.fn(async () => ({
        get: (name: string) =>
          name in values ? { name, value: values[name] } : undefined,
        getAll: () =>
          Object.entries(values).map(([name, value]) => ({ name, value })),
      })),
    }));
    const fetchSpy = rs.fn(
      async (url: string, _init?: RequestInit) =>
        new Response(
          JSON.stringify(
            url.endsWith("/setup-status")
              ? { needs_setup: false }
              : status === 200
                ? user
                : { detail: { code: "session_invalid" } },
          ),
          { status, headers: { "Content-Type": "application/json" } },
        ),
    );
    rs.stubGlobal("fetch", fetchSpy);
    return fetchSpy;
  }

  test("forwards a named-only session to the fixed Gateway me endpoint", async () => {
    const fetchSpy = mockCookies({ [`deerflow_session_${sidA}`]: "opaque-a" });
    const { getServerSideUser } = await loadFreshServerAuth();
    await expect(getServerSideUser()).resolves.toMatchObject({
      tag: "authenticated",
      user: { id: user.id },
    });
    expect(fetchSpy).toHaveBeenCalledExactlyOnceWith(
      "http://127.0.0.1:8001/api/v1/auth/me",
      expect.objectContaining({
        cache: "no-store",
        headers: { Cookie: `deerflow_session_${sidA}=opaque-a` },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  test("forwards both generations and the stale selector without choosing an identity", async () => {
    const fetchSpy = mockCookies({
      access_token: "generic-legacy",
      [`deerflow_session_${sidA}`]: "opaque-a",
      deerflow_active_session: sidA,
      [`deerflow_session_${sidB}`]: "opaque-b",
      csrf_token: "csrf-not-authentication",
      cvci_token: "upstream-secret",
      next: "https://attacker.invalid",
      deerflow_session_persistent: "1",
      deerflow_session_invalid: "ignored",
    });
    const { getServerSideUser } = await loadFreshServerAuth();
    await expect(getServerSideUser()).resolves.toMatchObject({
      tag: "authenticated",
    });
    const init = fetchSpy.mock.calls[0]![1]!;
    const forwarded = new Headers(init.headers)
      .get("cookie")!
      .split("; ")
      .sort();
    expect(forwarded).toEqual(
      [
        "access_token=generic-legacy",
        `deerflow_active_session=${sidA}`,
        `deerflow_session_${sidA}=opaque-a`,
        `deerflow_session_${sidB}=opaque-b`,
      ].sort(),
    );
  });

  test("a selector alone never authenticates or sends a me request", async () => {
    const fetchSpy = mockCookies({ deerflow_active_session: sidA });
    const { getServerSideUser } = await loadFreshServerAuth();
    await expect(getServerSideUser()).resolves.toEqual({
      tag: "unauthenticated",
    });
    expect(
      fetchSpy.mock.calls.every(([url]) => url.endsWith("/setup-status")),
    ).toBe(true);
  });

  test("preserves the generic legacy access_token path", async () => {
    const fetchSpy = mockCookies({
      access_token: "generic-legacy",
      unrelated: "private",
    });
    const { getServerSideUser } = await loadFreshServerAuth();
    await expect(getServerSideUser()).resolves.toMatchObject({
      tag: "authenticated",
    });
    expect(fetchSpy.mock.calls[0]![1]!.headers).toEqual({
      Cookie: "access_token=generic-legacy",
    });
  });

  test("leaves forged or revoked named-session rejection to Gateway", async () => {
    const fetchSpy = mockCookies(
      { [`deerflow_session_${sidA}`]: "not-a-JWT" },
      401,
    );
    const { getServerSideUser } = await loadFreshServerAuth();
    await expect(getServerSideUser()).resolves.toEqual({
      tag: "unauthenticated",
    });
    expect(fetchSpy.mock.calls[0]![0]).toBe(
      "http://127.0.0.1:8001/api/v1/auth/me",
    );
  });

  test("fails closed on an oversized named-cookie jar rather than truncating to an older identity", async () => {
    const values = Object.fromEntries(
      Array.from({ length: 33 }, (_, index) => [
        `deerflow_session_${String(index).padStart(16, "0")}`,
        "opaque",
      ]),
    );
    values.access_token = "must-not-revive-legacy";
    const fetchSpy = mockCookies(values);
    const { getServerSideUser } = await loadFreshServerAuth();
    await expect(getServerSideUser()).resolves.toEqual({
      tag: "unauthenticated",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  test.each([
    "opaque; injected=secret",
    "opaque\r\nX-Injected: secret",
    "x".repeat(8193),
  ])(
    "rejects unsafe or oversized cookie values without logging them",
    async (value) => {
      const fetchSpy = mockCookies({ [`deerflow_session_${sidA}`]: value });
      const consoleSpy = rs.spyOn(console, "error");
      const { getServerSideUser } = await loadFreshServerAuth();
      await expect(getServerSideUser()).resolves.toEqual({
        tag: "unauthenticated",
      });
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(consoleSpy).not.toHaveBeenCalled();
      consoleSpy.mockRestore();
    },
  );
});

describe("getServerSideUser", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("DEER_FLOW_AUTH_DISABLED", undefined);
    setEnv("DEER_FLOW_ENV", undefined);
    setEnv("ENVIRONMENT", undefined);
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", undefined);
  });

  afterEach(() => {
    restoreEnv(saved);
    rs.unstubAllGlobals();
  });

  test("bypasses gateway auth in static website mode", async () => {
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", "true");
    const fetchSpy = rs.fn(() => {
      throw new Error("fetch should not be called in static website mode");
    });
    rs.stubGlobal("fetch", fetchSpy);

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "authenticated",
      user: STATIC_WEBSITE_USER,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  test("bypasses gateway auth in auth-disabled mode", async () => {
    setEnv("DEER_FLOW_AUTH_DISABLED", "1");
    const fetchSpy = rs.fn(() => {
      throw new Error("fetch should not be called in auth-disabled mode");
    });
    rs.stubGlobal("fetch", fetchSpy);

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "authenticated",
      user: AUTH_DISABLED_USER,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  test("does not enable auth-disabled mode in explicit production environments", async () => {
    setEnv("DEER_FLOW_AUTH_DISABLED", "1");
    setEnv("DEER_FLOW_ENV", "production");

    const { isAuthDisabledMode } =
      await import("@/core/auth/auth-disabled-user");

    expect(isAuthDisabledMode()).toBe(false);
  });
});

describe("getServerSideUser — gateway_unavailable contract (issue #3493)", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("DEER_FLOW_AUTH_DISABLED", undefined);
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", undefined);
  });

  afterEach(() => {
    restoreEnv(saved);
    rs.unstubAllGlobals();
    rs.doUnmock("next/headers");
  });

  test("returns gateway_unavailable when /auth/me fetch rejects (e.g. AbortError)", async () => {
    rs.doMock("next/headers", () => ({
      cookies: rs.fn(async () => ({
        get: (name: string) =>
          name === "access_token" ? { value: "stub-token" } : undefined,
        getAll: () => [],
      })),
    }));
    const abortErr = new DOMException("Aborted", "AbortError");
    rs.stubGlobal(
      "fetch",
      rs.fn(() => Promise.reject(abortErr)),
    );

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "gateway_unavailable",
    });
  });

  test("returns gateway_unavailable when /auth/me responds with a 5xx", async () => {
    rs.doMock("next/headers", () => ({
      cookies: rs.fn(async () => ({
        get: (name: string) =>
          name === "access_token" ? { value: "stub-token" } : undefined,
        getAll: () => [],
      })),
    }));
    rs.stubGlobal(
      "fetch",
      rs.fn(() =>
        Promise.resolve(
          new Response("upstream error", {
            status: 503,
            statusText: "Service Unavailable",
          }),
        ),
      ),
    );

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "gateway_unavailable",
    });
  });
});

// ── account_disabled contract (RFC #4063 gap 3) ────────────────────

describe("getServerSideUser — account_disabled contract", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("DEER_FLOW_AUTH_DISABLED", undefined);
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", undefined);
    rs.doMock("next/headers", () => ({
      cookies: rs.fn(async () => ({
        get: (name: string) =>
          name === "access_token" ? { value: "stub-token" } : undefined,
        getAll: () => [],
      })),
    }));
  });

  afterEach(() => {
    restoreEnv(saved);
    rs.unstubAllGlobals();
    rs.doUnmock("next/headers");
  });

  function stubMeResponse(body: unknown, status = 401) {
    rs.stubGlobal(
      "fetch",
      rs.fn(
        async () =>
          new Response(JSON.stringify(body), {
            status,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
  }

  test("maps a 401 account_disabled session to the account_disabled tag", async () => {
    stubMeResponse({
      detail: { code: "account_disabled", message: "Account disabled" },
    });

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "account_disabled",
    });
  });

  test("keeps every other 401 a plain unauthenticated result", async () => {
    stubMeResponse({
      detail: { code: "token_expired", message: "Token expired" },
    });

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "unauthenticated",
    });
  });

  test("a non-JSON 401 body still degrades to unauthenticated", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => new Response("nope", { status: 401 })),
    );

    const { getServerSideUser } = await loadFreshServerAuth();

    await expect(getServerSideUser()).resolves.toEqual({
      tag: "unauthenticated",
    });
  });
});

// ── email contract ─────────────────────────────────────────────────

// Each address is one the backend registers with EmailStr and returns from
// /auth/me as shown (IDN domains come back in Unicode).
const BACKEND_ACCEPTED_EMAILS = [
  "a&b@example.com",
  "a@b.c",
  "a~b@example.com",
  "josé@example.com",
  "user@exämple.com",
];

describe("getServerSideUser — accepts every address the backend accepts", () => {
  let saved: EnvSnapshot;

  beforeEach(() => {
    saved = snapshotEnv();
    setEnv("DEER_FLOW_AUTH_DISABLED", undefined);
    setEnv("NEXT_PUBLIC_STATIC_WEBSITE_ONLY", undefined);
    rs.doMock("next/headers", () => ({
      cookies: rs.fn(async () => ({
        get: (name: string) =>
          name === "access_token" ? { value: "stub-token" } : undefined,
        getAll: () => [],
      })),
    }));
  });

  afterEach(() => {
    restoreEnv(saved);
    rs.unstubAllGlobals();
    rs.doUnmock("next/headers");
  });

  test.each(BACKEND_ACCEPTED_EMAILS)(
    "authenticates a session for %s",
    async (email) => {
      const me = {
        id: "u-1",
        email,
        system_role: "user",
        needs_setup: false,
        oauth_provider: null,
        permissions: null,
      };
      rs.stubGlobal(
        "fetch",
        rs.fn(
          async () =>
            new Response(JSON.stringify(me), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
        ),
      );

      const { getServerSideUser } = await loadFreshServerAuth();

      await expect(getServerSideUser()).resolves.toMatchObject({
        tag: "authenticated",
        user: { email },
      });
    },
  );
});

describe("userSchema email", () => {
  test.each(BACKEND_ACCEPTED_EMAILS)("keeps %s verbatim", (email) => {
    const parsed = userSchema.parse({
      id: "1",
      email,
      system_role: "user",
    });

    expect(parsed.email).toBe(email);
  });

  test("still rejects an empty or missing email", () => {
    expect(() =>
      userSchema.parse({ id: "1", email: "", system_role: "user" }),
    ).toThrow();
    expect(() => userSchema.parse({ id: "1", system_role: "user" })).toThrow();
  });
});

// ── system_role contract (RFC #4063 gap 2) ─────────────────────────

describe("userSchema system_role", () => {
  test("accepts a provider-configured custom role verbatim", () => {
    const parsed = userSchema.parse({
      id: "1",
      email: "g@example.com",
      system_role: "guest",
      needs_setup: false,
      oauth_provider: null,
      permissions: null,
    });

    expect(parsed.system_role).toBe("guest");
  });

  test("still rejects an empty role string", () => {
    expect(() =>
      userSchema.parse({
        id: "1",
        email: "g@example.com",
        system_role: "",
        needs_setup: false,
        disabled: false,
        oauth_provider: null,
        permissions: null,
      }),
    ).toThrow();
  });
});
