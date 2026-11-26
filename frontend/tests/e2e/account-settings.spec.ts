import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

for (const provider of ["cvci", "oidc", null] as const) {
  test(`account profile preserves ${provider ?? "local"} identity presentation`, async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    const email =
      provider === "cvci"
        ? "cvci-internal-identity@identity.invalid"
        : "contact@example.com";
    await page.route("**/api/v1/auth/me", (route) =>
      route.fulfill({
        json: {
          id: "account-profile",
          email,
          system_role: "user",
          needs_setup: false,
          oauth_provider: provider,
        },
      }),
    );
    await page.goto("/workspace/chats/new?settings=account");
    const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await expect(dialog).toBeVisible();
    // The fixture server initially renders its auth-disabled account; refresh the real provider.
    const identity = page.waitForResponse((response) =>
      response.url().endsWith("/api/v1/auth/me"),
    );
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    await identity;
    if (provider === "cvci") {
      await expect(
        dialog.getByText("Managed by CVCI", { exact: true }),
      ).toBeVisible();
      await expect(dialog).not.toContainText(email);
      await expect(dialog.getByText("Email", { exact: true })).toBeHidden();
    } else {
      await expect(dialog.getByText(email, { exact: true })).toBeVisible();
      await expect(dialog.getByText("Email", { exact: true })).toBeVisible();
    }
    if (provider) {
      await expect(
        dialog.getByText("Password is managed by your SSO provider.", {
          exact: true,
        }),
      ).toBeVisible();
      await expect(dialog.getByPlaceholder("Current password")).toBeHidden();
    } else
      await expect(dialog.getByPlaceholder("Current password")).toBeVisible();
  });
}
