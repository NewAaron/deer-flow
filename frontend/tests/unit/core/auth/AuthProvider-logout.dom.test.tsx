import { afterEach, beforeEach, expect, test, rs } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useContext } from "react";

import { useArtifactDraftOwner } from "@/core/artifacts/draft-context";
import { AuthProvider, useAuth } from "@/core/auth/AuthProvider";
import { AuthQueryScopeContext } from "@/core/auth/query-scope";
import type { User } from "@/core/auth/types";

const push = rs.fn();
let staticMode = false;
rs.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => window.location.pathname,
}));
rs.mock("@/core/static-mode", () => ({
  isStaticWebsiteOnly: () => staticMode,
}));
rs.mock("@/core/artifacts/draft-context", () => ({
  useArtifactDraftOwner: rs.fn(),
}));

const originalFetch = globalThis.fetch;
let context: ReturnType<typeof useAuth> | null = null;
let owner: string | null | undefined;
function Probe() {
  context = useAuth();
  owner = useContext(AuthQueryScopeContext);
  return null;
}
function mount(
  provider: string | null = "cvci",
  fence: string | null = "synthetic-session-fence-a",
) {
  const user: User = {
    id: "u-1",
    email: "internal@example.test",
    system_role: "user",
    needs_setup: false,
    oauth_provider: provider,
    session_fence: fence,
  };
  render(
    <AuthProvider initialUser={user}>
      <Probe />
    </AuthProvider>,
  );
}

beforeEach(() => {
  staticMode = false;
  window.history.replaceState({}, "", "/workspace/chats/old?view=files");
});
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  push.mockReset();
  globalThis.fetch = originalFetch;
  context = null;
});

test("CVCI locks its workspace and preserves its owner until global logout is confirmed", async () => {
  const navigate = rs
    .spyOn(window.location, "assign")
    .mockImplementation(() => undefined);
  let resolve!: (response: Response) => void;
  globalThis.fetch = rs.fn(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  ) as typeof fetch;
  mount();
  let completion!: Promise<void>;
  act(() => {
    completion = context!.logout();
  });
  expect(context!.user?.id).toBe("u-1");
  expect(owner).toBe("u-1");
  expect(screen.getByRole("status")).toBeTruthy();
  expect(navigate).not.toHaveBeenCalled();
  await act(async () => {
    resolve(new Response(null, { status: 204 }));
    await completion;
  });
  expect(globalThis.fetch).toHaveBeenCalledWith("/api/v1/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_session: "synthetic-session-fence-a" }),
  });
  expect(useArtifactDraftOwner).toHaveBeenLastCalledWith(null);
  expect(navigate).toHaveBeenCalledExactlyOnceWith(
    "/auth/cvci-logout-complete",
  );
  expect(push).not.toHaveBeenCalled();
});

test.each([null, "oidc", "github"])(
  "successful %s logout retains the existing home navigation",
  async (provider) => {
    const navigate = rs
      .spyOn(window.location, "assign")
      .mockImplementation(() => undefined);
    globalThis.fetch = rs.fn(
      async () => new Response(null, { status: 204 }),
    ) as typeof fetch;
    mount(provider);
    await act(async () => {
      await context!.logout();
    });
    expect(owner).toBeNull();
    expect(push).toHaveBeenCalledExactlyOnceWith("/");
    expect(navigate).not.toHaveBeenCalled();
  },
);

test.each(["http", "network"])(
  "failed CVCI logout stays locked with its original proof for retry: %s",
  async (failure) => {
    const navigate = rs
      .spyOn(window.location, "assign")
      .mockImplementation(() => undefined);
    rs.spyOn(console, "error").mockImplementation(() => undefined);
    globalThis.fetch = rs.fn(async () => {
      if (failure === "network") throw new Error("offline");
      return new Response(null, { status: 503 });
    }) as typeof fetch;
    mount();
    await act(async () => {
      await context!.logout();
    });
    expect(owner).toBe("u-1");
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button")).toBeTruthy();
    expect(window.location.pathname).toBe("/workspace/chats/old");
    expect(navigate).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  },
);

test("CVCI retry keeps its original fence", async () => {
  const navigate = rs
    .spyOn(window.location, "assign")
    .mockImplementation(() => undefined);
  let attempts = 0;
  globalThis.fetch = rs.fn(
    async () => new Response(null, { status: ++attempts === 1 ? 503 : 204 }),
  ) as typeof fetch;
  mount();
  await act(async () => {
    await context!.logout();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button"));
  });
  expect(navigate).toHaveBeenCalledExactlyOnceWith(
    "/auth/cvci-logout-complete",
  );
  expect(
    rs.mocked(fetch).mock.calls.map(([, options]) => options?.body),
  ).toEqual([
    JSON.stringify({ expected_session: "synthetic-session-fence-a" }),
    JSON.stringify({ expected_session: "synthetic-session-fence-a" }),
  ]);
});

test("a late A logout cannot clear or navigate after B is applied", async () => {
  const navigate = rs
    .spyOn(window.location, "assign")
    .mockImplementation(() => undefined);
  let resolve!: (value: Response) => void;
  globalThis.fetch = rs.fn(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  ) as typeof fetch;
  mount();
  let pending!: Promise<void>;
  act(() => {
    pending = context!.logout();
  });
  act(() =>
    context!.applyUser({
      id: "u-b",
      email: "b@example.test",
      system_role: "user",
      needs_setup: false,
      oauth_provider: "cvci",
      session_fence: "fence-b",
    }),
  );
  await act(async () => {
    resolve(new Response(null, { status: 204 }));
    await pending;
  });
  expect(context!.user?.id).toBe("u-b");
  expect(useArtifactDraftOwner).toHaveBeenLastCalledWith("u-b");
  expect(navigate).not.toHaveBeenCalled();
});

test.each([401, 409])(
  "changed strict session %s offers no blind global retry",
  async (status) => {
    const navigate = rs
      .spyOn(window.location, "assign")
      .mockImplementation(() => undefined);
    globalThis.fetch = rs.fn(
      async () => new Response(null, { status }),
    ) as typeof fetch;
    mount();
    await act(async () => {
      await context!.logout();
    });
    expect(screen.getByRole("alert").textContent).toContain("changed");
    expect(screen.queryByRole("button", { name: "Retry sign-out" })).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  },
);

test("generic CVCI without a session fence retains its immediate-clear logout behavior", async () => {
  const navigate = rs
    .spyOn(window.location, "assign")
    .mockImplementation(() => undefined);
  globalThis.fetch = rs.fn(
    async () => new Response(null, { status: 204 }),
  ) as typeof fetch;
  mount("cvci", null);
  await act(async () => {
    await context!.logout();
  });
  expect(owner).toBeNull();
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/auth/cvci-logout");
  expect(fetch).toHaveBeenCalledWith("/api/v1/auth/logout", {
    method: "POST",
    credentials: "include",
  });
});

test("static CVCI mode keeps its existing local navigation without a logout request", async () => {
  staticMode = true;
  globalThis.fetch = rs.fn();
  const navigate = rs
    .spyOn(window.location, "assign")
    .mockImplementation(() => undefined);
  mount();
  await act(async () => {
    await context!.logout();
  });
  expect(globalThis.fetch).not.toHaveBeenCalled();
  expect(push).toHaveBeenCalledExactlyOnceWith("/");
  expect(navigate).not.toHaveBeenCalled();
});
