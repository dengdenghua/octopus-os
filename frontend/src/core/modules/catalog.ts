/**
 * The module catalog — single source of truth for pluggable sidebar entries.
 *
 * Ids are stable persistence keys: renaming one silently resets that module to
 * its default for every existing user, so treat them as a wire contract.
 */
import {
  LOCAL_DATABASE_APP_ID,
  WORKBENCH_BUILTIN_APPS,
} from "@/core/workbench/apps";
import type { ModuleDescriptor, ModuleGroup, ModuleSection } from "./types";

function workbenchModule(
  appId: string,
  metadata: Pick<
    ModuleDescriptor,
    "labelKey" | "group" | "section" | "removable"
  >,
): ModuleDescriptor {
  const app = WORKBENCH_BUILTIN_APPS.find((entry) => entry.id === appId);
  if (!app) throw new Error(`unknown workbench app: ${appId}`);
  return {
    id: app.moduleId,
    to: app.workspaceRoute,
    ...metadata,
  };
}

const SIDEBAR_MODULES: ModuleDescriptor[] = [
  workbenchModule(LOCAL_DATABASE_APP_ID, {
    labelKey: "navDatabase",
    group: "knowledge",
    section: "chatCapability",
    removable: true,
  }),
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
  workbenchModule("intelligence", {
    labelKey: "navIntelligence",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  }),
  workbenchModule("paper-trading", {
    // 模拟炒股插件页:内嵌平台原版网页(iframe),复刻版已拆到 paper_trading_replica(插件中心)
    labelKey: "navPaperTrading",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  }),
  workbenchModule("projects", {
    // 项目管理(Project OS)驾驶舱:里程碑健康度/风险/下一步/复盘 —— 真实 PM 视角
    labelKey: "navProjects",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  }),
  workbenchModule("design", {
    // 设计创作平台：自由画布与工作流共用节点，角色、技能、插件可视化编排。
    labelKey: "navDesign",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  }),
  workbenchModule("narrative", {
    // 叙事工坊：角色、世界观、剧情线与叙事资产的统一创作工作台。
    labelKey: "navNarrative",
    group: "workspace",
    section: "chatCapability",
    removable: true,
  }),

  // ─── 成长与运营 ────────────────────────────────────────────
  workbenchModule("evolution", {
    labelKey: "navEvolution",
    group: "growth",
    section: "chatCapability",
    removable: true,
  }),

  // ─── 社区与发现 ────────────────────────────────────────────
  workbenchModule("community", {
    labelKey: "navCommunity",
    group: "community",
    section: "community",
    removable: true,
  }),

  // ─── 知识与存储 ────────────────────────────────────────────
  {
    id: "knowledge",
    to: "/workspace/knowledge?surface=chat",
    labelKey: "navKnowledgeGraph",
    group: "knowledge",
    section: "chatCapability",
    removable: true,
  },
];

/** Application routes are shared with the app center and desktop. */
export const MODULE_CATALOG: ModuleDescriptor[] = SIDEBAR_MODULES;

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
