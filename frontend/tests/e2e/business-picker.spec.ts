import { expect, test } from "@playwright/test";

import {
  businessComposer,
  mockBusinessPicker,
  nativeRun,
  openBusinessPicker,
  pickerPage,
  pickerRecord,
  searchBusinessPicker,
} from "./utils/business-picker";
import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  mockLangGraphAPI(page);
});

test("vehicle prefix search confirms an empty draft once through the original run endpoint", async ({
  page,
}) => {
  const record = pickerRecord("VEHICLE", "浙A12345");
  const mock = await mockBusinessPicker(page, async (query, route) => {
    await route.fulfill({ json: pickerPage(query, [record]) });
  });
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page);
  await searchBusinessPicker(page, "浙A12");
  expect(mock.searches.at(-1)).toMatchObject({
    kind: "VEHICLE",
    keyword: "浙A12",
    pageNo: 1,
  });
  expect(mock.searches.at(-1)).not.toHaveProperty("keywordMode");
  await picker.results.first().click();
  await expect(picker.results.first()).toHaveAttribute("aria-checked", "true");
  const request = nativeRun(page);
  await picker.confirm.click();
  const body = (await request).postDataJSON();
  const message = body.input.messages.at(-1);
  expect(message.additional_kwargs.extension_mentions).toEqual([
    record.reference,
  ]);
  expect(JSON.stringify(message.content)).toContain("@浙A12345");
  expect(JSON.stringify(message.content)).not.toContain(record.reference.id);
  expect(body.assistant_id).toBe("lead_agent");
  await expect(picker.dialog).toBeHidden();
  await expect.poll(() => mock.runs.length).toBe(1);
});

test("a selected project preserves existing text, focuses the editor and waits for manual send", async ({
  page,
}) => {
  const record = pickerRecord("SITE", "合成项目");
  const mock = await mockBusinessPicker(page, async (query, route) => {
    await route.fulfill({ json: pickerPage(query, [record]) });
  });
  await page.goto("/workspace/chats/new");
  const composer = businessComposer(page);
  await composer.fill("Keep the existing draft.  ");
  const picker = await openBusinessPicker(page, 2);
  await searchBusinessPicker(page, "合成");
  expect(mock.searches.at(-1)).toMatchObject({
    kind: "SITE",
    keywordMode: "NAME",
  });
  await picker.results.first().click();
  await picker.confirm.click();
  await expect(picker.dialog).toBeHidden();
  await expect(composer).toBeFocused();
  await expect(composer).toContainText("Keep the existing draft.");
  await expect(page.getByTestId("extension-mention-chip")).toContainText(
    "合成项目",
  );
  expect(mock.runs).toHaveLength(0);
  const request = nativeRun(page);
  await composer.press("Enter");
  const message = (await request).postDataJSON().input.messages.at(-1);
  expect(JSON.stringify(message.content)).toContain("Keep the existing draft.");
  expect(message.additional_kwargs.extension_mentions).toEqual([
    record.reference,
  ]);
});

test("an attachment-only draft remains manual and retains its file during native submission", async ({
  page,
}) => {
  const mock = await mockBusinessPicker(page);
  await page.route("**/api/threads/*/uploads", (route) =>
    route.fulfill({
      json: {
        success: true,
        message: "Uploaded",
        files: [
          {
            filename: "brief.txt",
            size: 4,
            path: "brief.txt",
            virtual_path: "/mnt/user-data/uploads/brief.txt",
            artifact_url: "/api/threads/test/uploads/brief.txt",
            extension: ".txt",
          },
        ],
      },
    }),
  );
  await page.goto("/workspace/chats/new");
  const [fileChooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByTestId("add-attachments-button").click(),
  ]);
  await fileChooser.setFiles({
    name: "brief.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("test"),
  });
  await expect(page.getByText("brief.txt", { exact: true })).toBeVisible();
  const picker = await openBusinessPicker(page, 3);
  await searchBusinessPicker(page);
  await picker.results.first().click();
  await picker.confirm.click();
  await expect(picker.dialog).toBeHidden();
  await expect(page.getByText("brief.txt", { exact: true })).toBeVisible();
  expect(mock.runs).toHaveLength(0);
  const request = nativeRun(page);
  await businessComposer(page).press("Enter");
  const message = (await request).postDataJSON().input.messages.at(-1);
  expect(message.additional_kwargs.files).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        filename: "brief.txt",
        path: "/mnt/user-data/uploads/brief.txt",
      }),
    ]),
  );
  expect(message.additional_kwargs.extension_mentions).toEqual([
    pickerRecord("DEVICE").reference,
  ]);
});

test("business selection retains existing skill and conversation references", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    skills: [
      { name: "research", description: "Research a topic", enabled: true },
    ],
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Writer brief",
        agent_name: "writer",
      },
    ],
  });
  await page.route("**/api/features", (route) =>
    route.fulfill({
      json: {
        agents_api: { enabled: true },
        conversation_references: { enabled: true, max_references: 3 },
      },
    }),
  );
  const mock = await mockBusinessPicker(page);
  await page.goto("/workspace/chats/new");
  const composer = businessComposer(page);
  await composer.fill("Use @res");
  await page.getByRole("option", { name: "research Research a topic" }).click();
  await composer.pressSequentially("@Writer");
  await page.getByRole("option", { name: "Writer brief", exact: true }).click();
  await expect(page.getByTestId("inline-skill-reference")).toBeVisible();
  await expect(page.getByTestId("conversation-reference-chip")).toBeVisible();
  const picker = await openBusinessPicker(page);
  await searchBusinessPicker(page);
  await picker.results.first().click();
  await picker.confirm.click();
  await expect(page.getByTestId("inline-skill-reference")).toBeVisible();
  await expect(page.getByTestId("conversation-reference-chip")).toBeVisible();
  expect(mock.runs).toHaveLength(0);
  const request = nativeRun(page);
  await composer.press("Enter");
  const body = (await request).postDataJSON();
  const message = body.input.messages.at(-1);
  expect(message.additional_kwargs.skill_references).toEqual(["research"]);
  expect(message.additional_kwargs.conversation_references).toEqual([
    { thread_id: MOCK_THREAD_ID, title: "Writer brief", agent_name: "writer" },
  ]);
  expect(message.additional_kwargs.extension_mentions).toEqual([
    pickerRecord("VEHICLE").reference,
  ]);
});

test("approval modes query only the selected site, vehicle or permit subject", async ({
  page,
}) => {
  const mock = await mockBusinessPicker(page);
  await page.goto("/workspace/chats/new");
  await openBusinessPicker(page, 1);
  for (const [tab, kind, keywordMode] of [
    [/按工地|^site$/i, "SITE", "NAME"],
    [/按车辆|^vehicle$/i, "VEHICLE", undefined],
    [/按核准|^approval$/i, "PERMIT", "NAME"],
  ] as const) {
    await page.getByRole("tab", { name: tab }).click();
    await searchBusinessPicker(page, `Subject ${kind}`);
    await expect.poll(() => mock.searches.at(-1)?.kind).toBe(kind);
    expect(mock.searches.at(-1)?.keywordMode).toBe(keywordMode);
  }
  await page
    .getByLabel(/搜索方式|search (?:mode|by)/i)
    .selectOption("IDENTIFIER");
  await searchBusinessPicker(page, "PERMIT-001");
  await expect.poll(() => mock.searches.at(-1)?.keywordMode).toBe("IDENTIFIER");
  expect(mock.runs).toHaveLength(0);
});

test("equipment modes support device number, vehicle and each installation host type", async ({
  page,
}) => {
  const mock = await mockBusinessPicker(page);
  await page.goto("/workspace/chats/new");
  await openBusinessPicker(page, 3);
  await searchBusinessPicker(page, "DEVICE-001");
  expect(mock.searches.at(-1)).toMatchObject({
    kind: "DEVICE",
    keywordMode: "IDENTIFIER",
  });
  await page.getByRole("tab", { name: /按车辆|^vehicle$/i }).click();
  await searchBusinessPicker(page, "浙A12345");
  expect(mock.searches.at(-1)).toMatchObject({
    kind: "VEHICLE",
  });
  expect(mock.searches.at(-1)).not.toHaveProperty("keywordMode");
  await page.getByRole("tab", { name: /按场地|^location$/i }).click();
  for (const kind of ["SITE", "DISPOSAL", "CHECKPOINT"]) {
    await page.getByLabel(/场地类型|location type/i).selectOption(kind);
    await searchBusinessPicker(page, `Host ${kind}`);
    await expect.poll(() => mock.searches.at(-1)?.kind).toBe(kind);
    expect(mock.searches.at(-1)?.keywordMode).toBe("NAME");
  }
});

test("approval categories remain distinct when their IDs match", async ({
  page,
}) => {
  const records = [
    pickerRecord("PERMIT", "清运核准", 1, "TRANSPORT"),
    pickerRecord("PERMIT", "处置核准", 1, "DISPOSAL"),
  ];
  await mockBusinessPicker(page, async (query, route) =>
    route.fulfill({ json: pickerPage(query, records) }),
  );
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page, 1);
  await page.getByRole("tab", { name: /按核准|^approval$/i }).click();
  await searchBusinessPicker(page);
  await expect(picker.results).toHaveCount(2);
  await picker.results.filter({ hasText: "处置核准" }).click();
  const request = nativeRun(page);
  await picker.confirm.click();
  expect(
    (await request).postDataJSON().input.messages.at(-1).additional_kwargs
      .extension_mentions,
  ).toEqual([records[1]!.reference]);
});

test("load more preserves selection, retries the failed page and deduplicates appended candidates", async ({
  page,
}) => {
  let failMore = true;
  const mock = await mockBusinessPicker(page, async (query, route) => {
    if (query.pageNo === 2 && failMore) {
      await route.fulfill({ status: 503, json: { detail: "unavailable" } });
      return;
    }
    const records =
      query.pageNo === 1
        ? Array.from({ length: 12 }, (_, offset) =>
            pickerRecord(query.kind, `Archive ${offset + 1}`, offset + 1),
          )
        : [
            pickerRecord(query.kind, "Archive 12", 12),
            pickerRecord(query.kind, "Archive 13", 13),
          ];
    await route.fulfill({ json: pickerPage(query, records, 13) });
  });
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page, 2);
  await searchBusinessPicker(page, "Original keyword");
  await expect(picker.results).toHaveCount(12);
  await picker.results.first().click();
  await expect(picker.confirm).toBeEnabled();
  await expect(page.getByTestId("cvci-next")).toContainText(
    /加载更多|load more/i,
  );
  await page.getByTestId("cvci-next").click();
  await expect(page.getByTestId("cvci-next")).toContainText(/重试|retry/i);
  await expect(picker.results).toHaveCount(12);
  await expect(picker.results.first()).toHaveAttribute("aria-checked", "true");
  await expect(picker.confirm).toBeEnabled();
  failMore = false;
  await page.getByTestId("cvci-next").click();
  await expect(picker.results).toHaveCount(13);
  await expect(picker.results.last()).toContainText("Archive 13");
  await expect(picker.results.first()).toHaveAttribute("aria-checked", "true");
  await expect(picker.confirm).toBeEnabled();
  expect(mock.searches.at(-1)).toMatchObject({
    pageNo: 2,
    keyword: "Original keyword",
    kind: "SITE",
  });
  expect(mock.searches.map((query) => query.pageNo)).toEqual([1, 2, 2]);
});

test("search debounces, ignores IME composition and discards late responses after a new query", async ({
  page,
}) => {
  let releaseOld!: () => void;
  const held = new Promise<void>((resolve) => {
    releaseOld = resolve;
  });
  const mock = await mockBusinessPicker(page, async (query, route) => {
    if (query.keyword === "Old") await held;
    await route
      .fulfill({
        json: pickerPage(query, [pickerRecord(query.kind, query.keyword)]),
      })
      .catch(() => undefined);
  });
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page, 2);
  await page.clock.install();
  await picker.input.dispatchEvent("compositionstart");
  await picker.input.fill("合成");
  await page.clock.fastForward(500);
  expect(mock.searches).toHaveLength(0);
  await picker.input.dispatchEvent("compositionend", { data: "合成" });
  await page.clock.fastForward(300);
  await expect(picker.results.first()).toContainText("合成");
  const initialCount = mock.searches.length;
  await picker.input.fill("O");
  await picker.input.fill("Old");
  await page.clock.fastForward(249);
  expect(mock.searches).toHaveLength(initialCount);
  await page.clock.fastForward(1);
  await expect.poll(() => mock.searches.at(-1)?.keyword).toBe("Old");
  await picker.input.fill("Latest");
  await page.clock.fastForward(300);
  await expect(picker.results.first()).toContainText("Latest");
  releaseOld();
  await expect(picker.results.first()).toContainText("Latest");
  await expect(picker.results.filter({ hasText: "Old" })).toHaveCount(0);
  expect(mock.searches.filter((query) => query.keyword === "O")).toHaveLength(
    0,
  );
  await page.keyboard.press("Escape");
  await expect(picker.opener).toBeFocused();
});

test("failed searches can retry and an empty result cannot be confirmed", async ({
  page,
}) => {
  let failed = true;
  await mockBusinessPicker(page, async (query, route) => {
    if (failed)
      return route.fulfill({ status: 503, json: { detail: "unavailable" } });
    return route.fulfill({ json: pickerPage(query, [], 0) });
  });
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page, 2);
  await picker.input.fill("Missing");
  await expect(page.getByTestId("cvci-status")).toContainText(
    /失败|重试|unavailable|retry/i,
  );
  await expect(picker.confirm).toBeDisabled();
  failed = false;
  await page.getByRole("button", { name: /^重试$|^retry$/i }).click();
  await expect(page.getByTestId("cvci-status")).toContainText(
    /未找到|没有|no matching records|no results/i,
  );
  await expect(picker.results).toHaveCount(0);
  await expect(picker.confirm).toBeDisabled();
});

test("closing a pending search aborts the action and cannot contaminate a reopened selector", async ({
  page,
}) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await mockBusinessPicker(page, async (query, route) => {
    if (query.keyword === "Delayed") await held;
    await route
      .fulfill({
        json: pickerPage(query, [pickerRecord(query.kind, query.keyword)]),
      })
      .catch(() => undefined);
  });
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page, 2);
  const pending = page.waitForRequest(
    (request) =>
      request.url().includes("/actions/search_subjects") &&
      request.postDataJSON().keyword === "Delayed",
  );
  await picker.input.fill("Delayed");
  await pending;
  const canceled = page.waitForEvent("requestfailed", {
    predicate: (request) =>
      request.url().includes("/actions/search_subjects") &&
      request.postDataJSON().keyword === "Delayed",
  });
  try {
    await page.keyboard.press("Escape");
    await canceled;
    await expect(picker.dialog).toBeHidden();
    await expect(picker.opener).toBeFocused();
    const reopened = await openBusinessPicker(page, 2);
    await searchBusinessPicker(page, "Fresh");
    release();
    await expect(reopened.results).toHaveCount(1);
    await expect(reopened.results.first()).toContainText("Fresh");
    await expect(reopened.results.filter({ hasText: "Delayed" })).toHaveCount(
      0,
    );
  } finally {
    release();
  }
});

test("keyboard focus reaches shadow controls, stays modal and returns to the opening card", async ({
  page,
}) => {
  await mockBusinessPicker(page);
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page);
  await searchBusinessPicker(page);
  await picker.results.first().click();
  await picker.input.focus();
  const focusedControls: string[] = [];
  for (let index = 0; index < 10; index++) {
    await page.keyboard.press("Tab");
    const focus = await picker.dialog.evaluate((dialog) => {
      let active: Element | null = document.activeElement;
      while (active?.shadowRoot?.activeElement)
        active = active.shadowRoot.activeElement;
      const id = active?.getAttribute("data-testid") ?? "";
      let composed: Node | null = active;
      while (composed && !dialog.contains(composed)) {
        const root = composed.getRootNode();
        composed = root instanceof ShadowRoot ? root.host : null;
      }
      return { inside: !!composed, id };
    });
    expect(focus.inside).toBe(true);
    focusedControls.push(focus.id);
  }
  expect(focusedControls).toContain("cvci-result");
  expect(focusedControls).toContain("cvci-confirm");
  await picker.input.focus();
  await page.keyboard.press("Shift+Tab");
  await expect(picker.dialog).toBeVisible();
  expect(
    await picker.dialog.evaluate((dialog) => {
      let active: Node | null = document.activeElement;
      while (active && !dialog.contains(active)) {
        const root = active.getRootNode();
        active = root instanceof ShadowRoot ? root.host : null;
      }
      return !!active;
    }),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(picker.dialog).toBeHidden();
  await expect(picker.opener).toBeFocused();
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 720 },
]) {
  test(`dark picker fits the viewport and keeps actions reachable at ${viewport.width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await mockBusinessPicker(page);
    await page.goto("/workspace/chats/new");
    const picker = await openBusinessPicker(page, 3);
    await searchBusinessPicker(page);
    await picker.results.first().click();
    await picker.confirm.scrollIntoViewIfNeeded();
    await expect(picker.confirm).toBeInViewport();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width);
    const box = await picker.dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(box!.y).toBeGreaterThanOrEqual(-1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
    await page.screenshot({
      path: testInfo.outputPath(`picker-dark-${viewport.width}.png`),
    });
  });
}

test("desktop picker retains the selected visual style in light mode", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockBusinessPicker(page);
  await page.goto("/workspace/chats/new");
  const picker = await openBusinessPicker(page, 1);
  await searchBusinessPicker(page, "Synthetic site");
  await picker.results.first().click();
  await page.screenshot({
    path: testInfo.outputPath("picker-light-desktop.png"),
  });
});
