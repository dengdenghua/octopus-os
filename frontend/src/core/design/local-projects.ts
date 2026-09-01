export type LocalCreativeProject = {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
};

export const CREATIVE_PROJECTS_CHANGED_EVENT =
  "echo:creative-projects-changed";

const STORAGE_PREFIX = "echo.design.local-projects.v1";

function safePersonaId(personaId: string): string {
  return personaId.trim() || "general";
}

export function creativeProjectsStorageKey(personaId: string): string {
  return `${STORAGE_PREFIX}:${safePersonaId(personaId)}`;
}

export function creativeCanvasStorageKey(
  baseKey: string,
  personaId: string,
  projectId: string | null,
): string {
  const room = projectId ? `project:${projectId}` : "room";
  return `${baseKey}:creation:${safePersonaId(personaId)}:${room}`;
}

export function readLocalCreativeProjects(
  personaId: string,
  storage: Pick<Storage, "getItem"> = window.localStorage,
): LocalCreativeProject[] {
  try {
    const parsed = JSON.parse(
      storage.getItem(creativeProjectsStorageKey(personaId)) || "[]",
    ) as unknown;
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
    ...readLocalCreativeProjects(personaId, storage).filter(
      (item) => item.id !== project.id,
    ),
  ];
  storage.setItem(creativeProjectsStorageKey(personaId), JSON.stringify(next));
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(CREATIVE_PROJECTS_CHANGED_EVENT, {
        detail: { personaId: safePersonaId(personaId) },
      }),
    );
  }
  return project;
}
