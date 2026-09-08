import { currentActorId } from "@/core/auth/api";
import {
  actorScopedStorageKey,
  readActorScopedStorageValue,
} from "@/core/auth/scoped-storage";

export const RECENT_WORKDIRS_KEY = "echo:recentWorkdirs";

export type RememberedWorkdirKind = "chat" | "code";

export function recentWorkdirsStorageKey(): string {
  return actorScopedStorageKey(RECENT_WORKDIRS_KEY);
}

/** Read recent folders and migrate the pre-account device key on first use. */
export function readRecentWorkdirs(actor = currentActorId()): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = readActorScopedStorageValue(RECENT_WORKDIRS_KEY, actor);
    const parsed = JSON.parse(raw ?? "[]") as unknown;
    return Array.isArray(parsed)
      ? parsed.filter(
          (item): item is string =>
            typeof item === "string" && item.trim().length > 0,
        )
      : [];
  } catch {
    return [];
  }
}

/** Persist recent folders in the same account namespace used by every host. */
export function writeRecentWorkdirs(
  paths: readonly string[],
  actor = currentActorId(),
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      actorScopedStorageKey(RECENT_WORKDIRS_KEY, actor),
      JSON.stringify(paths),
    );
  } catch {
    // A disabled or full preference store must not block workspace navigation.
  }
}

export function rememberedWorkdirStorageKey(
  kind: RememberedWorkdirKind,
  actor?: string,
): string {
  return actorScopedStorageKey(`${kind}:workdir:lastUsed`, actor);
}
