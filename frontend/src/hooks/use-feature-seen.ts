/* Feature discovery · track which features the user has opened
   so the sidebar can hide one-time "NEW" badges once visited.

   Storage key convention: `echo.seen.<feature>` · boolean
*/

import { useEffect } from "react";

import { useLocalStorage } from "./use-local-storage";

export type SeenFeature =
  | "agents"
  | "skills"
  | "evolution"
  | "intelligence"
  | "mcp"
  | "store";

const STORAGE_PREFIX = "echo.seen.";

export function useFeatureSeen(
  feature: SeenFeature,
  shouldMarkSeen: boolean,
): boolean {
  const [seen, setSeen] = useLocalStorage<boolean>(
    `${STORAGE_PREFIX}${feature}`,
    false,
  );

  // Mark as seen once the condition is met (e.g. user lands on the page)
  useEffect(() => {
    if (shouldMarkSeen && !seen) {
      setSeen(true);
    }
  }, [shouldMarkSeen, seen, setSeen]);

  return seen;
}
