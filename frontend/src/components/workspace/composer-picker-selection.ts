import type { ComposerPickerSelection } from "@/core/extensions/contracts";
import {
  extensionMentionId,
  MAX_EXTENSION_MENTIONS,
  parseExtensionMention,
} from "@/core/extensions/mentions";

import {
  extensionMentionMetadata,
  referenceToken,
} from "./mentions/inline-references";

/** A picker supplies data and user text, never an independent run request. */
export function appendComposerSelection(
  draft: string,
  selection: ComposerPickerSelection,
  namespace: string,
) {
  const reference = selection?.reference;
  if (
    reference?.namespace !== namespace ||
    !parseExtensionMention(extensionMentionId(reference), reference.label) ||
    typeof selection.text !== "string" ||
    !selection.text.trim() ||
    selection.text.length > 2000
  )
    throw new Error("Invalid picker selection");
  const token = referenceToken(
    "extension",
    extensionMentionId(reference),
    reference.label,
  );
  const next = `${draft}${draft ? "\n" : ""}${token} ${selection.text.trim()}`;
  if (
    (extensionMentionMetadata(next).extension_mentions?.length ?? 0) >
    MAX_EXTENSION_MENTIONS
  )
    throw new Error("Too many extension mentions");
  return next;
}
