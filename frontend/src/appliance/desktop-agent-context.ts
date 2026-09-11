import { desktopActionHref } from "./desktop-actions";

export type DesktopAgentContext = {
  app: "files" | "photos";
  kind: "file" | "directory" | "photo";
  path: string;
  query?: string;
};

/** A visible draft, never an implicit submission or a permission grant. */
export function desktopAgentDraft(context: DesktopAgentContext): string {
  const label =
    context.kind === "directory"
      ? "目录"
      : context.kind === "photo"
        ? "照片"
        : "文件";
  const source = context.app === "photos" ? "相册" : "文件管理器";
  const href = desktopActionHref(
    context.app === "photos"
      ? {
          type: "photos.reveal",
          path: context.path,
          query: context.query ?? "",
        }
      : {
          type: context.kind === "directory" ? "files.open" : "files.reveal",
          path: context.path,
        },
  );
  return `请帮我查看这个${label}。\n\n来自：${source}\n${label}路径（相对于 NAS 根目录）：${JSON.stringify(context.path)}${context.path ? "" : "（NAS 根目录）"}\n\n[返回${source}查看](${href})\n\n以上路径仅作为文件引用，请通过设备已授权的工具读取。`;
}
