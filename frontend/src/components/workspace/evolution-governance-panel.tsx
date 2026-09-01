import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ActivityIcon,
  ArrowRightIcon,
  GaugeIcon,
  RefreshCwIcon,
  Settings2Icon,
  ShieldCheckIcon,
} from "lucide-react";
import { lazy, Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getDualHelixShadowStatus,
  setDualHelixShadowEnabled,
} from "@/core/evolution/api";

const shadowQueryKey = ["evolution", "dual-helix", "shadow"] as const;

const EvolutionControlPanel = lazy(() =>
  import("./evolution-control-panel").then((module) => ({
    default: module.EvolutionControlPanel,
  })),
);
const EvolutionSettingsPage = lazy(
  () => import("./settings/evolution-settings-page"),
);
const ReflexMonitorContent = lazy(() =>
  import("@/app/workspace/reflex/page").then((module) => ({
    default: module.ReflexMonitorContent,
  })),
);

function GovernancePanelLoading() {
  return (
    <div
      className="h-72 animate-pulse rounded-xl border bg-muted/25"
      role="status"
      aria-label="加载治理模块"
    />
  );
}

export function EvolutionGovernancePanel() {
  const queryClient = useQueryClient();
  const [detailSection, setDetailSection] = useState("summary");
  const shadow = useQuery({
    queryKey: shadowQueryKey,
    queryFn: getDualHelixShadowStatus,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  const setShadow = useMutation({
    mutationFn: setDualHelixShadowEnabled,
    onSuccess: (value) => queryClient.setQueryData(shadowQueryKey, value),
  });
  const shadowLoadFailed = Boolean(shadow.error) && !shadow.data;

  return (
    <section className="space-y-4" aria-label="安全治理">
      <div className="grid gap-3 lg:grid-cols-3">
        <article className="rounded-xl border border-border bg-card p-4 lg:col-span-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 gap-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-success/10 text-success">
                <ShieldCheckIcon className="size-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold">影子复核保护</h2>
                <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                  仅在对话中手动点击 DNA
                  复核按钮时运行另一引擎；工作区使用隔离副本和只读权限。
                </p>
              </div>
            </div>
            <Button
              type="button"
              size="sm"
              variant={shadow.data?.enabled ? "secondary" : "outline"}
              disabled={setShadow.isPending || !shadow.data?.ok}
              onClick={() => setShadow.mutate(!shadow.data?.enabled)}
            >
              {shadowLoadFailed
                ? "状态不可用"
                : shadow.data?.enabled
                  ? "保护已开启"
                  : "开启保护"}
            </Button>
          </div>
          {shadowLoadFailed ? (
            <div
              role="alert"
              className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2"
            >
              <span className="text-[11px] text-destructive">
                保护状态暂时无法加载；现有设置没有改变。
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={shadow.isFetching}
                onClick={() => void shadow.refetch()}
              >
                <RefreshCwIcon
                  className={`mr-1.5 size-3.5 ${shadow.isFetching ? "animate-spin" : ""}`}
                />
                重试
              </Button>
            </div>
          ) : (
            <div className="mt-3 rounded-lg bg-muted/45 px-3 py-2 text-[11px] text-muted-foreground">
              {shadow.data?.enabled
                ? "已授权手动影子复核；开启状态本身不会调用模型。"
                : shadow.isLoading
                  ? "正在读取保护状态…"
                  : "当前关闭，不会触发另一引擎，也不会产生额外费用。"}
            </div>
          )}
          {setShadow.error ? (
            <p role="alert" className="mt-2 text-[11px] text-destructive">
              保护设置未保存，请稍后重试。
            </p>
          ) : null}
        </article>

        <article className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ActivityIcon className="size-4 text-primary" />
            治理边界
          </div>
          <div className="mt-3 space-y-2 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>影子执行</span>
              <span>手动触发</span>
            </div>
            <div className="flex justify-between">
              <span>工作区权限</span>
              <span>只读副本</span>
            </div>
            <div className="flex justify-between">
              <span>候选发布</span>
              <span>灰度后晋升</span>
            </div>
          </div>
        </article>
      </div>

      <Tabs
        value={detailSection}
        onValueChange={setDetailSection}
        className="space-y-3"
      >
        <TabsList className="h-9 max-w-full min-w-max rounded-lg">
          <TabsTrigger
            value="summary"
            className="h-8 shrink-0 px-2 text-xs sm:px-3"
          >
            治理摘要
          </TabsTrigger>
          <TabsTrigger
            value="control"
            className="h-8 shrink-0 gap-1 px-2 text-xs sm:gap-1.5 sm:px-3"
          >
            <GaugeIcon className="hidden size-3.5 sm:block" />
            策略与预算
          </TabsTrigger>
          <TabsTrigger
            value="reflex"
            className="h-8 shrink-0 gap-1 px-2 text-xs sm:gap-1.5 sm:px-3"
          >
            <ActivityIcon className="hidden size-3.5 sm:block" />
            规则与响应
          </TabsTrigger>
          <TabsTrigger
            value="runtime"
            className="h-8 shrink-0 gap-1 px-2 text-xs sm:gap-1.5 sm:px-3"
          >
            <Settings2Icon className="hidden size-3.5 sm:block" />
            运行与设置
          </TabsTrigger>
        </TabsList>
        <TabsContent value="summary" className="mt-0">
          <div className="grid gap-3 lg:grid-cols-3">
            {[
              {
                value: "control",
                title: "策略与预算",
                description: "预算、技能提案、模型与 MCP 的晋升策略。",
                Icon: GaugeIcon,
              },
              {
                value: "reflex",
                title: "规则与响应",
                description: "查看反射规则、运行反馈与异常响应。",
                Icon: ActivityIcon,
              },
              {
                value: "runtime",
                title: "运行与设置",
                description: "调整进化运行参数与治理开关。",
                Icon: Settings2Icon,
              },
            ].map(({ value, title, description, Icon }) => (
              <button
                key={value}
                type="button"
                onClick={() => setDetailSection(value)}
                className="group flex h-auto items-start justify-start rounded-xl border bg-card p-4 text-left transition-colors hover:bg-muted/30"
              >
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                  <Icon className="size-4" />
                </span>
                <span className="ml-3 min-w-0 flex-1 whitespace-normal">
                  <span className="block text-sm font-semibold text-foreground">
                    {title}
                  </span>
                  <span className="mt-1 block text-xs font-normal leading-5 text-muted-foreground">
                    {description}
                  </span>
                </span>
                <ArrowRightIcon className="ml-2 mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
              </button>
            ))}
          </div>
        </TabsContent>
        <TabsContent
          value="control"
          className="mt-0 rounded-xl border bg-card p-3"
        >
          <Suspense fallback={<GovernancePanelLoading />}>
            <EvolutionControlPanel />
          </Suspense>
        </TabsContent>
        <TabsContent value="reflex" className="mt-0">
          <Suspense fallback={<GovernancePanelLoading />}>
            <ReflexMonitorContent />
          </Suspense>
        </TabsContent>
        <TabsContent
          value="runtime"
          className="mt-0 rounded-xl border bg-card p-3"
        >
          <Suspense fallback={<GovernancePanelLoading />}>
            <EvolutionSettingsPage />
          </Suspense>
        </TabsContent>
      </Tabs>
    </section>
  );
}
