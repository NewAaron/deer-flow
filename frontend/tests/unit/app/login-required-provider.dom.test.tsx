import { afterEach, beforeEach, expect, test, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import LoginPage from "@/app/(auth)/login/page";
import type * as AuthProviderModule from "@/core/auth/AuthProvider";
import { enUS } from "@/core/i18n/locales/en-US";

const push = rs.fn();
const replace = rs.fn();
let authenticated = false;
let query = "";
rs.mock("@/core/auth/AuthProvider", () => {
  const actual = rs.requireActual<typeof AuthProviderModule>(
    "@/core/auth/AuthProvider",
  );
  return { ...actual, useAuth: () => ({ isAuthenticated: authenticated }) };
});
rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ t: enUS }) }));
rs.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(query),
}));
rs.mock("next-themes", () => ({ useTheme: () => ({ theme: "light" }) }));
rs.mock("@/components/ui/flickering-grid", () => ({
  FlickeringGrid: () => null,
}));
rs.mock("@/core/auth/setup", () => ({
  canCreateRegularAccount: () => true,
  fetchSetupStatus: async () => ({
    needs_setup: false,
    registration_enabled: true,
  }),
}));

beforeEach(() => {
  authenticated = false;
  query = "";
  window.history.replaceState({}, "", "/login");
});
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  push.mockReset();
  replace.mockReset();
});
function policy(local = false) {
  globalThis.fetch = rs.fn(async () =>
    Response.json({
      providers: [{ id: "cvci", display_name: "CVCI", type: "oidc" }],
      local_login_enabled: local,
      required_oidc_provider: local ? null : "cvci",
    }),
  ) as typeof fetch;
}

test.each([false, true])(
  "required CVCI login never exposes local form or reuses old DF auth: %s",
  async (existing) => {
    authenticated = existing;
    policy();
    const navigate = rs
      .spyOn(window.location, "replace")
      .mockImplementation(() => undefined);
    render(<LoginPage />);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledExactlyOnceWith(
        "/api/v1/auth/oauth/cvci?next=%2Fworkspace&remember_me=false",
      ),
    );
    expect(screen.queryByLabelText(enUS.login.email)).toBeNull();
    expect(screen.queryByLabelText(enUS.login.password)).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  },
);

test("an OAuth failure offers an explicit retry without an automatic loop", async () => {
  query = "error=sso_failed";
  policy();
  const navigate = rs
    .spyOn(window.location, "replace")
    .mockImplementation(() => undefined);
  render(<LoginPage />);
  const button = await screen.findByRole("button", {
    name: "Continue with CVCI",
  });
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(window.location.pathname).toBe("/api/v1/auth/oauth/cvci");
  expect(screen.queryByLabelText(enUS.login.email)).toBeNull();
});

test("generic local form stays available and its SSO button cannot submit saved admin credentials", async () => {
  policy(true);
  render(<LoginPage />);
  const email = await screen.findByLabelText(enUS.login.email);
  fireEvent.change(email, { target: { value: "admin@example.com" } });
  fireEvent.change(screen.getByLabelText(enUS.login.password), {
    target: { value: "synthetic-password" },
  });
  const sso = await screen.findByRole("button", { name: "Continue with CVCI" });
  expect(sso.getAttribute("type")).toBe("button");
  expect(sso.closest("form")).toBeNull();
  fireEvent.click(sso);
  expect(
    rs
      .mocked(fetch)
      .mock.calls.some(([url]) =>
        (typeof url === "string"
          ? url
          : url instanceof URL
            ? url.href
            : url.url
        ).includes("/login/local"),
      ),
  ).toBe(false);
});
