/**
 * Adapter: in-app workbench replay (``WorkBlock[]``) → a portable ``ReplayData``
 * for the self-contained HTML exporter in ``@/core/sharing/replay-html``.
 *
 * This is where **export-time redaction** happens — the produced HTML is a file
 * the user can send to anyone, so before a step's command/output is embedded we
 * truncate it and scrub obvious secrets (API keys, JWTs, bearer tokens, long
 * hex digests). Best-effort, not a guarantee: the user still inspects the file
 * before sharing. Screenshots are inlined only when the event already carries a
 * ``data:`` URL — this module never fetches the network.
 */

import type {
  ReplayData,
  ReplayReceipt,
  ReplayReceiptItem,
  ReplayStep,
} from "@/core/sharing/replay-html";

import {
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
} from "@/core/messages/utils";
import {
  isRecord,
  stringFromKeys,
  textFromUnknown,
} from "./agent-workbench-utils";
import { normalizeAgentPhaseTitle } from "./agent-phases";
import {
  workBlockTitle,
  type WorkBlock,
  type WorkBlockLabels,
} from "./work-blocks";

const MAX_BODY = 1200;
/** Cap an inlined image's base64 length (~1.5 MB binary) so the HTML stays portable. */
const MAX_IMAGE_B64 = 2_000_000;
const INTERNAL_REPLAY_BLOCK_RE =
  /`?<(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)\b[^<>`]*>[\s\S]*?<\/(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)>`?/g;
const RAW_TOOL_NAME_RE =
  /\b(?:read_file|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace)\b/gi;

/** Best-effort secret scrub applied to every embedded body. */
export function redactSecrets(text: string): string {
  return text
    .replace(
      /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/g,
      "«redacted-jwt»",
    )
    .replace(
      /\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9]{12,}\b/g,
      "«redacted-token»",
    )
    .replace(
      /\b(?:Bearer|Authorization:?)\s+[A-Za-z0-9._-]{12,}/gi,
      "«redacted-bearer»",
    )
    .replace(
      /(["']?(?:api[_-]?key|secret|password|passwd|token)["']?\s*[:=]\s*)["']?[^\s"',}]{6,}/gi,
      "$1«redacted»",
    )
    .replace(/\b[A-Fa-f0-9]{40,}\b/g, "«redacted-hex»");
}

function truncate(text: string, max = MAX_BODY): string {
  const clean = text.trimEnd();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1)}…`;
}

export function sanitizeReplayText(
  text: string,
  options: { max?: number; normalizeTitle?: boolean } = {},
): string {
  const withoutInternalBlocks = text.replace(INTERNAL_REPLAY_BLOCK_RE, "");
  const withoutProtocol = stripLeakedRendererMarkup(
    stripInternalToolProtocol(withoutInternalBlocks),
  );
  const withoutSecrets = redactSecrets(withoutProtocol);
  const normalized = options.normalizeTitle
    ? normalizeAgentPhaseTitle(withoutSecrets)
    : withoutSecrets.trim();
  return truncate(
    normalized.replace(RAW_TOOL_NAME_RE, "operation"),
    options.max ?? MAX_BODY,
  );
}

function bodyForBlock(block: WorkBlock): string {
  const parts: string[] = [];
  if (block.kind === "terminal") {
    const out = block.outputText || textFromUnknown(block.event.output);
    if (out) parts.push(out);
  } else {
    if (block.inputText && block.inputText !== block.subtitle) {
      parts.push(block.inputText);
    }
    if (block.outputText) parts.push(block.outputText);
  }
  const joined = parts.join("\n").trim();
  return joined ? sanitizeReplayText(joined) : "";
}

/**
 * Inline an image that the event **already carries** — never fetches:
 *   1. a ready-made ``data:image/...`` URL in a known field, or
 *   2. a structured image record from reading an image file
 *      (``{ kind: "image", media_type, data_base64 }`` — see
 *      ``builtins._read_file_image``), reconstructed into a data-URL.
 *
 * Note: computer-use screenshots are NOT available here — the backend strips
 * their ``data_url`` before the event stream (``_compact_screenshot``), leaving
 * only a size marker. So there is nothing remote to fetch even if we wanted to.
 */
function imageForBlock(block: WorkBlock): string | undefined {
  for (const source of [block.event.output, block.event.input]) {
    const candidate = stringFromKeys(source, [
      "screenshot",
      "image",
      "image_data_url",
      "data_url",
      "dataUrl",
      "preview",
    ]);
    if (candidate.startsWith("data:image/")) return candidate;

    if (isRecord(source) && source.kind === "image") {
      const media =
        typeof source.media_type === "string" ? source.media_type : "";
      const b64 =
        typeof source.data_base64 === "string" ? source.data_base64 : "";
      if (media.startsWith("image/") && b64 && b64.length <= MAX_IMAGE_B64) {
        return `data:${media};base64,${b64}`;
      }
    }
  }
  return undefined;
}

function isLifecycleBlock(block: WorkBlock): boolean {
  return Boolean(
    block.event.lifecycle ||
    /subagent_(spawned|finished)/i.test(block.event.name),
  );
}

export interface ReplayMeta {
  title: string;
  brand?: string;
  footer?: string;
  frameMs?: number;
}

export function buildReplayFromBlocks(
  blocks: WorkBlock[],
  meta: ReplayMeta,
  labels?: WorkBlockLabels,
): ReplayData {
  const steps: ReplayStep[] = blocks
    .filter((block) => !isLifecycleBlock(block))
    .map((block): ReplayStep => {
      const body = bodyForBlock(block);
      const image = imageForBlock(block);
      return {
        kind: block.kind,
        title: sanitizeReplayText(workBlockTitle(block, labels), {
          max: 180,
          normalizeTitle: true,
        }),
        subtitle: block.subtitle
          ? sanitizeReplayText(block.subtitle, { max: 240 })
          : undefined,
        body: body || undefined,
        status: block.status,
        image,
      };
    })
    .filter((step) => step.title || step.body || step.image);
  const receipt = receiptFromBlocks(blocks, steps, labels);

  return {
    title: meta.title,
    steps,
    brand: meta.brand,
    footer: meta.footer,
    frameMs: meta.frameMs,
    receipt,
  };
}

function receiptFromBlocks(
  blocks: WorkBlock[],
  steps: ReplayStep[],
  labels?: WorkBlockLabels,
): ReplayReceipt | undefined {
  const latestTodo = [...blocks]
    .reverse()
    .find((block) => block.kind === "todo");
  const items = checklistFromTodo(latestTodo?.event.input);
  const recoveryCount = blocks.filter((block) => block.status === "warning").length;
  const attentionCount = blocks.filter(
    (block) => block.status === "error" || block.status === "waiting_approval",
  ).length;
  const completedCount = steps.filter((step) => step.status === "done").length;
  const verification = blocks
    .filter(
      (block) =>
        block.status === "done" &&
        (block.kind === "terminal" || block.actionKey === "submitResult"),
    )
    .slice(-3)
    .map((block) => sanitizeReplayText(workBlockTitle(block, labels), { max: 160 }))
    .filter(Boolean);

  if (items.length === 0 && recoveryCount === 0 && attentionCount === 0) {
    return undefined;
  }

  const summaryParts = [
    `${completedCount}/${steps.length} visible steps completed`,
    attentionCount > 0
      ? `${attentionCount} item${attentionCount === 1 ? "" : "s"} need attention`
      : "no unresolved items",
    recoveryCount > 0
      ? `${recoveryCount} ${recoveryCount === 1 ? "recovery" : "recoveries"} recorded`
      : "",
  ].filter(Boolean);

  return {
    summary: summaryParts.join(" · "),
    items,
    verification,
  };
}

function checklistFromTodo(input: unknown): ReplayReceiptItem[] {
  if (!input || typeof input !== "object") return [];
  const record = input as Record<string, unknown>;
  const raw = Array.isArray(record.items)
    ? record.items
    : Array.isArray(record.todos)
      ? record.todos
      : [];
  return raw.flatMap((item): ReplayReceiptItem[] => {
    if (!item || typeof item !== "object") return [];
    const candidate = item as Record<string, unknown>;
    const title = ["content", "title", "text", "task"]
      .map((key) => candidate[key])
      .find((value): value is string => typeof value === "string" && value.trim().length > 0);
    if (!title) return [];
    const rawStatus = typeof candidate.status === "string" ? candidate.status : "pending";
    const status = replayReceiptStatus(rawStatus);
    const detail = ["activeForm", "active_form", "detail", "description"]
      .map((key) => candidate[key])
      .find((value): value is string => typeof value === "string" && value.trim().length > 0);
    return [
      {
        title: sanitizeReplayText(title, { max: 180 }),
        status,
        detail: detail ? sanitizeReplayText(detail, { max: 240 }) : undefined,
      },
    ];
  });
}

function replayReceiptStatus(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (/done|complete|success|finished/.test(normalized)) return "done";
  if (/error|fail|blocked/.test(normalized)) return "error";
  if (/wait|approval|review/.test(normalized)) return "waiting_approval";
  if (/progress|running|active/.test(normalized)) return "running";
  return "pending";
}
