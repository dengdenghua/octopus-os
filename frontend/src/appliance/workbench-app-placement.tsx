import { useActiveAgentId } from "@/core/agents/active";
import {
  setModuleEnabled,
  useUserEnabledModuleIds,
} from "@/core/modules/enabled-modules";
import {
  supportsPresentation,
  WORKBENCH_BUILTIN_APPS,
} from "@/core/workbench/apps";
import type { AgentHubAsset } from "./agent-assets";

export function workbenchAppForAsset(
  asset: Pick<AgentHubAsset, "kind" | "id" | "installId">,
) {
  if (asset.kind !== "workbench") return undefined;
  return WORKBENCH_BUILTIN_APPS.find(
    (app) =>
      app.packageId === asset.installId ||
      (app.cloudId && asset.id === `workbench:${app.cloudId}`),
  );
}

export function canOpenWorkbenchAsset(asset: AgentHubAsset): boolean {
  return (
    asset.installed &&
    asset.enabled &&
    !asset.permissionReviewRequired &&
    asset.lifecycleState !== "broken" &&
    asset.compatibility !== "incompatible"
  );
}

export function WorkbenchAppPlacement({
  asset,
  onOpenWindow,
  onOpenWorkbench,
}: {
  asset: AgentHubAsset;
  onOpenWindow?: (route: string) => void;
  onOpenWorkbench?: (route: string) => void;
}) {
  const agentId = useActiveAgentId() ?? "general";
  const pinnedIds = useUserEnabledModuleIds(agentId);
  const app = workbenchAppForAsset(asset);
  if (!app || !canOpenWorkbenchAsset(asset)) return null;
  const pinned = pinnedIds.includes(app.moduleId);
  const canOpenStandalone = supportsPresentation(app, "standalone");
  const canOpenWorkbench = supportsPresentation(app, "workbench");
  return (
    <div className="mt-3 flex flex-wrap justify-end gap-2 text-xs">
      {canOpenWorkbench && (
        <button
          type="button"
          aria-label={`${pinned ? "从侧栏移除" : "添加到侧栏"}“${app.name}”`}
          aria-pressed={pinned}
          className="rounded-full border px-3 py-1.5"
          onClick={() => setModuleEnabled(app.moduleId, !pinned, agentId)}
        >
          {pinned ? "从侧栏移除" : "添加到侧栏"}
        </button>
      )}
      {onOpenWorkbench && canOpenWorkbench && (
        <button
          type="button"
          className="rounded-full border px-3 py-1.5"
          onClick={() => onOpenWorkbench(app.workspaceRoute)}
        >
          在工作台中打开
        </button>
      )}
      {onOpenWindow && canOpenStandalone && (
        <button
          type="button"
          className="rounded-full border px-3 py-1.5"
          onClick={() => onOpenWindow(app.workspaceRoute)}
        >
          独立窗口打开
        </button>
      )}
    </div>
  );
}
