import { useEffect, useRef } from "react";
import { eventBus } from "@/core/events/event-bus";
import { parseDesktopAction } from "./desktop-actions";

type PhotoToolEvent = {
  id: string;
  name: string;
  status: string;
  output?: unknown;
};

export function completedPhotoSearch(event: PhotoToolEvent): string | null {
  if (event.name !== "photos_search" || event.status !== "done") return null;
  let output = event.output;
  if (typeof output === "string") {
    try {
      output = JSON.parse(output);
    } catch {
      return null;
    }
  }
  if (!output || typeof output !== "object") return null;
  const result = output as Record<string, unknown>;
  if (
    result.ok !== true ||
    !result.desktopAction ||
    typeof result.desktopAction !== "object"
  )
    return null;
  const intent = result.desktopAction as Record<string, unknown>;
  if (intent.type !== "photos.search" || typeof intent.query !== "string")
    return null;
  const parsed = parseDesktopAction(
    new URLSearchParams({
      desktopAction: "photos.search",
      query: intent.query,
    }).toString(),
  );
  return parsed?.type === "photos.search" ? parsed.query : null;
}

/** Only observed live calls may open a window; replayed history is inert. */
export function useAgentPhotoResults(events: readonly PhotoToolEvent[]) {
  const pending = useRef(new Set<string>());
  const handled = useRef(new Set<string>());
  useEffect(() => {
    const currentIds = new Set(events.map((event) => event.id));
    for (const id of pending.current)
      if (!currentIds.has(id)) pending.current.delete(id);
    for (const id of handled.current)
      if (!currentIds.has(id)) handled.current.delete(id);
    for (const event of events) {
      if (event.name !== "photos_search" || handled.current.has(event.id))
        continue;
      if (event.status === "running") {
        pending.current.add(event.id);
        continue;
      }
      if (!pending.current.delete(event.id)) continue;
      handled.current.add(event.id);
      const query = completedPhotoSearch(event);
      if (query) eventBus.emit("desktop:photo-search", { query });
    }
  }, [events]);
}
