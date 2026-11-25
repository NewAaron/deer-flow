"use client";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

export default function CvciSignInFailed() {
  const { t } = useI18n();
  const copy = t.login.cvciFailure;

  return (
    <main className="bg-background flex min-h-screen items-center justify-center px-6">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <h1 className="text-xl font-semibold">{copy.title}</h1>
        <p className="text-muted-foreground">{copy.description}</p>
        <Button
          type="button"
          onClick={() =>
            window.location.assign(
              "/api/v1/auth/oauth/cvci?next=/workspace&remember_me=false",
            )
          }
        >
          {copy.retry}
        </Button>
      </div>
    </main>
  );
}
