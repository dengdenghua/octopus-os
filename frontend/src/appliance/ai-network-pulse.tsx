import { useState } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ActivityIcon,
  BotIcon,
  CheckIcon,
  CpuIcon,
  GlobeIcon,
  RadioIcon,
  ShieldCheckIcon,
  SparklesIcon,
  ZapIcon,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

export type RoutingPolicy =
  | "local-first"
  | "auto"
  | "quality-first"
  | "cost-first"
  | "privacy-first";

export type ModelOption = {
  id: string;
  name: string;
  provider: string;
  tag?: string;
};

export const AVAILABLE_MODELS: ModelOption[] = [
  { id: "auto", name: "自动智能路由", provider: "Echo Routing", tag: "智能自适应" },
  { id: "claude-3-5-sonnet", name: "Claude 3.5 Sonnet", provider: "Anthropic", tag: "架构/代码" },
  { id: "gpt-4o", name: "GPT-4o", provider: "OpenAI", tag: "全模态旗舰" },
  { id: "deepseek-v3", name: "DeepSeek-V3", provider: "DeepSeek", tag: "深度推理" },
  { id: "gemini-1-5-pro", name: "Gemini 1.5 Pro", provider: "Google", tag: "超长上下文" },
  { id: "echo-local-npu", name: "Echo Local NPU", provider: "Station (私有)", tag: "100% 私有免流" },
];

export interface AiNetworkPulseProps {
  onOpenWorkbench?: () => void;
  onOpenSettings?: () => void;
  onSelectModel?: (modelId: string) => void;
  selectedModel?: string;
  defaultOpen?: boolean;
  className?: string;
}

export function AiNetworkPulse({
  onOpenWorkbench,
  onOpenSettings,
  onSelectModel,
  selectedModel = "auto",
  defaultOpen = false,
  className,
}: AiNetworkPulseProps) {
  const [policy, setPolicy] = useState<RoutingPolicy>("local-first");
  const [activeModel, setActiveModel] = useState(selectedModel);
  const [open, setOpen] = useState(defaultOpen);

  const policyLabels: Record<RoutingPolicy, { name: string; desc: string }> = {
    "local-first": {
      name: "本地优先",
      desc: "敏感数据零出网，仅在本地算力受限时申请云端",
    },
    auto: {
      name: "智能自适应",
      desc: "根据任务复杂度、时延与配额动态平衡",
    },
    "quality-first": {
      name: "极致品质",
      desc: "无视成本优先调度最顶尖旗舰大模型",
    },
    "cost-first": {
      name: "成本压制",
      desc: "优先采用本地与经济型模型，控制月度预算",
    },
    "privacy-first": {
      name: "极致隐私",
      desc: "物理断网隔离，严禁任何数据上传云端",
    },
  };

  const handleSelectPolicy = (newPolicy: RoutingPolicy) => {
    setPolicy(newPolicy);
    toast.success(`调度原则已切换为：${policyLabels[newPolicy].name}`, {
      description: policyLabels[newPolicy].desc,
    });
  };

  const fallbackModel: ModelOption = AVAILABLE_MODELS[0]!;
  const activeModelObj: ModelOption =
    AVAILABLE_MODELS.find((m) => m.id === activeModel) ?? fallbackModel;

  const handleSelectModel = (model: ModelOption) => {
    setActiveModel(model.id);
    onSelectModel?.(model.id);
    toast.success(`系统主模型已切换为：${model.name}`, {
      description: `算力供应：${model.provider} · 特性：${model.tag || ""}`,
    });
  };

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className={cn(
            "group inline-flex h-6 items-center gap-1.5 rounded-full border border-black/10 bg-white/60 px-2.5 text-[11px] font-medium text-slate-700 shadow-sm backdrop-blur-md transition-all hover:scale-[1.02] hover:bg-white/80 dark:border-white/15 dark:bg-white/10 dark:text-slate-200 dark:hover:bg-white/20",
            open &&
              "scale-[1.02] bg-white/90 ring-1 ring-black/15 dark:bg-white/20 dark:ring-white/30",
            className
          )}
          aria-label="打开 AI Network 实时脉搏"
        >
          <span className="relative flex size-2 items-center justify-center">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex size-1.5 rounded-full bg-emerald-500" />
          </span>
          <span className="font-semibold tracking-tight">AI Network</span>
          <span className="opacity-40">·</span>
          <span className="text-[10px] text-slate-500 dark:text-slate-400">
            {activeModelObj.id === "auto"
              ? "32% Local"
              : activeModelObj.name.split(" ")[0]}
          </span>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        sideOffset={8}
        className="w-[420px] rounded-2xl border border-black/10 bg-white/90 p-4 text-slate-800 shadow-2xl shadow-black/20 backdrop-blur-2xl dark:border-white/15 dark:bg-slate-900/90 dark:text-slate-100"
      >
        {/* 顶部标题与状态 */}
        <div className="flex items-center justify-between border-b border-black/5 pb-3 dark:border-white/10">
          <div className="flex items-center gap-2">
            <RadioIcon className="size-4 text-emerald-500" />
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider">
                ECHO AI NETWORK
              </h3>
              <p className="text-[10px] text-slate-500 dark:text-slate-400">
                全局算力宽带、资费套餐与智能路由中枢
              </p>
            </div>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] font-semibold text-emerald-600 dark:text-emerald-400">
            <span className="size-1.5 rounded-full bg-emerald-500" /> 实时调度中
          </span>
        </div>

        {/* 实时心跳度量条 */}
        <div className="my-3 grid grid-cols-4 gap-2 rounded-xl bg-black/[0.03] p-2.5 text-center dark:bg-white/[0.04]">
          <div>
            <div className="text-[9px] font-semibold uppercase text-slate-400">
              今日吞吐
            </div>
            <div className="text-xs font-bold text-slate-700 dark:text-slate-200">
              4.8M
            </div>
          </div>
          <div>
            <div className="text-[9px] font-semibold uppercase text-slate-400">
              本地私有
            </div>
            <div className="text-xs font-bold text-emerald-600 dark:text-emerald-400">
              48%
            </div>
          </div>
          <div>
            <div className="text-[9px] font-semibold uppercase text-slate-400">
              预估花费
            </div>
            <div className="text-xs font-bold text-slate-700 dark:text-slate-200">
              $2.31
            </div>
          </div>
          <div>
            <div className="text-[9px] font-semibold uppercase text-slate-400">
              数字员工
            </div>
            <div className="text-xs font-bold text-slate-700 dark:text-slate-200">
              3 协同
            </div>
          </div>
        </div>

        {/* 1. Policy 调度原则 (Master Rule) */}
        <div className="mb-3 space-y-1.5">
          <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            <span>1. 全局调度原则 (Policy)</span>
            <span className="text-slate-700 dark:text-slate-200">
              {policyLabels[policy].name}
            </span>
          </div>
          <div className="grid grid-cols-5 gap-1">
            {(
              [
                "local-first",
                "auto",
                "quality-first",
                "cost-first",
                "privacy-first",
              ] as RoutingPolicy[]
            ).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => handleSelectPolicy(item)}
                className={cn(
                  "rounded-lg border border-black/5 py-1 text-[10px] font-medium transition-all dark:border-white/10",
                  policy === item
                    ? "bg-slate-900 text-white shadow-sm dark:bg-white dark:text-slate-900"
                    : "bg-white/60 text-slate-600 hover:bg-white hover:text-slate-900 dark:bg-white/5 dark:text-slate-400 dark:hover:bg-white/10"
                )}
              >
                {item === "local-first" && "Local"}
                {item === "auto" && "Auto"}
                {item === "quality-first" && "Quality"}
                {item === "cost-first" && "Cost"}
                {item === "privacy-first" && "Privacy"}
              </button>
            ))}
          </div>
        </div>

        {/* 2. 状态栏主选模型快速切换 (Model Switcher) */}
        <div className="mb-3 space-y-1.5">
          <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            <span>2. 状态栏主选模型 (Model Switcher)</span>
            <span className="font-semibold text-emerald-600 dark:text-emerald-400">
              {activeModelObj.name}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {AVAILABLE_MODELS.map((m) => {
              const isSelected = activeModel === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => handleSelectModel(m)}
                  className={cn(
                    "flex items-center justify-between rounded-lg border p-2 text-left transition-all",
                    isSelected
                      ? "border-blue-500/50 bg-blue-50/80 font-medium text-blue-900 shadow-sm dark:border-blue-400/50 dark:bg-blue-950/50 dark:text-blue-200"
                      : "border-black/5 bg-white/50 text-slate-700 hover:bg-white dark:border-white/10 dark:bg-white/5 dark:text-slate-300 dark:hover:bg-white/10"
                  )}
                >
                  <div className="min-w-0 pr-1">
                    <div className="truncate text-[11px] font-medium leading-tight">
                      {m.name}
                    </div>
                    <div className="text-[9px] text-slate-400">
                      {m.provider} · {m.tag}
                    </div>
                  </div>
                  {isSelected && (
                    <CheckIcon className="size-3.5 shrink-0 text-blue-600 dark:text-blue-400" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* 3. Agents 数字员工 (Digital Coworkers) */}
        <div className="mb-3 space-y-1.5">
          <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            <span>3. 数字工作人员 (Agents)</span>
            <span>当前主选路线</span>
          </div>
          <div className="space-y-1 text-xs">
            <div className="flex items-center justify-between rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center gap-2">
                <span className="grid size-6 place-items-center rounded-md bg-blue-500/10 text-[10px] font-bold text-blue-600 dark:text-blue-400">
                  R
                </span>
                <div>
                  <div className="font-semibold">Researcher (调研员)</div>
                  <div className="text-[9px] text-slate-400">
                    论文检索 · 专利拆解 · 知识图谱
                  </div>
                </div>
              </div>
              <span className="rounded bg-black/5 px-1.5 py-0.5 text-[9px] font-medium dark:bg-white/10">
                Gemini 1.5 Pro
              </span>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center gap-2">
                <span className="grid size-6 place-items-center rounded-md bg-amber-500/10 text-[10px] font-bold text-amber-600 dark:text-amber-400">
                  B
                </span>
                <div>
                  <div className="font-semibold">Builder (架构师)</div>
                  <div className="text-[9px] text-slate-400">
                    终端指令 · 代码编译 · 方案生成
                  </div>
                </div>
              </div>
              <span className="rounded bg-black/5 px-1.5 py-0.5 text-[9px] font-medium dark:bg-white/10">
                Claude 3.5 Sonnet
              </span>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center gap-2">
                <span className="grid size-6 place-items-center rounded-md bg-emerald-500/10 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">
                  O
                </span>
                <div>
                  <div className="font-semibold">Operator (管家)</div>
                  <div className="text-[9px] text-slate-400">
                    ZFS 存储 · Docker · 相册聚类
                  </div>
                </div>
              </div>
              <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-600 dark:text-emerald-400">
                Station 100% Local
              </span>
            </div>
          </div>
        </div>

        {/* 4. Plans 套餐池 */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            <span>4. 算力套餐池 (Plans)</span>
            <span>4 个已连接源</span>
          </div>
          <div className="grid grid-cols-2 gap-1.5 text-xs">
            <div className="rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center justify-between text-[11px] font-medium">
                <span>OpenAI Pro</span>
                <span className="text-[9px] text-slate-400">42%</span>
              </div>
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                <div
                  className="h-full bg-slate-800 dark:bg-slate-200"
                  style={{ width: "42%" }}
                />
              </div>
            </div>

            <div className="rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center justify-between text-[11px] font-medium">
                <span>Anthropic Max</span>
                <span className="text-[9px] text-slate-400">68%</span>
              </div>
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                <div
                  className="h-full bg-slate-800 dark:bg-slate-200"
                  style={{ width: "68%" }}
                />
              </div>
            </div>

            <div className="rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center justify-between text-[11px] font-medium">
                <span>Google AI Pro</span>
                <span className="text-[9px] text-slate-400">31%</span>
              </div>
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                <div
                  className="h-full bg-slate-800 dark:bg-slate-200"
                  style={{ width: "31%" }}
                />
              </div>
            </div>

            <div className="rounded-lg border border-black/5 bg-white/50 p-2 dark:border-white/10 dark:bg-white/5">
              <div className="flex items-center justify-between text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                <span>Echo Station (NPU)</span>
                <span className="text-[9px]">48%</span>
              </div>
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                <div
                  className="h-full bg-emerald-500"
                  style={{ width: "48%" }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* 底部功能按钮 */}
        <div className="mt-3 flex items-center justify-between border-t border-black/5 pt-3 text-[11px] dark:border-white/10">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onOpenWorkbench?.();
            }}
            className="font-medium text-blue-600 transition hover:underline dark:text-blue-400"
          >
            打开完整 AI Network 工作台 →
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onOpenSettings?.();
            }}
            className="text-slate-500 transition hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          >
            连接设置
          </button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
