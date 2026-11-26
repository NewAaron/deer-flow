import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { AccountSettingsPage } from "@/components/workspace/settings/account-settings-page";
import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";

rs.mock("@/core/auth/AuthProvider", () => ({ useAuth: rs.fn() }));
rs.mock("@/core/i18n/hooks", () => ({ useI18n: rs.fn() }));
rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
  getCsrfHeaders: () => ({}),
}));

const alias = "cvci-internal-identity@identity.invalid";
function auth(provider: string | null, email = alias) {
  return {
    user: {
      id: "account",
      email,
      system_role: "user",
      needs_setup: false,
      oauth_provider: provider,
    },
    logout: rs.fn(),
  } as unknown as ReturnType<typeof useAuth>;
}

beforeEach(() => {
  rs.mocked(useAuth).mockReturnValue(auth("cvci"));
  rs.mocked(useI18n).mockReturnValue({ t: enUS } as ReturnType<typeof useI18n>);
});
afterEach(() => {
  cleanup();
  rs.clearAllMocks();
});

for (const [locale, dictionary, message] of [
  ["en", enUS, "Managed by CVCI"],
  ["zh", zhCN, "由CVCI统一管理"],
] as const) {
  test(`CVCI ${locale} profile shows unified management without rendering an internal email`, () => {
    rs.mocked(useI18n).mockReturnValue({ t: dictionary } as ReturnType<
      typeof useI18n
    >);
    render(<AccountSettingsPage />);
    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByText(alias)).toBeNull();
    expect(
      screen.queryByText(dictionary.settings.account.email, { exact: true }),
    ).toBeNull();
    expect(
      screen.getByText(dictionary.settings.account.ssoPasswordDescription),
    ).toBeTruthy();
    expect(
      screen.getByText(
        dictionary.settings.account.ssoPasswordMessage.replace(
          "{provider}",
          "cvci",
        ),
      ),
    ).toBeTruthy();
    expect(
      screen.queryByPlaceholderText(
        dictionary.settings.account.currentPassword,
      ),
    ).toBeNull();
    expect(screen.getByText("user", { exact: true })).toBeTruthy();
  });
}

test("another OIDC provider retains its email and SSO password instructions", () => {
  rs.mocked(useAuth).mockReturnValue(auth("oidc", "contact@example.com"));
  render(<AccountSettingsPage />);
  expect(screen.getByText("contact@example.com")).toBeTruthy();
  expect(
    screen.getByText(enUS.settings.account.email, { exact: true }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      enUS.settings.account.ssoPasswordMessage.replace("{provider}", "oidc"),
    ),
  ).toBeTruthy();
  expect(screen.queryByText("Managed by CVCI")).toBeNull();
});

test("a local account retains its email and native password change request", async () => {
  rs.mocked(useAuth).mockReturnValue(auth(null, "local@example.com"));
  rs.mocked(fetch).mockResolvedValue(new Response("{}", { status: 200 }));
  render(<AccountSettingsPage />);
  expect(screen.getByText("local@example.com")).toBeTruthy();
  fireEvent.change(
    screen.getByPlaceholderText(enUS.settings.account.currentPassword),
    { target: { value: "old password" } },
  );
  fireEvent.change(
    screen.getByPlaceholderText(enUS.settings.account.newPassword),
    { target: { value: "new password" } },
  );
  fireEvent.change(
    screen.getByPlaceholderText(enUS.settings.account.confirmNewPassword),
    { target: { value: "new password" } },
  );
  fireEvent.submit(
    screen
      .getByRole("button", { name: enUS.settings.account.updatePassword })
      .closest("form")!,
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/auth/change-password",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          current_password: "old password",
          new_password: "new password",
        }),
      }),
    ),
  );
  expect(
    await screen.findByText(enUS.settings.account.passwordChangedSuccess),
  ).toBeTruthy();
});
