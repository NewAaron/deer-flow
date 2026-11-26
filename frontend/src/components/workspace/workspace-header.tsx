"use client";

import { MessageSquarePlus, SearchIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { WorkspaceBrand } from "./workspace-brand";
import { useWorkspaceSidebarExpanded } from "./workspace-sidebar-frame";
import { WorkspaceSidebarTrigger } from "./workspace-sidebar-trigger";

export function WorkspaceHeader({ className }: { className?: string }) {
  const { t } = useI18n();
  const { isMobile } = useSidebar();
  const isSidebarOpen = useWorkspaceSidebarExpanded();
  const pathname = usePathname();
  return (
    <>
      <div
        className={cn(
          "cvci-sidebar-header group/workspace-header flex flex-col justify-center",
          className,
        )}
      >
        {!isSidebarOpen ? (
          <div className="group-has-data-[collapsible=icon]/sidebar-wrapper:-translate-y flex w-full cursor-pointer items-center justify-center">
            <div
              className={cn(
                "text-primary block pt-1 font-serif group-hover/workspace-header:hidden",
                isMobile && "hidden",
              )}
            >
              <WorkspaceBrand compact />
            </div>
            <WorkspaceSidebarTrigger
              className={cn(
                "pl-2",
                !isMobile && "hidden group-hover/workspace-header:block",
              )}
            />
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2">
            {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
              <Link href="/" className="min-w-0">
                <WorkspaceBrand />
              </Link>
            ) : (
              <div className="min-w-0 cursor-default">
                <WorkspaceBrand />
              </div>
            )}
            <WorkspaceSidebarTrigger />
          </div>
        )}
      </div>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            className="cvci-new-chat"
            isActive={pathname === "/workspace/chats/new"}
            asChild
          >
            <Link href="/workspace/chats/new">
              <MessageSquarePlus size={16} />
              <span>{t.sidebar.newChat}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            asChild
            className="cvci-search-chats"
            tooltip={t.workspaceBrand.searchChats}
          >
            <Link href="/workspace/chats#chat-search">
              <SearchIcon size={16} />
              <span>{t.workspaceBrand.searchChats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
