import { lazy, Suspense } from "react";

import AppearanceSettingsPage from "./appearance-settings-page";
import type { SettingsSection } from "./settings-sections";

const ConversationSettingsPage = lazy(
  () => import("./conversation-settings-page"),
);
const ModelSettingsPage = lazy(() => import("./model-settings-page"));
const MemorySettingsPage = lazy(() => import("./memory-settings-page"));
const NotificationSettingsPage = lazy(
  () => import("./notification-settings-page"),
);
const McpSettingsPage = lazy(() =>
  import("./mcp-settings-page").then((module) => ({
    default: module.McpSettingsPage,
  })),
);
const BrowserAutomationSettingsPage = lazy(
  () => import("./browser-automation-settings-page"),
);
const DesktopAutomationSettingsPage = lazy(
  () => import("./desktop-automation-settings-page"),
);
const AutomationSecuritySettingsPage = lazy(
  () => import("./automation-security-settings-page"),
);
const PrivacySettingsPage = lazy(() => import("./privacy-settings-page"));

export const OS_AGENT_SETTINGS_ITEMS = [
  { id: "models", label: "模型与 Codex" },
  { id: "tools", label: "工具、技能与 MCP" },
  { id: "memory", label: "记忆与个人规则" },
  { id: "browserAutomation", label: "浏览器自动化" },
  { id: "desktopAutomation", label: "桌面自动化" },
  { id: "automationSecurity", label: "执行与安全" },
  { id: "conversation", label: "对话" },
  { id: "notification", label: "通知" },
  { id: "appearance", label: "界面与语言" },
  { id: "privacy", label: "个人空间与安全" },
] as const satisfies ReadonlyArray<{ id: SettingsSection; label: string }>;

export type OsAgentSettingsSection =
  (typeof OS_AGENT_SETTINGS_ITEMS)[number]["id"];

function SettingsContentSkeleton() {
  return (
    <div className="animate-pulse space-y-4" role="status" aria-label="正在加载设置">
      <div className="h-7 w-44 rounded bg-slate-200" />
      <div className="h-4 w-80 max-w-full rounded bg-slate-200/80" />
      <div className="h-32 rounded-2xl bg-slate-200/70" />
      <div className="h-24 rounded-2xl bg-slate-200/60" />
    </div>
  );
}

export function SystemAgentSettingsContent({
  section,
}: {
  section: OsAgentSettingsSection;
}) {
  let content;
  switch (section) {
    case "models":
      content = <ModelSettingsPage />;
      break;
    case "tools":
      content = <McpSettingsPage />;
      break;
    case "memory":
      content = <MemorySettingsPage />;
      break;
    case "browserAutomation":
      content = <BrowserAutomationSettingsPage />;
      break;
    case "desktopAutomation":
      content = <DesktopAutomationSettingsPage />;
      break;
    case "automationSecurity":
      content = <AutomationSecuritySettingsPage />;
      break;
    case "conversation":
      content = <ConversationSettingsPage />;
      break;
    case "notification":
      content = <NotificationSettingsPage />;
      break;
    case "privacy":
      content = <PrivacySettingsPage />;
      break;
    case "appearance":
      content = <AppearanceSettingsPage />;
      break;
  }

  return <Suspense fallback={<SettingsContentSkeleton />}>{content}</Suspense>;
}
