import { currentActorId } from "@/core/auth/api";
import { actorScopedStorageKey } from "@/core/auth/scoped-storage";

export type LocalCreativeProject = {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
};

export const CREATIVE_PROJECTS_CHANGED_EVENT = "echo:creative-projects-changed";

const STORAGE_PREFIX = "echo.design.local-projects.v1";
type CreativeStorage = Pick<Storage, "getItem" | "setItem"> &
  Partial<Pick<Storage, "removeItem">>;

function safePersonaId(personaId: string): string {
  return personaId.trim() || "general";
}

function legacyCreativeProjectsStorageKey(personaId: string): string {
  return `${STORAGE_PREFIX}:${safePersonaId(personaId)}`;
}

export function creativeProjectsStorageKey(
  personaId: string,
  actor = currentActorId(),
): string {
  return actorScopedStorageKey(
    legacyCreativeProjectsStorageKey(personaId),
    actor,
  );
}

export function legacyCreativeCanvasStorageKey(
  baseKey: string,
  personaId: string,
  projectId: string | null,
): string {
  const room = projectId ? `project:${projectId}` : "room";
  return `${baseKey}:creation:${safePersonaId(personaId)}:${room}`;
}

export function creativeCanvasStorageKey(
  baseKey: string,
  personaId: string,
  projectId: string | null,
  actor = currentActorId(),
): string {
  return actorScopedStorageKey(
    legacyCreativeCanvasStorageKey(baseKey, personaId, projectId),
    actor,
  );
}

export function readCreativeCanvasValue(
  storageKey: string,
  legacyKey: string,
  actor = currentActorId(),
  storage: CreativeStorage = window.localStorage,
): string | null {
  const scoped = storage.getItem(storageKey);
  if (scoped !== null) return scoped;
  const legacy = storage.getItem(legacyKey);
  if (legacy === null || !actor.trim() || actor.trim() === "anonymous") {
    return legacy;
  }
  try {
    storage.setItem(storageKey, legacy);
    storage.removeItem?.(legacyKey);
  } catch {
    // Keep the legacy value usable if migration is blocked.
  }
  return legacy;
}

export function readLocalCreativeProjects(
  personaId: string,
  storage: CreativeStorage = window.localStorage,
  actor = currentActorId(),
): LocalCreativeProject[] {
  try {
    const scopedKey = creativeProjectsStorageKey(personaId, actor);
    let raw = storage.getItem(scopedKey);
    if (raw === null && actor.trim() && actor.trim() !== "anonymous") {
      raw = storage.getItem(legacyCreativeProjectsStorageKey(personaId));
      if (raw !== null) {
        try {
          storage.setItem(scopedKey, raw);
          storage.removeItem?.(legacyCreativeProjectsStorageKey(personaId));
        } catch {
          // Keep the legacy value usable if migration is blocked.
        }
      }
    }
    const parsed = JSON.parse(raw || "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is LocalCreativeProject =>
        !!item &&
        typeof item === "object" &&
        typeof (item as LocalCreativeProject).id === "string" &&
        typeof (item as LocalCreativeProject).name === "string",
    );
  } catch {
    return [];
  }
}

export function createLocalCreativeProject(
  personaId: string,
  name: string,
  storage: Pick<Storage, "getItem" | "setItem"> = window.localStorage,
  actor = currentActorId(),
): LocalCreativeProject {
  const trimmed = name.trim();
  if (!trimmed) throw new Error("项目名称不能为空");
  const now = new Date().toISOString();
  const project: LocalCreativeProject = {
    id:
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `creative-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
    name: trimmed.slice(0, 120),
    createdAt: now,
    updatedAt: now,
  };
  const next = [
    project,
    ...readLocalCreativeProjects(personaId, storage, actor).filter(
      (item) => item.id !== project.id,
    ),
  ];
  storage.setItem(
    creativeProjectsStorageKey(personaId, actor),
    JSON.stringify(next),
  );
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(CREATIVE_PROJECTS_CHANGED_EVENT, {
        detail: { personaId: safePersonaId(personaId) },
      }),
    );
  }
  return project;
}
