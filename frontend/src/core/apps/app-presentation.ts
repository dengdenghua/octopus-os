export type AppPresentation = "standalone" | "workbench";

export const APP_PRESENTATION_LABELS: Record<AppPresentation, string> = {
  standalone: "独立窗口",
  workbench: "工作台内嵌",
};

export const APP_PRESENTATION_DESCRIPTIONS: Record<AppPresentation, string> = {
  standalone: "安装到设备后，以独立窗口打开完整 Web 界面。",
  workbench: "安装后直接嵌入工作台，复用当前任务与上下文。",
};

export function isWorkbenchApplication(asset: { kind: string }): boolean {
  return asset.kind === "workbench";
}

export const OPEN_ECHO_HUB_EVENT = "echo:open-hub";

export function requestOpenEchoHub(appId?: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(OPEN_ECHO_HUB_EVENT, {
      detail: appId ? { appId } : undefined,
    }),
  );
}
