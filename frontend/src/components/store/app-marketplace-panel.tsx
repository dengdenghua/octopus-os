import { useState } from "react";
import { Library, LayoutGrid, Network, Sparkles } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import { CapabilityMarketPanel } from "./capability-market-panel";
import { UnifiedAssetsPanel } from "./unified-assets-panel";
import { A2AAgentsPanel } from "@/components/workspace/a2a-agents-panel";

export type AppMarketplaceView =
  | "featured"
  | "all"
  | "codex"
  | "library"
  | "remote";

export interface AppMarketplacePanelProps {
  searchQuery?: string;
  /** 传入时由上层控制当前分区。 */
  view?: AppMarketplaceView;
  /** 非受控模式的初始分区。 */
  defaultView?: AppMarketplaceView;
  onViewChange?: (view: AppMarketplaceView) => void;
  /** 在 HUB 的扁平入口中隐藏应用内部的二级导航。 */
  hideNavigation?: boolean;
  className?: string;
}

export const DEFAULT_FEATURED_APP_IDS = [
  "opencode-zen",
  "browser",
  "documents",
  "spreadsheets",
  "presentations",
  "pdf",
  "visualize",
] as const;

const VIEW_OPTIONS: ReadonlyArray<{
  value: AppMarketplaceView;
  label: string;
  icon: typeof Sparkles;
}> = [
  { value: "featured", label: "精选", icon: Sparkles },
  { value: "all", label: "全部应用", icon: LayoutGrid },
  { value: "codex", label: "Codex 插件", icon: Sparkles },
  { value: "library", label: "我的库", icon: Library },
  { value: "remote", label: "远程Agent", icon: Network },
];

export function AppMarketplacePanel({
  searchQuery = "",
  view,
  defaultView = "featured",
  onViewChange,
  hideNavigation = false,
  className,
}: AppMarketplacePanelProps) {
  const [internalView, setInternalView] =
    useState<AppMarketplaceView>(defaultView);
  const activeView = view ?? internalView;

  const selectView = (nextView: AppMarketplaceView) => {
    if (view === undefined) setInternalView(nextView);
    onViewChange?.(nextView);
  };

  return (
    <Tabs
      value={activeView}
      onValueChange={(nextView) => selectView(nextView as AppMarketplaceView)}
      aria-label="应用市场"
      className={cn("gap-4", className)}
    >
      {!hideNavigation && (
        <div className="flex flex-col gap-3 border-b border-border-subtle pb-3 sm:flex-row sm:items-end sm:justify-between">
          <p className="max-w-xl text-sm leading-6 text-muted-foreground">
            安装一个应用，为团队补充一组可直接使用的能力。
          </p>
          <TabsList
            aria-label="应用市场分区"
            className="flex w-fit items-center gap-1 rounded-lg bg-muted/60 p-1"
          >
            {VIEW_OPTIONS.map((option) => {
              const Icon = option.icon;
              return (
                <TabsTrigger
                  key={option.value}
                  value={option.value}
                  className="h-8 gap-1.5 px-2.5 text-xs"
                >
                  <Icon className="size-3.5" />
                  {option.label}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </div>
      )}

      <TabsContent value="featured">
        <CapabilityMarketPanel
          searchQuery={searchQuery}
          view="featured"
          featuredIds={DEFAULT_FEATURED_APP_IDS}
          maxItems={7}
          showToolbar={false}
        />
      </TabsContent>
      <TabsContent value="all">
        <CapabilityMarketPanel
          searchQuery={searchQuery}
          view="all"
          featuredIds={DEFAULT_FEATURED_APP_IDS}
          showToolbar={false}
        />
      </TabsContent>
      <TabsContent value="codex">
        <CapabilityMarketPanel
          searchQuery={searchQuery}
          view="all"
          source="codex_plugin"
          showToolbar={false}
        />
      </TabsContent>
      <TabsContent value="library">
        <UnifiedAssetsPanel
          searchQuery={searchQuery}
          allowedKinds={["plugin", "skill"]}
          showSyncAction={false}
        />
      </TabsContent>
      <TabsContent value="remote" className="mt-0">
        <A2AAgentsPanel />
      </TabsContent>
    </Tabs>
  );
}
