import { GlobeIcon } from "lucide-react";
import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

import { EchoMark } from "@/components/brand/echo-mark";
import { useI18n } from "@/core/i18n/hooks";
import {
  BROWSER_WORKSPACE_ROUTE,
  workspaceAgentReturnRoute,
} from "@/core/workspace/sidebar-routing";
import { cn } from "@/lib/utils";

type WorkspaceSurfaceMode = "agent" | "browser";

export const LAST_AGENT_WORKSPACE_ROUTE_KEY = "echo:last-agent-workspace-route";

export function WorkspaceSurfaceSwitch({
  active,
}: {
  active: WorkspaceSurfaceMode;
}) {
  const { t } = useI18n();
  const location = useLocation();
  let rememberedAgentRoute: string | null = null;
  try {
    if (typeof window !== "undefined") {
      rememberedAgentRoute = window.sessionStorage.getItem(
        LAST_AGENT_WORKSPACE_ROUTE_KEY,
      );
    }
  } catch {
    // Storage may be disabled by the host; the primary route remains usable.
  }
  const agentReturnRoute = workspaceAgentReturnRoute(
    location.pathname,
    location.search,
    rememberedAgentRoute,
  );

  useEffect(() => {
    if (active !== "agent") return;
    try {
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem(
          LAST_AGENT_WORKSPACE_ROUTE_KEY,
          agentReturnRoute,
        );
      }
    } catch {
      // A privacy-restricted host can still use the switch's default route.
    }
  }, [active, agentReturnRoute]);

  const items = [
    {
      to: agentReturnRoute,
      label: t.desktop.header.brand,
      icon: EchoMark,
      value: "agent" as const,
      kind: "brand" as const,
    },
    {
      to: BROWSER_WORKSPACE_ROUTE,
      label: t.sidebar.navBrowserSurface,
      icon: GlobeIcon,
      value: "browser" as const,
      kind: "icon" as const,
    },
  ];
  const activeIndex = items.findIndex((item) => item.value === active);
  const radiusVar = "var(--appearance-radius-control)";

  return (
    <div
      className={cn(
        "relative grid h-8 items-center gap-0 p-0.5",
        "w-[96px] grid-cols-[minmax(0,1fr)_28px]",
        "border border-border-default bg-muted/40",
        "group-data-[collapsible=icon]:hidden",
      )}
      style={{ borderRadius: radiusVar }}
      role="tablist"
      aria-label="Workspace surface"
    >
      <span
        className={cn(
          "absolute top-0.5 bottom-0.5 z-0 translate-y-px",
          "border bg-background shadow-[var(--shadow-xs)]",
          "transition-[left,width]",
          activeIndex === 0
            ? "left-[2px] w-[calc(100%-32px)]"
            : "left-[calc(100%-28px)] w-[24px]",
        )}
        style={{ borderRadius: `calc(${radiusVar} - 2px)` }}
        aria-hidden="true"
      />
      {items.map((item, index) => {
        const Icon = item.icon;
        const isActive = index === activeIndex;
        return (
          <Link
            key={item.to}
            to={item.to}
            aria-current={isActive ? "page" : undefined}
            aria-label={item.label}
            title={item.label}
            role="tab"
            aria-selected={isActive}
            className={cn(
              "relative z-10 flex h-7 items-center justify-center",
              "text-xs font-medium",
              "transition-colors",
              isActive
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
              item.kind === "brand" ? "px-0.5" : "px-0",
              "group-data-[collapsible=icon]:grid group-data-[collapsible=icon]:size-7 group-data-[collapsible=icon]:place-items-center group-data-[collapsible=icon]:px-0",
              isActive
                ? "group-data-[collapsible=icon]:flex"
                : "group-data-[collapsible=icon]:hidden",
            )}
            style={{ borderRadius: `calc(${radiusVar} - 2px)` }}
          >
            <Icon
              className={cn(
                "size-3.5 shrink-0",
                item.kind === "brand" &&
                  "hidden group-data-[collapsible=icon]:block",
              )}
            />
            {item.kind === "brand" && (
              <span className="min-w-0 truncate group-data-[collapsible=icon]:sr-only">
                {item.label}
              </span>
            )}
          </Link>
        );
      })}
    </div>
  );
}
