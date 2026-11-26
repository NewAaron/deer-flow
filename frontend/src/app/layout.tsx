import "@/styles/globals.css";

import { type Metadata } from "next";

import { ThemeProvider } from "@/components/theme-provider";
import { ArtifactDraftsProvider } from "@/core/artifacts/draft-context";
import { DEFAULT_LOCALE } from "@/core/i18n/locale";

export const metadata: Metadata = {
  title: "MSAI",
  description: "MSAI 智能助手，为工作提供智能问答、数据分析与任务协作。",
  icons: {
    icon: [
      { url: "/favicon.ico?v=msai-20261126" },
      { url: "/cvci/emblem.png", type: "image/png" },
    ],
    shortcut: "/favicon.ico?v=msai-20261126",
    apple: "/cvci/emblem.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang={DEFAULT_LOCALE}
      suppressContentEditableWarning
      suppressHydrationWarning
    >
      <body>
        <ThemeProvider attribute="class" enableSystem disableTransitionOnChange>
          <ArtifactDraftsProvider>{children}</ArtifactDraftsProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
