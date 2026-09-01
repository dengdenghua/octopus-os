import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import {
  useEvolutionOverview,
  useSkillPerformance,
  useEvolutionStory,
} from "@/core/evolution/hooks";
import { CollectiveIntelligencePanel } from "./collective-intelligence-panel";
import { AgentGrid } from "./agent-grid";
import { CharacterCard } from "./character-card";
import { SkillTree } from "./skill-tree";
import { GrowthTimeline } from "./growth-timeline";
import {
  calculateCollectiveStats,
  transformToAgentCard,
  transformToCharacterStats,
  transformToSkills,
  transformToTimelineEvents,
  extractAchievements,
} from "./game-data-transformer";
import { Loader2Icon } from "lucide-react";

// Mock agent data - 实际应该从 Hub 或用户配置中获取
const MOCK_AGENTS = [
  { id: "agent-code", name: "代码助手", icon: "👨‍💻" },
  { id: "agent-design", name: "设计师", icon: "🎨" },
  { id: "agent-doc", name: "文档助手", icon: "📝" },
  { id: "agent-ops", name: "运维专家", icon: "🔧" },
];

export default function GameifiedEvolutionDashboard() {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [collectivePanelExpanded, setCollectivePanelExpanded] = useState(true);

  // Fetch data for all agents (群体数据)
  const overviewQ = useEvolutionOverview();
  const skillPerformanceQ = useSkillPerformance();
  const storyQ = useEvolutionStory();

  const isLoading =
    overviewQ.isLoading || skillPerformanceQ.isLoading || storyQ.isLoading;

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2Icon className="size-5 animate-spin" />
          <span>加载进化数据...</span>
        </div>
      </div>
    );
  }

  const overview = overviewQ.data;
  const skillPerformances = skillPerformanceQ.data ?? [];
  const story = storyQ.data;

  if (!overview) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-3 flex size-16 items-center justify-center rounded-full bg-muted text-3xl">
            🤖
          </div>
          <h3 className="text-sm font-medium">暂无进化数据</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            完成第一个任务开始你的成长之旅
          </p>
        </div>
      </div>
    );
  }

  // Transform data
  const collectiveStats = calculateCollectiveStats(overview);
  const agentCards = MOCK_AGENTS.map((agent) =>
    transformToAgentCard(
      agent.id,
      agent.name,
      agent.icon,
      overview,
      skillPerformances
    )
  );

  const selectedAgent = selectedAgentId
    ? MOCK_AGENTS.find((a) => a.id === selectedAgentId)
    : null;

  const characterStats = transformToCharacterStats(overview, skillPerformances);
  const skills = transformToSkills(skillPerformances);
  const timelineEvents = story ? transformToTimelineEvents(story) : [];
  const achievements = extractAchievements(overview, skillPerformances);

  return (
    <div className="space-y-6 p-6">
      {/* Collective Intelligence Panel - Collapsible */}
      <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        <button
          type="button"
          onClick={() => setCollectivePanelExpanded(!collectivePanelExpanded)}
          className="flex w-full items-center justify-between px-6 py-4 text-left transition-colors hover:bg-muted/50"
          aria-expanded={collectivePanelExpanded}
          aria-label={collectivePanelExpanded ? "折叠群体智能面板" : "展开群体智能面板"}
        >
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-xl">
              🧠
            </span>
            <div>
              <h2 className="text-base font-semibold">群体智能</h2>
              <p className="text-xs text-muted-foreground">
                系统整体健康度 {collectiveStats.healthScore}%
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {collectivePanelExpanded ? "收起" : "展开"}
            </span>
            {collectivePanelExpanded ? (
              <ChevronUpIcon className="size-5 text-muted-foreground" />
            ) : (
              <ChevronDownIcon className="size-5 text-muted-foreground" />
            )}
          </div>
        </button>
        {collectivePanelExpanded && (
          <div className="border-t border-border px-6 pb-6">
            <CollectiveIntelligencePanel stats={collectiveStats} />
          </div>
        )}
      </div>

      {/* Agent Grid */}
      <AgentGrid
        agents={agentCards}
        selectedAgentId={selectedAgentId}
        onSelectAgent={setSelectedAgentId}
      />

      {/* Selected Agent Details */}
      {selectedAgent && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold">
              {selectedAgent.icon} {selectedAgent.name} 的成长详情
            </h2>
            <button
              type="button"
              onClick={() => setSelectedAgentId(null)}
              className="text-sm text-muted-foreground hover:text-foreground"
            >
              ← 返回角色列表
            </button>
          </div>

          <Tabs defaultValue="overview" className="w-full">
            <TabsList>
              <TabsTrigger value="overview">概览</TabsTrigger>
              <TabsTrigger value="skills">技能树</TabsTrigger>
              <TabsTrigger value="timeline">成长日志</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="mt-6">
              <CharacterCard
                name={selectedAgent.name}
                stats={characterStats}
                recentAchievements={achievements}
              />
            </TabsContent>

            <TabsContent value="skills" className="mt-6">
              <SkillTree skills={skills} />
            </TabsContent>

            <TabsContent value="timeline" className="mt-6">
              <GrowthTimeline events={timelineEvents} />
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
