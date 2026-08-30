import type { Model } from "./types";

export const DEFAULT_CONTEXT_WINDOW_TOKENS = 128000;

const EXPLICIT_CONTEXT_WINDOW_KEYS = [
  "context_window",
  "contextWindow",
  "context_length",
  "contextLength",
  "context_size",
  "contextSize",
  "max_context_tokens",
  "maxContextTokens",
  "max_input_tokens",
  "maxInputTokens",
] as const;

function positiveInteger(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return Math.floor(value);
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number.parseInt(value.replace(/[, _]/g, ""), 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }
  return null;
}

function modelProbe(model: Model | undefined | null): string {
  if (!model) return "";
  return [
    model.id,
    model.name,
    model.model,
    model.display_name,
    model.description,
  ]
    .filter((part): part is string => typeof part === "string")
    .join(" ")
    .toLowerCase();
}

function inferContextWindowFromName(
  model: Model | undefined | null,
): number | null {
  const probe = modelProbe(model);
  if (!probe) return null;
  if (/\b1m\b|1000k|1,000k|1000000|1m-token/.test(probe)) return 1_000_000;
  if (/\b2m\b|2000k|2,000k|2000000|2m-token/.test(probe)) return 2_000_000;
  if (/gemini.*1\.5|gemini.*2\.0|gemini.*2\.5/.test(probe)) return 1_000_000;
  if (/claude|anthropic/.test(probe)) return 200_000;
  if (/kimi|moonshot/.test(probe)) return 128_000;
  if (/gpt-4\.1|gpt-4o|gpt-5|\bo[134]\b|openai/.test(probe)) return 128_000;
  return null;
}

export function resolveModelContextWindow(
  model: Model | undefined | null,
): number {
  if (model) {
    for (const key of EXPLICIT_CONTEXT_WINDOW_KEYS) {
      const value = positiveInteger(model[key]);
      if (value) return value;
    }
  }
  return inferContextWindowFromName(model) ?? DEFAULT_CONTEXT_WINDOW_TOKENS;
}
