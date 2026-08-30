import { BotIcon, BriefcaseIcon, GlobeIcon } from "lucide-react";

import { resolveAgentAppUrl } from "@/appliance/agent-workspace";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type BrowserSurface = "agent" | "work" | "browser";

export function BrowserSurfaceSwitch({ active }: { active: BrowserSurface }) {
  const { t } = useI18n();
  const items = [
    {
      href: resolveAgentAppUrl("/workspace/projects"),
      label: t.sidebar.navCompany,
      icon: BriefcaseIcon,
      active: active === "work",
      brand: false,
    },
    {
      href: resolveAgentAppUrl("/workspace/realtime/new"),
      label: "Echo",
      icon: BotIcon,
      active: active === "agent",
      brand: true,
    },
    {
      href: "/#/browser",
      label: "浏览器",
      icon: GlobeIcon,
      active: active === "browser",
      brand: false,
    },
  ];

  return (
    <div className="grid w-[150px] min-w-0 -translate-x-3 grid-cols-[30px_minmax(0,1fr)_30px] items-center gap-0.5 rounded-[14px] border border-border/45 bg-background/72 p-px shadow-[0_1px_2px_rgba(15,23,42,0.04),inset_0_1px_0_rgba(255,255,255,0.42)]">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <a
            key={item.href}
            href={item.href}
            aria-current={item.active ? "page" : undefined}
            aria-label={item.label}
            className={cn(
              "flex h-7 min-w-0 items-center justify-center rounded-xl transition-[background-color,color,box-shadow,opacity] duration-150",
              item.active
                ? "bg-background text-foreground shadow-[0_1px_2px_rgba(15,23,42,0.055)] ring-1 ring-border/50"
                : "text-muted-foreground hover:bg-background/55 hover:text-foreground",
              item.brand ? "px-1 text-[12px] font-bold" : "px-0",
            )}
          >
            {item.brand ? (
              <span className="min-w-0 truncate">{item.label}</span>
            ) : (
              <Icon className="size-3.5 shrink-0" />
            )}
          </a>
        );
      })}
    </div>
  );
}
