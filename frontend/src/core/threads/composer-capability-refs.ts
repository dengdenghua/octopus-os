import { composerModeMarker, type ComposerMode } from "./codex-composer-mode";

export type ComposerCommandMode = ComposerMode | "project";

export type ComposerCapabilityRef =
  | { type: "plugin"; id: string }
  | { type: "skill"; id: string }
  | { type: "surface"; id: "browser" | "chrome" };

export interface ParsedComposerDraft {
  mode?: ComposerCommandMode;
  refs: ComposerCapabilityRef[];
  body: string;
}

const REF_RE =
  /^@(Browser|Chrome|plugin:([A-Za-z0-9][A-Za-z0-9._/-]*)|skill:([A-Za-z0-9][A-Za-z0-9._/-]*))(?:\s+|$)/i;
const MODE_RE =
  /^\s*\/(?:codex|mode)\s+(plan|spec|goal)(?:(?:[ \t]+)|(?:\r?\n)|$)/i;
const PROJECT_MODE_RE = /^\s*\/project\s+run(?:(?:[ \t]+)|(?:\r?\n)|$)/i;

function refKey(ref: ComposerCapabilityRef): string {
  return `${ref.type}:${ref.id}`.toLowerCase();
}

export function composerCapabilityToken(ref: ComposerCapabilityRef): string {
  if (ref.type === "surface") {
    return ref.id === "chrome" ? "@Chrome" : "@Browser";
  }
  return `@${ref.type}:${ref.id}`;
}

export function parseComposerDraft(raw: string): ParsedComposerDraft {
  const projectModeMatch = PROJECT_MODE_RE.exec(raw);
  const modeMatch = projectModeMatch ? null : MODE_RE.exec(raw);
  const mode: ComposerCommandMode | undefined = projectModeMatch
    ? "project"
    : (modeMatch?.[1]?.toLowerCase() as ComposerMode | undefined);
  const commandMatch = projectModeMatch ?? modeMatch;
  let remaining = commandMatch ? raw.slice(commandMatch[0].length) : raw;
  if (commandMatch) remaining = remaining.trimStart();
  const refs: ComposerCapabilityRef[] = [];
  const seen = new Set<string>();

  while (remaining) {
    const match = REF_RE.exec(remaining);
    if (!match) break;
    const ref: ComposerCapabilityRef = match[2]
      ? { type: "plugin", id: match[2] }
      : match[3]
        ? { type: "skill", id: match[3] }
        : {
            type: "surface",
            id: match[1]?.toLowerCase() === "chrome" ? "chrome" : "browser",
          };
    const key = refKey(ref);
    if (!seen.has(key)) {
      refs.push(ref);
      seen.add(key);
    }
    remaining = remaining.slice(match[0].length).trimStart();
  }

  return { mode, refs, body: remaining };
}

export function serializeComposerDraft({
  mode,
  refs,
  body,
}: ParsedComposerDraft): string {
  const lines: string[] = [];
  if (mode) {
    lines.push(mode === "project" ? "/project run" : composerModeMarker(mode));
  }
  if (refs.length > 0) {
    lines.push(refs.map(composerCapabilityToken).join(" "));
  }
  const prefix = lines.join("\n");
  if (!prefix) return body;
  return body ? `${prefix}\n${body}` : `${prefix}\n`;
}

export function addComposerCapabilityRef(
  raw: string,
  nextRef: ComposerCapabilityRef,
): string {
  const parsed = parseComposerDraft(raw);
  const nextKey = refKey(nextRef);
  if (nextRef.type === "surface") {
    // Browser and Chrome are alternative execution surfaces for one turn.
    // Keeping both would make backend preference depend on ordering instead
    // of the user's latest explicit choice.
    parsed.refs = parsed.refs.filter((ref) => ref.type !== "surface");
  }
  if (!parsed.refs.some((ref) => refKey(ref) === nextKey)) {
    parsed.refs.push(nextRef);
  }
  return serializeComposerDraft(parsed);
}

export function removeComposerCapabilityRef(
  raw: string,
  target: ComposerCapabilityRef,
): string {
  const parsed = parseComposerDraft(raw);
  const targetKey = refKey(target);
  parsed.refs = parsed.refs.filter((ref) => refKey(ref) !== targetKey);
  return serializeComposerDraft(parsed);
}

export function setComposerDraftMode(
  raw: string,
  mode: ComposerCommandMode | undefined,
): string {
  const parsed = parseComposerDraft(raw);
  parsed.mode = mode;
  return serializeComposerDraft(parsed);
}
