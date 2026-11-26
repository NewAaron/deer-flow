import { findSuggestionTemplatePlaceholder } from "@/core/suggestions/placeholders";

/** Preserve the draft verbatim and select the placeholder in the newly appended template. */
export function appendStarterPrompt(draft: string, template: string) {
  const separator = draft && !draft.endsWith("\n") ? "\n" : "";
  const prefix = draft + separator;
  const placeholder = findSuggestionTemplatePlaceholder(template);
  return {
    text: prefix + template,
    start: prefix.length + (placeholder?.start ?? template.length),
    end: prefix.length + (placeholder?.end ?? template.length),
  };
}
