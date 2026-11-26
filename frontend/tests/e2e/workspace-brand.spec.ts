import { expect, test } from "@playwright/test";

import {
  mockBusinessPicker,
  openBusinessPicker,
} from "./utils/business-picker";
import { mockLangGraphAPI } from "./utils/mock-api";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  mockLangGraphAPI(page);
});

test("business cards open a selector without rewriting the draft or submitting", async ({
  page,
}) => {
  const { runs, searches } = await mockBusinessPicker(page);
  await page.goto("/workspace/chats/new");
  const cards = page.locator(".cvci-starter-card");
  await expect(cards).toHaveCount(4);
  const input = page.getByPlaceholder(/how can i assist you/i);
  await expect(input).toBeVisible();
  await input.fill("Keep this existing draft.  ");
  const picker = await openBusinessPicker(page);
  await expect(input).toHaveValue("Keep this existing draft.  ");
  await expect(picker.input).toBeEmpty();
  expect(searches).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(picker.dialog).toBeHidden();
  await expect(picker.opener).toBeFocused();
  await expect(input).toHaveValue("Keep this existing draft.  ");
  expect(runs).toHaveLength(0);
  await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
});

for (const width of [1487, 390]) {
  test(`home reference picker remains inside the viewport at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1058 });
    await page.goto("/workspace/chats/new");
    await page.getByTestId("mention-button").click();
    const picker = page.getByTestId("mention-picker");
    await expect(picker).toBeVisible();
    const box = await picker.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(
      (page.viewportSize()?.height ?? 0) + 1,
    );
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(width);
  });
}

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 390, height: 844 },
]) {
  test(`branded homepage can scroll to all primary controls at ${viewport.width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto("/workspace/chats/new");
    const cards = page.locator(".cvci-starter-card");
    await expect(cards).toHaveCount(4);
    await cards.last().scrollIntoViewIfNeeded();
    await expect(cards.last()).toBeInViewport();
    await page
      .getByPlaceholder(/how can i assist you/i)
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByRole("button", { name: "Submit", exact: true }),
    ).toBeInViewport();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width);
    await page.screenshot({
      path: testInfo.outputPath(`workspace-${viewport.width}.png`),
    });
  });
}

test("workspace dark tokens do not leak onto the public homepage", async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/workspace/chats/new");
  await expect(page.locator("[data-cvci-workspace]")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() =>
        getComputedStyle(document.body)
          .getPropertyValue("--cvci-gradient-start")
          .trim(),
      ),
    )
    .not.toBe("");
  await page.screenshot({ path: testInfo.outputPath("workspace-dark.png") });
  await page.goto("/");
  await expect(page.locator("[data-cvci-workspace]")).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      getComputedStyle(document.body)
        .getPropertyValue("--cvci-gradient-start")
        .trim(),
    ),
  ).toBe("");
});
