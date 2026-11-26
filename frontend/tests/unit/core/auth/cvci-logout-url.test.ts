import { expect, test } from "@rstest/core";

import { buildCvciLogoutUrl } from "@/core/auth/cvci-logout-url";

test.each([
  ["127.0.0.1:2026", "127.0.0.1"],
  ["localhost:2026", "localhost"],
  ["127.0.0.1", "127.0.0.1"],
  ["LOCALHOST:2026", "localhost"],
])(
  "prefers a strict browser Host %s over Next's internal URL",
  (host, expected) => {
    expect(
      buildCvciLogoutUrl(
        "http://localhost:18080",
        "http://localhost:3000/auth/cvci-logout",
        host,
      ),
    ).toBe(`http://${expected}:18080/logout/complete`);
  },
);

test.each([
  "evil.example",
  "127.0.0.1.evil.example:2026",
  "127.1:2026",
  "2130706433:2026",
  "user@localhost:2026",
  "localhost:2026/path",
  "localhost:2026?next=evil",
  "localhost:2026#fragment",
  "localhost:2026,evil.example",
  "localhost:",
  "localhost:0",
  "localhost:65536",
  "localhost:abc",
  " localhost:2026",
  "localhost:2026\n",
  "",
])("invalid Host %s cannot override the configured origin", (host) => {
  expect(
    buildCvciLogoutUrl(
      "http://127.0.0.1:18080",
      "http://localhost:3000/auth/cvci-logout",
      host,
    ),
  ).toBe("http://127.0.0.1:18080/logout/complete");
});

test("a local Host cannot change a configured remote HTTPS origin", () => {
  expect(
    buildCvciLogoutUrl(
      "https://cvci.example.com:8443",
      "http://localhost:3000/auth/cvci-logout",
      "127.0.0.1:2026",
    ),
  ).toBe("https://cvci.example.com:8443/logout/complete");
});

test.each([
  ["https://cvci.example.com", "https://cvci.example.com/logout/complete"],
  ["https://cvci.example.com/", "https://cvci.example.com/logout/complete"],
  [
    "https://cvci.example.com:8443",
    "https://cvci.example.com:8443/logout/complete",
  ],
  ["http://localhost:18080", "http://localhost:18080/logout/complete"],
  ["http://127.0.0.1:18080/", "http://127.0.0.1:18080/logout/complete"],
  ["http://localhost:80", "http://localhost/logout/complete"],
])("accepts a trusted pure origin %s", (origin, expected) => {
  expect(
    buildCvciLogoutUrl(origin, "https://deerflow.example.com/auth/cvci-logout"),
  ).toBe(expected);
});

test.each([
  undefined,
  "",
  " https://cvci.example.com",
  "https://cvci.example.com\n",
  "//cvci.example.com",
  "javascript:alert(1)",
  "http://cvci.example.com:18080",
  "http://localhost",
  "http://127.0.0.1",
  "http://127.1:18080",
  "http://2130706433:18080",
  "http://localhost.evil.com:18080",
  "https://user:secret@cvci.example.com",
  "https://@cvci.example.com",
  "https://cvci.example.com/login",
  "https://cvci.example.com/x/..",
  "https://cvci.example.com?",
  "https://cvci.example.com#",
  "https://cvci.example.com/?next=evil",
  "https://cvci.example.com/#fragment",
  "https:\\cvci.example.com",
  "http://localhost:0",
  "http://localhost:65536",
])("rejects an untrusted or non-origin setting %s", (origin) => {
  expect(
    buildCvciLogoutUrl(origin, "http://localhost:2026/auth/cvci-logout"),
  ).toBeNull();
});

test.each([
  ["http://127.0.0.1:18080", "http://localhost:2026", "http://localhost:18080"],
  ["http://localhost:18080", "http://127.0.0.1:2026", "http://127.0.0.1:18080"],
  [
    "https://localhost:18443",
    "http://127.0.0.1:2026",
    "https://127.0.0.1:18443",
  ],
  [
    "https://cvci.example.com",
    "http://localhost:2026",
    "https://cvci.example.com",
  ],
  [
    "http://127.0.0.1:18080",
    "https://untrusted.example.com",
    "http://127.0.0.1:18080",
  ],
])("aligns only explicit loopback hosts", (origin, requestOrigin, expected) => {
  expect(
    buildCvciLogoutUrl(
      origin,
      `${requestOrigin}/auth/cvci-logout?next=https://evil.example&tx=secret&assistant=1`,
    ),
  ).toBe(`${expected}/logout/complete`);
});
