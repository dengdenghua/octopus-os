import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  BrainCircuitIcon,
  ImageIcon,
  ImagesIcon,
  Loader2Icon,
  RefreshCwIcon,
  ScanFaceIcon,
  SearchIcon,
  SparklesIcon,
  UnplugIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyPhotoIndex,
  createPhotoIndexPlan,
  fetchPhotoLibrary,
  fetchPhotoStatus,
  photoOriginalUrl,
  photoThumbnailUrl,
  searchPhotos,
  type PhotoIndexPlan,
  type PhotoItem,
  type PhotoLibrary,
  type PhotoStatus,
} from "@/appliance/photos";
import { cn } from "@/lib/utils";

function countLabel(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function PhotoTile({
  photo,
  onOpen,
}: {
  photo: PhotoItem;
  onOpen: () => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group relative aspect-square overflow-hidden rounded-[16px] bg-white/52 text-left shadow-[0_8px_24px_rgba(51,65,85,.07)] transition hover:-translate-y-0.5 hover:shadow-[0_16px_32px_rgba(51,65,85,.14)]"
      aria-label={`查看 ${photo.name}`}
    >
      {failed ? (
        <span className="grid size-full place-items-center bg-gradient-to-br from-rose-100 to-orange-100 text-rose-300">
          <ImageIcon className="size-9" strokeWidth={1.5} />
        </span>
      ) : (
        <img
          src={photoThumbnailUrl(photo.path)}
          alt={photo.name}
          loading="lazy"
          onError={() => setFailed(true)}
          className="size-full object-cover transition duration-300 group-hover:scale-[1.025]"
        />
      )}
      <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-slate-950/66 via-slate-950/16 to-transparent px-3 pb-2.5 pt-9 opacity-0 transition group-hover:opacity-100">
        <span className="block truncate text-[11px] font-medium text-white">
          {photo.name}
        </span>
      </span>
      {photo.indexed && (
        <span className="absolute right-2 top-2 grid size-5 place-items-center rounded-full bg-slate-950/35 text-white backdrop-blur-md">
          <SparklesIcon className="size-3" />
        </span>
      )}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[104px] rounded-[16px] bg-white/58 px-3.5 py-3 shadow-[0_8px_22px_rgba(51,65,85,.05)]">
      <div className="text-lg font-semibold tracking-tight text-slate-900">
        {value}
      </div>
      <div className="mt-0.5 text-[10px] font-medium text-slate-400">
        {label}
      </div>
    </div>
  );
}

export function PhotosPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [library, setLibrary] = useState<PhotoLibrary | null>(null);
  const [status, setStatus] = useState<PhotoStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"semantic" | "filename" | null>(
    null,
  );
  const [searching, setSearching] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [includeFaces, setIncludeFaces] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [pendingPlan, setPendingPlan] = useState<PhotoIndexPlan | null>(null);
  const [selected, setSelected] = useState<PhotoItem | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextLibrary, nextStatus] = await Promise.all([
        fetchPhotoLibrary(),
        fetchPhotoStatus(),
      ]);
      setLibrary(nextLibrary);
      setStatus(nextStatus);
      setSearchMode(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "照片库暂时不可用");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void refresh();
  }, [open]);

  useEffect(() => {
    if (!open || status?.job.state !== "running") return;
    const timer = window.setInterval(() => {
      fetchPhotoStatus()
        .then((next) => {
          setStatus(next);
          if (next.job.state === "succeeded") {
            toast.success("照片智能索引已建立");
            void fetchPhotoLibrary().then(setLibrary);
          } else if (next.job.state === "failed") {
            toast.error("照片智能索引建立失败，可稍后重试");
          }
        })
        .catch(() => undefined);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [open, status?.job.state]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (selected) setSelected(null);
      else if (!pendingPlan) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open, pendingPlan, selected]);

  const photos = library?.items ?? [];
  const indexedPercent = useMemo(() => {
    const total = status?.library.imageCount ?? 0;
    if (!total) return 0;
    return Math.min(
      100,
      Math.round(((status?.index.indexed ?? 0) / total) * 100),
    );
  }, [status]);

  const submitSearch = async (event: FormEvent) => {
    event.preventDefault();
    const value = query.trim();
    if (!value) {
      await refresh();
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const result = await searchPhotos(value);
      setLibrary({
        schema: "echo.photos.library.v1",
        total: result.total,
        offset: 0,
        limit: 50,
        scanTruncated: false,
        unsafeLinksSkipped: status?.library.unsafeLinksSkipped ?? 0,
        items: result.items,
      });
      setSearchMode(result.mode);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "照片搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const beginIndex = async () => {
    if (planning || status?.job.state === "running") return;
    setPlanning(true);
    try {
      const plan = await createPhotoIndexPlan(includeFaces);
      if (!plan.ready) {
        toast.error(plan.blockers[0]?.message || "智能索引暂不可用");
        void fetchPhotoStatus().then(setStatus);
        return;
      }
      setPendingPlan(plan);
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "无法检查索引计划",
      );
    } finally {
      setPlanning(false);
    }
  };

  const confirmIndex = async (password: string) => {
    if (!pendingPlan) return;
    const approval = await requestHighRiskApproval(
      "photos.index.build",
      pendingPlan.planId,
      password,
    );
    const result = await applyPhotoIndex(
      pendingPlan.planId,
      pendingPlan.includeFaces,
      approval.approvalToken,
    );
    setPendingPlan(null);
    setStatus((current) =>
      current ? { ...current, job: result.job } : current,
    );
    toast.success("已在设备后台开始建立索引");
  };

  const loadMore = async () => {
    if (!library || loadingMore || searchMode) return;
    setLoadingMore(true);
    try {
      const next = await fetchPhotoLibrary("", library.items.length, 120);
      setLibrary((current) => {
        if (!current) return next;
        const known = new Set(current.items.map((item) => item.path));
        return {
          ...next,
          offset: 0,
          items: [
            ...current.items,
            ...next.items.filter((item) => !known.has(item.path)),
          ],
        };
      });
    } catch (reason) {
      toast.error(
        reason instanceof Error ? reason.message : "更多照片读取失败",
      );
    } finally {
      setLoadingMore(false);
    }
  };

  if (!open) return null;

  return (
    <>
      <div
        data-desktop-interactive
        className="fixed inset-0 z-[88] flex items-center justify-center bg-slate-950/18 p-4 backdrop-blur-[2px]"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <section
          role="dialog"
          aria-modal="true"
          aria-label="照片"
          className="relative flex h-[min(800px,calc(100vh-64px))] w-[min(1160px,calc(100vw-32px))] flex-col overflow-hidden rounded-[24px] border border-white/72 bg-slate-50/91 text-slate-900 shadow-[0_34px_100px_rgba(15,23,42,.34)] backdrop-blur-3xl"
        >
          <header className="relative flex h-14 shrink-0 items-center bg-white/52 px-5 shadow-[0_1px_0_rgba(148,163,184,.18)]">
            <div className="flex gap-2">
              <button
                type="button"
                aria-label="关闭照片"
                onClick={onClose}
                className="grid size-3.5 place-items-center rounded-full bg-[#ff5f57] text-transparent hover:text-red-900/70"
              >
                <XIcon className="size-2.5" />
              </button>
              <span className="size-3.5 rounded-full bg-[#febc2e]" />
              <span className="size-3.5 rounded-full bg-[#28c840]" />
            </div>
            <div className="pointer-events-none absolute left-1/2 flex -translate-x-1/2 items-center gap-2 text-sm font-semibold text-slate-700">
              <ImagesIcon className="size-4 text-rose-500" />
              照片
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              aria-label="刷新照片库"
              className="ml-auto grid size-8 place-items-center rounded-full text-slate-500 transition hover:bg-slate-200/70 disabled:opacity-50"
            >
              <RefreshCwIcon
                className={cn("size-4", loading && "animate-spin")}
              />
            </button>
          </header>

          <div className="shrink-0 px-6 pb-5 pt-6">
            <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
              <div>
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.16em] text-rose-500">
                  <SparklesIcon className="size-3.5" />
                  本地智能相册
                </div>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
                  你的照片，只在这台设备理解
                </h1>
                <p className="mt-1.5 max-w-xl text-[13px] leading-5 text-slate-500">
                  浏览 NAS
                  原图，缩略图与语义索引都在本地生成。智能索引只读取照片，不移动、不删除原文件。
                </p>
              </div>
              <form
                onSubmit={submitSearch}
                className="flex h-11 w-full items-center gap-2 rounded-full bg-white/82 px-4 shadow-[inset_0_0_0_1px_rgba(148,163,184,.24),0_5px_18px_rgba(51,65,85,.06)] xl:w-[360px]"
              >
                {searching ? (
                  <Loader2Icon className="size-4 animate-spin text-rose-400" />
                ) : (
                  <SearchIcon className="size-4 text-slate-400" />
                )}
                <input
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  placeholder="试试“海边的家人”或文件名"
                  aria-label="搜索照片"
                  className="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
                {query && (
                  <button
                    type="button"
                    aria-label="清除照片搜索"
                    onClick={() => {
                      setQuery("");
                      void refresh();
                    }}
                    className="grid size-6 place-items-center rounded-full text-slate-400 hover:bg-slate-100"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                )}
              </form>
            </div>

            <div className="mt-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex gap-2 overflow-x-auto pb-1">
                <Metric
                  label="媒体库"
                  value={countLabel(
                    status?.library.imageCount ?? library?.total ?? 0,
                  )}
                />
                <Metric
                  label="已理解"
                  value={`${countLabel(status?.index.indexed ?? 0)} · ${indexedPercent}%`}
                />
                <Metric
                  label="人脸记录"
                  value={countLabel(status?.index.faces ?? 0)}
                />
                <Metric
                  label="重复组 / 模糊"
                  value={`${status?.index.duplicateGroups ?? 0} / ${status?.index.blurry ?? 0}`}
                />
              </div>
              <div className="flex shrink-0 items-center gap-2 rounded-full bg-white/58 p-1.5 pl-3">
                <label className="flex cursor-pointer items-center gap-2 text-[11px] font-medium text-slate-500">
                  <ScanFaceIcon className="size-3.5" />
                  人物聚类
                  <input
                    type="checkbox"
                    checked={includeFaces}
                    onChange={(event) =>
                      setIncludeFaces(event.currentTarget.checked)
                    }
                    disabled={status?.job.state === "running"}
                    className="size-3.5 accent-rose-500"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void beginIndex()}
                  disabled={
                    planning ||
                    status?.job.state === "running" ||
                    !status?.index.backendAvailable
                  }
                  className="inline-flex h-9 items-center gap-1.5 rounded-full bg-slate-900 px-4 text-[11px] font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {planning || status?.job.state === "running" ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : status?.index.databaseExists ? (
                    <RefreshCwIcon className="size-3.5" />
                  ) : (
                    <BrainCircuitIcon className="size-3.5" />
                  )}
                  {status?.job.state === "running"
                    ? "后台索引中"
                    : status?.index.databaseExists
                      ? "更新智能索引"
                      : "建立智能索引"}
                </button>
              </div>
            </div>

            {searchMode && (
              <div className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-500">
                {searchMode === "semantic" ? (
                  <SparklesIcon className="size-3.5 text-rose-500" />
                ) : (
                  <SearchIcon className="size-3.5" />
                )}
                {searchMode === "semantic"
                  ? `本地语义结果 · ${library?.total ?? 0} 张`
                  : `智能索引未就绪，已按文件名查找 · ${library?.total ?? 0} 张`}
              </div>
            )}
            {(status?.library.unsafeLinksSkipped ?? 0) > 0 && (
              <p className="mt-2 text-[10px] text-slate-400">
                已安全跳过 {status?.library.unsafeLinksSkipped}{" "}
                个图片链接，不会读取链接目标。
              </p>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
            {loading && !library ? (
              <div className="grid h-64 place-items-center text-sm text-slate-400">
                <span className="flex items-center gap-2">
                  <Loader2Icon className="size-4 animate-spin" />
                  正在整理照片时间线…
                </span>
              </div>
            ) : error ? (
              <div className="grid h-64 place-items-center text-center">
                <div>
                  <UnplugIcon className="mx-auto size-8 text-slate-300" />
                  <p className="mt-3 text-sm font-medium text-slate-700">
                    {error}
                  </p>
                  <button
                    type="button"
                    onClick={() => void refresh()}
                    className="mt-3 rounded-full bg-slate-900 px-4 py-2 text-xs font-medium text-white"
                  >
                    重新读取
                  </button>
                </div>
              </div>
            ) : photos.length === 0 ? (
              <div className="grid h-64 place-items-center text-center">
                <div>
                  <ImagesIcon
                    className="mx-auto size-10 text-slate-300"
                    strokeWidth={1.4}
                  />
                  <p className="mt-3 text-sm font-medium text-slate-700">
                    {query ? "没有找到匹配的照片" : "NAS 中还没有照片"}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    {query
                      ? "换一种描述试试"
                      : "通过文件管家上传后会自动出现在这里"}
                  </p>
                </div>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                  {photos.map((photo) => (
                    <PhotoTile
                      key={photo.path}
                      photo={photo}
                      onOpen={() => setSelected(photo)}
                    />
                  ))}
                </div>
                {!searchMode && photos.length < (library?.total ?? 0) && (
                  <div className="flex justify-center pb-2 pt-5">
                    <button
                      type="button"
                      onClick={() => void loadMore()}
                      disabled={loadingMore}
                      className="inline-flex h-9 items-center gap-2 rounded-full bg-white/72 px-4 text-[11px] font-semibold text-slate-600 shadow-[0_7px_20px_rgba(51,65,85,.07)] transition hover:bg-white disabled:opacity-50"
                    >
                      {loadingMore && (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      )}
                      {loadingMore
                        ? "正在读取"
                        : `加载更多 · ${photos.length} / ${library?.total ?? 0}`}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </section>
      </div>

      {selected && (
        <div
          className="fixed inset-0 z-[120] grid place-items-center bg-slate-950/72 p-6 backdrop-blur-xl"
          data-desktop-interactive
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelected(null);
          }}
        >
          <button
            type="button"
            aria-label="关闭照片预览"
            onClick={() => setSelected(null)}
            className="absolute right-6 top-6 grid size-10 place-items-center rounded-full bg-white/12 text-white backdrop-blur-md hover:bg-white/20"
          >
            <XIcon className="size-5" />
          </button>
          <div className="max-h-full max-w-full text-center">
            <img
              src={photoOriginalUrl(selected.path)}
              alt={selected.name}
              className="mx-auto max-h-[calc(100vh-130px)] max-w-[min(900px,calc(100vw-60px))] rounded-[18px] object-contain shadow-2xl"
            />
            <div className="mt-4 text-sm font-medium text-white">
              {selected.name}
            </div>
            <div className="mt-1 text-[11px] text-white/55">
              {selected.width && selected.height
                ? `${selected.width} × ${selected.height} · `
                : ""}
              {selected.path}
            </div>
          </div>
        </div>
      )}

      <HighRiskApprovalDialog
        open={pendingPlan !== null}
        title="建立本地照片智能索引？"
        description={
          pendingPlan
            ? `设备将读取最多 ${countLabel(Math.min(pendingPlan.imageCount, pendingPlan.maxFiles))} 张照片，生成语义${pendingPlan.includeFaces ? "与人物" : ""}索引。原图不会被修改、移动或上传。`
            : ""
        }
        targetLabel={
          pendingPlan
            ? `照片 ${countLabel(pendingPlan.imageCount)} 张 · 仅本机`
            : undefined
        }
        confirmLabel="开始建立"
        onCancel={() => setPendingPlan(null)}
        onConfirm={confirmIndex}
      />
    </>
  );
}
