/** Lazy workspace route loaders shared by the router and sidebar prefetch. */

export const loadAgentsPage = () => import("@/app/workspace/agents/page");
export const loadProjectsPage = () => import("@/app/workspace/projects/page");

const loaders: ReadonlyArray<readonly [string, () => Promise<unknown>]> = [
  ["/workspace/agents", loadAgentsPage],
  ["/workspace/projects", loadProjectsPage],
];

/** Warm a route chunk on intent (hover/focus) without delaying navigation. */
export function preloadWorkspaceRoute(to: string): void {
  const pathname = to.split("?", 1)[0] || to;
  const loader = loaders.find(([prefix]) => pathname.startsWith(prefix))?.[1];
  if (loader) void loader().catch(() => undefined);
}
