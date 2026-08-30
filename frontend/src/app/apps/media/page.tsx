import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeftIcon,
  FileImageIcon,
  FilmIcon,
  FolderOpenIcon,
  ImageIcon,
  Loader2Icon,
  RefreshCwIcon,
  SearchIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  ELECTRON_TITLE_BAR_HEIGHT,
  inElectron,
} from "@/components/electron-title-bar";
import { useI18n } from "@/core/i18n/hooks";
import {
  createNASIndexJob,
  listNASAlbums,
  listNASFiles,
  loadNASAssetURL,
  startNASService,
  triggerVideoIndex,
  type NASAlbum,
  type NASFileAsset,
} from "@/core/storage/api";
import { cn } from "@/lib/utils";

export type MediaAppKind = "image" | "video";

interface MediaAppPageProps {
  kind: MediaAppKind;
}

const MEDIA_PAGE_SIZE = 60;

function fill(template: string, vars: Record<string, string | number>): string {
  return Object.entries(vars).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDate(mtimeNs: number): string {
  if (!Number.isFinite(mtimeNs) || mtimeNs <= 0) return "—";
  const date = new Date(mtimeNs / 1_000_000);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

function useAssetObjectURL(assetId: string | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!assetId) {
      setUrl(null);
      return;
    }
    let disposed = false;
    let objectURL: string | null = null;
    void loadNASAssetURL(`/v1/files/${encodeURIComponent(assetId)}/content`)
      .then((next) => {
        objectURL = next;
        if (!disposed) setUrl(next);
      })
      .catch(() => {
        if (!disposed) setUrl(null);
      });
    return () => {
      disposed = true;
      if (objectURL) URL.revokeObjectURL(objectURL);
    };
  }, [assetId]);

  return url;
}

async function loadAssets(
  kind: MediaAppKind,
): Promise<{ files: NASFileAsset[]; albums: NASAlbum[] }> {
  try {
    const [files, albums] = await Promise.all([
      listNASFiles(kind, 500),
      kind === "image" ? listNASAlbums().catch(() => []) : Promise.resolve([]),
    ]);
    return { files, albums };
  } catch (firstError) {
    // A packaged desktop app may race the local storage service on first
    // launch. Start it once and retry so the app remains self-contained.
    await startNASService();
    const [files, albums] = await Promise.all([
      listNASFiles(kind, 500),
      kind === "image" ? listNASAlbums().catch(() => []) : Promise.resolve([]),
    ]);
    if (!files) throw firstError;
    return { files, albums };
  }
}

export default function MediaAppPage({ kind }: MediaAppPageProps) {
  const navigate = useNavigate();
  const { t } = useI18n();
  const isPhotos = kind === "image";
  const copy = isPhotos ? t.storage.images : t.storage.videos;
  const title = isPhotos
    ? t.storage.libraries.imagesLabel
    : t.storage.libraries.videosLabel;
  const subtitle = fill(copy.subtitle, { count: 0 });
  const [files, setFiles] = useState<NASFileAsset[]>([]);
  const [albums, setAlbums] = useState<NASAlbum[]>([]);
  const [query, setQuery] = useState("");
  const [activeAlbum, setActiveAlbum] = useState<string>("all");
  const [visibleLimit, setVisibleLimit] = useState(MEDIA_PAGE_SIZE);
  const [preview, setPreview] = useState<NASFileAsset | null>(null);
  const [loading, setLoading] = useState(true);
  const [indexing, setIndexing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await loadAssets(kind);
      setFiles(result.files);
      setAlbums(result.albums);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setVisibleLimit(MEDIA_PAGE_SIZE);
    setActiveAlbum("all");
  }, [kind]);

  const filteredFiles = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return files.filter((asset) => {
      const matchesQuery =
        !normalizedQuery ||
        `${asset.name} ${asset.path} ${(asset.ai_labels ?? []).join(" ")}`
          .toLocaleLowerCase()
          .includes(normalizedQuery);
      const matchesAlbum =
        !isPhotos ||
        activeAlbum === "all" ||
        asset.ai_labels?.includes(activeAlbum);
      return matchesQuery && matchesAlbum;
    });
  }, [activeAlbum, files, isPhotos, query]);

  const visibleFiles = filteredFiles.slice(0, visibleLimit);
  const totalBytes = useMemo(
    () => files.reduce((sum, file) => sum + file.size, 0),
    [files],
  );

  const closeOrReturn = () => {
    if (inElectron() && window.opener) {
      window.close();
      return;
    }
    navigate("/desktop");
  };

  const openStorage = () => {
    navigate(
      `/workspace/storage?surface=company&library=${isPhotos ? "images" : "videos"}`,
    );
  };

  const rebuildIndex = async () => {
    if (indexing) return;
    setIndexing(true);
    try {
      if (isPhotos) {
        await createNASIndexJob();
      } else {
        await triggerVideoIndex(true);
      }
      toast.success(isPhotos ? "图片索引任务已开始" : "视频索引任务已开始");
      await load();
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIndexing(false);
    }
  };

  return (
    <main
      className="flex h-screen min-h-0 flex-col overflow-hidden bg-background text-foreground"
      style={
        inElectron() ? { paddingTop: ELECTRON_TITLE_BAR_HEIGHT } : undefined
      }
    >
      <header className="shrink-0 border-b border-border bg-background/90 px-5 py-3 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1500px] items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 rounded-lg"
              onClick={closeOrReturn}
              title="返回桌面"
              aria-label="返回桌面"
            >
              <ArrowLeftIcon className="size-4" />
            </Button>
            <div
              className={cn(
                "grid size-10 shrink-0 place-items-center rounded-xl text-white shadow-sm",
                isPhotos
                  ? "bg-gradient-to-br from-cyan-400 to-blue-500"
                  : "bg-gradient-to-br from-violet-500 to-fuchsia-500",
              )}
            >
              {isPhotos ? (
                <FileImageIcon className="size-5" />
              ) : (
                <FilmIcon className="size-5" />
              )}
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold">{title}</h1>
              <p className="truncate text-xs text-muted-foreground">
                {fill(copy.subtitle, { count: files.length }) || subtitle}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge
              variant="outline"
              className="hidden rounded-full border-border bg-card px-2.5 py-1 text-xs sm:inline-flex"
            >
              本机索引 · {files.length} 项 · {formatBytes(totalBytes)}
            </Badge>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 rounded-lg"
              onClick={() => void load()}
              disabled={loading}
            >
              <RefreshCwIcon
                className={cn("size-3.5", loading && "animate-spin")}
              />
              <span className="hidden sm:inline">刷新</span>
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-8 rounded-lg"
              onClick={rebuildIndex}
              disabled={indexing}
            >
              {indexing ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <SparklesIcon className="size-3.5" />
              )}
              <span className="hidden sm:inline">
                {indexing ? "索引中…" : isPhotos ? "更新索引" : "重建索引"}
              </span>
            </Button>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-4 px-5 py-5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex min-w-[240px] flex-1 items-center gap-2 rounded-xl border border-border bg-card px-3 shadow-[var(--shadow-xs)]">
              <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={
                  isPhotos
                    ? "搜索图片名称、路径或标签…"
                    : t.storage.videos.searchPlaceholder
                }
                className="h-10 border-0 bg-transparent px-0 text-sm shadow-none focus-visible:ring-0"
              />
              {query ? (
                <button
                  type="button"
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  onClick={() => setQuery("")}
                  aria-label="清除搜索"
                >
                  <XIcon className="size-3.5" />
                </button>
              ) : null}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-10 rounded-xl"
              onClick={openStorage}
            >
              <FolderOpenIcon className="size-3.5" />
              管理授权目录
            </Button>
          </div>

          {isPhotos && albums.length > 0 ? (
            <div className="flex min-w-0 items-center gap-2 overflow-x-auto pb-1">
              <FilterChip
                label={`全部 ${files.length}`}
                active={activeAlbum === "all"}
                onClick={() => setActiveAlbum("all")}
              />
              {albums.map((album) => (
                <FilterChip
                  key={album.label}
                  label={`${album.label} ${album.count}`}
                  active={activeAlbum === album.label}
                  onClick={() => setActiveAlbum(album.label)}
                />
              ))}
            </div>
          ) : null}

          {error && files.length === 0 ? (
            <div className="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-dashed border-warning/50 bg-warning/5 px-6 text-center">
              <div className="grid size-12 place-items-center rounded-xl bg-warning/10 text-warning">
                {isPhotos ? (
                  <ImageIcon className="size-6" />
                ) : (
                  <FilmIcon className="size-6" />
                )}
              </div>
              <h2 className="mt-4 text-sm font-semibold">
                本机媒体服务暂时不可用
              </h2>
              <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
                {error}
              </p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="rounded-lg"
                  onClick={() => void load()}
                >
                  <RefreshCwIcon className="size-3.5" />
                  重试
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="rounded-lg"
                  onClick={openStorage}
                >
                  <FolderOpenIcon className="size-3.5" />
                  打开存储管理
                </Button>
              </div>
            </div>
          ) : loading && files.length === 0 ? (
            <div className="grid min-h-[360px] place-items-center rounded-2xl border border-border bg-card">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                正在读取本机索引…
              </div>
            </div>
          ) : visibleFiles.length > 0 ? (
            <>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {query || activeAlbum !== "all"
                    ? `筛选后 ${filteredFiles.length} 项`
                    : `最近更新 · ${files.length} 项`}
                </span>
                <span>
                  {isPhotos ? "点击图片查看大图" : "点击视频打开预览"}
                </span>
              </div>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
                {visibleFiles.map((asset) => (
                  <MediaAssetCard
                    key={asset.asset_id}
                    asset={asset}
                    kind={kind}
                    onOpen={() => setPreview(asset)}
                  />
                ))}
              </div>
              {filteredFiles.length > visibleFiles.length ? (
                <div className="flex justify-center py-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="rounded-lg"
                    onClick={() =>
                      setVisibleLimit((current) => current + MEDIA_PAGE_SIZE)
                    }
                  >
                    再显示{" "}
                    {Math.min(
                      MEDIA_PAGE_SIZE,
                      filteredFiles.length - visibleFiles.length,
                    )}{" "}
                    项
                  </Button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-card px-6 text-center">
              <div className="grid size-12 place-items-center rounded-xl bg-muted text-muted-foreground">
                {isPhotos ? (
                  <ImageIcon className="size-6" />
                ) : (
                  <FilmIcon className="size-6" />
                )}
              </div>
              <h2 className="mt-4 text-sm font-semibold">
                {query || activeAlbum !== "all"
                  ? "没有符合条件的内容"
                  : `还没有${title}`}
              </h2>
              <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
                授权本机目录并完成索引后，内容会在这里出现。
              </p>
              <Button
                type="button"
                size="sm"
                className="mt-4 rounded-lg"
                onClick={openStorage}
              >
                <FolderOpenIcon className="size-3.5" />
                管理授权目录
              </Button>
            </div>
          )}
        </div>
      </div>

      <MediaPreviewDialog
        asset={preview}
        onClose={() => setPreview(null)}
        kind={kind}
      />
    </main>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "shrink-0 rounded-lg border px-3 py-1.5 text-xs transition-colors",
        active
          ? "border-foreground bg-foreground text-background"
          : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

function MediaAssetCard({
  asset,
  kind,
  onOpen,
}: {
  asset: NASFileAsset;
  kind: MediaAppKind;
  onOpen: () => void;
}) {
  const imageURL = useAssetObjectURL(
    kind === "image" ? asset.asset_id : undefined,
  );
  const isImage = kind === "image";
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group min-w-0 overflow-hidden rounded-2xl border border-border bg-card text-left shadow-[var(--shadow-xs)] transition hover:-translate-y-0.5 hover:border-foreground/30 hover:shadow-[var(--shadow-md)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info"
    >
      <div className="relative aspect-[4/3] overflow-hidden bg-muted">
        {isImage && imageURL ? (
          <img
            src={imageURL}
            alt={asset.name}
            className="size-full object-cover transition duration-300 group-hover:scale-105"
          />
        ) : (
          <div
            className={cn(
              "grid size-full place-items-center",
              isImage
                ? "bg-gradient-to-br from-cyan-400/20 to-blue-500/20 text-blue-500"
                : "bg-gradient-to-br from-violet-500/20 to-fuchsia-500/20 text-violet-500",
            )}
          >
            {isImage ? (
              <ImageIcon className="size-10 opacity-70" />
            ) : (
              <FilmIcon className="size-10 opacity-70" />
            )}
          </div>
        )}
        {!isImage ? (
          <span className="absolute bottom-2 left-2 rounded-md bg-black/65 px-2 py-1 text-[10px] font-medium text-white">
            点击预览
          </span>
        ) : null}
      </div>
      <div className="min-w-0 px-3 py-2.5">
        <div className="truncate text-xs font-medium" title={asset.name}>
          {asset.name}
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span className="truncate">{formatDate(asset.mtime_ns)}</span>
          <span className="shrink-0">{formatBytes(asset.size)}</span>
        </div>
      </div>
    </button>
  );
}

function MediaPreviewDialog({
  asset,
  kind,
  onClose,
}: {
  asset: NASFileAsset | null;
  kind: MediaAppKind;
  onClose: () => void;
}) {
  const contentURL = useAssetObjectURL(asset?.asset_id);
  const isImage = kind === "image";
  return (
    <Dialog
      open={Boolean(asset)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[92vh] max-w-5xl overflow-hidden p-0">
        <DialogTitle className="sr-only">
          {asset?.name ?? "媒体预览"}
        </DialogTitle>
        <div className="flex max-h-[92vh] flex-col bg-background">
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{asset?.name}</div>
              <div className="mt-0.5 truncate text-xs text-muted-foreground">
                {asset?.path}
              </div>
            </div>
            <Badge variant="outline" className="shrink-0 rounded-full">
              {asset ? formatBytes(asset.size) : ""}
            </Badge>
          </div>
          <div className="grid min-h-[280px] place-items-center overflow-auto bg-black/95 p-4">
            {contentURL ? (
              isImage ? (
                <img
                  src={contentURL}
                  alt={asset?.name ?? ""}
                  className="max-h-[72vh] max-w-full object-contain"
                />
              ) : (
                <video
                  src={contentURL}
                  controls
                  autoPlay
                  className="max-h-[72vh] max-w-full"
                />
              )
            ) : (
              <div className="flex items-center gap-2 text-sm text-white/70">
                <Loader2Icon className="size-4 animate-spin" />
                正在加载预览…
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
