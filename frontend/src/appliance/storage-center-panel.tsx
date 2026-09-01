import { useEffect, useMemo, useState } from "react";
import {
  ArchiveIcon,
  AudioLinesIcon,
  FileTextIcon,
  FilmIcon,
  FolderIcon,
  FoldersIcon,
  HardDriveIcon,
  ImageIcon,
  Loader2Icon,
  PackageIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  Trash2Icon,
  TriangleAlertIcon,
  UploadCloudIcon,
  XIcon,
} from "lucide-react";

import {
  fetchStorageUsage,
  formatSize,
  type StorageUsage,
} from "@/appliance/files";
import { OmvSharingPanel } from "@/appliance/omv-sharing-panel";
import { OmvStorageHealth } from "@/appliance/omv-storage-health";
import { cn } from "@/lib/utils";

type StorageCenterSection = "overview" | "health" | "sharing";

const CATEGORY_META = {
  photos: { label: "照片", icon: ImageIcon, color: "bg-rose-500" },
  videos: { label: "视频", icon: FilmIcon, color: "bg-violet-500" },
  audio: { label: "音频", icon: AudioLinesIcon, color: "bg-cyan-500" },
  documents: { label: "文档", icon: FileTextIcon, color: "bg-blue-500" },
  archives: { label: "归档", icon: ArchiveIcon, color: "bg-amber-500" },
  other: { label: "其他", icon: PackageIcon, color: "bg-slate-500" },
} as const;

function percent(value: number, total: number) {
  return total > 0 ? Math.max(0, Math.min(100, (value / total) * 100)) : 0;
}

function count(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function StorageOverview({ onOpenFiles }: { onOpenFiles?: () => void }) {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = (fresh = false) => {
    setLoading(true);
    setError(null);
    fetchStorageUsage(fresh)
      .then(setUsage)
      .catch((reason) => {
        setUsage(null);
        setError(reason instanceof Error ? reason.message : "无法读取存储容量");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => refresh(), []);

  const categories = useMemo(
    () => (usage?.categories ?? []).filter((category) => category.bytes > 0),
    [usage],
  );
  const capacityState = !usage
    ? "unknown"
    : usage.disk.usedPercent >= 95
      ? "critical"
      : usage.disk.usedPercent >= 90
        ? "warning"
        : "healthy";
  const largestFolderBytes = usage?.topFolders[0]?.bytes ?? 0;

  return (
    <div className="mx-auto w-full max-w-[980px] px-7 py-7">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[25px] font-semibold tracking-tight text-slate-900">
            存储概览
          </h1>
          <p className="mt-1 text-[12px] text-slate-500">
            本机容量、内容分布、回收站与上传预留，一处看清
          </p>
        </div>
        <button
          type="button"
          onClick={() => refresh(true)}
          disabled={loading}
          className="inline-flex h-9 items-center gap-1.5 rounded-full bg-white/78 px-3.5 text-[11px] font-semibold text-slate-600 shadow-sm ring-1 ring-slate-200 transition hover:bg-white disabled:opacity-50"
        >
          <RefreshCwIcon
            className={cn("size-3.5", loading && "animate-spin")}
          />
          重新分析
        </button>
      </header>

      {error && (
        <div
          role="alert"
          className="mt-5 rounded-2xl bg-red-50 px-4 py-3 text-xs text-red-700 ring-1 ring-red-100"
        >
          {error}
        </div>
      )}

      {loading && !usage ? (
        <div className="grid min-h-72 place-items-center text-slate-400">
          <span className="flex items-center gap-2 text-sm">
            <Loader2Icon className="size-5 animate-spin" /> 正在分析本机存储…
          </span>
        </div>
      ) : usage ? (
        <>
          <section className="mt-5 overflow-hidden rounded-[22px] bg-gradient-to-br from-slate-900 via-slate-800 to-blue-950 p-5 text-white shadow-[0_20px_50px_rgba(15,23,42,.22)]">
            <div className="flex flex-wrap items-start justify-between gap-5">
              <div>
                <span className="text-[11px] font-medium text-white/55">
                  设备总容量
                </span>
                <div className="mt-1 text-[30px] font-semibold tracking-tight">
                  {formatSize(usage.disk.totalBytes)}
                </div>
                <p className="mt-1 text-[11px] text-white/55">
                  可用 {formatSize(usage.disk.freeBytes)} · 文件库占用{" "}
                  {formatSize(usage.library.logicalBytes)}
                </p>
              </div>
              <div
                className={cn(
                  "flex items-center gap-2 rounded-full px-3 py-1.5 text-[11px] font-semibold",
                  capacityState === "critical"
                    ? "bg-red-400/18 text-red-100"
                    : capacityState === "warning"
                      ? "bg-amber-300/18 text-amber-100"
                      : "bg-emerald-300/16 text-emerald-100",
                )}
              >
                {capacityState === "healthy" ? (
                  <ShieldCheckIcon className="size-4" />
                ) : (
                  <TriangleAlertIcon className="size-4" />
                )}
                已使用 {usage.disk.usedPercent}%
              </div>
            </div>
            <div className="mt-5 flex h-3 overflow-hidden rounded-full bg-white/10">
              {categories.map((category) => {
                const meta = CATEGORY_META[category.id];
                return (
                  <span
                    key={category.id}
                    className={meta.color}
                    style={{
                      width: `${percent(category.bytes, usage.disk.totalBytes)}%`,
                      minWidth: category.bytes > 0 ? 3 : 0,
                    }}
                    title={`${meta.label} ${formatSize(category.bytes)}`}
                  />
                );
              })}
              {usage.trash.bytes > 0 && (
                <span
                  className="bg-slate-300/75"
                  style={{
                    width: `${percent(usage.trash.bytes, usage.disk.totalBytes)}%`,
                    minWidth: 3,
                  }}
                  title={`回收站 ${formatSize(usage.trash.bytes)}`}
                />
              )}
            </div>
            <div className="mt-2 flex justify-between text-[10px] text-white/45">
              <span>已用 {formatSize(usage.disk.usedBytes)}</span>
              <span>
                上传可用 {formatSize(usage.disk.availableForUploadsBytes)}
              </span>
            </div>
          </section>

          <section className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            {categories.map((category) => {
              const meta = CATEGORY_META[category.id];
              const Icon = meta.icon;
              return (
                <article
                  key={category.id}
                  className="rounded-[18px] bg-white/76 p-4 shadow-[0_8px_28px_rgba(51,65,85,.07)] ring-1 ring-white/90"
                >
                  <span
                    className={cn(
                      "grid size-9 place-items-center rounded-xl text-white shadow-sm",
                      meta.color,
                    )}
                  >
                    <Icon className="size-[18px]" />
                  </span>
                  <strong className="mt-3 block text-[16px] text-slate-900">
                    {formatSize(category.bytes)}
                  </strong>
                  <span className="mt-0.5 block text-[10px] text-slate-400">
                    {meta.label} · {count(category.files)} 个文件
                  </span>
                </article>
              );
            })}
            {categories.length === 0 && (
              <p className="col-span-full rounded-[18px] bg-white/70 p-5 text-sm text-slate-500">
                文件库还是空的，可以从文件管理器上传内容。
              </p>
            )}
          </section>

          <div className="mt-4 grid gap-4 lg:grid-cols-[1.35fr_.9fr]">
            <section className="rounded-[20px] bg-white/78 p-5 shadow-[0_10px_30px_rgba(51,65,85,.07)] ring-1 ring-white/90">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-[14px] font-semibold text-slate-900">
                    占用最多的文件夹
                  </h2>
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    仅统计 NAS 文件库，不读取文件内容
                  </p>
                </div>
                {onOpenFiles && (
                  <button
                    type="button"
                    onClick={onOpenFiles}
                    className="rounded-full bg-blue-50 px-3 py-1.5 text-[10px] font-semibold text-blue-700 transition hover:bg-blue-100"
                  >
                    管理文件
                  </button>
                )}
              </div>
              <div className="mt-4 space-y-3">
                {usage.topFolders.length === 0 ? (
                  <p className="text-xs text-slate-400">暂无顶层文件夹数据</p>
                ) : (
                  usage.topFolders.slice(0, 7).map((folder) => (
                    <div key={folder.name}>
                      <div className="flex items-center justify-between gap-3 text-[11px]">
                        <span className="flex min-w-0 items-center gap-1.5 font-medium text-slate-700">
                          <FolderIcon className="size-3.5 shrink-0 text-blue-500" />
                          <span className="truncate">{folder.name}</span>
                        </span>
                        <span className="shrink-0 text-slate-400">
                          {formatSize(folder.bytes)}
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-blue-500 to-cyan-400"
                          style={{
                            width: `${percent(folder.bytes, largestFolderBytes)}%`,
                          }}
                        />
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="space-y-3">
              <article className="rounded-[18px] bg-white/78 p-4 shadow-[0_10px_28px_rgba(51,65,85,.06)] ring-1 ring-white/90">
                <div className="flex items-center gap-3">
                  <span className="grid size-9 place-items-center rounded-xl bg-slate-100 text-slate-600">
                    <Trash2Icon className="size-4" />
                  </span>
                  <div>
                    <strong className="block text-[13px] text-slate-800">
                      回收站 {formatSize(usage.trash.bytes)}
                    </strong>
                    <span className="text-[10px] text-slate-400">
                      {count(usage.trash.files)}{" "}
                      个文件，可在文件管理器恢复或清空
                    </span>
                  </div>
                </div>
              </article>
              <article className="rounded-[18px] bg-white/78 p-4 shadow-[0_10px_28px_rgba(51,65,85,.06)] ring-1 ring-white/90">
                <div className="flex items-center gap-3">
                  <span className="grid size-9 place-items-center rounded-xl bg-cyan-50 text-cyan-600">
                    <UploadCloudIcon className="size-4" />
                  </span>
                  <div>
                    <strong className="block text-[13px] text-slate-800">
                      {usage.uploads.active > 0
                        ? `${usage.uploads.active} 个上传任务保留空间`
                        : "当前没有上传预留"}
                    </strong>
                    <span className="text-[10px] text-slate-400">
                      已预留 {formatSize(usage.uploads.reservedBytes)}
                      ，不会与新上传争抢容量
                    </span>
                  </div>
                </div>
              </article>
              {usage.quotas.map((quota) => (
                <article
                  key={quota.path}
                  className="rounded-[18px] bg-white/78 p-4 shadow-[0_10px_28px_rgba(51,65,85,.06)] ring-1 ring-white/90"
                >
                  <div className="flex items-center justify-between gap-3 text-[11px]">
                    <strong className="truncate text-slate-800">
                      {quota.path === "." ? "文件库" : quota.path} 配额
                    </strong>
                    <span className="text-slate-400">
                      {quota.estimated ? "估算 " : ""}
                      {Math.round(
                        percent(
                          quota.usedBytes + quota.reservedBytes,
                          quota.limitBytes,
                        ),
                      )}
                      %
                    </span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-blue-500"
                      style={{
                        width: `${percent(quota.usedBytes + quota.reservedBytes, quota.limitBytes)}%`,
                      }}
                    />
                  </div>
                </article>
              ))}
            </section>
          </div>

          {(usage.library.truncated || usage.library.skippedLinks > 0) && (
            <p className="mt-4 rounded-xl bg-amber-50 px-4 py-3 text-[11px] leading-5 text-amber-800">
              {usage.library.truncated
                ? `文件超过 ${count(usage.library.maxEntries)} 项，本次展示为安全上限内的估算。`
                : ""}
              {usage.library.skippedLinks > 0
                ? ` 已跳过 ${count(usage.library.skippedLinks)} 个符号链接，不会读取 NAS 根目录之外的内容。`
                : ""}
            </p>
          )}
        </>
      ) : null}
    </div>
  );
}

export function StorageCenterPanel({
  open,
  onClose,
  onOpenFiles,
}: {
  open: boolean;
  onClose: () => void;
  onOpenFiles?: () => void;
}) {
  const [section, setSection] = useState<StorageCenterSection>("overview");

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  const sections = [
    { id: "overview" as const, label: "容量概览", icon: HardDriveIcon },
    { id: "health" as const, label: "磁盘健康", icon: ShieldCheckIcon },
    { id: "sharing" as const, label: "共享与用户", icon: FoldersIcon },
  ];

  return (
    <div
      className="fixed inset-0 z-[89] flex items-center justify-center bg-slate-950/18 p-4 backdrop-blur-[2px]"
      data-desktop-interactive
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label="存储中心"
        className="relative flex h-[min(780px,calc(100vh-56px))] w-[min(1120px,calc(100vw-28px))] overflow-hidden rounded-[24px] border border-white/72 bg-slate-50/94 text-slate-900 shadow-[0_34px_100px_rgba(15,23,42,.34)] backdrop-blur-3xl"
      >
        <aside className="w-[210px] shrink-0 border-r border-slate-200/75 bg-white/52 p-4 backdrop-blur-2xl">
          <div className="flex items-center gap-2 px-1">
            <button
              type="button"
              aria-label="关闭存储中心"
              onClick={onClose}
              className="grid size-3.5 place-items-center rounded-full bg-[#ff5f57] text-transparent hover:text-red-900/70"
            >
              <XIcon className="size-2.5" />
            </button>
            <span className="size-3.5 rounded-full bg-[#febc2e]" />
            <span className="size-3.5 rounded-full bg-[#28c840]" />
          </div>
          <div className="mt-7 px-2">
            <span className="grid size-12 place-items-center rounded-[15px] bg-gradient-to-br from-cyan-400 to-blue-700 text-white shadow-lg shadow-blue-500/20">
              <HardDriveIcon className="size-6" />
            </span>
            <h1 className="mt-3 text-[18px] font-semibold tracking-tight">
              存储中心
            </h1>
            <p className="mt-1 text-[10px] leading-4 text-slate-400">
              容量、磁盘与家庭共享
            </p>
          </div>
          <nav className="mt-7 space-y-1" aria-label="存储中心导航">
            {sections.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setSection(item.id)}
                  className={cn(
                    "flex h-10 w-full items-center gap-2.5 rounded-xl px-3 text-left text-[12px] font-semibold transition",
                    section === item.id
                      ? "bg-blue-600 text-white shadow-sm"
                      : "text-slate-600 hover:bg-white/82 hover:text-slate-900",
                  )}
                >
                  <Icon className="size-4" />
                  {item.label}
                </button>
              );
            })}
          </nav>
          <div className="mt-auto hidden" />
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_85%_0%,rgba(125,211,252,.16),transparent_38%)]">
          {section === "overview" ? (
            <StorageOverview onOpenFiles={onOpenFiles} />
          ) : section === "health" ? (
            <div className="mx-auto w-full max-w-[980px] px-7 py-7">
              <OmvStorageHealth />
            </div>
          ) : (
            <div className="mx-auto w-full max-w-[980px] px-7 py-7">
              <OmvSharingPanel />
            </div>
          )}
        </main>
      </section>
    </div>
  );
}
