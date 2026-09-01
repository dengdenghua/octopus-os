/**
 * Custom display name for the Echo assistant (the global fixed role).
 *
 * The assistant's default name comes from the backend agent profile
 * (`agents/echo/profile.jsonc` → "Echo"). Users may override it to
 * a personal nickname; the override lives in localStorage so it applies
 * instantly across every UI surface (welcome, header badge, sidebar entry)
 * without a backend restart.
 */
import { swallow } from "@/core/utils/log";

export const ASSISTANT_NAME_KEY = "echo.assistant-name";

/** Default assistant name — mirrors `agents/echo/profile.jsonc`. */
export const DEFAULT_ASSISTANT_NAME = "EchoAI";

export function getAssistantDisplayName(): string {
  try {
    const raw = window.localStorage.getItem(ASSISTANT_NAME_KEY)?.trim();
    if (raw) return raw;
  } catch (e) {
    swallow(e, "storage");
  }
  return DEFAULT_ASSISTANT_NAME;
}

export function setAssistantDisplayName(name: string): void {
  const trimmed = name.trim();
  try {
    if (trimmed) {
      window.localStorage.setItem(ASSISTANT_NAME_KEY, trimmed);
    } else {
      window.localStorage.removeItem(ASSISTANT_NAME_KEY);
    }
  } catch (e) {
    swallow(e, "storage");
  }
}
