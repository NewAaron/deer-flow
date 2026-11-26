import { describe, expect, it } from "@rstest/core";

import { appendComposerSelection } from "@/components/workspace/composer-picker-selection";
import {
  extensionMentionMetadata,
  readableReferences,
  referenceToken,
} from "@/components/workspace/mentions/inline-references";
import { extensionMentionId } from "@/core/extensions/mentions";

const reference = {
  namespace: "cvci-entities",
  provider: "vehicle",
  id: "fa4c0fc8-ef8c-4adc-89ae-08baf7821a83",
  label: "浙GA7666",
};
const selection = { reference, text: "帮我查询这辆车的档案。" };

describe("native composer picker selection", () => {
  it("preserves the raw draft and its original reference, while adding a stable entity", () => {
    const draft = `原稿  ${referenceToken("skill", "research", "研究")}  `;
    const next = appendComposerSelection(draft, selection, "cvci-entities");
    expect(next.startsWith(`${draft}\n`)).toBe(true);
    expect(readableReferences(next)).toContain("浙GA7666");
    expect(extensionMentionMetadata(next).extension_mentions).toEqual([
      reference,
    ]);
  });
  it("does not accept another plugin's references or empty request text", () => {
    expect(() => appendComposerSelection("", selection, "other")).toThrow();
    expect(() =>
      appendComposerSelection("", { ...selection, text: " " }, "cvci-entities"),
    ).toThrow();
  });
  it("respects the native reference limit before touching the draft", () => {
    const draft = Array.from({ length: 16 }, (_, i) =>
      referenceToken(
        "extension",
        extensionMentionId({ ...reference, id: String(i) }),
        `车辆${i}`,
      ),
    ).join(" ");
    expect(() =>
      appendComposerSelection(draft, selection, "cvci-entities"),
    ).toThrow("Too many extension mentions");
  });
});
