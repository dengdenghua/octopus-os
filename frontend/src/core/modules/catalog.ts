/**
 * The module catalog — single source of truth for pluggable sidebar entries.
 *
 * Ids are stable persistence keys: renaming one silently resets that module to
 * its default for every existing user, so treat them as a wire contract.
 */
import type { ModuleDescriptor, ModuleGroup, ModuleSection } from "./types";

export const MODULE_CATALOG: ModuleDescriptor[] = [
  // ─── 工作台核心 ────────────────────────────────────────────
  {
    id: "hr",
    to: "/workspace/agents?surface=chat",
    labelKey: "navHR",
    group: "workspace",
    section: "chatCapability",
    // The agent roster is how you pick who you talk to — keep it pinned.
    removable: false,
  },
  {
    id: "assistant",
    to: "/workspace/realtime/echo-assistant?agent=echo",
    labelKey: "navAssistant",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  },
  {
    id: "intelligence",
    to: "/workspace/intelligence?surface=chat",
    labelKey: "navIntelligence",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  },
  {
    // 模拟炒股插件页:内嵌平台原版网页(iframe),复刻版已拆到 paper_trading_replica(插件中心)
    id: "paper.trading",
    to: "/workspace/paper-trading",
    labelKey: "navPaperTrading",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  },
  {
    // 项目管理(Project OS)驾驶舱:里程碑健康度/风险/下一步/复盘 —— 真实 PM 视角
    id: "projects",
    to: "/workspace/projects",
    labelKey: "navProjects",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  },
  {
    // 设计创作平台：自由画布与工作流共用节点，角色、技能、插件可视化编排。
    id: "design",
    to: "/workspace/design",
    labelKey: "navDesign",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  },
  {
    // 叙事工坊：角色、世界观、剧情线与叙事资产的统一创作工作台。
    id: "narrative",
    to: "/workspace/narrative",
    labelKey: "navNarrative",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  },

  // ─── 成长与运营 ────────────────────────────────────────────
  {
    id: "evolution",
    to: "/workspace/evolution?surface=chat",
    labelKey: "navEvolution",
    group: "growth",
    section: "chatCapability",
    removable: true,
  },

  // ─── 社区与发现 ────────────────────────────────────────────
  {
    id: "community",
    to: "/workspace/community",
    labelKey: "navCommunity",
    group: "community",
    section: "community",
    removable: true,
  },

  // ─── 知识与存储 ────────────────────────────────────────────
  // NOTE: the five storage libraries below share one lazy chunk
  // (`storage/page.tsx`). Hiding a subset saves no download — they are
  // separate entries only because each is a distinct destination.
  {
    id: "knowledge",
    to: "/workspace/knowledge?surface=chat",
    labelKey: "navKnowledgeGraph",
    group: "knowledge",
    section: "storageLibrary",
    removable: true,
  },
  {
    id: "library.apps",
    to: "/workspace/storage?surface=company&library=apps",
    labelKey: "libraryApps",
    group: "knowledge",
    section: "storageLibrary",
    removable: true,
  },
  {
    id: "library.docs",
    to: "/workspace/storage?surface=company&library=docs",
    labelKey: "libraryDocs",
    group: "knowledge",
    section: "storageLibrary",
    removable: true,
  },
  {
    id: "library.images",
    to: "/workspace/storage?surface=company&library=images",
    labelKey: "libraryImages",
    group: "knowledge",
    section: "storageLibrary",
    removable: true,
  },
  {
    id: "library.videos",
    to: "/workspace/storage?surface=company&library=videos",
    labelKey: "libraryVideos",
    group: "knowledge",
    section: "storageLibrary",
    removable: true,
  },
  {
    id: "library.computer",
    to: "/workspace/storage?surface=company&library=computer",
    labelKey: "libraryComputer",
    group: "knowledge",
    section: "storageLibrary",
    removable: true,
  },
];

/** Display order of groups in the editor panel. */
export const MODULE_GROUP_ORDER: ModuleGroup[] = [
  "workspace",
  "knowledge",
  "community",
  "growth",
];

/** i18n keys for group headings, resolved against the `sidebar` namespace. */
export const MODULE_GROUP_LABEL_KEYS: Record<ModuleGroup, string> = {
  workspace: "moduleGroupWorkspace",
  knowledge: "moduleGroupKnowledge",
  community: "moduleGroupCommunity",
  growth: "moduleGroupGrowth",
};

export function moduleById(id: string): ModuleDescriptor | undefined {
  return MODULE_CATALOG.find((m) => m.id === id);
}

export function modulesInSection(section: ModuleSection): ModuleDescriptor[] {
  return MODULE_CATALOG.filter((m) => m.section === section);
}

/** Ids visible by default on a fresh install (currently: everything). */
export function defaultEnabledModuleIds(): string[] {
  return MODULE_CATALOG.map((m) => m.id);
}

/** Ids a user may never hide — always force-visible. */
export function pinnedModuleIds(): string[] {
  return MODULE_CATALOG.filter((m) => !m.removable).map((m) => m.id);
}
