import { afterEach, expect, test } from "@rstest/core";

import { GET } from "@/app/auth/cvci-logout/route";

const originalOrigin = process.env.CVCI_LOGIN_ORIGIN;
afterEach(() => {
  if (originalOrigin === undefined) delete process.env.CVCI_LOGIN_ORIGIN;
  else process.env.CVCI_LOGIN_ORIGIN = originalOrigin;
});

function checkHeaders(response: Response) {
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
}

test.each([
  ["127.0.0.1:2026", "http://127.0.0.1:18080/logout/complete"],
  ["localhost:2026", "http://localhost:18080/logout/complete"],
])(
  "uses actual browser Host %s when Next's internal URL is localhost",
  (host, expected) => {
    process.env.CVCI_LOGIN_ORIGIN = "http://localhost:18080";
    const response = GET(
      new Request("http://localhost:3000/auth/cvci-logout", {
        headers: { host, "x-forwarded-host": "evil.example" },
      }),
    );
    expect(response.headers.get("Location")).toBe(expected);
    checkHeaders(response);
  },
);

test("invalid Host and forwarded headers cannot override a trusted target", () => {
  process.env.CVCI_LOGIN_ORIGIN = "http://127.0.0.1:18080";
  const response = GET(
    new Request("http://localhost:3000/auth/cvci-logout", {
      headers: { host: "evil.example", "x-forwarded-host": "localhost:2026" },
    }),
  );
  expect(response.headers.get("Location")).toBe(
    "http://127.0.0.1:18080/logout/complete",
  );
  checkHeaders(response);
});

test("reads trusted configuration at request time and ignores all client targets", async () => {
  process.env.CVCI_LOGIN_ORIGIN = "http://127.0.0.1:18080";
  const first = GET(
    new Request(
      "http://localhost:2026/auth/cvci-logout?next=https://evil.example&tx=secret&assistant=1",
    ),
  );
  expect(first.status).toBe(302);
  expect(first.headers.get("Location")).toBe(
    "http://localhost:18080/logout/complete",
  );
  checkHeaders(first);

  process.env.CVCI_LOGIN_ORIGIN = "https://cvci.example.com";
  const second = GET(new Request("http://127.0.0.1:2026/auth/cvci-logout"));
  expect(second.headers.get("Location")).toBe(
    "https://cvci.example.com/logout/complete",
  );
  checkHeaders(second);
});

test.each([
  undefined,
  "https://user:secret@cvci.example.com/unsafe?private=value",
])(
  "unavailable configuration gives a safe fixed retry page",
  async (origin) => {
    if (origin === undefined) delete process.env.CVCI_LOGIN_ORIGIN;
    else process.env.CVCI_LOGIN_ORIGIN = origin;
    const response = GET(
      new Request(
        "http://localhost:2026/auth/cvci-logout?next=private-request&tx=secret",
      ),
    );
    expect(response.status).toBe(503);
    expect(response.headers.get("Location")).toBeNull();
    checkHeaders(response);
    const body = await response.text();
    expect(body).toContain(
      "暂时无法打开CVCI登录页，请重试或联系管理员检查登录入口。",
    );
    expect(body).toContain('href="/auth/cvci-logout"');
    for (const sensitive of [
      "CVCI_LOGIN_ORIGIN",
      "secret",
      "private",
      "cvci.example.com",
      "assistant=",
      "oauth",
    ]) {
      expect(body).not.toContain(sensitive);
    }
  },
);
