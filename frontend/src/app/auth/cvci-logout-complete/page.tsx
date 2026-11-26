import { CvciLogoutComplete } from "@/components/auth/cvci-logout-complete";
import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import { detectLocaleServer } from "@/core/i18n/server";

export const dynamic = "force-dynamic";

export default async function LogoutCompletePage() {
  const locale = await detectLocaleServer();
  return (
    <CvciLogoutComplete
      copy={(locale === "zh-CN" ? zhCN : enUS).login.sharedLogout}
    />
  );
}
