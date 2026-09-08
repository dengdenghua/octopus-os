import { useLocation, useNavigate } from "react-router-dom";
import { useActiveAgentId } from "@/core/agents/active";
import {
  setModuleEnabled,
  useUserEnabledModuleIds,
} from "@/core/modules/enabled-modules";
import {
  supportsPresentation,
  type WorkbenchBuiltinApp,
} from "@/core/workbench/apps";
import { preserveWorkbenchPresentation } from "@/core/router/desktop-workspace-route";

/** System apps share their identity and sidebar preference with the workbench. */
export function CoreAppCard({
  app,
  onOpen,
  onOpenWorkbench,
}: {
  app: WorkbenchBuiltinApp;
  onOpen?: (route: string) => void;
  onOpenWorkbench?: (route: string) => void;
}) {
  const navigate = useNavigate();
  const { search } = useLocation();
  const standalone = supportsPresentation(app, "standalone");
  const workbench = supportsPresentation(app, "workbench");
  const openWorkbench = () =>
    onOpenWorkbench
      ? onOpenWorkbench(app.workspaceRoute)
      : navigate(preserveWorkbenchPresentation(app.workspaceRoute, search));
  const agentId = useActiveAgentId() ?? "general";
  const pinned = useUserEnabledModuleIds(agentId).includes(app.moduleId);
  return (
    <article className="flex flex-col gap-3 rounded-2xl bg-white/80 p-4 text-slate-900">
      <h3 className="font-semibold">{app.name}</h3>
      <p className="text-xs text-slate-500">系统内置 · 已安装</p>
      <p className="text-xs text-slate-500">
        {standalone && workbench
          ? "独立窗口 · 工作台内嵌"
          : standalone
            ? "独立窗口"
            : workbench
              ? "工作台内嵌"
              : "未声明呈现方式"}
      </p>
      <p className="flex-1 text-sm text-slate-600">{app.description}</p>
      <div className="flex flex-wrap justify-end gap-2 text-xs">
        {workbench && (
          <button
            className="rounded-full border px-3 py-2"
            aria-pressed={pinned}
            onClick={() => setModuleEnabled(app.moduleId, !pinned, agentId)}
          >
            {pinned ? "从侧栏移除" : "添加到侧栏"}
          </button>
        )}
        {standalone && onOpen && (
          <button
            className="rounded-full border px-3 py-2"
            onClick={() => onOpen(app.workspaceRoute)}
          >
            独立窗口打开
          </button>
        )}
        {workbench && (
          <button
            className="rounded-full bg-slate-900 px-3 py-2 text-white"
            onClick={openWorkbench}
          >
            在工作台中打开
          </button>
        )}
      </div>
    </article>
  );
}
