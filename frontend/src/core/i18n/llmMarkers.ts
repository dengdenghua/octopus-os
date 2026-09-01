/**
 * LLM trace markers used by the ReAct parser.
 *
 * The system prompt is bilingual (and we are adding Japanese and Korean
 * localisations), so the parser that splits a model response into
 * `Thought:` / `Action:` / `Observation:` / `Final Answer:` segments
 * must recognise all of those languages. The union is exported as a
 * precompiled regex, mirroring the role the backend plays for
 * camouflage / safety markers.
 *
 * These markers are language-agnostic by design: we only need to
 * recognise the four section headers the model emits, so the regex
 * is built from a static union of all four locales. This is the
 * frontend analogue of the backend
 * `runtime/platform/i18n/__init__.py::get_safety_relax_markers()`.
 */

import { SUPPORTED_LOCALES } from "./locale";

export type TraceKind =
  | "thought"
  | "update"
  | "action"
  | "observation"
  | "finalAnswer";

/**
 * Per-locale canonical spelling of each marker, indexed by TraceKind.
 * The English form is the default the system prompt was authored in;
 * the others mirror how the user-facing translation in `locales/`
 * renders the same concept. Keep these short — they only need to be
 * unambiguous inside a regex.
 */
export const LLM_TRACE_MARKERS: Record<TraceKind, ReadonlyArray<string>> = {
  thought: ["Thought", "思考", "考え", "생각"],
  update: [
    "Update",
    "Progress",
    "进展",
    "進捗",
    "アップデート",
    "업데이트",
    "진행",
  ],
  action: ["Action", "行动", "行動", "アクション", "행동"],
  observation: ["Observation", "观察", "観察", "オブザベーション", "관찰"],
  finalAnswer: [
    "Final Answer",
    "最终答案",
    "最終回答",
    "ファイナルアンサー",
    "최종답변",
  ],
};

/** Static set of supported marker categories. */
export const TRACE_KINDS: ReadonlyArray<TraceKind> = [
  "thought",
  "update",
  "action",
  "observation",
  "finalAnswer",
];

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Build a regex that matches any marker in any supported language
 * followed by an ASCII or full-width colon. Cached at module load
 * because the union is static.
 */
function buildMarkerPattern(flags?: string): RegExp {
  const all: string[] = [];
  for (const kind of TRACE_KINDS) {
    for (const spelling of LLM_TRACE_MARKERS[kind]) {
      all.push(escapeRegex(spelling));
    }
  }
  // Sort by length descending so longer phrases (e.g. "Final Answer")
  // win over prefixes (e.g. "Final").
  all.sort((a, b) => b.length - a.length);
  return new RegExp(`(^|\\n)\\s*(${all.join("|")})\\s*[:：]\\s*`, flags);
}

const MARKER_DETECT = buildMarkerPattern();
const MARKER_SPLIT = buildMarkerPattern("g");

/**
 * Detect the union of all marker variants in a given text. Returns
 * `true` if any of the four sections appears at least once. Useful
 * for the "early return when there are no ReAct markers" fast path.
 */
export function hasLLMTraceMarkers(text: string): boolean {
  if (!text) return false;
  return MARKER_DETECT.test(text);
}

/**
 * Walk the text and segment it by marker lines, returning an ordered
 * list of `{ kind, text }` records. The text between two markers
 * belongs to the preceding marker's slot. Caller is responsible for
 * collapsing / rendering.
 */
export function segmentLLMTrace(
  text: string,
): Array<{ kind: TraceKind | "prelude"; text: string }> {
  if (!text) return [];
  const segs: Array<{ kind: TraceKind | "prelude"; text: string }> = [];
  let match: RegExpExecArray | null;
  let lastEnd = 0;
  let lastKind: TraceKind | "prelude" = "prelude";
  // Reset lastIndex because the regex is module-level with /g flag.
  MARKER_SPLIT.lastIndex = 0;
  while ((match = MARKER_SPLIT.exec(text)) !== null) {
    if (match.index > lastEnd) {
      segs.push({ kind: lastKind, text: text.slice(lastEnd, match.index) });
    }
    lastKind = normalizeTraceKind(match[2]!);
    lastEnd = MARKER_SPLIT.lastIndex;
  }
  if (lastEnd < text.length) {
    segs.push({ kind: lastKind, text: text.slice(lastEnd) });
  }
  return segs;
}

/**
 * Map a raw matched marker (in any supported language) back to the
 * canonical TraceKind. Falls back to "prelude" for unknown text.
 */
export function normalizeTraceKind(raw: string): TraceKind | "prelude" {
  const lower = raw.toLowerCase();
  for (const kind of TRACE_KINDS) {
    for (const spelling of LLM_TRACE_MARKERS[kind]) {
      if (lower === spelling.toLowerCase() || raw === spelling) {
        return kind;
      }
    }
  }
  return "prelude";
}

/**
 * Status-only-text heuristics used by the realtime adapter to
 * recognise a "task delivered" pseudo-message. Both `mentionsDelivered`
 * and `mentionsCompletion` need to fire for the candidate to be
 * considered a status-only delivery summary.
 */
const DELIVERED_KEYWORDS: ReadonlyArray<string> = [
  "final answer",
  "report has been delivered",
  "report has already been delivered",
  "already delivered",
  "provided above",
  "交付",
  "报告",
  "最終回答",
  "최종답변",
  "보고",
  "전달",
];

const COMPLETION_KEYWORDS: ReadonlyArray<string> = [
  "todo",
  "task",
  "checklist",
  "all tasks",
  "completed",
  "marked",
  "fully completed",
  "任务",
  "清单",
  "完成",
  "已完成",
  "タスク",
  "チェックリスト",
  "完了",
  "완료",
  "체크리스트",
];

function escapeAlternation(values: ReadonlyArray<string>): string {
  return values.map(escapeRegex).join("|");
}

const MENTIONS_DELIVERED_RE = new RegExp(
  `(?:${escapeAlternation(DELIVERED_KEYWORDS)})`,
  "i",
);
const MENTIONS_COMPLETION_RE = new RegExp(
  `(?:${escapeAlternation(COMPLETION_KEYWORDS)})`,
  "i",
);

export function mentionsDelivered(text: string): boolean {
  return MENTIONS_DELIVERED_RE.test(text);
}

export function mentionsCompletion(text: string): boolean {
  return MENTIONS_COMPLETION_RE.test(text);
}

/**
 * Locale coverage summary, used by tests / diagnostics. Mirrors the
 * union-of-locales approach in the backend camouflage safety markers.
 */
export function getLLMTraceLocaleCoverage(): ReadonlyArray<string> {
  return [...SUPPORTED_LOCALES];
}
