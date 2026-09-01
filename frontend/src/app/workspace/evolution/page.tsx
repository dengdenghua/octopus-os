import {
  ActivityIcon,
  DnaIcon,
  GitBranchIcon,
  RocketIcon,
  ShieldCheckIcon,
} from "lucide-react";
import { lazy, Suspense } from "react";
import { useSearchParams } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DualHelixEvolutionPanel } from "@/components/workspace/dual-helix-evolution-panel";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";

const EvolutionGovernancePanel = lazy(() =>
  import("@/components/workspace/evolution-governance-panel").then(
    (module) => ({
      default: module.EvolutionGovernancePanel,
    }),
  ),
);

function SectionLoading() {
  return (
    <div className="space-y-3" role="status" aria-label="加载自进化模块">
      <div className="h-24 animate-pulse border-y bg-muted/30" />
      <div className="h-72 animate-pulse border-y bg-muted/20" />
    </div>
  );
}

type EvolutionSection =
  | "overview"
  | "experiments"
  | "candidates"
  | "deployments"
  | "governance";

function normalizeSection(value: string | null): EvolutionSection {
  if (value === "evidence") return "experiments";
  return value === "experiments" ||
    value === "candidates" ||
    value === "deployments" ||
    value === "governance"
    ? value
    : "overview";
}

export default function EvolutionPage() {
  const { t } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const section = normalizeSection(searchParams.get("section"));
  const changeSection = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next === "overview") params.delete("section");
    else params.set("section", next);
    setSearchParams(params, { replace: true });
  };

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="pt-0">
        <div className="flex h-full min-h-0 w-full flex-col bg-card">
          <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">
                {t.evolutionDashboard.title}
              </div>
              <div className="truncate text-xs text-muted-foreground">
                从真实任务中学习，并在验证、安全和可回退的前提下应用改进。
              </div>
            </div>
            <Tabs
              value={section}
              onValueChange={changeSection}
              className="max-w-full overflow-x-auto"
            >
              <TabsList className="h-9 min-w-max rounded-none bg-transparent p-0">
                <TabsTrigger
                  value="overview"
                  className="h-9 shrink-0 gap-1 rounded-none border-b-2 border-transparent bg-transparent px-2 text-xs shadow-none sm:gap-1.5 sm:px-3 data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <DnaIcon className="hidden size-3.5 sm:block" />
                  进化总览
                </TabsTrigger>
                <TabsTrigger
                  value="experiments"
                  className="h-9 shrink-0 gap-1 rounded-none border-b-2 border-transparent bg-transparent px-2 text-xs shadow-none sm:gap-1.5 sm:px-3 data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <ActivityIcon className="hidden size-3.5 sm:block" />
                  实验
                </TabsTrigger>
                <TabsTrigger
                  value="candidates"
                  className="h-9 shrink-0 gap-1 rounded-none border-b-2 border-transparent bg-transparent px-2 text-xs shadow-none sm:gap-1.5 sm:px-3 data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <GitBranchIcon className="hidden size-3.5 sm:block" />
                  候选
                </TabsTrigger>
                <TabsTrigger
                  value="deployments"
                  className="h-9 shrink-0 gap-1 rounded-none border-b-2 border-transparent bg-transparent px-2 text-xs shadow-none sm:gap-1.5 sm:px-3 data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <RocketIcon className="hidden size-3.5 sm:block" />
                  部署
                </TabsTrigger>
                <TabsTrigger
                  value="governance"
                  className="h-9 shrink-0 gap-1 rounded-none border-b-2 border-transparent bg-transparent px-2 text-xs shadow-none sm:gap-1.5 sm:px-3 data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <ShieldCheckIcon className="hidden size-3.5 sm:block" />
                  安全治理
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </header>

          <div className="min-h-0 flex-1 overflow-auto bg-card p-3">
            <Tabs value={section} onValueChange={changeSection}>
              <TabsContent value="overview" className="mt-0">
                <DualHelixEvolutionPanel view="overview" />
              </TabsContent>
              <TabsContent value="experiments" className="mt-0">
                <DualHelixEvolutionPanel view="experiments" />
              </TabsContent>
              <TabsContent value="candidates" className="mt-0">
                <DualHelixEvolutionPanel view="candidates" />
              </TabsContent>
              <TabsContent value="deployments" className="mt-0">
                <DualHelixEvolutionPanel view="deployments" />
              </TabsContent>
              <TabsContent value="governance" className="mt-0">
                <Suspense fallback={<SectionLoading />}>
                  <EvolutionGovernancePanel />
                </Suspense>
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
