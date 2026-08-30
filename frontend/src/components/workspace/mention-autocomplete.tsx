/**
 * Mention Autocomplete — @ context references in chat input.
 *
 * Provides a dropdown that appears when the user types "@" in the chat input,
 * with categories for files, symbols, docs, web, git, folders, and terminal.
 *
 * Features:
 * - Trigger: typing "@" at word boundary
 * - Category browsing: shows all categories when no filter typed
 * - Type-specific search: "@file:main" searches files, "@symbol:My" searches symbols
 * - Fuzzy matching on partial input after @
 * - Keyboard navigation (ArrowUp/Down, Enter to select, Escape to close, Tab to complete)
 * - Selected mentions inserted as @type:reference tokens
 * - Debounced API calls for responsive autocomplete
 */

import { swallow } from "@/core/utils/log";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  BookOpenIcon,
  BotIcon,
  BracesIcon,
  DatabaseIcon,
  FileIcon,
  FolderIcon,
  GitBranchIcon,
  GlobeIcon,
  HistoryIcon,
  Loader2Icon,
  PackageIcon,
  PuzzleIcon,
  TerminalIcon,
  ZapIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { cn } from "@/lib/utils";

// ============================================================================
// Types
// ============================================================================

export interface MentionItem {
  type: string;
  label: string;
  value: string;
  description: string;
  icon: string;
  /** Real avatar image (group members) — rendered instead of the icon. */
  avatarUrl?: string;
}

export type MentionCategory =
  | "file"
  | "symbol"
  | "folder"
  | "git"
  | "docs"
  | "web"
  | "terminal"
  | "agent"
  | "plugin"
  | "skill"
  | "pack"
  | "session";

const CATEGORY_ICONS: Record<string, React.ElementType> = {
  file: FileIcon,
  symbol: BracesIcon,
  folder: FolderIcon,
  "git-branch": GitBranchIcon,
  "git-commit": GitBranchIcon,
  book: BookOpenIcon,
  globe: GlobeIcon,
  terminal: TerminalIcon,
  braces: BracesIcon,
  agent: BotIcon,
  plugin: PuzzleIcon,
  skill: ZapIcon,
  pack: PackageIcon,
  database: DatabaseIcon,
  session: HistoryIcon,
};

const CATEGORY_COLORS: Record<string, string> = {
  file: "text-info",
  symbol: "text-chart-1",
  folder: "text-warning",
  git: "text-chart-7",
  docs: "text-success",
  web: "text-info",
  terminal: "text-muted-foreground",
  agent: "text-chart-6",
  plugin: "text-chart-3",
  skill: "text-warning",
  pack: "text-chart-5",
  database: "text-success",
  session: "text-chart-2",
};

// Echo Native mention encoding — canonical ``echo-session:`` URI of the
// session id (base64url of ASCII-escaped JSON), mirrored from
// ``session_reference_uri.py`` so the backend's strict canonical re-encode
// check accepts what the UI inserts.
function ensureAsciiJson(value: string): string {
  const json = JSON.stringify(value);
  let out = "";
  for (const ch of json) {
    const code = ch.charCodeAt(0);
    // Keep JSON's short escapes for control chars; escape anything ≥ 0x7F
    // as \uXXXX (surrogate pairs stay paired, matching Python's
    // ``json.dumps(..., ensure_ascii=True)``).
    out += code < 0x7f ? ch : `\\u${code.toString(16).padStart(4, "0")}`;
  }
  return out;
}

export function encodeSessionReferenceUri(sessionId: string): string {
  const bytes = new TextEncoder().encode(ensureAsciiJson(sessionId));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const payload = btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `echo-session:${payload}`;
}

function escapeMentionLabel(label: string): string {
  return label.replace(/[\\\]]/g, (ch) => `\\${ch}`);
}

export function formatSessionReferenceMention(
  sessionId: string,
  label?: string,
): string {
  const safeLabel = escapeMentionLabel(label ?? sessionId);
  return `@[${safeLabel}](${encodeSessionReferenceUri(sessionId)})`;
}

const SESSION_CATEGORY_ITEM: MentionItem = {
  type: "session",
  label: "会话",
  value: "session:",
  description: "引用此前子代理会话的转录,只读注入上下文",
  icon: "session",
};

type SessionAutocomplete =
  | { mode: "none" }
  | { mode: "category-only" }
  | { mode: "candidates"; search: string };

/** Classify an @-query into the session lane: prefix → category row,
 * ``session:`` → live candidates from the backend seam. */
function sessionAutocompleteState(query: string): SessionAutocomplete {
  const q = query.trim().toLowerCase();
  if (q.startsWith("session:")) {
    return { mode: "candidates", search: q.slice("session:".length).trim() };
  }
  if (q.length <= "session".length && "session".startsWith(q) && q !== "") {
    return { mode: "category-only" };
  }
  return { mode: "none" };
}

async function fetchSessionCandidates(
  search: string,
  target: string | undefined,
  signal: AbortSignal,
): Promise<MentionItem[]> {
  const params = new URLSearchParams({ query: search, limit: "20" });
  if (target) params.set("target", target);
  try {
    const res = await fetch(`${BASE_URL}/api/subagents/sessions?${params}`, {
      signal,
    });
    if (!res.ok) return [];
    const data = await res.json();
    const candidates = Array.isArray(data.candidates) ? data.candidates : [];
    return candidates.map(
      (candidate: { session_id?: string; label?: string }) => ({
        type: "session",
        label: candidate.label || candidate.session_id || "会话",
        value: candidate.session_id || "",
        description: `会话 ${candidate.session_id || ""}`,
        icon: "session",
      }),
    );
  } catch (e) {
    swallow(e);
    return [];
  }
}

const LOCAL_FILE_AGENT_MENTION: MentionItem = {
  type: "agent",
  label: "本地数据库",
  value: "本地数据库",
  description: "只在本机索引和检索授权资料，确认后再带入任务上下文",
  icon: "database",
};

function withLocalFileAgentMention(
  query: string,
  results: MentionItem[],
): MentionItem[] {
  const normalized = query.trim().toLowerCase();
  const shouldShow =
    normalized === "" ||
    "本地数据库".includes(normalized) ||
    "私域资料库".includes(normalized) ||
    "本地资料官".includes(normalized) ||
    "local database".includes(normalized) ||
    "private storage".includes(normalized) ||
    "local file agent".includes(normalized) ||
    "nas".includes(normalized) ||
    "storage".includes(normalized) ||
    normalized === "agent:" ||
    normalized.startsWith("agent:本地数据库") ||
    normalized.startsWith("agent:私域") ||
    normalized.startsWith("agent:本地") ||
    normalized.startsWith("agent:local");
  if (!shouldShow) return results;
  if (results.some((item) => item.value === LOCAL_FILE_AGENT_MENTION.value)) {
    return results;
  }
  return [LOCAL_FILE_AGENT_MENTION, ...results];
}

/** A team member offered by the @ typeahead (group roster). */
export interface MentionMemberInput {
  name: string;
  display_name?: string | null;
  icon?: string | null;
  description?: string | null;
  avatar_url?: string | null;
  /** Optional stable token inserted after `@` (for example `agent:planner`). */
  mention_value?: string | null;
}

/** Resolve an agent avatar_url to an absolute, cache-busted src. */
function resolveMentionAvatar(url?: string | null): string | undefined {
  if (!url) return undefined;
  return url.startsWith("http") ? url : `${getBackendBaseURL()}${url}`;
}

/** Prepend matching group members so typing "@" in a team room summons the
 * roster inline — no hard button needed. */
function withMemberMentions(
  query: string,
  members: MentionMemberInput[],
  results: MentionItem[],
): MentionItem[] {
  if (!members.length) return results;
  const q = query.trim().toLowerCase();
  // WeChat-style: a team @ is for mentioning people, so drop the tool/skill
  // category noise (the "Bundled skills:" packs and bare category rows). Keep
  // people + 本地数据库 + any concrete file/symbol hits the user typed.
  const peopleFocused = results.filter(
    (r) => !/^Bundled skills:/.test(String(r.description ?? "")),
  );
  results = peopleFocused;
  const existing = new Set(results.map((r) => r.value));
  const matched: MentionItem[] = members
    .filter((m) => {
      const dn = (m.display_name ?? m.name).toLowerCase();
      return (
        q === "" ||
        q === "agent:" ||
        dn.includes(q) ||
        m.name.toLowerCase().includes(q) ||
        q.startsWith(`agent:${dn}`)
      );
    })
    .map((m) => {
      const label = m.display_name ?? m.name;
      const rawAvatar = m.avatar_url ?? null;
      return {
        type: "agent",
        label,
        value: m.mention_value?.trim() || label,
        description: m.description || "群成员",
        icon: m.icon?.trim() || "bot",
        avatarUrl: resolveMentionAvatar(
          rawAvatar ? withAgentAvatarVersion(rawAvatar) : null,
        ),
      };
    })
    .filter((m) => !existing.has(m.value));

  // WeChat/DingTalk style: "@所有人" first, to address the whole group.
  const showAll =
    q === "" ||
    "所有人".includes(q) ||
    "全员".includes(q) ||
    "everyone".includes(q) ||
    "all".includes(q);
  const everyone: MentionItem[] =
    showAll && !existing.has("所有人")
      ? [
          {
            type: "agent",
            label: "所有人",
            value: "所有人",
            description: "通知群里全体成员",
            icon: "users",
          },
        ]
      : [];
  return [...everyone, ...matched, ...results];
}

// ============================================================================
// API
// ============================================================================

const BASE_URL = getBackendBaseURL();

async function fetchMentionAutocomplete(
  query: string,
  workspace: string,
  signal?: AbortSignal,
  threadId?: string,
  actor?: string,
): Promise<MentionItem[]> {
  const params = new URLSearchParams({
    q: query,
    workspace,
    limit: "20",
  });
  if (threadId) params.set("thread_id", threadId);
  if (actor) params.set("actor", actor);
  try {
    const res = await fetch(`${BASE_URL}/api/mentions/autocomplete?${params}`, {
      signal,
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.items ?? [];
  } catch (e) {
    swallow(e);
    return [];
  }
}

// ============================================================================
// Hook: useMentionAutocomplete
// ============================================================================

interface UseMentionAutocompleteOptions {
  value: string;
  onChange: (value: string) => void;
  workDir?: string;
  threadId?: string;
  actor?: string;
  /** Group members offered first when typing "@" in a team room. */
  members?: MentionMemberInput[];
  /** Debounce delay in ms for API calls. Default 150. */
  debounceMs?: number;
}

export function useMentionAutocomplete({
  value,
  onChange,
  workDir,
  threadId,
  actor,
  members,
  debounceMs = 150,
}: UseMentionAutocompleteOptions) {
  // Stable handle to the latest roster so the fetch effect can read it
  // without re-running on every render (members is a fresh array each time).
  const membersRef = useRef<MentionMemberInput[]>(members ?? []);
  membersRef.current = members ?? [];
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [items, setItems] = useState<MentionItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");

  // Track the position of the @ trigger in the input value
  const triggerPosRef = useRef(-1);
  const abortRef = useRef<AbortController | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---- IME/paste-robust trigger ----
  // The keydown handler only opens the popup when the browser reports
  // key === "@". Under a Chinese IME (where "@" is Shift+2 routed through
  // composition) that key never arrives, so typing "@" did nothing. As a
  // fallback, open from the *value*: if it ends with an "@mention" at a word
  // boundary, treat that "@" as the trigger.
  useEffect(() => {
    if (isOpen) return;
    const match = /(?:^|[\s\n\t])@([^\s@]*)$/.exec(value);
    if (!match) return;
    const atPos = value.length - (match[1] ?? "").length - 1;
    if (atPos < 0 || value[atPos] !== "@") return;
    triggerPosRef.current = atPos;
    setIsOpen(true);
  }, [value, isOpen]);

  // ---- Detect @ trigger and extract query ----
  useEffect(() => {
    if (!isOpen) return;

    const atPos = triggerPosRef.current;
    if (atPos < 0 || atPos >= value.length) {
      setIsOpen(false);
      setMentionQuery("");
      return;
    }

    // Check that the @ is still at the trigger position
    if (value[atPos] !== "@") {
      setIsOpen(false);
      setMentionQuery("");
      return;
    }

    // Extract query: everything after @ until whitespace or end
    // But for @web: queries, allow spaces (handle specially in the parser)
    const afterAt = value.slice(atPos + 1);

    // If the user typed a newline after @, close
    if (afterAt.includes("\n")) {
      setIsOpen(false);
      setMentionQuery("");
      return;
    }

    // For typed mention with colon (e.g., "@file:main"), pass the whole thing
    // For category-level (e.g., "@fi"), just pass the partial
    const colonIdx = afterAt.indexOf(":");
    if (colonIdx !== -1) {
      // Has a type prefix — allow the full typed text as the query
      const typeStr = afterAt.slice(0, colonIdx);
      const validTypes = [
        "file",
        "symbol",
        "folder",
        "git",
        "docs",
        "web",
        "terminal",
        "agent",
        "plugin",
        "skill",
        "pack",
        "session",
      ];
      if (validTypes.includes(typeStr.toLowerCase())) {
        // For @web: allow spaces in the query
        if (
          typeStr.toLowerCase() === "web" ||
          typeStr.toLowerCase() === "docs"
        ) {
          setMentionQuery(afterAt);
        } else {
          // For other types, stop at first space after colon
          const afterColon = afterAt.slice(colonIdx + 1);
          const spaceIdx = afterColon.indexOf(" ");
          if (spaceIdx !== -1) {
            // User moved past the mention — close it
            setIsOpen(false);
            setMentionQuery("");
            return;
          }
          setMentionQuery(afterAt);
        }
      } else {
        // Not a valid type — treat as category filter
        const spaceIdx = afterAt.indexOf(" ");
        if (spaceIdx !== -1) {
          setIsOpen(false);
          setMentionQuery("");
          return;
        }
        setMentionQuery(afterAt);
      }
    } else {
      // No colon yet — partial category name like "fi"
      const spaceIdx = afterAt.indexOf(" ");
      if (spaceIdx !== -1) {
        setIsOpen(false);
        setMentionQuery("");
        return;
      }
      setMentionQuery(afterAt);
    }
  }, [value, isOpen]);

  // ---- Fetch autocomplete results (debounced) ----
  useEffect(() => {
    if (!isOpen) {
      setItems([]);
      return;
    }

    // Cancel previous request
    abortRef.current?.abort();
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    setIsLoading(true);

    debounceTimerRef.current = setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;

      Promise.all([
        fetchMentionAutocomplete(
          mentionQuery,
          workDir || ".",
          controller.signal,
          threadId,
          actor,
        ),
        (async () => {
          const state = sessionAutocompleteState(mentionQuery);
          if (state.mode === "candidates") {
            return fetchSessionCandidates(
              state.search,
              threadId,
              controller.signal,
            );
          }
          return [];
        })(),
      ])
        .then(([genericResults, sessionResults]) => {
          if (controller.signal.aborted) return;
          const state = sessionAutocompleteState(mentionQuery);
          let merged = genericResults;
          if (state.mode !== "none") {
            merged = [
              SESSION_CATEGORY_ITEM,
              ...sessionResults,
              ...genericResults,
            ];
          }
          setItems(
            withMemberMentions(
              mentionQuery,
              membersRef.current,
              withLocalFileAgentMention(mentionQuery, merged),
            ),
          );
          setSelectedIndex(0);
          setIsLoading(false);
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            // Members still resolve locally even if the backend mention API
            // is unreachable.
            setItems(withMemberMentions(mentionQuery, membersRef.current, []));
            setIsLoading(false);
          }
        });
    }, debounceMs);

    return () => {
      abortRef.current?.abort();
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [isOpen, mentionQuery, workDir, threadId, actor, debounceMs]);

  // ---- Reset selected index when items change ----
  useEffect(() => {
    setSelectedIndex(0);
  }, [items]);

  // ---- Select an item ----
  const selectItem = useCallback(
    (item: MentionItem) => {
      const atPos = triggerPosRef.current;
      if (atPos < 0) return;

      // Build the mention text to insert. Session candidates insert the
      // host-neutral canonical mention ``@[label](echo-session:...)`` so the
      // backend resolver's canonical lane picks them up.
      const mentionText =
        item.type === "session" && !item.value.endsWith(":")
          ? `${formatSessionReferenceMention(item.value, item.label)} `
          : `@${item.value} `;

      // Replace from @ to current cursor position
      const before = value.slice(0, atPos);
      // Find the end of the current mention text
      const afterAt = value.slice(atPos + 1);
      let endOffset = afterAt.length;

      // Find where the mention query ends
      const colonIdx = afterAt.indexOf(":");
      if (colonIdx !== -1) {
        const typeStr = afterAt.slice(0, colonIdx).toLowerCase();
        if (typeStr === "web" || typeStr === "docs") {
          // For web/docs, consume to end of line or next @ mention
          const lineEnd = afterAt.indexOf("\n");
          endOffset = lineEnd !== -1 ? lineEnd : afterAt.length;
        } else {
          const afterColon = afterAt.slice(colonIdx + 1);
          const spaceIdx = afterColon.indexOf(" ");
          endOffset =
            spaceIdx !== -1 ? colonIdx + 1 + spaceIdx : afterAt.length;
        }
      } else {
        const spaceIdx = afterAt.indexOf(" ");
        endOffset = spaceIdx !== -1 ? spaceIdx : afterAt.length;
      }

      const after = value.slice(atPos + 1 + endOffset);
      onChange(before + mentionText + after);

      setIsOpen(false);
      setMentionQuery("");
      triggerPosRef.current = -1;
    },
    [value, onChange],
  );

  // ---- Keyboard handler ----
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Detect @ trigger to open the autocomplete
      if (!isOpen) {
        if (e.key === "@" && !e.ctrlKey && !e.metaKey) {
          const pos = e.currentTarget.selectionStart;
          const charBefore = pos > 0 ? value[pos - 1] : undefined;
          // Only trigger if @ is at start or after whitespace
          if (
            charBefore === undefined ||
            charBefore === " " ||
            charBefore === "\n" ||
            charBefore === "\t" ||
            pos === 0
          ) {
            triggerPosRef.current = pos;
            setIsOpen(true);
            setMentionQuery("");
            setItems([]);
          }
        }
        return;
      }

      // Handle navigation within open dropdown
      if (items.length === 0 && !isLoading) {
        // Nothing to navigate — let default behavior through
        if (e.key === "Escape") {
          e.preventDefault();
          setIsOpen(false);
          return;
        }
        return;
      }

      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setSelectedIndex((i) =>
            items.length > 0 ? (i + 1) % items.length : 0,
          );
          break;

        case "ArrowUp":
          e.preventDefault();
          setSelectedIndex((i) =>
            items.length > 0 ? (i - 1 + items.length) % items.length : 0,
          );
          break;

        case "Enter":
          if (items.length > 0 && !e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
            const item = items[selectedIndex];
            if (item) {
              // If item is a category (value ends with ":"), append it to the query
              if (item.value.endsWith(":")) {
                // Replace current text after @ with the category prefix
                const atPos = triggerPosRef.current;
                if (atPos >= 0) {
                  const before = value.slice(0, atPos + 1);
                  // Find end of current query
                  const afterAt = value.slice(atPos + 1);
                  const spaceIdx = afterAt.indexOf(" ");
                  const after = spaceIdx !== -1 ? afterAt.slice(spaceIdx) : "";
                  onChange(before + item.value + after);
                  // Don't close — let the user continue typing
                  setMentionQuery(item.value);
                }
              } else {
                selectItem(item);
              }
            }
          }
          break;

        case "Tab":
          if (items.length > 0) {
            e.preventDefault();
            const item = items[selectedIndex];
            if (item) {
              if (item.value.endsWith(":")) {
                const atPos = triggerPosRef.current;
                if (atPos >= 0) {
                  const before = value.slice(0, atPos + 1);
                  const afterAt = value.slice(atPos + 1);
                  const spaceIdx = afterAt.indexOf(" ");
                  const after = spaceIdx !== -1 ? afterAt.slice(spaceIdx) : "";
                  onChange(before + item.value + after);
                  setMentionQuery(item.value);
                }
              } else {
                selectItem(item);
              }
            }
          }
          break;

        case "Escape":
          e.preventDefault();
          setIsOpen(false);
          setMentionQuery("");
          break;

        case "Backspace": {
          // If we've deleted back to before the @, close
          const pos = e.currentTarget.selectionStart;
          if (pos !== null && pos <= triggerPosRef.current) {
            setIsOpen(false);
            setMentionQuery("");
          }
          break;
        }
      }
    },
    [isOpen, items, selectedIndex, isLoading, value, onChange, selectItem],
  );

  return {
    isOpen,
    items,
    selectedIndex,
    isLoading,
    mentionQuery,
    handleKeyDown,
    selectItem,
    close: useCallback(() => {
      setIsOpen(false);
      setMentionQuery("");
    }, []),
  };
}

// ============================================================================
// Popup Component
// ============================================================================

interface MentionAutocompletePopupProps {
  items: MentionItem[];
  selectedIndex: number;
  isLoading?: boolean;
  mentionQuery?: string;
  onSelect?: (item: MentionItem) => void;
  className?: string;
}

export function MentionAutocompletePopup({
  items,
  selectedIndex,
  isLoading,
  mentionQuery,
  onSelect,
  className,
}: MentionAutocompletePopupProps) {
  const { t } = useI18n();
  const listRef = useRef<HTMLDivElement>(null);

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return;
    const selected = listRef.current.querySelector(
      `[data-mention-index="${selectedIndex}"]`,
    );
    if (selected) {
      selected.scrollIntoView({ block: "nearest" });
    }
  }, [selectedIndex]);

  if (!isLoading && items.length === 0 && !mentionQuery) {
    return null;
  }

  return (
    <div
      className={cn(
        "bg-popover text-popover-foreground absolute bottom-full left-0 z-50 mb-1 w-80 overflow-hidden rounded-lg border shadow-[var(--shadow-md)]",
        className,
      )}
    >
      {/* Header */}
      <div className="border-b px-3 py-1.5">
        <div className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider">
          <span>@</span>
          <span>{t.mentions.mentions}</span>
          {mentionQuery && (
            <>
              <span className="text-muted-foreground/40">|</span>
              <span className="text-foreground/70 normal-case tracking-normal font-normal">
                {mentionQuery}
              </span>
            </>
          )}
        </div>
      </div>

      {/* Items */}
      <div ref={listRef} className="max-h-72 overflow-y-auto p-1">
        {isLoading && items.length === 0 ? (
          <div className="text-muted-foreground flex items-center justify-center gap-2 py-4 text-xs">
            <Loader2Icon className="size-3.5 animate-spin" />
            <span>{t.mentions.searching}</span>
          </div>
        ) : items.length === 0 ? (
          <div className="text-muted-foreground py-4 text-center text-xs">
            {t.mentions.noResults}
          </div>
        ) : (
          items.map((item, index) => {
            const IconComponent =
              CATEGORY_ICONS[item.icon] ||
              CATEGORY_ICONS[item.type] ||
              FileIcon;
            const colorClass =
              CATEGORY_COLORS[item.type] || "text-muted-foreground";
            const isSelected = index === selectedIndex;

            return (
              <button
                key={`${item.type}-${item.value}-${index}`}
                data-mention-index={index}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors",
                  isSelected
                    ? "bg-accent text-accent-foreground"
                    : "hover:bg-accent/50",
                )}
                onClick={() => onSelect?.(item)}
                onMouseEnter={() => {
                  // We don't set selectedIndex on hover to avoid conflicts
                  // with keyboard navigation
                }}
              >
                <div
                  className={cn(
                    "flex size-6 shrink-0 items-center justify-center overflow-hidden rounded",
                    isSelected ? "bg-accent-foreground/10" : "bg-muted",
                  )}
                >
                  {item.avatarUrl ? (
                    <img
                      src={item.avatarUrl}
                      alt={item.label}
                      className="size-full object-cover"
                    />
                  ) : (
                    <IconComponent className={cn("size-3.5", colorClass)} />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-sm leading-tight">
                    {item.label}
                  </div>
                  {item.description && (
                    <div className="text-muted-foreground truncate text-xs leading-tight">
                      {item.description}
                    </div>
                  )}
                </div>
                {item.value.endsWith(":") && (
                  <div className="text-muted-foreground/50 text-xs">
                    &rsaquo;
                  </div>
                )}
              </button>
            );
          })
        )}
      </div>

      {/* Footer hint */}
      <div className="border-t px-3 py-1">
        <div className="text-muted-foreground/60 flex items-center gap-3 text-xs">
          <span>
            <kbd className="bg-muted rounded px-1 font-mono text-xs">
              &uarr;&darr;
            </kbd>{" "}
            {t.mentions.navigate}
          </span>
          <span>
            <kbd className="bg-muted rounded px-1 font-mono text-xs">Tab</kbd>{" "}
            {t.mentions.select}
          </span>
          <span>
            <kbd className="bg-muted rounded px-1 font-mono text-xs">Esc</kbd>{" "}
            {t.mentions.close}
          </span>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Mention Badge (inline chip for rendered mentions in messages)
// ============================================================================

interface MentionBadgeProps {
  type: MentionCategory;
  reference: string;
  className?: string;
}

export function MentionBadge({
  type,
  reference,
  className,
}: MentionBadgeProps) {
  const IconComponent =
    CATEGORY_ICONS[type] || CATEGORY_ICONS[type] || FileIcon;
  const colorClass = CATEGORY_COLORS[type] || "text-muted-foreground";

  return (
    <span
      className={cn(
        "bg-accent/60 text-accent-foreground inline-flex items-center gap-1 rounded-lg px-1.5 py-0.5 text-xs font-medium",
        className,
      )}
    >
      <IconComponent className={cn("size-3", colorClass)} />
      <span className="max-w-[200px] truncate">{reference}</span>
    </span>
  );
}

// ============================================================================
// Backward-compatible exports (re-export as the old file-mention interface)
// ============================================================================

/**
 * Legacy interface — the old `useFileMention` hook is now powered by
 * the full mention autocomplete. This wrapper maintains backward
 * compatibility with existing consumers.
 */
export function useFileMention({
  value,
  onChange,
  workDir,
}: {
  value: string;
  onChange: (value: string) => void;
  workDir?: string;
}) {
  const { isOpen, items, selectedIndex, isLoading, handleKeyDown } =
    useMentionAutocomplete({ value, onChange, workDir });

  // Map items to the old FileEntry shape for the popup
  const filteredFiles = useMemo(
    () =>
      items.map((item) => ({
        name: item.label,
        path: item.description || item.value,
        type: (item.type === "folder" ? "dir" : "file") as "file" | "dir",
      })),
    [items],
  );

  return {
    isOpen,
    filteredFiles,
    selectedIndex,
    isLoading,
    handleKeyDown,
  };
}
