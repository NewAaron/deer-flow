import { describe, expect, it } from "@rstest/core";

import { appendStarterPrompt } from "@/components/workspace/starter-prompt";

describe("starter prompt draft insertion", () => {
  it("selects the existing recognized template token in an empty draft", () => {
    expect(appendStarterPrompt("", "查询车辆[主题]的档案。")).toEqual({
      text: "查询车辆[主题]的档案。",
      start: 4,
      end: 8,
    });
  });

  it("preserves whitespace and reference tokens and targets the appended placeholder", () => {
    const draft = "  Keep @[research](ref:skill:research) and [topic]  ";
    const template = "Find records for [topic].";
    const result = appendStarterPrompt(draft, template);
    expect(result.text).toBe(`${draft}\n${template}`);
    expect(result.start).toBe(result.text.lastIndexOf("[topic]"));
    expect(result.text.slice(result.start, result.end)).toBe("[topic]");
  });

  it("reuses an existing final newline and puts the caret at the end without a placeholder", () => {
    expect(appendStarterPrompt("Keep this.\n", "Help me")).toEqual({
      text: "Keep this.\nHelp me",
      start: 18,
      end: 18,
    });
  });
});
