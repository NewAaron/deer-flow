import type { Translations } from "@/core/i18n/locales/types";
import { APP_VERSION } from "@/version";

/** Localized product information, without deployment-specific support links. */
export function getAboutMarkdown(t: Translations) {
  return `# ${t.workspace.about}

${t.workspaceBrand.description}

**${t.common.version}**: ${APP_VERSION}
`;
}
