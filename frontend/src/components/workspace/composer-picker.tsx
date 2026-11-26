"use client";

import { useRef, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { ComposerPickerSelection } from "@/core/extensions/contracts";
import { useFrontendExtensions } from "@/core/extensions/hooks";
import { activeFrontendExtensions } from "@/core/extensions/registry";
import { useI18n } from "@/core/i18n/hooks";
import { useIsMobile } from "@/hooks/use-mobile";

import { deepActiveElement, pickerTabStops } from "./composer-picker-focus";
import { PluginSurfaceView } from "./plugin-surfaces";

export type ComposerPickerTarget = {
  namespace: string;
  surfaceId: string;
  title: string;
  draftKey: string;
  opener: HTMLElement | null;
};

/** The host owns modality, composer lifetime and native submission. */
export function ComposerPicker({
  target,
  threadId,
  onClose,
  onConfirm,
  focusComposer,
}: {
  target: ComposerPickerTarget;
  threadId?: string;
  onClose: () => void;
  onConfirm: (selection: ComposerPickerSelection) => Promise<void>;
  focusComposer: () => void;
}) {
  const query = useFrontendExtensions();
  const { t, locale } = useI18n();
  const mobile = useIsMobile();
  const confirmed = useRef(false);
  const container = useRef<HTMLDivElement>(null);
  const match = activeFrontendExtensions(query.data ?? [])
    .filter(({ contribution }) => contribution.namespace === target.namespace)
    .flatMap(({ contribution, extension }) =>
      (extension.surfaces ?? [])
        .filter(
          (surface) =>
            surface.slot === "composer-picker" &&
            surface.id === target.surfaceId,
        )
        .map((surface) => ({ entry: contribution, surface })),
    )[0];
  const description = locale.startsWith("zh")
    ? "搜索并选择一个对象，确认后通过对话查询。"
    : "Search and select a record, then query it in the conversation.";
  const content = query.isPending ? (
    <p role="status">{t.extensions.pageLoading}</p>
  ) : match ? (
    <PluginSurfaceView
      entry={match.entry}
      surface={match.surface}
      threadId={threadId}
      confirmSelection={async (selection) => {
        confirmed.current = true;
        try {
          await onConfirm(selection);
        } catch (error) {
          confirmed.current = false;
          throw error;
        }
      }}
    />
  ) : (
    <div className="space-y-4">
      <p role="alert">{t.extensions.pageUnavailableHint}</p>
      <Button variant="outline" onClick={() => void query.refetch()}>
        {t.inputBox.mentionRetry}
      </Button>
    </div>
  );
  const restoreFocus = (event: Event) => {
    event.preventDefault();
    if (confirmed.current) focusComposer();
    else if (target.opener?.isConnected) target.opener.focus();
  };
  const handleTab = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      event.key !== "Tab" ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      !container.current
    )
      return;
    const stops = pickerTabStops(container.current);
    if (!stops.length) return;
    const index = stops.findIndex((element) => element === deepActiveElement());
    const next =
      index < 0
        ? event.shiftKey
          ? stops.length - 1
          : 0
        : (index + (event.shiftKey ? -1 : 1) + stops.length) % stops.length;
    event.preventDefault();
    stops[next]!.focus();
  };
  const focusInitial = (event: Event) => {
    event.preventDefault();
    const first = container.current && pickerTabStops(container.current)[0];
    first?.focus();
  };
  if (mobile)
    return (
      <Sheet open onOpenChange={(open) => !open && onClose()}>
        <SheetContent
          ref={container}
          side="bottom"
          className="max-h-[92dvh] overflow-y-auto rounded-t-3xl p-5"
          onCloseAutoFocus={restoreFocus}
          onOpenAutoFocus={focusInitial}
          onKeyDownCapture={handleTab}
        >
          <SheetHeader className="p-0 pr-8">
            <SheetTitle>{target.title}</SheetTitle>
            <SheetDescription>{description}</SheetDescription>
          </SheetHeader>
          {content}
        </SheetContent>
      </Sheet>
    );
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        ref={container}
        className="max-h-[88dvh] overflow-y-auto rounded-3xl sm:max-w-2xl"
        onCloseAutoFocus={restoreFocus}
        onOpenAutoFocus={focusInitial}
        onKeyDownCapture={handleTab}
      >
        <DialogHeader>
          <DialogTitle>{target.title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {content}
      </DialogContent>
    </Dialog>
  );
}
