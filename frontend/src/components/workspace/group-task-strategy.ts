export const GROUP_TASK_STRATEGIES = [
  "auto",
  "build",
  "research",
  "develop",
  "audit",
  "uxui",
] as const;

export type GroupTaskStrategy = (typeof GROUP_TASK_STRATEGIES)[number];

export function isProjectTaskStrategy(
  strategy: GroupTaskStrategy,
): strategy is "develop" | "audit" | "uxui" {
  return strategy === "develop" || strategy === "audit" || strategy === "uxui";
}
