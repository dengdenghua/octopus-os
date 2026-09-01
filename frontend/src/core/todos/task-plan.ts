export type TaskPlanItemLike = Record<string, unknown>;

const ID_KEYS = ["id", "taskId", "task_id", "phaseId", "phase_id"] as const;
const CONTENT_KEYS = [
  "content",
  "text",
  "title",
  "task",
  "name",
  "description",
] as const;

function firstText(item: TaskPlanItemLike, keys: readonly string[]) {
  for (const key of keys) {
    const value = item[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function taskPlanItemContent(item: TaskPlanItemLike) {
  return firstText(item, CONTENT_KEYS);
}

export function taskPlanItemActiveLabel(item: TaskPlanItemLike) {
  return firstText(item, ["activeForm", "active_form"]);
}

function shortStableHash(value: string) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

/** Stable across status changes and list reordering when content is unchanged. */
export function taskPlanItemId(item: TaskPlanItemLike, occurrence = 0): string {
  const explicit = firstText(item, ID_KEYS);
  if (explicit) return explicit;
  const content = taskPlanItemContent(item)
    .replace(/\s+/g, " ")
    .toLocaleLowerCase();
  return `task-${shortStableHash(`${content}\u0000${occurrence}`)}`;
}
