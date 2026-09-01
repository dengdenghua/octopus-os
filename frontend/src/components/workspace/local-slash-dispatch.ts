// Client-side fast path for slash commands. Submitting `/clear` or
// `/mode plan` should not pay an LLM round-trip — the harness can
// resolve them locally in tens of milliseconds. This module is the
// small pure dispatcher: given the raw composer text and a bundle of
// callbacks, it returns true when it handled the input, and the
// composer skips the normal onSubmit path.
//
// Keep this file dependency-free (no React, no fetch) so it can be
// covered by plain unit tests and so the cost of importing it from
// the composer is trivial.

import {
  normalizePermissionMode,
  type PermissionMode,
} from "@/core/permissions";
import type { ReasoningMode } from "./reasoning-mode";

export interface LocalSlashContext {
  /** Switch the active reasoning topology. */
  onModeChange?: (mode: ReasoningMode) => void;
  /** Switch active model. Receives the raw model name. */
  onModelChange?: (modelName: string) => void;
  /** Set the permission mode (default / auto-edit / yolo / plan). */
  onPermissionModeChange?: (mode: PermissionMode) => void;
  /** Reset the conversation transcript. */
  onClear?: () => void;
  /** Trigger context compaction. */
  onCompact?: () => void;
  /** Switch the workspace right-rail panel by id. */
  onSwitchPanel?: (panel: string) => void;
  /** Apply a built-in personality preset. */
  onPersonalityApply?: (name: string) => void;
}

const VALID_MODES: ReadonlySet<ReasoningMode> = new Set([
  "chat",
  "code",
  "react",
  "deep",
  "flash",
  "thinking",
]);

const VALID_PERMISSION_MODES: ReadonlySet<string> = new Set<string>([
  "default",
  "acceptedits",
  "accept-edits",
  "bypasspermissions",
  "bypass-permissions",
  "bypass",
  "yolo",
  "plan",
  "sandbox",
  "full",
]);

const PANEL_ALIASES: Record<string, string> = {
  wiki: "wiki",
  monitor: "monitor",
  arena: "arena",
  quest: "quest",
  // ``skills`` no longer aliases the legacy panel — it now opens the
  // 能力包 / Meta-Skill catalog (handled via an explicit case in
  // tryLocalSlash so we can support an optional query argument).
  deploy: "deploy",
  record: "teach-repeat",
  replay: "teach-repeat",
};

interface ParsedSlash {
  cmd: string;
  arg: string;
}

function parseSlash(raw: string): ParsedSlash | null {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("/")) return null;
  const body = trimmed.slice(1);
  if (!body) return null;
  // Reject path-like inputs: `/usr/local/bin`, `/home/me/file.txt`,
  // URLs, etc. A real slash command is a single identifier
  // optionally followed by a space and an arg — no slashes or dots
  // inside the command token.
  const tokenEnd = body.search(/\s/);
  const token = tokenEnd === -1 ? body : body.slice(0, tokenEnd);
  if (!/^[A-Za-z][\w-]*$/.test(token)) return null;
  const arg = tokenEnd === -1 ? "" : body.slice(tokenEnd + 1).trim();
  return { cmd: token.toLowerCase(), arg };
}

/**
 * Try to handle a composer submission locally.
 *
 * Returns true iff the input was a recognized client-side command
 * AND the relevant callback was wired up. When it returns false the
 * composer should fall through to its normal onSubmit path.
 *
 * Commands that take an argument with no callback wired (e.g. /mode
 * with no onModeChange) are treated as unhandled so the user sees
 * the message reach the LLM rather than silently disappearing.
 */
export function tryLocalSlash(text: string, ctx: LocalSlashContext): boolean {
  const parsed = parseSlash(text);
  if (!parsed) return false;
  const { cmd, arg } = parsed;

  switch (cmd) {
    case "clear":
      if (!ctx.onClear) return false;
      ctx.onClear();
      return true;

    case "compact":
      if (!ctx.onCompact) return false;
      ctx.onCompact();
      return true;

    case "settings":
      // Open the unified settings dialog through the workspace event bridge.
      if (typeof window !== "undefined") {
        window.dispatchEvent(new Event("echo:open-settings"));
      }
      return true;

    case "skills":
    case "pack":
    case "packs":
    case "meta": {
      // Open the unified Hub skill catalog. With an arg, keep the query
      // so the Hub can pre-filter the skill list.
      if (typeof window !== "undefined") {
        const target = arg
          ? `#/workspace/agents?surface=chat&tab=skills&q=${encodeURIComponent(arg)}`
          : "#/workspace/agents?surface=chat&tab=skills";
        window.location.hash = target;
      }
      return true;
    }

    case "mode": {
      if (!ctx.onModeChange) return false;
      const next = arg.toLowerCase();
      if (!VALID_MODES.has(next as ReasoningMode)) return false;
      ctx.onModeChange(next as ReasoningMode);
      return true;
    }

    case "model": {
      if (!ctx.onModelChange || !arg) return false;
      ctx.onModelChange(arg);
      return true;
    }

    case "permission":
    case "perm": {
      if (!ctx.onPermissionModeChange) return false;
      const next = arg.toLowerCase();
      if (!VALID_PERMISSION_MODES.has(next as PermissionMode)) return false;
      ctx.onPermissionModeChange(normalizePermissionMode(arg));
      return true;
    }

    case "personality": {
      if (!ctx.onPersonalityApply || !arg) return false;
      ctx.onPersonalityApply(arg);
      return true;
    }

    default: {
      const panel = PANEL_ALIASES[cmd];
      if (panel && ctx.onSwitchPanel) {
        ctx.onSwitchPanel(panel);
        return true;
      }
      return false;
    }
  }
}

// Exposed for tests so we can verify parsing without going through
// the dispatcher's side effects.
export const __test = { parseSlash, VALID_MODES, PANEL_ALIASES };
