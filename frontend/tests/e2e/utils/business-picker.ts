import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, type Route } from "@playwright/test";

export type PickerQuery = {
  kind: string;
  keyword: string;
  keywordMode?: "NAME" | "IDENTIFIER";
  pageNo: number;
  pageSize: number;
};

export type PickerRecord = {
  reference: {
    namespace: "cvci-entities";
    provider: string;
    id: string;
    label: string;
  };
  kind: string;
  name: string;
  code: string;
  permitKind?: "TRANSPORT" | "DISPOSAL";
};

const providers: Record<string, string> = {
  VEHICLE: "vehicle",
  SITE: "site",
  DEVICE: "device",
  DISPOSAL: "disposal",
  CHECKPOINT: "checkpoint",
};

export function pickerRecord(
  kind: string,
  label = "Synthetic archive",
  index = 1,
  permitKind: "TRANSPORT" | "DISPOSAL" = "TRANSPORT",
): PickerRecord {
  return {
    reference: {
      namespace: "cvci-entities",
      provider:
        kind === "PERMIT"
          ? `permit-${permitKind.toLowerCase()}`
          : providers[kind]!,
      id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      label,
    },
    kind,
    name: label,
    code: `SYNTHETIC-${index}`,
    ...(kind === "PERMIT" ? { permitKind } : {}),
  };
}

export function pickerPage(
  query: PickerQuery,
  items = [pickerRecord(query.kind)],
  total = items.length,
) {
  return {
    items,
    pageNo: query.pageNo,
    pageSize: 12,
    total,
    hasMore: query.pageNo * 12 < total,
  };
}

/** Serve the actual packaged browser code; only the authorized search response is mocked. */
export async function mockBusinessPicker(
  page: Page,
  handler?: (query: PickerQuery, route: Route) => Promise<void>,
) {
  const packageRoot = path.resolve(
    process.cwd(),
    "../examples/deerflow-extension-cvci-entities/deerflow_extension_cvci_entities",
  );
  const source = readFileSync(path.join(packageRoot, "static/index.mjs"));
  const styles = readFileSync(path.join(packageRoot, "static/style.css"));
  const revision = createHash("sha256")
    .update(source)
    .update(styles)
    .digest("hex");
  const prefix = `/api/plugins/cvci-entities/assets/${revision}/static/`;
  const searches: PickerQuery[] = [];
  const runs: unknown[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/runs/stream")) {
      runs.push(request.postDataJSON());
    }
  });
  await page.route("**/api/plugins**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/plugins") {
      return route.fulfill({
        json: [
          {
            namespace: "cvci-entities",
            viewer_id: "default",
            module: "cvci-entities.v1",
            entry: `${prefix}index.mjs`,
            transport: "assets-v1",
            title: "CVCI archive search",
            description: "Select a business record",
            settings: { enabled: true },
            backend_actions: ["search_subjects"],
          },
        ],
      });
    }
    if (url.pathname === `${prefix}index.mjs`) {
      return route.fulfill({ contentType: "text/javascript", body: source });
    }
    if (url.pathname === `${prefix}style.css`) {
      return route.fulfill({ contentType: "text/css", body: styles });
    }
    if (url.pathname === "/api/plugins/cvci-entities/actions/search_subjects") {
      expect(route.request().headers()["x-deerflow-plugin-viewer"]).toBe(
        "default",
      );
      const query = route.request().postDataJSON() as PickerQuery;
      searches.push(query);
      expect(query.pageSize).toBe(12);
      const expectedKeys = ["keyword", "kind", "pageNo", "pageSize"];
      if (query.kind === "VEHICLE") {
        expect(query).not.toHaveProperty("keywordMode");
      } else {
        expectedKeys.push("keywordMode");
      }
      expect(Object.keys(query).sort()).toEqual(expectedKeys.sort());
      if (handler) return handler(query, route);
      return route.fulfill({ json: pickerPage(query) });
    }
    return route.fulfill({ status: 404 });
  });
  return { searches, runs };
}

export const businessComposer = (page: Page) =>
  page.getByRole("textbox", { name: /how can i assist you/i });

export async function openBusinessPicker(page: Page, index = 0) {
  const opener = page.locator(".cvci-starter-card").nth(index);
  await opener.click();
  await expect(page.getByTestId("cvci-picker")).toBeVisible();
  return {
    opener,
    input: page.getByTestId("cvci-search-input"),
    dialog: page.getByRole("dialog"),
    results: page.getByTestId("cvci-result"),
    confirm: page.getByTestId("cvci-confirm"),
  };
}

export async function searchBusinessPicker(page: Page, keyword = "Synthetic") {
  await page.getByTestId("cvci-search-input").fill(keyword);
  await expect(page.getByTestId("cvci-result").first()).toBeVisible();
}

export function nativeRun(page: Page) {
  return page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().includes("/runs/stream"),
  );
}
