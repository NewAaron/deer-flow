"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getCsrfHeaders } from "@/core/api/fetcher";
import { enUS } from "@/core/i18n/locales/en-US";
import type { Translations } from "@/core/i18n/locales/types";

export function CvciLogoutComplete({
  copy = enUS.login.sharedLogout,
}: {
  copy?: Translations["login"]["sharedLogout"];
}) {
  const [failed, setFailed] = useState(false);
  const active = useRef<AbortController | null>(null);
  const cleanup = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setFailed(false);
    try {
      const response = await fetch("/api/v1/auth/logout/cleanup", {
        method: "POST",
        credentials: "include",
        headers: getCsrfHeaders(),
        signal: controller.signal,
      });
      const result: unknown = response.ok ? await response.json() : null;
      if (controller.signal.aborted) return;
      if (
        !result ||
        typeof result !== "object" ||
        !("status" in result) ||
        !["cleared", "unchanged"].includes(String(result.status))
      )
        throw new Error("Unconfirmed cleanup");
      window.location.replace("/auth/cvci-logout");
    } catch {
      if (!controller.signal.aborted) setFailed(true);
    }
  }, []);
  useEffect(() => {
    void cleanup();
    return () => active.current?.abort();
  }, [cleanup]);
  return (
    <main className="bg-background text-foreground flex min-h-screen flex-col items-center justify-center gap-4 p-6">
      <p role={failed ? "alert" : "status"}>
        {failed ? copy.cleanupFailed : copy.pending}
      </p>
      {failed && (
        <button
          type="button"
          className="text-primary underline"
          onClick={() => void cleanup()}
        >
          {copy.retry}
        </button>
      )}
    </main>
  );
}
