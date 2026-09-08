import {
  type PersonaWorkbenchTab,
  workspacePresetForAgent,
} from "./workspace-presets";
import { currentActorId } from "@/core/auth/api";
import { actorScopedStorageKey } from "@/core/auth/scoped-storage";

const STORAGE_KEY = "echo.workbench.persona-tabs.v1";

function storageKey(actor = currentActorId()): string {
  return actorScopedStorageKey(STORAGE_KEY, actor);
}

const VALID_TABS = new Set<PersonaWorkbenchTab>([
  "agent",
  "terminal",
  "browser",
  "workspace",
]);

function readOverrides(): Record<string, PersonaWorkbenchTab> {
  if (typeof window === "undefined") return {};
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(storageKey()) ?? "{}",
    );
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, PersonaWorkbenchTab] =>
          typeof entry[0] === "string" &&
          VALID_TABS.has(entry[1] as PersonaWorkbenchTab),
      ),
    );
  } catch {
    return {};
  }
}

export function rememberedWorkbenchTab(
  agentId: string | null | undefined,
): PersonaWorkbenchTab | null {
  const key = agentId?.trim() || "general";
  return readOverrides()[key] ?? null;
}

export function rememberWorkbenchTab(
  agentId: string | null | undefined,
  tab: string,
): void {
  if (
    typeof window === "undefined" ||
    !VALID_TABS.has(tab as PersonaWorkbenchTab)
  ) {
    return;
  }
  const key = agentId?.trim() || "general";
  try {
    window.localStorage.setItem(
      storageKey(),
      JSON.stringify({ ...readOverrides(), [key]: tab }),
    );
  } catch {
    // Storage is a preference only; private mode must not break the workbench.
  }
}

export function preferredWorkbenchTab(
  agentId: string | null | undefined,
  hasBoundProject: boolean,
): PersonaWorkbenchTab | "project" {
  if (hasBoundProject) return "project";
  return (
    rememberedWorkbenchTab(agentId) ??
    workspacePresetForAgent(agentId).defaultWorkbenchTab
  );
}
