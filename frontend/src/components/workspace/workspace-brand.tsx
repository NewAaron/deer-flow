"use client";

import { PanelsTopLeftIcon } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function WorkspaceBrand({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  return (
    <div className={cn("cvci-brand", compact && "cvci-brand-compact")}>
      <Image
        src="/cvci/emblem.png"
        width={64}
        height={64}
        alt=""
        unoptimized
        className="cvci-brand-emblem"
      />
      {!compact && (
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            {t.workspaceBrand.name}
          </div>
          <div className="cvci-brand-wordmark">MSAI</div>
        </div>
      )}
    </div>
  );
}

export function WorkspaceBrandHeader() {
  const { t } = useI18n();
  return (
    <header className="cvci-workspace-header">
      <div className="min-w-0">
        <div className="truncate text-base font-semibold sm:text-xl">
          {t.workspaceBrand.title}
        </div>
        <div className="text-muted-foreground mt-0.5 truncate text-xs sm:text-sm">
          {t.workspaceBrand.subtitle}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <Button asChild variant="outline" className="cvci-workspace-link">
          <Link href="/workspace/chats">
            <PanelsTopLeftIcon className="size-4" />
            <span className="hidden sm:inline">
              {t.workspaceBrand.workspace}
            </span>
            <span className="sr-only sm:hidden">
              {t.workspaceBrand.workspace}
            </span>
          </Link>
        </Button>
        <span className="cvci-powered-badge hidden sm:inline-flex">MSAI</span>
      </div>
    </header>
  );
}
