import { RefreshCw } from "lucide-react";

import { ErrorState } from "@/components/ui/state";
import { type EchoApp } from "@/core/apps/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import type { PluginInfo } from "@/core/plugins/types";
import type { SkillInfo } from "@/core/skills/types";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────

export type StoreTab = "agents" | "plugins" | "skills" | "registry";

export type LocalSkill = SkillInfo & {
  affinity?: string[];
  group?: string | null;
  has_tests?: boolean;
  kind?: "system" | "automation" | "domain";
  trusted_source?: string | null;
  market_visibility?: string;
  market_reason?: string | null;
  canonical_skill?: string | null;
};

export type PluginRegistryItem = {
  id: string;
  name: string;
  description?: string;
  author?: string | null;
  plugin: PluginInfo | null;
  apps: EchoApp[];
  localCategory: string;
  source: "plugin" | "app";
};

export interface SkillCategory {
  key: string;
  pattern: RegExp;
}

// ── Constants ──────────────────────────────────────────────────────────

export const LOCAL_SKILL_CATEGORIES: SkillCategory[] = [
  {
    key: "browser-search",
    pattern:
      /browse|browser|search|scraper|scraping|crawler|playwright|devtools|web\s*research|fast-browser|rust-browser/i,
  },
  {
    key: "agent-tools",
    pattern:
      /memory|kg|recall|blackboard|computer|notebook|verify|approval|find-skills|skill-creator|skill-finder|skill-vetter|skill-writer|kimi-find-skills|kimi-skills-finder|mcp-tools|mcporter/i,
  },
  {
    key: "webapp-frontend",
    pattern:
      /webapp|website|landing|frontend|uiux|ui-|ui_|interface|design-system|theme|html|react|component|scaffold|prototype|proto/i,
  },
  {
    key: "backend-api",
    pattern:
      /backend|api-|api_|api\s|graphql|database|sql|openapi|route|gemini|vertex|http-load|load-tester|load-profiler/i,
  },
  {
    key: "code-quality",
    pattern:
      /code-review|quality|refactor|repo-audit|git-repo|secure-code|code-safety|code-vuln|architecture|best-practices|tdd|test-driven|test-suite|software-testing|smart-commit|conventional-commit|debug|perf-analyzer|optimization/i,
  },
  {
    key: "devops-cloud",
    pattern:
      /k8s|kubectl|terraform|deploy|devops|cluster|pipeline|log-|log_|incident|ops|r2-upload|gitlab-cli/i,
  },
  {
    key: "office-docs",
    pattern:
      /docx|pdf|xlsx|spreadsheet|table|document|process-doc|sop|markdown|file|batch-download/i,
  },
  {
    key: "slides-report",
    pattern:
      /pptx|slides?|keynote|deck|report|work-report|work-recap|business-plan-ppt|geo-magazine|photo-magazine/i,
  },
  {
    key: "chart-viz",
    pattern:
      /chart|diagram|visualization|viz|infographic|timeline|gantt|heatmap|story-map/i,
  },
  {
    key: "writing-editing",
    pattern:
      /general-writing|writing|writer|copy-edit|copy-editor|humanizer|longread|rhetoric|speech|article|story|journalistic|portrait|audience-adapter|translation|translator|localization|xindaya/i,
  },
  {
    key: "marketing-copy",
    pattern:
      /marketing-writer|copywriting|copywriter|ad-copy|ad-creative|campaign|ecom-copy|listing-copy|product-description|newsletter|content-research|wechat-post|xhs|zhihu|x-thread|social|short-video-script/i,
  },
  {
    key: "seo-growth",
    pattern:
      /seo|cro|split-test|ab-test|growth|retention|churn|pricing|customer-reply|support-response/i,
  },
  {
    key: "ecommerce",
    pattern:
      /ecom|ecommerce|shopify|amazon|amz|alibaba|1688|etsy|dropshipping|sourcing|supplier|selection|listing|tariff/i,
  },
  {
    key: "market-product",
    pattern:
      /market|competitor|competitive|product|prd|user-story|customer|saas|business|brand|naming|strategy|vc-industry|primary-market|product-spec-writer/i,
  },
  {
    key: "project-goal",
    pattern:
      /project|planner|planning|plan-builder|iteration|sprint|okr|goal|todo|workload|sizing|chrono|daily-planner/i,
  },
  {
    key: "finance-stock",
    pattern:
      /stock|equity|earnings|investor|investment|value-invest|signal|tech-analysis|finance-profiler|cn-finance/i,
  },
  {
    key: "finance-model",
    pattern:
      /financial|cashflow|dcf|valuation|discounted|ratio|statement|fund|risk|commodity|commodities|etf|backtest|trading/i,
  },
  {
    key: "data-stats",
    pattern:
      /stat|regression|correlation|hypothesis|outlier|dataset|data-quality|data-health|weighted|scoring|scorecard|modeler/i,
  },
  {
    key: "data-insight",
    pattern:
      /data|analytics|analysis|insight|metric|dashboard|forecast|scan|audit|research-brief/i,
  },
  {
    key: "academic-paper",
    pattern:
      /academic|paper|sci-paper|scholarly|citation|cite-style|ref-style|literature|research-paper|paper-review|paper-writing|scientific|experiment/i,
  },
  {
    key: "deep-research",
    pattern:
      /deep-research|research-advisor|research-writer|market-research|company-research|equity-research|commodity-research|auto-stat/i,
  },
  {
    key: "education-coach",
    pattern:
      /teach|tutor|mentor|learning|lesson|quiz|anki|flashcard|course|study|exam|coach|interview-simulator|mock-interview|programming-tutor/i,
  },
  {
    key: "hr-career",
    pattern: /resume|cv-|cv_|interview|career|job/i,
  },
  {
    key: "email-comms",
    pattern:
      /email|gmail|imap|smtp|mailer|mail|reply|calendar|meeting|minutes|recap|lark|whatsapp|communication|comms/i,
  },
  {
    key: "legal-compliance",
    pattern:
      /legal|contract|tos|clause|compliance|regulatory|iso-27001|policy|risk-assessment|evidence/i,
  },
  {
    key: "security-audit",
    pattern: /security|safety|vuln|privacy|secure|audit|risk-heatmap/i,
  },
  {
    key: "design-creative",
    pattern:
      /design|creative|image|prompt|fashion|sketch|illustration|retro-tech|visual|logo|color|theme-factory|theme-kit|awesome-design/i,
  },
  {
    key: "media-audio-video",
    pattern:
      /video|audio|tts|speech-synthesis|podcast|remotion|quality-diff|outline-planner/i,
  },
  {
    key: "personal-productivity",
    pattern:
      /adhd|daily|brainstorming|chrono-flow|personal|assistant|work-recap/i,
  },
];

export const APP_CATEGORIES: SkillCategory[] = [
  {
    key: "developer",
    pattern: /github|git|vercel|netlify|deploy|code|api|dev|repository/i,
  },
  {
    key: "ai",
    pattern: /openai|hugging|model|llm|ai|heygen|avatar|generation/i,
  },
  {
    key: "creative",
    pattern: /figma|canva|biorender|design|image|video|creative|figure/i,
  },
  {
    key: "research",
    pattern: /research|brief|paper|source|knowledge/i,
  },
  {
    key: "productivity",
    pattern: /docs?|calendar|mail|notion|drive|office|task/i,
  },
];

// ── Utility functions ──────────────────────────────────────────────────

export function pluginImageUrl(plugin: PluginInfo): string | null {
  const raw = plugin.logo_url || plugin.icon_url;
  if (!raw) return null;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  return `${getBackendBaseURL()}${raw}`;
}

export function appIconUrl(app: EchoApp): string | null {
  const raw = app.icon;
  if (!raw) return null;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  if (raw.startsWith("/")) return `${getBackendBaseURL()}${raw}`;
  return null;
}

export function useLocalSkillCategoryLabel() {
  const { t } = useI18n();
  return (key: string) =>
    key === "other"
      ? t.unifiedStore.skills.other
      : (t.unifiedStore.skills.categoryLabels[key] ?? key);
}

export function useAppCategoryLabel() {
  const { t } = useI18n();
  return (key: string) => t.storeUtils.appCategoryLabels[key] ?? key;
}

export function searchableSkillText(skill: LocalSkill): string {
  return [
    skill.name,
    skill.description,
    skill.category,
    skill.trusted_source,
    ...(skill.affinity ?? []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function classifiableSkillText(skill: LocalSkill): string {
  return [skill.name, skill.description, ...(skill.affinity ?? [])]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function classifyLocalSkill(skill: LocalSkill): string {
  const haystack = classifiableSkillText(skill);
  for (const category of LOCAL_SKILL_CATEGORIES) {
    if (category.pattern.test(haystack)) return category.key;
  }
  return "other";
}

export function sourceLabel(skill: LocalSkill): string {
  if (skill.trusted_source?.startsWith("skill://all_skills/")) return "local";
  if (skill.group) return "runtime";
  return "local";
}

export function searchableAppText(app: EchoApp): string {
  return [
    app.id,
    app.name,
    app.description,
    app.plugin,
    app.source_plugin,
    app.category,
    ...(app.permissions ?? []),
    ...(app.actions ?? []).map(
      (action) => `${action.name} ${action.description ?? ""}`,
    ),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function normalizePluginLookupKey(value?: string | null): string | null {
  const key = String(value ?? "")
    .trim()
    .toLowerCase();
  return key || null;
}

export function pluginLookupKeys(plugin: PluginInfo): string[] {
  return [plugin.id, plugin.name]
    .map(normalizePluginLookupKey)
    .filter((key): key is string => Boolean(key));
}

export function appPluginLookupKeys(app: EchoApp): string[] {
  return [app.plugin, app.source_plugin, app.id]
    .map(normalizePluginLookupKey)
    .filter((key): key is string => Boolean(key));
}

export function searchablePluginBundleText(
  plugin: PluginInfo | null,
  apps: EchoApp[],
): string {
  return [
    plugin?.id,
    plugin?.name,
    plugin?.description,
    plugin?.author,
    plugin?.state,
    ...(plugin?.dependencies ?? []),
    ...(plugin?.capabilities ?? []).map((capability) =>
      Object.values(capability).join(" "),
    ),
    ...apps.map(searchableAppText),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export function searchablePluginItemText(item: PluginRegistryItem): string {
  return searchablePluginBundleText(item.plugin, item.apps);
}

export function classifyPluginItem(
  plugin: PluginInfo | null,
  apps: EchoApp[],
): string {
  const declared = (apps[0]?.category ?? "").trim().toLowerCase();
  if (declared && declared !== "connector" && declared !== "other") {
    return declared;
  }
  const haystack = searchablePluginBundleText(plugin, apps);
  for (const category of APP_CATEGORIES) {
    if (category.pattern.test(haystack)) return category.key;
  }
  return declared || "connector";
}

export function useOpenCreatePluginChat() {
  const { t } = useI18n();
  return () => {
    window.location.hash = `/workspace/realtime/new?prompt=${encodeURIComponent(
      t.storeUtils.createPluginPrompt,
    )}`;
  };
}

// ── Shared UI ──────────────────────────────────────────────────────────

function StoreErrorDetails({ detail }: { detail: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <details className="text-xs">
      <summary className="cursor-pointer select-none">
        {t.storeUtils.technicalDetails}
      </summary>
      <code className="mt-1 block break-words rounded-md bg-background/70 px-2 py-1">
        {detail}
      </code>
    </details>
  );
}

export function StoreErrorState({
  title,
  detail,
  retryLabel,
  retrying = false,
  onRetry,
}: {
  title: string;
  detail?: string | null;
  retryLabel: string;
  retrying?: boolean;
  onRetry: () => void;
}) {
  return (
    <ErrorState
      title={title}
      detail={detail ? <StoreErrorDetails detail={detail} /> : null}
      actionDisabled={retrying}
      actionIcon={
        <RefreshCw className={cn("size-3.5", retrying && "animate-spin")} />
      }
      actionLabel={retryLabel}
      onAction={onRetry}
      className="min-h-[180px]"
    />
  );
}
