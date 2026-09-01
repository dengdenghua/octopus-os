import {
  ArrowLeftIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  BrainCircuitIcon,
  FilePlus2Icon,
  FileTextIcon,
  GitBranchIcon,
  GlobeIcon,
  InfoIcon,
  Loader2Icon,
  MonitorIcon,
  MoreHorizontalIcon,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  agentPhaseDisplayTitle,
  phaseStatusText,
  type AgentPhase,
  type AgentPhaseStatus,
} from "./agent-phases";
import {
  pickCurrentWorkBlock,
  workBlockLabelsFromShape,
  workBlockTitle,
  type WorkBlock,
  type WorkBlockStatus,
} from "./work-blocks";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import type { OutlineRound } from "@/core/threads/progress-outline";
import type { GroundingSource } from "@/core/realtime/items";
import {
  type AgentTile,
  type DiffEntry,
  type AgentWorkbenchTabId,
  statusIcon,
  agentEventGroupId,
  DIFF_TAB_LABEL,
  roleDescription,
} from "./agent-workbench-utils";
import {
  type AgentWorkbenchProcessEventSnapshot,
  emitAgentWorkbenchFocus,
} from "./agent-workbench-events";
import { useSubtask } from "@/core/tasks/context";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { SubtaskHoverPreview } from "./messages/parallel-subtasks-grid";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Shared UI primitives ──────────────────────────────────────────────

export function StatusGlyph({
  status,
  className,
}: {
  status: WorkBlockStatus | AgentPhaseStatus;
  className?: string;
}) {
  const Icon = statusIcon(status);
  return (
    <Icon
      className={cn(
        "size-4 shrink-0",
        status === "running" && "animate-spin text-foreground",
        status === "done" && "text-success",
        status === "warning" && "text-warning",
        status === "error" && "text-destructive",
        status === "pending" && "text-muted-foreground/45",
        status === "waiting_approval" && "text-warning",
        className,
      )}
    />
  );
}

export function WorkbenchEmptyPage({
  description,
  title,
}: {
  description: string;
  title: string;
}) {
  return (
    <div className="workbench-empty-page flex min-h-0 flex-1 items-center justify-center bg-muted/30 p-6 text-center">
      <div className="workbench-empty-page-content flex max-w-xs flex-col items-center gap-3">
        <div className="workbench-empty-page-icon relative">
          <div className="relative flex size-12 items-center justify-center rounded-lg border border-border bg-card">
            <MonitorIcon
              className="size-5 text-muted-foreground/60"
              strokeWidth={1.5}
            />
          </div>
        </div>
        <div className="text-sm font-medium text-foreground/90">{title}</div>
        <p className="workbench-empty-page-description text-xs leading-relaxed text-muted-foreground/70">
          {description}
        </p>
      </div>
    </div>
  );
}

function SummaryDiffEntryList({
  entries,
  kind,
  onOpenEntry,
}: {
  entries: DiffEntry[];
  kind: "artifact" | "change";
  onOpenEntry?: (entry: DiffEntry, kind: "artifact" | "change") => void;
}) {
  const { t } = useI18n();
  const Icon = kind === "artifact" ? FilePlus2Icon : FileTextIcon;
  return (
    <ul className="stable-scroll-viewport max-h-48 overflow-y-auto">
      {entries.map((entry) => (
        <li key={entry.id}>
          <button
            type="button"
            onClick={() => onOpenEntry?.(entry, kind)}
            className="flex w-full items-center gap-3 py-2 text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            aria-label={
              kind === "artifact"
                ? t.agentWorkbenchPages.openArtifact(entry.title)
                : t.agentWorkbenchPages.viewDiff(entry.title)
            }
            title={entry.path}
          >
            <Icon
              className={cn(
                "size-4 shrink-0",
                kind === "artifact"
                  ? "text-foreground/80"
                  : "text-muted-foreground",
              )}
            />
            <span className="min-w-0 flex-1 truncate text-sm text-foreground">
              {basename(entry.title || entry.path)}
            </span>
            <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground/55" />
          </button>
        </li>
      ))}
    </ul>
  );
}

type ObservedReferenceTabId = "files" | "web" | "memory" | "other";

interface ObservedReferenceItem {
  faviconUrl?: string;
  host?: string;
  id: string;
  thumbnailUrl?: string;
  title: string;
  subtitle?: string;
  tag?: string;
  url?: string;
}

interface ObservedReferenceTab {
  id: ObservedReferenceTabId;
  label: string;
  items: ObservedReferenceItem[];
}

const OBSERVED_REFERENCE_TABS: ObservedReferenceTabId[] = [
  "files",
  "web",
  "memory",
];

const OBSERVED_REFERENCE_META: Record<
  ObservedReferenceTabId,
  {
    Icon: LucideIcon;
    barClassName: string;
    dotClassName: string;
    iconClassName: string;
  }
> = {
  files: {
    Icon: FileTextIcon,
    barClassName: "bg-info",
    dotClassName: "bg-info",
    iconClassName: "text-info",
  },
  web: {
    Icon: GlobeIcon,
    barClassName: "bg-info",
    dotClassName: "bg-info",
    iconClassName: "text-info",
  },
  memory: {
    Icon: BrainCircuitIcon,
    barClassName: "bg-warning",
    dotClassName: "bg-warning",
    iconClassName: "text-warning",
  },
  other: {
    Icon: MoreHorizontalIcon,
    barClassName: "bg-muted-foreground/50",
    dotClassName: "bg-muted-foreground/50",
    iconClassName: "text-muted-foreground",
  },
};

function buildObservedReferenceTabs(
  blocks: WorkBlock[],
  t: Translations,
): ObservedReferenceTab[] {
  const buckets: Record<ObservedReferenceTabId, ObservedReferenceItem[]> = {
    files: [],
    web: [],
    memory: [],
    other: [],
  };

  for (const block of blocks) {
    if (isAgentLifecycleBlock(block)) continue;
    // A todo_write event is execution progress, not an observed input or
    // evidence source. It is rendered by the dedicated Progress section.
    if (block.kind === "todo") continue;
    const tabId = referenceTabForBlock(block);
    buckets[tabId].push(...referenceItemsForBlock(block, t));
  }

  return OBSERVED_REFERENCE_TABS.map((id) => ({
    id,
    label: t.agentWorkbenchPages.reference[id],
    items: dedupeReferenceItems(buckets[id]).slice(0, 50),
  })).filter((tab) => tab.items.length > 0);
}

function referenceTabForBlock(block: WorkBlock): ObservedReferenceTabId {
  const signature = [
    block.event.name,
    block.title,
    block.subtitle,
    block.event.agentName,
    block.event.subAgentRole,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (
    /(^|[_\s-])(memory|remember|recall|profile_memory|user_preferences)([_\s-]|$)/i.test(
      signature,
    )
  ) {
    return "memory";
  }
  if (block.kind === "file" || block.kind === "read") return "files";
  if (isLocalFileSearchBlock(block)) return "files";
  if (
    (block.kind === "search" || block.kind === "browser") &&
    hasHttpReference(block)
  ) {
    return "web";
  }
  return "other";
}

function referenceItemsForBlock(
  block: WorkBlock,
  t: Translations,
): ObservedReferenceItem[] {
  if (block.kind === "todo") return [];

  const blockTitle = workBlockTitle(
    block,
    workBlockLabelsFromShape(t.workBlocks),
  );
  if (block.kind === "file" || block.kind === "read") {
    const fileItems = fileReferenceItems(block, blockTitle);
    if (fileItems.length > 0) return fileItems;
  }
  if (isLocalFileSearchBlock(block)) {
    const fileItems = localFileSearchReferenceItems(block, blockTitle);
    if (fileItems.length > 0) return fileItems;
    // A pattern with no returned path is an attempted lookup, not confirmed
    // context. Do not inflate the evidence count with the query itself.
    return [];
  }
  if (block.kind === "search" || block.kind === "browser") {
    const webItems = webReferenceItems(block);
    if (webItems.length > 0) return webItems;
  }

  const target = referenceTarget(block);
  const title = target || blockTitle || block.event.name;
  if (!title) return [];
  const detail =
    target && blockTitle !== target
      ? blockTitle
      : block.subtitle || compactReference(block.outputText, 96);
  return [
    {
      id: block.id,
      title: compactReference(title, 120),
      subtitle: detail ? compactReference(detail, 128) : undefined,
      tag: referenceStatusLabel(block.status, t),
    },
  ];
}

function isLocalFileSearchBlock(block: WorkBlock): boolean {
  if (block.kind !== "search" || hasHttpReference(block)) return false;
  return /(?:^|[_:\s-])(grep|glob|list|search)(?:_?(?:text|file|files|cwd|dir|directory))?(?:$|[_:\s-])/i.test(
    block.event.name,
  );
}

function localFileSearchReferenceItems(
  block: WorkBlock,
  blockTitle: string,
): ObservedReferenceItem[] {
  const paths = uniqueStrings([
    ...filePathsFromSearchOutput(block.event.output),
    ...filePathsFromSearchOutput(block.event.input?.output),
  ]);
  return paths.slice(0, 50).map((path, index) => {
    const name = basename(path);
    return {
      id: `${block.id}:local-search:${index}:${path}`,
      title: compactReference(name || path, 120),
      subtitle: path !== name ? compactReference(path, 140) : blockTitle,
      tag: fileKindLabel(path),
    };
  });
}

function filePathsFromSearchOutput(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((entry) =>
      typeof entry === "string" && isConcreteLocalReference(entry)
        ? [entry.trim()]
        : filePathsFromSearchOutput(entry),
    );
  }
  if (isRecord(value)) {
    const direct = stringValuesFromInput(value, [
      "path",
      "file_path",
      "filepath",
      "filename",
    ]).filter(isConcreteLocalReference);
    return [
      ...direct,
      ...["results", "items", "files", "matches", "data"].flatMap((key) =>
        filePathsFromSearchOutput(value[key]),
      ),
    ];
  }
  if (typeof value !== "string" || !value.trim()) return [];

  const parsed = parsedJsonValuesFromText(value);
  // Never regex filenames out of arbitrary prose. CSS values, versions and
  // truncated words routinely resemble paths ("oklch(0.55", "globals.cs").
  // Legacy compatibility accepts JSON embedded in text; new runs use typed
  // workbench evidence and never enter this fallback.
  return parsed.flatMap(filePathsFromSearchOutput);
}

function isConcreteLocalReference(value: string): boolean {
  const path = value.trim();
  if (!path || path === "." || path === "./" || path === "../") return false;
  if (/[*?{}\[\]]/.test(path) || /^https?:\/\//i.test(path)) return false;
  return /[\\/]/.test(path) || /\.[A-Za-z0-9]{1,12}$/.test(path);
}

function fileReferenceItems(
  block: WorkBlock,
  blockTitle: string,
): ObservedReferenceItem[] {
  return uniqueStrings([
    ...changePaths(block.event.input),
    ...stringArrayFromInput(block.event.input, [
      "paths",
      "files",
      "file_paths",
    ]),
    ...stringValuesFromInput(block.event.input, [
      "path",
      "file_path",
      "filepath",
      "filename",
    ]),
    ...(block.event.filesTouched ?? []),
  ]).map((path, index) => {
    const name = basename(path);
    return {
      id: `${block.id}:file:${index}:${path}`,
      title: compactReference(name || path, 120),
      subtitle: path !== name ? compactReference(path, 140) : blockTitle,
      tag: fileKindLabel(path),
    };
  });
}

function webReferenceItems(block: WorkBlock): ObservedReferenceItem[] {
  const pages = dedupeWebPages([
    ...webPagesFromValue(block.event.output),
    ...webPagesFromValue(block.event.input?.output),
  ]).filter((page) => isHttpUrl(page.url));
  if (pages.length === 0) {
    const url = firstInputString(block.event.input, ["url"]);
    if (isHttpUrl(url)) {
      const host = hostLabel(url);
      return [
        {
          faviconUrl: faviconUrlForUrl(url),
          host,
          id: `${block.id}:url:${url}`,
          title: compactReference(pageTitleFromUrl(url), 120),
          subtitle: compactReference(url, 140),
          tag: host,
          url,
        },
      ];
    }
    return [];
  }

  return pages.slice(0, 8).map((page, index) => {
    const host = page.url ? hostLabel(page.url) : undefined;
    return {
      faviconUrl: page.url ? faviconUrlForUrl(page.url) : undefined,
      host,
      id: `${block.id}:web:${index}:${page.url || page.title}`,
      thumbnailUrl: page.thumbnailUrl,
      title: compactReference(page.title || pageTitleFromUrl(page.url), 120),
      subtitle: page.url
        ? compactReference(page.url, 140)
        : page.snippet
          ? compactReference(page.snippet, 140)
          : undefined,
      tag: host,
      url: page.url || undefined,
    };
  });
}

function hasHttpReference(block: WorkBlock): boolean {
  if (isHttpUrl(firstInputString(block.event.input, ["url"]))) return true;
  return dedupeWebPages([
    ...webPagesFromValue(block.event.output),
    ...webPagesFromValue(block.event.input?.output),
  ]).some((page) => isHttpUrl(page.url));
}

type WebReferencePage = {
  title: string;
  url: string;
  snippet?: string;
  thumbnailUrl?: string;
};

function webPagesFromValue(value: unknown): WebReferencePage[] {
  if (typeof value === "string") {
    return dedupeWebPages([
      ...parsedJsonValuesFromText(value).flatMap(webPagesFromValue),
      ...webPagesFromText(value),
    ]);
  }

  const candidates = resultRecordsFromValue(value);
  return candidates.flatMap((record) => {
    const url = firstInputString(record, ["url", "link", "href"]);
    const title =
      firstInputString(record, ["title", "name", "text"]) ||
      pageTitleFromUrl(url);
    const snippet = firstInputString(record, [
      "snippet",
      "description",
      "summary",
      "content",
    ]);
    const thumbnailUrl = firstInputString(record, [
      "thumbnail",
      "thumbnail_url",
      "thumbnailUrl",
      "image",
      "image_url",
      "imageUrl",
      "og_image",
      "ogImage",
      "previewUrl",
    ]);
    if (!isHttpUrl(url)) return [];
    return [
      {
        title: cleanWebText(title),
        url: cleanWebText(url),
        snippet: snippet ? cleanWebText(snippet) : undefined,
        thumbnailUrl: thumbnailUrl ? cleanWebText(thumbnailUrl) : undefined,
      },
    ];
  });
}

function resultRecordsFromValue(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.filter(isRecord);
  }
  if (!isRecord(value)) return [];
  for (const key of ["results", "items", "pages", "documents", "data"]) {
    const nested = value[key];
    if (Array.isArray(nested)) return nested.filter(isRecord);
  }
  return [value];
}

function parsedJsonValuesFromText(text: string): unknown[] {
  const trimmed = text.trim();
  const startIndexes = [trimmed.indexOf("{"), trimmed.indexOf("[")]
    .filter((index) => index >= 0)
    .sort((a, b) => a - b);

  const parsed: unknown[] = [];
  for (const start of startIndexes) {
    try {
      parsed.push(JSON.parse(trimmed.slice(start)));
      break;
    } catch {
      // Aggregated command output is sometimes truncated. Fall back to the
      // conservative title/url extractor below.
    }
  }
  return parsed;
}

function webPagesFromText(text: string): WebReferencePage[] {
  const pages: WebReferencePage[] = [];
  const patterns = [
    /"title"\s*:\s*"([^"]+)"[\s\S]{0,800}?"url"\s*:\s*"([^"\r\n}]*)/g,
    /'title'\s*:\s*'([^']+)'[\s\S]{0,800}?'url'\s*:\s*'([^'\r\n}]*)/g,
  ];

  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const title = cleanWebText(match[1] ?? "");
      const url = cleanWebText(match[2] ?? "");
      if (!isHttpUrl(url)) continue;
      pages.push({ title: title || pageTitleFromUrl(url), url });
    }
  }
  return pages;
}

function dedupeWebPages(pages: WebReferencePage[]): WebReferencePage[] {
  const seen = new Set<string>();
  const result: WebReferencePage[] = [];
  for (const page of pages) {
    const key = `${page.url || page.title}`.toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(page);
  }
  return result;
}

function cleanWebText(text: string): string {
  return text
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .trim();
}

function referenceTarget(block: WorkBlock): string {
  const input = block.event.input;
  return (
    changePaths(input)[0] ||
    firstInputString(input, ["path", "file_path", "filepath", "filename"]) ||
    firstInputString(input, ["url"]) ||
    firstInputString(input, ["query", "pattern"]) ||
    firstInputString(input, ["command", "cmd"]) ||
    firstInputString(input, ["key", "name", "title"])
  );
}

function stringValuesFromInput(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string[] {
  if (!input) return [];
  return keys.flatMap((key) => {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return [value.trim()];
    if (typeof value === "number") return [String(value)];
    return [];
  });
}

function stringArrayFromInput(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string[] {
  if (!input) return [];
  return keys.flatMap((key) => {
    const value = input[key];
    if (!Array.isArray(value)) return [];
    return value.flatMap((item) => {
      if (typeof item === "string" && item.trim()) return [item.trim()];
      if (isRecord(item)) {
        return stringValuesFromInput(item, ["path", "file_path", "filepath"]);
      }
      return [];
    });
  });
}

function firstInputString(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string {
  if (!input) return "";
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number") return String(value);
  }
  return "";
}

function changePaths(input: Record<string, unknown> | undefined): string[] {
  const changes = input?.changes;
  if (!Array.isArray(changes)) return [];
  return changes.flatMap((change) => {
    if (!isRecord(change)) return [];
    const path = change.path;
    return typeof path === "string" && path.trim() ? [path.trim()] : [];
  });
}

function dedupeReferenceItems(
  items: ObservedReferenceItem[],
): ObservedReferenceItem[] {
  const seen = new Set<string>();
  const result: ObservedReferenceItem[] = [];
  for (const item of items) {
    const pathLike = item.subtitle || item.url || item.title;
    const normalizedPath = pathLike
      .trim()
      .replace(/\\/g, "/")
      .replace(/:\d+(?::\d+)?$/, "")
      .replace(/^\.\//, "")
      .toLocaleLowerCase();
    // The compact list only displays the basename. Showing several visually
    // identical rows is not actionable; detailed paths remain available in
    // typed evidence and file/diff views.
    const key = item.tag
      ? `file:${item.title.trim().toLocaleLowerCase()}`
      : normalizedPath;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function referenceStatusLabel(
  status: WorkBlock["status"],
  t: Translations,
): string {
  if (status === "running") return t.agentWorkbenchPages.statusRunning;
  if (status === "waiting_approval")
    return t.agentWorkbenchPages.statusWaitingApproval;
  if (status === "warning") return "已恢复";
  if (status === "error") return t.agentWorkbenchPages.statusError;
  return t.agentWorkbenchPages.statusDone;
}

function fileKindLabel(path: string): string | undefined {
  const name = basename(path);
  const match = /\.([a-z0-9]+)$/i.exec(name);
  return match?.[1]?.toUpperCase();
}

function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

function pageTitleFromUrl(url: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "") || url;
  } catch {
    return url;
  }
}

function isHttpUrl(url: string | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function hostLabel(url: string): string | undefined {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return undefined;
  }
}

function faviconUrlForUrl(url: string): string | undefined {
  try {
    const hostname = new URL(url).hostname;
    if (!hostname) return undefined;
    return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(
      hostname,
    )}&sz=64`;
  } catch {
    return undefined;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const clean = value.trim();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    result.push(clean);
  }
  return result;
}

function compactReference(value: unknown, max: number): string {
  const text = typeof value === "string" ? value : "";
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1)}…`;
}

function compactTokenCount(value: number): string {
  if (value >= 1_000_000) {
    return `${Number((value / 1_000_000).toFixed(1))}M`;
  }
  if (value >= 1_000) {
    return `${Number((value / 1_000).toFixed(1))}K`;
  }
  return value.toLocaleString();
}

function ReferenceIcon({
  fallbackClassName,
  Icon,
  item,
  tabId,
}: {
  fallbackClassName: string;
  Icon: LucideIcon;
  item: ObservedReferenceItem;
  tabId: ObservedReferenceTabId;
}) {
  const [failed, setFailed] = useState(false);
  const imageUrl = item.thumbnailUrl || item.faviconUrl;
  const hostInitial = item.host?.charAt(0).toUpperCase();
  const canShowImage = tabId === "web" && imageUrl && !failed;

  return (
    <span
      className={cn(
        "flex size-5 shrink-0 items-center justify-center overflow-hidden text-muted-foreground",
      )}
    >
      {canShowImage ? (
        <img
          src={imageUrl}
          alt={item.host ? `${item.host} icon` : ""}
          className={cn(
            "size-full object-cover",
            item.thumbnailUrl ? "rounded-sm" : "",
          )}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : hostInitial ? (
        <span className="text-xs font-semibold text-muted-foreground">
          {hostInitial}
        </span>
      ) : (
        <Icon className={cn("size-4", fallbackClassName)} />
      )}
    </span>
  );
}

// 子 agent 行 — 与对话区 MiniSubtaskRow 同一实体化体验：
// 头像 + hover popover（复用 SubtaskHoverPreview）+ 点击/按钮跳转。
function SummaryAgentRow({ tile }: { tile: AgentTile }) {
  const { t } = useI18n();
  const subtask = useSubtask(tile.id);
  const displayLabel =
    tile.taskLabel ?? tile.codename ?? tile.name ?? tile.label;
  const previewId = `subtask-preview-${tile.id}`;
  const statusLabel = subtask
    ? (() => {
        const raw = t.subagents[subtask.status as keyof typeof t.subagents];
        return typeof raw === "string" ? raw : subtask.status;
      })()
    : "";
  const avatarEmoji = subtask?.avatarEmoji ?? tile.avatar;
  const hue = subtask?.hue;

  const handleClick = () => {
    emitAgentWorkbenchFocus({
      agentId: tile.id,
      tab: "agent",
      view: "role",
    });
  };

  return (
    <li className="group/subtask-row relative flex items-start gap-3">
      <button
        type="button"
        onClick={handleClick}
        // See MiniSubtaskRow: suppress mouse-click focus so the hover
        // preview doesn't stay pinned via group-focus-within after a click.
        onMouseDown={(e) => e.preventDefault()}
        aria-label={displayLabel}
        aria-describedby={subtask ? previewId : undefined}
        className="absolute inset-0 cursor-pointer rounded-lg"
      />
      <span
        className="flex size-5 shrink-0 items-center justify-center rounded-md text-xs text-muted-foreground"
        style={hue != null ? { background: `hsl(${hue} 70% 92%)` } : undefined}
      >
        {avatarEmoji ?? <BotIcon className="size-3" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
            {displayLabel}
          </span>
          <span
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              tile.status === "error"
                ? "bg-destructive"
                : tile.status === "running"
                  ? "bg-success"
                  : tile.status === "waiting_approval"
                    ? "bg-warning"
                    : tile.status === "done"
                      ? "bg-muted-foreground/45"
                      : "bg-muted-foreground/35",
            )}
            aria-hidden="true"
          />
        </span>
        {(tile.resultSummary ?? tile.error ?? tile.lastThought) && (
          <span className="mt-0.5 block line-clamp-2 text-xs leading-4 text-muted-foreground">
            {tile.error ?? tile.resultSummary ?? tile.lastThought}
          </span>
        )}
      </span>
      <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground/50" />
      {subtask && (
        <SubtaskHoverPreview
          task={subtask}
          statusLabel={statusLabel}
          id={previewId}
        />
      )}
    </li>
  );
}

// ============================================================================
// 看板概要页 (Agent Summary Dashboard)
// ============================================================================
function interruptedPhaseIndex(phases: AgentPhase[]): number {
  const activeIndex = phases.findIndex(
    (phase) =>
      phase.status === "running" || phase.status === "waiting_approval",
  );
  if (activeIndex >= 0) return activeIndex;

  const pendingIndex = phases.findIndex((phase) => phase.status === "pending");
  if (pendingIndex >= 0) return pendingIndex;

  // A provider can mark every todo complete just before the response stream
  // is interrupted. Keep earlier completed work, but do not present the final
  // handoff step as successfully settled when the turn never finished.
  for (let index = phases.length - 1; index >= 0; index -= 1) {
    if (phases[index]?.status === "done") return index;
  }
  return -1;
}

export function AgentSummaryPage({
  phases,
  diffEntries,
  agentTiles,
  blocks,
  focusedProcessEvent,
  progressOutline,
  userInput,
  groundingSources = [],
  preferStructuredReferences = false,
  terminalState,
  contextTokens,
  maxContextTokens,
  isCompressingContext,
  onCompressContext,
  onSelectTab,
  onOpenArtifact,
  resultPreviewUrl: _resultPreviewUrl,
}: {
  phases: AgentPhase[];
  diffEntries: DiffEntry[];
  agentTiles: AgentTile[];
  blocks: WorkBlock[];
  focusedProcessEvent?: AgentWorkbenchProcessEventSnapshot | null;
  focusedEventId?: string | null;
  /** 「进展」面板的叙事大纲（按 iteration 分组）；缺省或为空时回退为 phase 平铺。 */
  progressOutline?: OutlineRound[];
  /** 本轮用户输入（原始请求 + 上传文件/附件）；用于概要页 Inputs 区。 */
  userInput?: {
    text: string;
    uploadedFiles: Array<{ filename: string; path: string }>;
    attachments: Array<{ filename: string }>;
  } | null;
  groundingSources?: GroundingSource[];
  /** Ignore legacy tool-text inference once the server supplies evidence. */
  preferStructuredReferences?: boolean;
  terminalState?: "interrupted" | "failed" | "blocked" | null;
  contextTokens?: number;
  maxContextTokens?: number;
  isCompressingContext?: boolean;
  onCompressContext?: () => void | Promise<void>;
  onSelectTab?: (tabId: AgentWorkbenchTabId) => void;
  onOpenArtifact?: (path: string) => void;
  /** A deployed or local result preview for the current completed case. */
  resultPreviewUrl?: string | null;
}) {
  const { t } = useI18n();
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    () => new Set(["progress", "artifacts", "references"]),
  );
  const [refTab, setRefTab] = useState<ObservedReferenceTabId>("files");
  const artifactDiffEntries = useMemo(
    () => diffEntries.filter((entry) => entry.created),
    [diffEntries],
  );
  const changedFileEntries = useMemo(
    () => diffEntries.filter((entry) => !entry.created),
    [diffEntries],
  );
  const openDiffEntry = (entry: DiffEntry, kind: "artifact" | "change") => {
    if (kind === "artifact") {
      // Generated artifacts open in the artifacts side panel (with the entry
      // selected when the host wires onOpenArtifact); the summary page itself
      // has no artifact viewer.
      if (onOpenArtifact) {
        onOpenArtifact(entry.path);
      } else {
        onSelectTab?.("artifacts");
      }
      return;
    }
    onSelectTab?.("diff");
  };
  const donePhaseCount = phases.filter(
    (phase) => phase.status === "done",
  ).length;
  const errorPhaseCount = phases.filter(
    (phase) => phase.status === "error",
  ).length;
  const runningPhase = phases.find(
    (phase) =>
      phase.status === "running" || phase.status === "waiting_approval",
  );
  const liveAgentCount = agentTiles.filter(
    (agent) => agent.status === "running" || agent.status === "pending",
  ).length;
  const hasLiveAgents = liveAgentCount > 0;
  const runActive = Boolean(runningPhase || hasLiveAgents);
  const hasTodoPlan =
    phases.length > 0 && blocks.some((block) => block.kind === "todo");
  const interruptedTaskIndex =
    terminalState === "interrupted" ? interruptedPhaseIndex(phases) : -1;
  const displayedDonePhaseCount =
    donePhaseCount -
    (interruptedTaskIndex >= 0 &&
    phases[interruptedTaskIndex]?.status === "done"
      ? 1
      : 0);
  const progressSectionLabel = hasTodoPlan
    ? t.todoList.title
    : t.agentWorkbenchPages.progress;
  const toggleSection = (section: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  };
  const showOutline =
    progressOutline !== undefined && progressOutline.length > 0;
  const outlineExecutionCount = showOutline
    ? progressOutline.reduce((sum, round) => sum + round.executionCount, 0)
    : 0;
  const outlineFactCount = showOutline
    ? progressOutline.reduce((sum, round) => sum + round.facts.length, 0)
    : 0;
  // 对话开始时喂入的上下文文件（上传文件/附件）——它们进入对话上下文但只出现在
  // 用户消息里，不属于 process 事件块，因此不会被 buildObservedReferenceTabs 收录。
  // 这里单独提取，归入 files 分类并计入上下文容量统计，避免"上下文统计只算网络搜索"。
  const inputReferenceItems = useMemo(() => {
    if (!userInput) return [] as ObservedReferenceItem[];
    const items: ObservedReferenceItem[] = [];
    for (const f of userInput.uploadedFiles ?? []) {
      items.push({
        id: `input:upload:${f.path || f.filename}`,
        title: compactReference(f.filename || f.path, 120),
        subtitle:
          f.path && f.path !== f.filename
            ? compactReference(f.path, 140)
            : undefined,
        tag: fileKindLabel(f.filename || f.path),
      });
    }
    for (const a of userInput.attachments ?? []) {
      if (!a.filename) continue;
      items.push({
        id: `input:attach:${a.filename}`,
        title: compactReference(a.filename, 120),
        tag: fileKindLabel(a.filename),
      });
    }
    return dedupeReferenceItems(items);
  }, [userInput]);
  const injectedGroundingItems = useMemo(
    () =>
      dedupeReferenceItems(
        groundingSources.map((source, index) => {
          const path = source.path.trim();
          const title = source.title.trim() || basename(path);
          return {
            id: `grounding:${index}:${path}`,
            title: compactReference(title || path, 120),
            subtitle:
              path && path !== title ? compactReference(path, 140) : undefined,
            tag: source.kind === "doc" ? "DOC" : fileKindLabel(path),
          };
        }),
      ),
    [groundingSources],
  );

  const observedReferenceTabs = useMemo<ObservedReferenceTab[]>(() => {
    const tabs = buildObservedReferenceTabs(
      preferStructuredReferences ? [] : blocks,
      t,
    );
    const directFiles = dedupeReferenceItems([
      ...injectedGroundingItems,
      ...inputReferenceItems,
    ]);
    if (directFiles.length === 0) return tabs;
    const filesTab = tabs.find((tab) => tab.id === "files");
    if (filesTab) {
      return tabs.map((tab) =>
        tab.id === "files"
          ? {
              ...tab,
              items: dedupeReferenceItems([...directFiles, ...tab.items]),
            }
          : tab,
      );
    }
    return [
      {
        id: "files",
        label: t.agentWorkbenchPages.reference.files,
        items: directFiles,
      },
      ...tabs,
    ];
  }, [
    blocks,
    t,
    injectedGroundingItems,
    inputReferenceItems,
    preferStructuredReferences,
  ]);
  const activeRefTab = observedReferenceTabs.some((tab) => tab.id === refTab)
    ? refTab
    : (observedReferenceTabs[0]?.id ?? "files");
  const activeRefItems =
    observedReferenceTabs.find((tab) => tab.id === activeRefTab)?.items ?? [];
  const activeRefMeta = OBSERVED_REFERENCE_META[activeRefTab];
  const ActiveRefIcon = activeRefMeta.Icon;
  const totalReferenceItems = observedReferenceTabs.reduce(
    (sum, tab) => sum + tab.items.length,
    0,
  );
  useEffect(() => {
    setExpandedSections((previous) => {
      const next = new Set(previous);
      if (runActive && hasTodoPlan) {
        // During a planned run, the current task is the primary surface.
        // Outputs and context remain one click away without competing for the
        // same vertical space.
        next.delete("artifacts");
        next.delete("references");
      } else if (runActive) {
        return previous;
      }
      // Progress section stays always expanded when workbench is open
      next.add("progress");
      if (diffEntries.length > 0) {
        next.add("artifacts");
      }
      if (
        next.size === previous.size &&
        [...next].every((section) => previous.has(section))
      ) {
        return previous;
      }
      return next;
    });
  }, [diffEntries.length, hasTodoPlan, runActive]);
  const agentHealth = useMemo(() => {
    const total = agentTiles.length;
    const done = agentTiles.filter((agent) => agent.status === "done").length;
    const running = agentTiles.filter(
      (agent) => agent.status === "running",
    ).length;
    const failed = agentTiles.filter(
      (agent) => agent.status === "error",
    ).length;
    const pending = agentTiles.filter(
      (agent) => agent.status === "pending",
    ).length;
    const failedLabels = agentTiles
      .filter((agent) => agent.status === "error")
      .map((agent) => agent.taskLabel ?? agent.name ?? agent.role ?? agent.id);
    return { done, failed, failedLabels, pending, running, total };
  }, [agentTiles]);

  const recoveredCount = blocks.filter(
    (block) => block.status === "warning",
  ).length;
  const latestRecovery = [...blocks]
    .reverse()
    .find((block) => block.status === "warning");
  const latestAttention = [...blocks]
    .reverse()
    .find(
      (block) =>
        block.status === "error" || block.status === "waiting_approval",
    );
  const unresolvedCount =
    errorPhaseCount +
    phases.filter((phase) => phase.status === "waiting_approval").length;
  // The plan and artifact sections already describe normal progress. Keep the
  // receipt for the exceptional information users may need to act on or audit.
  const showResultReceipt =
    !runActive && (recoveredCount > 0 || unresolvedCount > 0);

  // 上下文容量估算
  const contextStats = useMemo(() => {
    // 粗略估算：每4个字符约等于1个token
    const estimateTokens = (text: string) => Math.ceil(text.length / 4);
    let fileTokens = 0;
    let otherTokens = 0;
    const tokenByTab: Record<ObservedReferenceTabId, number> = {
      files: 0,
      web: 0,
      memory: 0,
      other: 0,
    };

    for (const block of preferStructuredReferences ? [] : blocks) {
      if (isAgentLifecycleBlock(block)) continue;
      const referenceItems = referenceItemsForBlock(block, t);
      if (referenceItems.length === 0) continue;

      const referenceTab = referenceTabForBlock(block);
      // Commands and generic tool status belong to their dedicated evidence
      // surfaces. Counting them as "other context" makes the source total
      // look larger than the evidence the user can actually inspect.
      if (!OBSERVED_REFERENCE_TABS.includes(referenceTab)) continue;

      const inputTokens = estimateTokens(block.inputText || "");
      const outputTokens = estimateTokens(block.outputText || "");
      const total = inputTokens + outputTokens;
      tokenByTab[referenceTab] += total;

      if (block.kind === "file" || block.kind === "read") {
        fileTokens += total;
      } else {
        otherTokens += total;
      }
    }

    // 对话开始时喂入的上下文文件（上传文件/附件）同样占用上下文，计入 files 统计。
    for (const item of inputReferenceItems) {
      const tokens =
        estimateTokens(item.title || "") + estimateTokens(item.subtitle || "");
      fileTokens += tokens;
      tokenByTab.files += tokens;
    }
    for (const item of injectedGroundingItems) {
      const tokens =
        estimateTokens(item.title || "") + estimateTokens(item.subtitle || "");
      fileTokens += tokens;
      tokenByTab.files += tokens;
    }

    const totalTokens = fileTokens + otherTokens;
    const visualWindow = 128000;
    const percentage =
      totalTokens > 0
        ? Math.max(
            1,
            Math.min(Math.round((totalTokens / visualWindow) * 100), 100),
          )
        : 0;
    const filePercentage =
      totalTokens > 0 ? Math.round((fileTokens / totalTokens) * 100) : 0;
    const otherPercentage = totalTokens > 0 ? 100 - filePercentage : 0;
    const segments = OBSERVED_REFERENCE_TABS.map((id) => ({
      id,
      label: t.agentWorkbenchPages.reference[id],
      percentage:
        totalTokens > 0
          ? Math.max(1, Math.round((tokenByTab[id] / totalTokens) * 100))
          : 0,
      tokens: tokenByTab[id],
    })).filter((segment) => segment.tokens > 0);

    return {
      totalTokens,
      percentage,
      filePercentage,
      otherPercentage,
      segments,
    };
  }, [
    blocks,
    t,
    injectedGroundingItems,
    inputReferenceItems,
    preferStructuredReferences,
  ]);

  // Prefer the same real conversation-window estimate used by the composer.
  // Falling back to observed reference text keeps the standalone workbench
  // useful in tests and embedded surfaces that do not own thread messages.
  const resolvedContextTokens = Math.max(
    0,
    contextTokens ?? contextStats.totalTokens,
  );
  const resolvedMaxContextTokens = Math.max(0, maxContextTokens ?? 128_000);
  const resolvedContextPercentage =
    resolvedMaxContextTokens > 0 && resolvedContextTokens > 0
      ? Math.max(
          1,
          Math.min(
            Math.round(
              (resolvedContextTokens / resolvedMaxContextTokens) * 100,
            ),
            100,
          ),
        )
      : 0;
  const showContextEstimate = resolvedContextPercentage >= 1;
  const formattedContextLimit = compactTokenCount(resolvedMaxContextTokens);
  const contextUsageLabel = t.agentWorkbenchPages.contextUsed(
    resolvedContextPercentage,
    formattedContextLimit,
  );

  const isCompletelyEmpty =
    !focusedProcessEvent &&
    !showOutline &&
    phases.length === 0 &&
    diffEntries.length === 0 &&
    agentTiles.length === 0 &&
    totalReferenceItems === 0;

  return (
    <div className="stable-scroll-viewport flex min-h-0 flex-1 flex-col overflow-y-auto bg-background/35">
      <div className="mx-auto w-full max-w-2xl px-5 py-4 pb-8">
        {/* 思考/执行详情均在对话框内完整展示，右侧不再重复渲染。
            The task plan stays visible even when a transcript process event is
            focused: selecting evidence must not erase the user's todo list. */}
        {(phases.length > 0 || showOutline) && (
          <section
            className="border-b border-border-subtle py-4"
            data-testid="workbench-task-plan"
          >
            <button
              type="button"
              aria-expanded={expandedSections.has("progress")}
              onClick={() => toggleSection("progress")}
              className="flex w-full items-center gap-2 text-left transition-colors hover:text-foreground"
              data-testid="workbench-task-plan-toggle"
            >
              <h3 className="text-xs font-medium text-foreground">
                {progressSectionLabel}
              </h3>
              <span className="ml-auto truncate text-xs text-muted-foreground">
                {terminalState === "blocked"
                  ? t.streaming.blockedOnUser
                  : terminalState === "interrupted"
                    ? phases.length > 0
                      ? `${displayedDonePhaseCount}/${phases.length} ${t.agentWorkbenchPages.statusDone} · ${t.backgroundTasks.interrupted}`
                      : t.backgroundTasks.interrupted
                    : terminalState === "failed"
                      ? t.agentWorkbench.statusError
                      : phases.length > 0
                        ? `${donePhaseCount}/${phases.length} ${phaseStatusText(
                            runningPhase ? "running" : "done",
                          )}${
                            hasLiveAgents
                              ? ` · ${t.agentWorkbenchPages.subagentsRunning(liveAgentCount)}`
                              : ""
                          }`
                        : t.agentWorkbenchPages.roundActivitySummary(
                            outlineExecutionCount,
                            outlineFactCount,
                          )}
                {phases.length > 0 && errorPhaseCount > 0
                  ? ` · ${errorPhaseCount} ${phaseStatusText("error")}`
                  : ""}
              </span>
              {expandedSections.has("progress") ? (
                <ChevronDownIcon className="size-3.5 text-muted-foreground" />
              ) : (
                <ChevronRightIcon className="size-3.5 text-muted-foreground" />
              )}
            </button>
            {expandedSections.has("progress") &&
              (phases.length > 0 ? (
                <ul
                  className={cn(
                    "stable-scroll-viewport mt-3 overflow-y-auto pr-0.5",
                    hasTodoPlan ? "max-h-72 space-y-0.5" : "space-y-1",
                  )}
                  data-testid={
                    hasTodoPlan
                      ? "workbench-todo-list"
                      : "workbench-progress-list"
                  }
                >
                  {phases.map((phase, index) => {
                    const displayStatus =
                      index === interruptedTaskIndex
                        ? ("warning" as const)
                        : phase.status;
                    const displayStatusText =
                      displayStatus === "warning"
                        ? t.backgroundTasks.interrupted
                        : phaseStatusText(displayStatus);
                    return (
                      <li
                        key={phase.id}
                        className={cn(
                          "flex gap-2",
                          hasTodoPlan
                            ? "-mx-2 min-h-8 items-start rounded-md px-2 py-1.5"
                            : "min-h-7 items-center",
                          hasTodoPlan &&
                            (displayStatus === "running" ||
                              displayStatus === "waiting_approval") &&
                            "bg-info/5",
                          displayStatus === "warning" && "bg-warning/5",
                        )}
                        data-task-status={displayStatus}
                      >
                        <StatusGlyph
                          status={displayStatus}
                          className={cn("size-3.5", hasTodoPlan && "mt-0.5")}
                        />
                        {!hasTodoPlan && (
                          <span className="shrink-0 rounded bg-muted/65 px-1.5 py-0.5 font-mono text-micro font-medium leading-4 text-muted-foreground">
                            P{index + 1}
                          </span>
                        )}
                        <span
                          className={cn(
                            "min-w-0 flex-1 text-xs",
                            hasTodoPlan
                              ? "line-clamp-2 leading-5"
                              : "truncate text-foreground",
                            hasTodoPlan &&
                              (displayStatus === "running" ||
                                displayStatus === "waiting_approval")
                              ? "font-medium text-foreground"
                              : hasTodoPlan
                                ? "text-muted-foreground"
                                : null,
                          )}
                          title={phase.title}
                        >
                          {agentPhaseDisplayTitle(phase, t.agentPhases)}
                        </span>
                        {hasTodoPlan ? (
                          <span className="sr-only">{displayStatusText}</span>
                        ) : (
                          <span className="shrink-0 text-mini text-muted-foreground">
                            {displayStatusText}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <ul className="mt-3 space-y-1">
                  {(progressOutline ?? []).map((round) => (
                    <li
                      key={round.iteration}
                      className="flex min-h-7 items-center gap-2"
                    >
                      <span className="size-1.5 shrink-0 rounded-full bg-muted-foreground/45" />
                      <span className="shrink-0 rounded bg-muted/65 px-1.5 py-0.5 font-mono text-micro font-medium leading-4 text-muted-foreground">
                        T{round.iteration}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                        {t.agentWorkbenchPages.roundActivitySummary(
                          round.executionCount,
                          round.facts.length,
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              ))}
          </section>
        )}

        {showResultReceipt && (
          <section
            className="border-b border-border-subtle py-4"
            data-testid="workbench-result-receipt"
          >
            <div className="flex items-start gap-3">
              <CheckCircle2Icon
                className={cn(
                  "mt-0.5 size-4 shrink-0",
                  unresolvedCount > 0 ? "text-warning" : "text-success",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-xs font-medium text-foreground">
                    {t.agentWorkbenchPages.resultReceipt}
                  </h3>
                  <span className="ml-auto text-micro text-muted-foreground">
                    {runActive
                      ? t.agentWorkbenchPages.statusRunning
                      : unresolvedCount > 0
                        ? t.agentWorkbenchPages.statusError
                        : t.agentWorkbenchPages.statusDone}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t.agentWorkbenchPages.resultReceiptDescription}
                </p>
                {latestAttention?.subtitle && (
                  <p className="mt-2 rounded-md border border-warning/20 bg-warning/5 px-2.5 py-2 text-xs leading-5 text-warning-foreground">
                    <span className="font-medium">
                      {t.agentWorkbenchPages.errorLabel}
                    </span>{" "}
                    {latestAttention.subtitle}
                  </p>
                )}
                {!latestAttention && latestRecovery?.subtitle && (
                  <p className="mt-2 rounded-md border border-success/15 bg-success/5 px-2.5 py-2 text-xs leading-5 text-muted-foreground">
                    <span className="font-medium text-success">
                      {t.agentWorkbenchPages.recoveredOperations(1)}
                    </span>{" "}
                    {latestRecovery.subtitle}
                  </p>
                )}
              </div>
            </div>
          </section>
        )}

        {/* 产物 */}
        {diffEntries.length > 0 && (
          <section className="border-b border-border-subtle py-4">
            <button
              type="button"
              aria-expanded={expandedSections.has("artifacts")}
              onClick={() => toggleSection("artifacts")}
              className="flex w-full items-center gap-2 text-left transition-colors hover:text-foreground"
            >
              <h3 className="text-xs font-medium text-foreground">
                {t.agentWorkbenchPages.artifacts}
              </h3>
              <span className="ml-auto text-xs text-muted-foreground">
                {artifactDiffEntries.length > 0
                  ? `${t.agentWorkbenchPages.generatedArtifacts} ${artifactDiffEntries.length}`
                  : ""}
                {artifactDiffEntries.length > 0 && changedFileEntries.length > 0
                  ? " · "
                  : ""}
                {changedFileEntries.length > 0
                  ? `${t.agentWorkbenchPages.changedFiles} ${changedFileEntries.length}`
                  : ""}
              </span>
              {expandedSections.has("artifacts") ? (
                <ChevronDownIcon className="size-3.5 text-muted-foreground" />
              ) : (
                <ChevronRightIcon className="size-3.5 text-muted-foreground" />
              )}
            </button>
            {expandedSections.has("artifacts") && (
              <div className="mt-3">
                {artifactDiffEntries.length > 0 && (
                  <>
                    <div className="sr-only">
                      {t.agentWorkbenchPages.generatedArtifacts}
                    </div>
                    <SummaryDiffEntryList
                      entries={artifactDiffEntries}
                      kind="artifact"
                      onOpenEntry={openDiffEntry}
                    />
                  </>
                )}
                {changedFileEntries.length > 0 && (
                  <>
                    <div
                      className={cn(
                        "mb-1 mt-3 flex items-center gap-1.5",
                        artifactDiffEntries.length > 0 &&
                          "border-t border-border-subtle pt-3",
                      )}
                    >
                      <span className="text-xs font-medium text-foreground">
                        {t.agentWorkbenchPages.changedFiles}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {changedFileEntries.length}
                      </span>
                    </div>
                    <SummaryDiffEntryList
                      entries={changedFileEntries}
                      kind="change"
                      onOpenEntry={openDiffEntry}
                    />
                  </>
                )}
              </div>
            )}
          </section>
        )}

        {/* 子智能体 */}
        {agentTiles.length > 0 && (
          <section className="border-b border-border-subtle py-4">
            <button
              type="button"
              aria-expanded={expandedSections.has("subagents")}
              onClick={() => toggleSection("subagents")}
              className="flex w-full items-center gap-2 text-left transition-colors hover:text-foreground"
            >
              <h3 className="text-xs font-medium text-foreground">
                {t.agentWorkbenchPages.subagents}
              </h3>
              <span className="ml-auto text-xs text-muted-foreground">
                {t.agentWorkbenchPages.subagentsCompleted(
                  agentHealth.done,
                  agentHealth.total,
                )}
              </span>
              {expandedSections.has("subagents") ? (
                <ChevronDownIcon className="size-3.5 text-muted-foreground" />
              ) : (
                <ChevronRightIcon className="size-3.5 text-muted-foreground" />
              )}
            </button>
            {expandedSections.has("subagents") ? (
              <div className="mt-3">
                <div
                  className={cn(
                    "pb-2 text-xs",
                    agentHealth.failed > 0 && "text-destructive",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <BrainCircuitIcon
                      className={cn(
                        "size-3.5 shrink-0",
                        agentHealth.failed > 0
                          ? "text-destructive"
                          : "text-success",
                      )}
                    />
                    <span className="font-medium text-foreground">
                      {t.agentWorkbenchPages.subagentsCompleted(
                        agentHealth.done,
                        agentHealth.total,
                      )}
                    </span>
                    {agentHealth.failed > 0 && (
                      <span className="font-medium text-destructive">
                        {t.agentWorkbenchPages.subagentsFailed(
                          agentHealth.failed,
                        )}
                      </span>
                    )}
                    {agentHealth.running > 0 && (
                      <span className="text-muted-foreground">
                        {t.agentWorkbenchPages.subagentsRunning(
                          agentHealth.running,
                        )}
                      </span>
                    )}
                    {agentHealth.pending > 0 && (
                      <span className="text-muted-foreground">
                        {t.agentWorkbenchPages.subagentsPending(
                          agentHealth.pending,
                        )}
                      </span>
                    )}
                  </div>
                  {agentHealth.failedLabels.length > 0 && (
                    <div className="mt-1 line-clamp-2 text-destructive/90">
                      {t.agentWorkbenchPages.failedLanes(
                        agentHealth.failedLabels.join(" / "),
                      )}
                    </div>
                  )}
                </div>
                <ul className="space-y-3">
                  {agentTiles.map((tile) => (
                    <SummaryAgentRow key={tile.id} tile={tile} />
                  ))}
                </ul>
              </div>
            ) : (
              <div className="mt-2 text-xs text-muted-foreground">
                {agentHealth.running > 0
                  ? t.agentWorkbenchPages.subagentsRunning(agentHealth.running)
                  : agentHealth.failed > 0
                    ? t.agentWorkbenchPages.subagentsFailed(agentHealth.failed)
                    : t.agentWorkbenchPages.subagentsCompleted(
                        agentHealth.done,
                        agentHealth.total,
                      )}
              </div>
            )}
          </section>
        )}

        {/* 上下文（只展示本轮事件流里可确认的内容） */}
        {!isCompletelyEmpty && (
          <section className="py-4">
            <div className="flex items-center gap-2">
              <button
                type="button"
                aria-expanded={expandedSections.has("references")}
                onClick={() => toggleSection("references")}
                className="flex min-w-0 flex-1 items-center gap-1.5 text-left transition-colors hover:text-foreground"
              >
                <h3 className="text-xs font-medium text-foreground">
                  {t.agentWorkbenchPages.context}
                </h3>
                {expandedSections.has("references") ? (
                  <ChevronDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
                )}
              </button>
              <Tooltip delayDuration={200}>
                <TooltipTrigger asChild>
                  <span
                    role="img"
                    tabIndex={0}
                    aria-label={t.agentWorkbenchPages.contextDescription}
                    className="flex size-5 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                  >
                    <InfoIcon className="size-3.5" />
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" align="start">
                  {t.agentWorkbenchPages.contextDescription}
                </TooltipContent>
              </Tooltip>
              {!expandedSections.has("references") ? (
                <span className="ml-auto truncate text-xs text-muted-foreground">
                  {totalReferenceItems > 0
                    ? t.agentWorkbenchPages.sourceCount(totalReferenceItems)
                    : t.agentWorkbenchPages.noSources}
                  {showContextEstimate
                    ? ` · ${resolvedContextPercentage}%`
                    : ""}
                </span>
              ) : onCompressContext ? (
                <button
                  type="button"
                  onClick={() => void onCompressContext()}
                  disabled={isCompressingContext || resolvedContextTokens <= 0}
                  className="ml-auto h-7 shrink-0 rounded-md bg-muted px-3 text-xs text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground disabled:cursor-default disabled:opacity-50"
                >
                  {isCompressingContext
                    ? t.contextCompressor.compressing
                    : t.agentWorkbenchPages.contextCompress}
                </button>
              ) : null}
            </div>
            {expandedSections.has("references") && (
              <div className="mt-3">
                {/* Real context-window usage. The colored source segments fill
                    only the occupied portion; the remainder is capacity. */}
                <div>
                  {showContextEstimate ? (
                    <div className="mb-3 flex items-center gap-2">
                      <div
                        className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full border border-border-subtle bg-background"
                        data-testid="workbench-context-usage-bar"
                      >
                        <div
                          className="flex h-full overflow-hidden rounded-full bg-muted-foreground/20"
                          style={{ width: `${resolvedContextPercentage}%` }}
                        >
                          {contextStats.segments.length === 0 ? (
                            <div className="h-full w-full bg-muted-foreground/30" />
                          ) : (
                            contextStats.segments.map((segment) => (
                              <div
                                key={segment.id}
                                className={cn(
                                  "h-full transition-all",
                                  OBSERVED_REFERENCE_META[segment.id]
                                    .barClassName,
                                )}
                                style={{ width: `${segment.percentage}%` }}
                                title={`${segment.label} ${t.agentWorkbenchPages.estimatedTokens(segment.tokens)}`}
                              />
                            ))
                          )}
                        </div>
                      </div>
                      <Tooltip delayDuration={200}>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            aria-label={contextUsageLabel}
                            className="shrink-0 font-mono text-xs text-foreground"
                          >
                            {resolvedContextPercentage}%
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="top" align="end">
                          {contextUsageLabel}
                        </TooltipContent>
                      </Tooltip>
                    </div>
                  ) : null}
                  <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    {observedReferenceTabs.length === 0 ? (
                      <span className="text-muted-foreground">
                        {t.agentWorkbenchPages.noSources}
                      </span>
                    ) : (
                      observedReferenceTabs.map((tab) => {
                        const meta = OBSERVED_REFERENCE_META[tab.id];
                        const isActive = activeRefTab === tab.id;
                        return (
                          <button
                            key={tab.id}
                            type="button"
                            aria-label={t.agentWorkbenchPages.sourceCountWithLabel(
                              tab.label,
                              tab.items.length,
                            )}
                            onClick={() => setRefTab(tab.id)}
                            className={cn(
                              "inline-flex shrink-0 items-center gap-1.5 border-b pb-1 transition-colors",
                              isActive
                                ? "border-foreground/70 font-medium text-foreground"
                                : "border-transparent text-muted-foreground hover:text-foreground",
                            )}
                          >
                            <span
                              className={cn(
                                "inline-block size-2 rounded-sm transition-all",
                                meta.dotClassName,
                              )}
                            />
                            <span>{tab.label}</span>
                          </button>
                        );
                      })
                    )}
                  </div>
                </div>
                {/* 上下文列表 */}
                <ul className="stable-scroll-viewport mt-2 max-h-64 space-y-1 overflow-y-auto pr-0.5">
                  {observedReferenceTabs.length === 0 ? (
                    <li className="py-4 text-center text-xs text-muted-foreground">
                      {t.agentWorkbenchPages.noObservableReferences}
                    </li>
                  ) : (
                    activeRefItems.map((ref) => {
                      const row = (
                        <>
                          <ReferenceIcon
                            fallbackClassName={activeRefMeta.iconClassName}
                            Icon={ActiveRefIcon}
                            item={ref}
                            tabId={activeRefTab}
                          />
                          <div className="min-w-0 flex-1">
                            <span className="block truncate text-xs text-foreground">
                              {ref.title}
                            </span>
                          </div>
                          {ref.tag && (
                            <span className="max-w-28 shrink-0 truncate text-xs text-muted-foreground/70">
                              {ref.tag}
                            </span>
                          )}
                          {ref.url && (
                            <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground/35 transition-colors group-hover:text-muted-foreground" />
                          )}
                        </>
                      );

                      return (
                        <li key={ref.id}>
                          {ref.url ? (
                            <RoutedWebLink
                              href={ref.url}
                              openTargetSource="agent-reference"
                              title={ref.subtitle || ref.url}
                              className="group -mx-1 flex min-h-9 items-center gap-3 rounded-md px-1 py-1.5 transition-colors hover:bg-muted/45 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                            >
                              {row}
                            </RoutedWebLink>
                          ) : (
                            <div className="-mx-1 flex min-h-9 items-center gap-3 px-1 py-1.5">
                              {row}
                            </div>
                          )}
                        </li>
                      );
                    })
                  )}
                </ul>
              </div>
            )}
          </section>
        )}

        {/* 空状态 */}
        {isCompletelyEmpty && (
          <div className="flex flex-col items-center justify-center px-4 py-8 text-center">
            <BotIcon className="mb-2 size-8 text-muted-foreground/50" />
            <p className="text-xs font-medium text-foreground">
              {t.agentWorkbenchPages.dashboardOverview}
            </p>
            <p className="mt-1 max-w-[var(--text-truncate-xl)] text-xs text-muted-foreground">
              {t.agentWorkbenchPages.dashboardOverviewDescription}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function AgentCreationCard({
  agent,
  agentStatusClass,
  agentStatusLabel,
}: {
  agent: AgentTile;
  agentStatusClass: (status: AgentTile["status"]) => string;
  agentStatusLabel: (status: AgentTile["status"]) => string;
}) {
  const { t } = useI18n();
  const [showBrief, setShowBrief] = useState(false);
  const displayName = agent.codename ?? agent.name;
  // The role's name/说明 come from the backend built-in role catalog
  // (BUILTIN_ROLES) when the spawn resolved to a known role; free-form role
  // labels fall back to the frontend name/description maps. The delegated
  // brief (task/prompt) is work info — only a last-resort fill so the card
  // never renders empty.
  const roleName =
    agent.roleDisplayName ?? friendlyRoleName(agent.role ?? agent.name);
  const roleBrief =
    agent.roleDescription ?? roleDescription(agent.role ?? agent.name);
  const fullBrief =
    roleBrief ?? agent.prompt ?? agent.task ?? agent.lastThought ?? "";
  const motto =
    roleBrief ??
    agent.task ??
    agent.prompt ??
    agent.lastThought ??
    agent.currentTool ??
    t.agentWorkbenchPages.defaultMotto;
  const active = agent.status === "running";
  const waiting = agent.status === "waiting_approval";

  return (
    <section className="overflow-hidden rounded-lg border border-border-default bg-background shadow-[var(--shadow-xs)]">
      <div className="flex items-center justify-center border-b border-border-subtle px-3 py-2 text-sm font-medium text-muted-foreground">
        {t.agentWorkbenchPages.agentClusterCreateAssistant}
      </div>
      <div className="flex justify-center overflow-hidden bg-[color:color-mix(in_oklch,var(--muted)_38%,var(--background))] px-7 pb-8 pt-16">
        <div className="relative w-full max-w-sm rotate-[3deg] transition-transform duration-slow hover:rotate-0">
          <div
            aria-hidden="true"
            className="absolute -top-20 left-1/2 z-10 h-24 w-12 -translate-x-1/2 rounded-b-lg border-x border-border-default bg-foreground shadow-[var(--shadow-xs)]"
          />
          <div
            aria-hidden="true"
            className="absolute -top-3 left-1/2 z-20 h-5 w-16 -translate-x-1/2 rounded-full border-4 border-foreground bg-background"
          />
          <div className="relative min-h-[28rem] rounded-lg border border-border-default bg-background px-5 py-5">
            {showBrief ? (
              <div className="flex h-full min-h-[25rem] flex-col">
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setShowBrief(false)}
                    className="flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    aria-label={t.agentWorkbenchPages.backToRoleCard}
                    title={t.agentWorkbenchPages.backToRoleCard}
                  >
                    <ChevronRightIcon className="size-4 rotate-180" />
                  </button>
                  <div className="min-w-0 flex-1 text-center text-xl font-semibold text-foreground">
                    {t.agentWorkbenchPages.roleDescription}
                  </div>
                  <span className="w-8" aria-hidden="true" />
                </div>
                <div className="mt-6 flex items-center gap-3 rounded-lg bg-muted/35 px-3 py-2">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-background text-xl">
                    {agent.avatar || (
                      <BotIcon className="size-5 text-muted-foreground" />
                    )}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-foreground">
                      {displayName}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {roleName}
                    </div>
                  </div>
                  <span className="ml-auto font-mono text-sm text-foreground">
                    {agent.label}
                  </span>
                </div>
                <div className="mt-5 max-h-72 flex-1 overflow-y-auto whitespace-pre-wrap break-words text-base leading-7 text-foreground">
                  {fullBrief || t.agentWorkbenchPages.noFullRoleDescription}
                </div>
              </div>
            ) : (
              <>
                <div className="rounded-md bg-foreground px-3 py-2 text-base font-semibold text-background">
                  {displayName}
                </div>
                <div className="mt-12 flex size-28 items-center justify-center rounded-lg border border-border bg-muted/20">
                  {agent.avatar ? (
                    <span className="text-5xl" aria-hidden="true">
                      {agent.avatar}
                    </span>
                  ) : (
                    <BotIcon className="size-14 text-foreground" />
                  )}
                </div>
                <div className="mt-8 text-2xl font-semibold tracking-normal text-foreground">
                  {roleName}
                </div>
                <p className="mt-3 line-clamp-3 text-base leading-7 text-muted-foreground">
                  {motto}
                </p>
                <div className="my-5 border-t border-dashed border-border" />
                <div className="flex items-end gap-3">
                  <div className="text-3xl font-bold tracking-normal text-foreground">
                    ECHO
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowBrief(true)}
                    className="ml-auto rounded-md bg-foreground px-2.5 py-1.5 text-xs font-medium text-background transition-opacity hover:opacity-85"
                    title={t.agentWorkbenchPages.roleDescription}
                  >
                    {t.agentWorkbenchPages.roleDescription}
                  </button>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
                      agentStatusClass(agent.status),
                      active
                        ? "bg-success/10"
                        : waiting
                          ? "bg-warning/10"
                          : "bg-muted",
                    )}
                  >
                    {active && <Loader2Icon className="size-3 animate-spin" />}
                    {agentStatusLabel(agent.status)}
                  </span>
                  {agent.iterationCount !== undefined && (
                    <span className="text-xs text-muted-foreground">
                      {t.agentWorkbenchPages.iterationRound(
                        agent.iterationCount,
                      )}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

export function isAgentCreationBlock(
  block: WorkBlock | null | undefined,
): boolean {
  const event = block?.event;
  return Boolean(
    event &&
    (event.lifecycle === "spawned" || /subagent_spawned/i.test(event.name)),
  );
}

export function isAgentLifecycleBlock(block: WorkBlock): boolean {
  return Boolean(
    block.event.lifecycle ||
    /subagent_(spawned|finished)/i.test(block.event.name),
  );
}

export function pickCurrentScreenFrame(blocks: WorkBlock[]): WorkBlock | null {
  const newestFirst = [...blocks].reverse();
  return (
    newestFirst.find(
      (block) =>
        !isAgentLifecycleBlock(block) &&
        (block.status === "running" || block.status === "waiting_approval"),
    ) ??
    newestFirst.find((block) => !isAgentLifecycleBlock(block)) ??
    pickCurrentWorkBlock(blocks) ??
    blocks[blocks.length - 1] ??
    null
  );
}

export function agentTileForBlock(
  block: WorkBlock | null | undefined,
  agents: AgentTile[],
): AgentTile | undefined {
  const event = block?.event;
  if (!event) return undefined;
  const id = agentEventGroupId(event);
  return agents.find(
    (agent) =>
      agent.id === id ||
      agent.id === event.agentId ||
      agent.codename === event.subagentCodename ||
      agent.name === event.agentName ||
      agent.role === event.subAgentRole,
  );
}

export function findAgentTileByFocusId(
  focusId: string,
  agents: AgentTile[],
): AgentTile | undefined {
  return agents.find((agent) =>
    [
      agent.id,
      agent.codename,
      agent.name,
      agent.role,
      agent.parentToolUseId,
      agent.label,
    ].some((value) => value === focusId),
  );
}

export function friendlyRoleName(role: string | undefined | null): string {
  const value = role?.trim();
  if (!value) return "Task Agent";
  const lower = value.toLowerCase();
  const map: Record<string, string> = {
    architect: "Architect",
    critic: "Reviewer",
    debugger: "Debugger",
    designer: "Designer",
    implementer: "Builder",
    planner: "Planner",
    researcher: "Researcher",
    reviewer: "Reviewer",
    security: "Security Reviewer",
    synthesizer: "Synthesizer",
    writer: "Writer",
  };
  return map[lower] ?? value.replace(/[_-]+/g, " ");
}

export function DiffText({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  return (
    <pre className="max-h-[22rem] overflow-auto whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-xs leading-5 text-foreground/80">
      {lines.map((line, index) => (
        <span
          key={`${index}-${line}`}
          className={cn(
            "block min-h-5",
            line.startsWith("+") &&
              !line.startsWith("+++") &&
              "bg-success/10 text-success",
            line.startsWith("-") &&
              !line.startsWith("---") &&
              "bg-destructive/10 text-destructive",
            (line.startsWith("@@") ||
              line.startsWith("diff --git") ||
              line.startsWith("+++") ||
              line.startsWith("---")) &&
              "text-muted-foreground",
          )}
        >
          {line || " "}
        </span>
      ))}
    </pre>
  );
}

export function AgentDiffPage({
  entries,
  onBackToSummary,
}: {
  entries: DiffEntry[];
  onBackToSummary?: () => void;
}) {
  const { t } = useI18n();
  if (entries.length === 0) {
    return (
      <WorkbenchEmptyPage
        title={DIFF_TAB_LABEL}
        description={t.agentWorkbenchPages.noDiffEntriesDescription}
      />
    );
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-background/70 p-3">
      <div className="mx-auto w-full max-w-2xl space-y-3">
        {onBackToSummary && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onBackToSummary}
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
            >
              <ArrowLeftIcon className="size-3.5" />
              {t.agentWorkbenchPages.dashboardOverview}
            </button>
            <span className="min-w-0 truncate text-xs text-muted-foreground">
              {DIFF_TAB_LABEL}
            </span>
          </div>
        )}
        {entries.map((entry) => (
          <section
            key={entry.id}
            className="overflow-hidden rounded-lg border border-border-default bg-background/85 shadow-[var(--shadow-xs)]"
          >
            <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
              <StatusGlyph status={entry.status} />
              <GitBranchIcon className="size-4 shrink-0 text-muted-foreground" />
              <span
                className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground"
                title={entry.path}
              >
                {basename(entry.title || entry.path)}
              </span>
            </div>
            <DiffText text={entry.text} />
          </section>
        ))}
      </div>
    </div>
  );
}
