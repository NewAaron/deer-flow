import { afterEach, expect, test, rs } from "@rstest/core";

const original = process.env.NEXT_PUBLIC_APP_VERSION;

afterEach(() => {
  rs.resetModules();
  if (original === undefined) {
    delete process.env.NEXT_PUBLIC_APP_VERSION;
  } else {
    process.env.NEXT_PUBLIC_APP_VERSION = original;
  }
});

test("localized product information includes the configured app version", async () => {
  process.env.NEXT_PUBLIC_APP_VERSION = "9.9.9-test";
  const { enUS } = await import("@/core/i18n/locales/en-US");
  const { zhCN } = await import("@/core/i18n/locales/zh-CN");
  const { getAboutMarkdown } =
    await import("@/components/workspace/settings/about-content");
  const english = getAboutMarkdown(enUS);
  const chinese = getAboutMarkdown(zhCN);
  expect(english).toContain("# About MSAI");
  expect(english).toContain(enUS.workspaceBrand.description);
  expect(english).toContain("**Version**: 9.9.9-test");
  expect(chinese).toContain("# 关于 MSAI");
  expect(chinese).toContain(zhCN.workspaceBrand.description);
  expect(chinese).toContain("**版本**: 9.9.9-test");
  expect(english + chinese).not.toMatch(
    /deerflow|deer-flow|github|mailto:|🦌/i,
  );
});

test("product information reflects the package version when env is unset", async () => {
  delete process.env.NEXT_PUBLIC_APP_VERSION;
  const { APP_VERSION } = await import("@/version");
  const { enUS } = await import("@/core/i18n/locales/en-US");
  const { getAboutMarkdown } =
    await import("@/components/workspace/settings/about-content");
  expect(getAboutMarkdown(enUS)).toContain(`**Version**: ${APP_VERSION}`);
});
