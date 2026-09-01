export type ComposerMode = "plan" | "spec" | "goal";

const MODE_RE = /^\/(codex|mode)\s+(plan|spec|goal)(?:\s+|$)/i;

export interface ComposerModeParseResult {
  text: string;
  mode?: ComposerMode;
}

export function composerModeMarker(mode: ComposerMode): string {
  return `/mode ${mode}`;
}

export function parseComposerModeMarker(
  rawText: string,
): ComposerModeParseResult {
  const source = rawText.trim();
  const match = MODE_RE.exec(source);
  if (!match) return { text: source };
  const mode = match[2]?.toLowerCase() as ComposerMode;
  const text = source.slice(match[0].length).trimStart();
  return { text, mode };
}

export function applyComposerModeContext(
  context: Record<string, unknown>,
  mode: ComposerMode | undefined,
): Record<string, unknown> {
  if (!mode) return context;
  return {
    ...context,
    workflow_mode: mode,
    completion_policy: mode,
    ...(mode === "goal" ? { goal_mode: true } : {}),
    mode_preset: `${mode}.mode`,
    workflow_preset: `${mode}.mode`,
  };
}

// Legacy exports for backward compatibility
export type CodexComposerMode = ComposerMode;
export const codexComposerModeMarker = composerModeMarker;
export const parseCodexComposerModeMarker = parseComposerModeMarker;
export const applyCodexComposerModeContext = applyComposerModeContext;
