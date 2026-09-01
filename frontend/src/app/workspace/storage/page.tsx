import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AppWindowIcon,
  ArchiveIcon,
  ChevronRightIcon,
  CopyIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  EyeIcon,
  FileImageIcon,
  FileSearchIcon,
  FileTextIcon,
  FilmIcon,
  FolderIcon,
  FolderOpenIcon,
  FolderSearchIcon,
  FolderPlusIcon,
  HardDriveIcon,
  LayoutListIcon,
  LockKeyholeIcon,
  MessageSquarePlusIcon,
  PlayIcon,
  RefreshCwIcon,
  SearchIcon,
  ServerIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  TagIcon,
  UserIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales";
import { copyTextToClipboard } from "@/core/clipboard";
import {
  createNASIndexJob,
  createNASSource,
  deleteNASSource,
  getNASBaseURL,
  getNASManifest,
  getNASIndexJob,
  getNASPolicy,
  getVideoCoverURL,
  isNASAuthenticationError,
  loadNASAssetURL,
  listNASApps,
  listNASAlbums,
  listNASFiles,
  listVideoFaceGroups,
  classifyVideoTags,
  searchVideoByText,
  ocrVideoKeyframes,
  listNASModels,
  listNASSources,
  listNASDirectory,
  NASRequestTimeoutError,
  openNASApp,
  revealNASApp,
  downloadNASModel,
  searchNAS,
  startNASService,
  updateNASPolicy,
  triggerVideoIndex,
  type NASManifest,
  type NASApp,
  type NASAlbum,
  type NASFileAsset,
  type NASPolicy,
  type NASSearchHit,
  type NASSource,
  type NASVideoSearchHit,
  type NASVideoFaceGroup,
  type NASVideoClassifyResult,
  type NASVideoOcrHit,
} from "@/core/storage/api";
import { pickLocalDirectory } from "@/core/workspace/pick-local-directory";
import { basename, isAbsolutePath } from "@/lib/path-utils";
import { cn } from "@/lib/utils";

type LibraryKey =
  | "overview"
  | "apps"
  | "docs"
  | "images"
  | "videos"
  | "computer"
  | "sources";

interface AppItem {
  id: string;
  name: string;
  type: string;
  path: string;
  status: string;
  icon: LucideIcon;
  tone: string;
  iconUrl?: string;
}

interface DiskItem {
  name: string;
  path: string;
  type: string;
  size: string;
  icon: LucideIcon;
  active?: boolean;
  isDirectory?: boolean;
}

interface FileItem {
  name: string;
  path: string;
  kind: string;
  updated: string;
  size: string;
  icon: LucideIcon;
  tone: string;
}

type StorageCopy = Translations["storage"];

interface LibraryMeta {
  key: LibraryKey;
  label: string;
  detail: string;
  icon: LucideIcon;
}

function buildLibraries(copy: StorageCopy): LibraryMeta[] {
  return [
    {
      key: "overview",
      label: copy.libraries.overviewLabel,
      detail: copy.libraries.overviewDetail,
      icon: DatabaseIcon,
    },
    {
      key: "apps",
      label: copy.libraries.appsLabel,
      detail: copy.libraries.appsDetail,
      icon: AppWindowIcon,
    },
    {
      key: "docs",
      label: copy.libraries.docsLabel,
      detail: copy.libraries.docsDetail,
      icon: FileTextIcon,
    },
    {
      key: "images",
      label: copy.libraries.imagesLabel,
      detail: copy.libraries.imagesDetail,
      icon: FileImageIcon,
    },
    {
      key: "videos",
      label: copy.libraries.videosLabel,
      detail: copy.libraries.videosDetail,
      icon: PlayIcon,
    },
    {
      key: "computer",
      label: copy.libraries.computerLabel,
      detail: copy.libraries.computerDetail,
      icon: HardDriveIcon,
    },
    {
      key: "sources",
      label: copy.libraries.sourcesLabel,
      detail: copy.libraries.sourcesDetail,
      icon: FolderPlusIcon,
    },
  ];
}

const LIBRARY_KEYS = new Set<LibraryKey>([
  "overview",
  "apps",
  "docs",
  "images",
  "videos",
  "computer",
  "sources",
]);

const VISION_AUTO_DOWNLOAD_KEY = "echo.storage.clip-autodownload.v1";
const DOCUMENT_PAGE_SIZE = 60;
const IMAGE_PAGE_SIZE = 96;

function fill(template: string, vars: Record<string, string | number>): string {
  return Object.entries(vars).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

function isLibraryKey(value: string | null): value is LibraryKey {
  return value !== null && LIBRARY_KEYS.has(value as LibraryKey);
}

const DEFAULT_POLICY: NASPolicy = {
  mode: "efficiency",
  allow_cloud_answering: true,
  allow_snippet_export: true,
  max_exported_snippets: 8,
  max_snippet_chars: 1200,
  redact_file_paths_for_cloud: true,
};

const delay = (ms: number) =>
  new Promise((resolve) => window.setTimeout(resolve, ms));

function mapNASApp(item: NASApp, copy: StorageCopy): AppItem {
  const category =
    item.category === "system"
      ? copy.apps.typeSystemApp
      : item.category === "office"
        ? copy.apps.typeDocsSheets
        : copy.apps.typeWebResources;
  return {
    id: item.app_id,
    name: item.name,
    type: category,
    path: item.path,
    status: copy.apps.statusRegistered,
    icon: AppWindowIcon,
    tone:
      item.category === "system"
        ? "blue"
        : item.category === "office"
          ? "amber"
          : "violet",
    iconUrl: item.icon_available
      ? `/v1/apps/${encodeURIComponent(item.app_id)}/icon`
      : undefined,
  };
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(2)} MB`;
}

function formatModified(mtimeNs: number): string {
  const date = new Date(mtimeNs / 1_000_000);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
        .format(date)
        .replaceAll("/", "-");
}

function formatSeconds(sec: number): string {
  const total = Math.max(0, Math.floor(sec));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

type VideoPlayerHit = {
  videoPath: string;
  timeSec: number;
};

type VideoPlayerTarget = {
  video: NASFileAsset;
  hits: VideoPlayerHit[];
  index: number;
};

type DocumentSmartFilter =
  | "all"
  | "recent"
  | "pdf"
  | "office"
  | "sheet"
  | "text";
type ImageSmartFilter = "all" | "screenshot" | "receipt" | "document" | "photo";

const DOCUMENT_SMART_FILTERS: Array<[DocumentSmartFilter, string]> = [
  ["all", "全部"],
  ["recent", "最近"],
  ["pdf", "PDF"],
  ["office", "Office"],
  ["sheet", "表格"],
  ["text", "文本"],
];

const IMAGE_SMART_FILTERS: Array<[ImageSmartFilter, string]> = [
  ["all", "全部"],
  ["screenshot", "截图"],
  ["receipt", "票据"],
  ["document", "文档图"],
  ["photo", "照片"],
];

function assetSearchText(asset: NASFileAsset): string {
  return `${asset.name} ${asset.path} ${asset.extension} ${(asset.ai_labels ?? []).join(" ")}`.toLocaleLowerCase();
}

function matchesDocumentFilter(
  asset: NASFileAsset,
  filter: DocumentSmartFilter,
  newestMtime: number,
): boolean {
  const extension = asset.extension.replace(/^\./, "").toLowerCase();
  if (filter === "all") return true;
  if (filter === "recent")
    return newestMtime - asset.mtime_ns <= 30 * 24 * 60 * 60 * 1_000_000_000;
  if (filter === "pdf") return extension === "pdf";
  if (filter === "office")
    return ["doc", "docx", "ppt", "pptx", "xls", "xlsx"].includes(extension);
  if (filter === "sheet")
    return ["xls", "xlsx", "csv", "tsv"].includes(extension);
  return ["md", "txt", "html", "htm", "rtf"].includes(extension);
}

function imageSmartCategory(
  asset: NASFileAsset,
): Exclude<ImageSmartFilter, "all"> {
  const aiLabels = asset.ai_labels ?? [];
  if (aiLabels.includes("screenshot")) return "screenshot";
  if (aiLabels.includes("receipt")) return "receipt";
  if (aiLabels.includes("document")) return "document";
  const text = assetSearchText(asset);
  const extension = asset.extension.replace(/^\./, "").toLowerCase();
  if (/(截图|screen ?shot|截屏|capture)/i.test(text)) return "screenshot";
  if (/(票据|收据|发票|报销|receipt|invoice)/i.test(text)) return "receipt";
  if (/(文档|报告|表格|ppt|pdf|doc|xls|logo|slide)/i.test(text))
    return "document";
  if (["png", "webp"].includes(extension)) return "screenshot";
  return "photo";
}

function fileAssetToItem(asset: NASFileAsset): FileItem {
  const extension = asset.extension.replace(/^\./, "").toUpperCase() || "FILE";
  return {
    name: asset.name,
    path: asset.path,
    kind: extension,
    updated: formatModified(asset.mtime_ns),
    size: formatBytes(asset.size),
    icon: asset.kind === "image" ? FileImageIcon : FileTextIcon,
    tone: asset.kind === "image" ? "green" : "blue",
  };
}

function useNASAsset(path: string | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let disposed = false;
    let objectUrl: string | null = null;
    void loadNASAssetURL(path)
      .then((next) => {
        objectUrl = next;
        if (!disposed) setUrl(next);
      })
      .catch(() => setUrl(null));
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return url;
}

function disk(
  name: string,
  path: string,
  type: string,
  size: string,
  icon: LucideIcon,
  active = false,
): DiskItem {
  return { name, path, type, size, icon, active };
}

export default function StoragePage() {
  const { t } = useI18n();
  const copy = t.storage;
  const [searchParams] = useSearchParams();
  const [manifest, setManifest] = useState<NASManifest | null>(null);
  const [policy, setPolicy] = useState<NASPolicy>(DEFAULT_POLICY);
  const [sources, setSources] = useState<NASSource[]>([]);
  const [apps, setApps] = useState<NASApp[]>([]);
  const [documents, setDocuments] = useState<NASFileAsset[]>([]);
  const [images, setImages] = useState<NASFileAsset[]>([]);
  const [videos, setVideos] = useState<NASFileAsset[]>([]);
  const [albums, setAlbums] = useState<NASAlbum[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<NASSearchHit[]>([]);
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [hasSearchResult, setHasSearchResult] = useState(false);
  const [isPickingFolder, setIsPickingFolder] = useState(false);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [isIndexing, setIsIndexing] = useState(false);
  const didAutoStartRef = useRef(false);

  const libraryParam = searchParams.get("library");
  const activeLibrary = isLibraryKey(libraryParam) ? libraryParam : "computer";
  const libraries = useMemo(() => buildLibraries(copy), [copy]);
  const activeMeta = libraries.find((item) => item.key === activeLibrary)!;

  const stats = useMemo(() => {
    const files = sources.reduce((sum, source) => sum + source.file_count, 0);
    const chunks = sources.reduce((sum, source) => sum + source.chunk_count, 0);
    return { files, chunks, sources: sources.length };
  }, [sources]);

  const refreshNAS = useCallback(async () => {
    try {
      setServiceError(null);
      // Manifest, policy and sources define whether the knowledge service is
      // usable.  Apps/media are optional capabilities; one unavailable lane
      // must not make the whole knowledge base look offline.
      const [nextManifest, nextPolicy, nextSources] = await Promise.all([
        getNASManifest(),
        getNASPolicy(),
        listNASSources(),
      ]);
      const [nextApps, nextDocuments, nextImages, nextVideos, nextAlbums] =
        await Promise.allSettled([
          listNASApps(),
          listNASFiles("document"),
          listNASFiles("image"),
          listNASFiles("video"),
          listNASAlbums(),
        ]);
      setManifest(nextManifest);
      setPolicy(nextPolicy);
      setSources(nextSources);
      setApps(nextApps.status === "fulfilled" ? nextApps.value : []);
      setDocuments(
        nextDocuments.status === "fulfilled" ? nextDocuments.value : [],
      );
      setImages(nextImages.status === "fulfilled" ? nextImages.value : []);
      setVideos(nextVideos.status === "fulfilled" ? nextVideos.value : []);
      setAlbums(nextAlbums.status === "fulfilled" ? nextAlbums.value : []);
      return true;
    } catch (error) {
      setManifest(null);
      setSources([]);
      setApps([]);
      setDocuments([]);
      setImages([]);
      setVideos([]);
      setAlbums([]);
      const isNetworkError =
        error instanceof TypeError &&
        /Failed to fetch|NetworkError|network error/i.test(error.message);
      setServiceError(
        isNASAuthenticationError(error)
          ? copy.service.credentialsExpired
          : isNetworkError
            ? copy.service.networkError
            : error instanceof Error
              ? error.message
              : String(error),
      );
      return false;
    }
  }, [copy]);

  const maybeAutoDownloadVision = useCallback(async () => {
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem(VISION_AUTO_DOWNLOAD_KEY)) return;
    try {
      const models = await listNASModels();
      const vision = models.find((item) => item.model_id === "vision-default");
      if (!vision || vision.provider === "local" || vision.status === "loading")
        return;
      const accepted = await downloadNASModel("vision-default");
      if (accepted.status === "loading" || accepted.status === "running") {
        window.localStorage.setItem(VISION_AUTO_DOWNLOAD_KEY, "started");
      }
    } catch {
      // Model download is optional; settings keeps a manual retry path.
    }
  }, []);

  const ensureNASService = useCallback(async () => {
    const startResult = await startNASService();
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (await refreshNAS()) return true;
      await delay(500);
    }
    if (startResult.status === "not_found") {
      setServiceError(copy.service.notFound);
      return false;
    }
    if (startResult.status === "error") {
      setServiceError(copy.service.startFailed);
      return false;
    }
    setServiceError(fill(copy.service.notConnected, { url: getNASBaseURL() }));
    return false;
  }, [copy, refreshNAS]);

  useEffect(() => {
    const init = async () => {
      if (await refreshNAS()) {
        void maybeAutoDownloadVision();
        return;
      }
      if (didAutoStartRef.current) return;
      didAutoStartRef.current = true;
      try {
        if (await ensureNASService()) void maybeAutoDownloadVision();
      } catch (error) {
        const isNetworkError =
          error instanceof TypeError &&
          /Failed to fetch|NetworkError|network error/i.test(error.message);
        if (isNetworkError) {
          setServiceError(copy.service.networkError);
        }
      }
    };
    void init();
  }, [
    copy.service.networkError,
    ensureNASService,
    maybeAutoDownloadVision,
    refreshNAS,
  ]);

  useEffect(() => {
    const reconnect = () => {
      if (document.visibilityState === "visible") {
        void refreshNAS();
      }
    };
    window.addEventListener("focus", reconnect);
    document.addEventListener("visibilitychange", reconnect);
    return () => {
      window.removeEventListener("focus", reconnect);
      document.removeEventListener("visibilitychange", reconnect);
    };
  }, [refreshNAS]);

  const addSource = async (path: string) => {
    const cleanPath = path.trim();
    if (!cleanPath) return;
    await createNASSource(cleanPath);
    await refreshNAS();
  };

  const pickFolder = async () => {
    setIsPickingFolder(true);
    try {
      const selected = await pickLocalDirectory();
      if (selected) await addSource(selected);
    } catch (err) {
      setServiceError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsPickingFolder(false);
    }
  };

  const reconnectNAS = async () => {
    if (isReconnecting) return;
    setIsReconnecting(true);
    setServiceError(null);
    try {
      await ensureNASService();
    } catch (error) {
      if (!(await refreshNAS())) {
        const isNetworkError =
          error instanceof TypeError &&
          /Failed to fetch|NetworkError|network error/i.test(error.message);
        setServiceError(
          isNetworkError
            ? copy.service.networkError
            : error instanceof Error
              ? error.message
              : String(error),
        );
      }
    } finally {
      setIsReconnecting(false);
    }
  };

  const removeSource = async (id: string) => {
    await deleteNASSource(id);
    await refreshNAS();
  };

  const startIndexing = async () => {
    setIsIndexing(true);
    setServiceError(null);
    try {
      const job = await createNASIndexJob();
      toast.success("扫描任务已开始");
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const current = await getNASIndexJob(job.job_id);
        if (current.status === "complete") {
          await refreshNAS();
          toast.success("知识库扫描完成");
          return;
        }
        if (current.status === "failed") {
          throw new Error(current.message || "知识库扫描失败");
        }
        await delay(500);
      }
      throw new Error("扫描仍在后台运行，可稍后刷新查看结果");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setServiceError(message);
      toast.error(message);
    } finally {
      setIsIndexing(false);
    }
  };

  const runSearch = async () => {
    if (!query.trim()) return;
    setIsSearching(true);
    setServiceError(null);
    try {
      const result = await searchNAS(query.trim());
      setHits(result.hits);
      setSearchMessage(result.message);
      setHasSearchResult(true);
    } catch (error) {
      setHits([]);
      setSearchMessage(null);
      setHasSearchResult(false);
      setServiceError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsSearching(false);
    }
  };

  const clearSearchResult = () => {
    setHasSearchResult(false);
    setHits([]);
    setSearchMessage(null);
  };

  const togglePrivacy = async () => {
    const mode = policy.mode === "privacy" ? "efficiency" : "privacy";
    const next = await updateNASPolicy({ ...policy, mode });
    setPolicy(next);
  };

  return (
    <WorkspaceContainer className="px-0 pb-0 md:px-0">
      <WorkspaceBody className="overflow-hidden pt-0">
        <div className="flex size-full overflow-hidden">
          <section className="workspace-panel flex min-h-0 flex-1 overflow-hidden rounded-none border-0 bg-card">
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-card">
              {serviceError && (
                <div className="flex items-center justify-between gap-3 border-b border-warning/70 bg-warning/5 px-4 py-2 text-xs text-warning">
                  <span className="min-w-0 truncate">{serviceError}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 shrink-0 rounded-md border-warning/40 bg-card px-3 text-warning hover:bg-warning/10"
                    onClick={() => void reconnectNAS()}
                    disabled={isReconnecting}
                  >
                    <RefreshCwIcon
                      className={cn(
                        "size-3.5",
                        isReconnecting && "animate-spin",
                      )}
                    />
                    {isReconnecting
                      ? copy.toolbar.reconnecting
                      : copy.toolbar.reconnect}
                  </Button>
                </div>
              )}

              {activeLibrary !== "sources" && (
                <div className="flex shrink-0 items-center justify-end gap-1.5 border-b border-border bg-muted px-3 py-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-8 rounded-md bg-card px-2 text-xs shadow-[var(--shadow-xs)]"
                    onClick={pickFolder}
                    disabled={isPickingFolder}
                  >
                    <FolderPlusIcon className="size-3.5" />
                    {copy.toolbar.authorize}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-8 rounded-md bg-card px-2 text-xs shadow-[var(--shadow-xs)]"
                    onClick={startIndexing}
                    disabled={isIndexing || !manifest}
                  >
                    <RefreshCwIcon
                      className={cn("size-3.5", isIndexing && "animate-spin")}
                    />
                    {copy.toolbar.scan}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 rounded-md px-2 text-xs text-muted-foreground"
                    onClick={() => void togglePrivacy()}
                  >
                    {policy.mode === "privacy" ? (
                      <LockKeyholeIcon className="size-3.5" />
                    ) : (
                      <ServerIcon className="size-3.5" />
                    )}
                    {policy.mode === "privacy"
                      ? copy.toolbar.privacy
                      : copy.toolbar.efficiency}
                  </Button>
                  <span className="ml-1 flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                    <span
                      className={cn(
                        "size-1.5 rounded-full",
                        manifest ? "bg-success" : "bg-warning",
                      )}
                    />
                    <span>
                      {manifest ? copy.toolbar.online : copy.toolbar.offline}
                    </span>
                  </span>
                </div>
              )}

              {activeLibrary === "sources" ? (
                <SourcesView
                  sources={sources}
                  stats={stats}
                  manifest={manifest}
                  serviceError={serviceError}
                  isPickingFolder={isPickingFolder}
                  isReconnecting={isReconnecting}
                  onPickFolder={pickFolder}
                  onReconnect={() => void reconnectNAS()}
                  onScan={() => void startIndexing()}
                  onTogglePrivacy={() => void togglePrivacy()}
                  isIndexing={isIndexing}
                  policy={policy}
                  onRemoveSource={removeSource}
                />
              ) : hasSearchResult ? (
                <SearchResultsView
                  hits={hits}
                  query={query}
                  setQuery={setQuery}
                  runSearch={runSearch}
                  isSearching={isSearching}
                  manifest={manifest}
                  message={searchMessage}
                  libraryLabel={activeMeta.label}
                  onBack={clearSearchResult}
                />
              ) : activeLibrary === "computer" ? (
                <LocalDiskView
                  query={query}
                  setQuery={setQuery}
                  runSearch={runSearch}
                  isSearching={isSearching}
                  manifest={manifest}
                />
              ) : activeLibrary === "apps" ? (
                <AppsView
                  apps={apps}
                  query={query}
                  setQuery={setQuery}
                  runSearch={runSearch}
                  isSearching={isSearching}
                  manifest={manifest}
                />
              ) : (
                <TopicCenterView
                  documents={documents}
                  images={images}
                  videos={videos}
                  albums={albums}
                  activeLibrary={activeLibrary}
                  activeMeta={activeMeta}
                  query={query}
                  setQuery={setQuery}
                  runSearch={runSearch}
                  isSearching={isSearching}
                  manifest={manifest}
                  searchMessage={searchMessage}
                />
              )}
            </div>
          </section>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function ToolbarSearch({
  label,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
}: {
  label: string;
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  return (
    <div className="flex min-w-0 max-w-full flex-1 items-center gap-1 rounded-md border border-border bg-card px-2 shadow-[var(--shadow-xs)] sm:min-w-[300px]">
      <div className="flex min-w-0 flex-1 items-center">
        <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
        <span className="ml-2 hidden shrink-0 text-xs text-muted-foreground xl:inline">
          {label}
        </span>
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") runSearch();
          }}
          placeholder={copy.toolbar.searchPlaceholder}
          className="h-8 min-w-36 border-0 bg-transparent px-1 text-sm shadow-none focus-visible:ring-0"
        />
      </div>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-label={copy.toolbar.searchAria}
        className="h-7 shrink-0 rounded-md px-2"
        onClick={runSearch}
        disabled={isSearching || !manifest}
      >
        {isSearching ? (
          <RefreshCwIcon className="size-3.5 animate-spin" />
        ) : (
          <SearchIcon className="size-3.5" />
        )}
      </Button>
    </div>
  );
}

function TopicCenterView({
  documents,
  images,
  videos,
  albums,
  activeLibrary,
  activeMeta,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
  searchMessage,
}: {
  documents: NASFileAsset[];
  images: NASFileAsset[];
  videos: NASFileAsset[];
  albums: NASAlbum[];
  activeLibrary: LibraryKey;
  activeMeta: LibraryMeta;
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
  searchMessage: string | null;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const [overviewPreview, setOverviewPreview] = useState<FileItem | null>(null);
  if (activeLibrary === "docs") {
    return (
      <DocumentLibraryView
        files={documents}
        query={query}
        setQuery={setQuery}
        runSearch={runSearch}
        isSearching={isSearching}
        manifest={manifest}
      />
    );
  }

  if (activeLibrary === "images") {
    return (
      <ImageLibraryView
        files={images}
        query={query}
        setQuery={setQuery}
        runSearch={runSearch}
        isSearching={isSearching}
        manifest={manifest}
        albums={albums}
      />
    );
  }

  if (activeLibrary === "videos") {
    return (
      <VideoLibraryView
        files={videos}
        query={query}
        setQuery={setQuery}
        runSearch={runSearch}
        isSearching={isSearching}
        manifest={manifest}
      />
    );
  }

  const recentItems = [...documents, ...images, ...videos]
    .sort((left, right) => right.mtime_ns - left.mtime_ns)
    .slice(0, 24)
    .map(fileAssetToItem);

  return (
    <>
      <div className="flex min-h-[52px] shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/50 px-4 py-2">
        <div>
          <div className="text-sm font-semibold">最近文件</div>
          <div className="text-xs text-muted-foreground">
            来自已授权目录的最近更新内容
          </div>
        </div>
        <ToolbarSearch
          label={fill(copy.toolbar.searchIn, { label: activeMeta.label })}
          query={query}
          setQuery={setQuery}
          runSearch={runSearch}
          isSearching={isSearching}
          manifest={manifest}
        />
      </div>

      <main className="min-h-0 flex-1 overflow-y-auto bg-card p-4">
        {recentItems.length > 0 ? (
          <>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">全部类型</div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {searchMessage || copy.overview.aggregateDesc}
                </div>
              </div>
              <Badge
                variant="outline"
                className="rounded-full border-border bg-card"
              >
                {copy.overview.localDatabaseBadge}
              </Badge>
            </div>
            <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
              {recentItems.map((item) => (
                <FileCard
                  key={item.path}
                  item={item}
                  onPreview={() => setOverviewPreview(item)}
                />
              ))}
            </div>
          </>
        ) : (
          <div className="flex min-h-[320px] items-center justify-center text-sm text-muted-foreground">
            授权文件夹并完成扫描后，最近文件会显示在这里
          </div>
        )}
      </main>
      <FilePreviewDialog
        item={overviewPreview}
        onClose={() => setOverviewPreview(null)}
      />
    </>
  );
}

function DocumentLibraryView({
  files,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
}: {
  files: NASFileAsset[];
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const [smartFilter, setSmartFilter] = useState<DocumentSmartFilter>("all");
  const [visibleLimit, setVisibleLimit] = useState(DOCUMENT_PAGE_SIZE);
  const [previewItem, setPreviewItem] = useState<FileItem | null>(null);
  const docFiles = useMemo(() => files.map(fileAssetToItem), [files]);
  const newestMtime = useMemo(
    () => files.reduce((latest, file) => Math.max(latest, file.mtime_ns), 0),
    [files],
  );
  const visibleDocFiles = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return files
      .map((asset, index) => ({ asset, item: docFiles[index]! }))
      .filter(
        ({ asset }) =>
          matchesDocumentFilter(asset, smartFilter, newestMtime) &&
          (!normalizedQuery ||
            assetSearchText(asset).includes(normalizedQuery)),
      );
  }, [docFiles, files, newestMtime, query, smartFilter]);
  useEffect(() => setVisibleLimit(DOCUMENT_PAGE_SIZE), [query, smartFilter]);
  const renderedDocFiles = visibleDocFiles.slice(0, visibleLimit);
  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:py-0">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            {copy.docs.title}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {fill(copy.docs.subtitle, { count: docFiles.length })}
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1.5">
          <div className="flex min-w-0 items-center gap-0.5 overflow-x-auto">
            {DOCUMENT_SMART_FILTERS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={smartFilter === value}
                onClick={() => setSmartFilter(value)}
                className={cn(
                  "shrink-0 rounded-md px-2 py-1 text-xs transition-colors",
                  smartFilter === "all" && value === "all"
                    ? "bg-foreground text-background"
                    : smartFilter === value
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-card",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <ToolbarSearch
            label={copy.docs.searchLabel}
            query={query}
            setQuery={setQuery}
            runSearch={runSearch}
            isSearching={isSearching}
            manifest={manifest}
          />
        </div>
      </div>
      <main className="min-h-0 flex-1 overflow-hidden bg-card">
        <div className="grid grid-cols-[minmax(180px,1fr)_76px_104px] items-center gap-3 border-b border-border bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground lg:grid-cols-[minmax(220px,1fr)_minmax(140px,220px)_76px_104px] xl:grid-cols-[minmax(240px,1fr)_minmax(180px,280px)_92px_120px_104px]">
          <span>{copy.docs.colName}</span>
          <span className="hidden lg:block">{copy.docs.colLocation}</span>
          <span>{copy.docs.colSize}</span>
          <span className="hidden text-right xl:block">
            {copy.docs.colModified}
          </span>
          <span className="text-right">{copy.docs.colActions}</span>
        </div>
        <div className="min-h-0 overflow-y-auto">
          {renderedDocFiles.length > 0 ? (
            renderedDocFiles.map(({ item }) => (
              <FileManagerRow
                key={item.path}
                item={item}
                onPreview={() => setPreviewItem(item)}
              />
            ))
          ) : (
            <div className="px-4 py-16 text-center text-sm text-muted-foreground">
              没有符合条件的文档
            </div>
          )}
          {visibleDocFiles.length > renderedDocFiles.length && (
            <div className="flex items-center justify-center border-t border-border/40 px-4 py-4">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() =>
                  setVisibleLimit((current) => current + DOCUMENT_PAGE_SIZE)
                }
              >
                再显示{" "}
                {Math.min(
                  DOCUMENT_PAGE_SIZE,
                  visibleDocFiles.length - renderedDocFiles.length,
                )}{" "}
                项
              </Button>
            </div>
          )}
        </div>
        <div className="border-t border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          {copy.docs.footerNote}
        </div>
      </main>
      <FilePreviewDialog
        item={previewItem}
        onClose={() => setPreviewItem(null)}
      />
    </>
  );
}

function ImageLibraryView({
  files,
  albums,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
}: {
  files: NASFileAsset[];
  albums: NASAlbum[];
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const [smartFilter, setSmartFilter] = useState<string>("all");
  const [visibleLimit, setVisibleLimit] = useState(IMAGE_PAGE_SIZE);
  const [previewItem, setPreviewItem] = useState<FileItem | null>(null);
  const imageFiles = useMemo(() => files.map(fileAssetToItem), [files]);
  const visibleImageFiles = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return files
      .map((asset, index) => ({ asset, item: imageFiles[index]! }))
      .filter(
        ({ asset }) =>
          (smartFilter === "all" ||
            (smartFilter.startsWith("album:")
              ? asset.ai_labels?.includes(smartFilter.slice("album:".length))
              : imageSmartCategory(asset) === smartFilter)) &&
          (!normalizedQuery ||
            assetSearchText(asset).includes(normalizedQuery)),
      );
  }, [files, imageFiles, query, smartFilter]);
  useEffect(() => setVisibleLimit(IMAGE_PAGE_SIZE), [query, smartFilter]);
  const renderedImageFiles = visibleImageFiles.slice(0, visibleLimit);
  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:py-0">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            {copy.images.title}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {fill(copy.images.subtitle, { count: imageFiles.length })}
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <ToolbarSearch
            label={copy.images.searchLabel}
            query={query}
            setQuery={setQuery}
            runSearch={runSearch}
            isSearching={isSearching}
            manifest={manifest}
          />
          <Badge
            variant="outline"
            className="h-8 rounded-md border-border bg-card px-2.5 text-xs"
          >
            {copy.images.badgeAllImages}
          </Badge>
        </div>
      </div>
      <main className="min-h-0 flex-1 overflow-y-auto bg-card px-4 py-4 lg:px-6">
        <div className="mb-4 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {IMAGE_SMART_FILTERS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={smartFilter === value}
              onClick={() => setSmartFilter(value)}
              className={cn(
                "rounded-md px-2.5 py-1 transition-colors",
                smartFilter === value
                  ? "bg-foreground text-background"
                  : "border border-border bg-card hover:bg-muted",
              )}
            >
              {label}
            </button>
          ))}
          {albums.map((album) => (
            <AlbumChip
              key={album.label}
              album={album}
              active={smartFilter === `album:${album.label}`}
              onClick={() => setSmartFilter(`album:${album.label}`)}
            />
          ))}
          <span className="ml-1">
            {visibleImageFiles.length} 项 · 本地智能分类
          </span>
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-1">
          {renderedImageFiles.length > 0 ? (
            renderedImageFiles.map(({ item, asset }) => (
              <ImageAssetTile
                key={item.path}
                item={item}
                asset={asset}
                onPreview={() => setPreviewItem(item)}
              />
            ))
          ) : (
            <div className="col-span-full px-4 py-16 text-center text-sm text-muted-foreground">
              没有符合条件的图片
            </div>
          )}
        </div>
        {visibleImageFiles.length > renderedImageFiles.length && (
          <div className="flex justify-center py-5">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                setVisibleLimit((current) => current + IMAGE_PAGE_SIZE)
              }
            >
              再显示{" "}
              {Math.min(
                IMAGE_PAGE_SIZE,
                visibleImageFiles.length - renderedImageFiles.length,
              )}{" "}
              项
            </Button>
          </div>
        )}
      </main>
      <FilePreviewDialog
        item={previewItem}
        onClose={() => setPreviewItem(null)}
      />
    </>
  );
}

function AlbumChip({
  album,
  active,
  onClick,
}: {
  album: NASAlbum;
  active: boolean;
  onClick: () => void;
}) {
  const coverUrl = useNASAsset(
    album.cover_asset_id
      ? `/v1/files/${encodeURIComponent(album.cover_asset_id)}/content`
      : undefined,
  );
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md pr-2 text-xs transition-colors",
        active
          ? "bg-foreground text-background"
          : "border border-border bg-card hover:bg-muted",
      )}
    >
      <span className="size-6 overflow-hidden rounded-l-md bg-muted">
        {coverUrl ? (
          <img src={coverUrl} alt="" className="size-full object-cover" />
        ) : null}
      </span>
      {album.label} <span className="opacity-60">{album.count}</span>
    </button>
  );
}

type VideoTab = "videos" | "people" | "tags";

function VideoLibraryView({
  files,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
}: {
  files: NASFileAsset[];
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const [activeTab, setActiveTab] = useState<VideoTab>("videos");
  const [isIndexing, setIsIndexing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  // 安全检查：确保 files 引用稳定（files 始终为数组）
  const safeFiles = useMemo(() => files, [files]);
  const [hasSearched, setHasSearched] = useState(false);
  const [isVideoSearching, setIsVideoSearching] = useState(false);
  const [searchHits, setSearchHits] = useState<NASVideoSearchHit[]>([]);
  const [ocrHits, setOcrHits] = useState<NASVideoOcrHit[]>([]);
  const [faceGroups, setFaceGroups] = useState<NASVideoFaceGroup[]>([]);
  const [classifyResults, setClassifyResults] = useState<
    NASVideoClassifyResult[]
  >([]);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [playerTarget, setPlayerTarget] = useState<VideoPlayerTarget | null>(
    null,
  );

  const videoFiles = useMemo(() => safeFiles.map(fileAssetToItem), [safeFiles]);

  const assetByVideoPath = useMemo(() => {
    const map = new Map<string, NASFileAsset>();
    for (const asset of safeFiles) {
      map.set(asset.path, asset);
      map.set(basename(asset.path), asset);
    }
    return map;
  }, [safeFiles]);

  const resolveAsset = useCallback(
    (videoPath: string): NASFileAsset | null => {
      const byExact = assetByVideoPath.get(videoPath);
      if (byExact) return byExact;
      const byBase = assetByVideoPath.get(basename(videoPath));
      if (byBase) return byBase;
      const lower = videoPath.toLowerCase();
      return (
        safeFiles.find(
          (asset) =>
            asset.path.toLowerCase().endsWith(lower) ||
            lower.endsWith(asset.path.toLowerCase()),
        ) ?? null
      );
    },
    [assetByVideoPath, safeFiles],
  );

  const loadFaces = useCallback(async () => {
    try {
      const res = await listVideoFaceGroups();
      setFaceGroups(res.groups);
    } catch {
      setFaceGroups([]);
    }
  }, []);

  const loadTags = useCallback(async () => {
    try {
      const res = await classifyVideoTags();
      setClassifyResults(res.results);
    } catch {
      setClassifyResults([]);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "people") void loadFaces();
  }, [activeTab, loadFaces]);

  useEffect(() => {
    if (activeTab === "tags") void loadTags();
  }, [activeTab, loadTags]);

  const rebuildIndex = async () => {
    if (isIndexing) return;
    setIsIndexing(true);
    try {
      await triggerVideoIndex();
      await delay(1500);
      void loadFaces();
      void loadTags();
    } finally {
      setIsIndexing(false);
    }
  };

  const runVideoSearch = async () => {
    const q = searchQuery.trim();
    if (!q) return;
    setIsVideoSearching(true);
    try {
      const [semantic, ocr] = await Promise.all([
        searchVideoByText(q),
        ocrVideoKeyframes(q),
      ]);
      setSearchHits(semantic.hits);
      setOcrHits(ocr.hits);
    } catch {
      setSearchHits([]);
      setOcrHits([]);
    } finally {
      setHasSearched(true);
      setIsVideoSearching(false);
    }
  };

  const openPlayer = useCallback(
    (video: NASFileAsset, hits: VideoPlayerHit[], index: number) => {
      setPlayerTarget({ video, hits, index });
    },
    [],
  );

  const openAsset = useCallback(
    (video: NASFileAsset) => {
      openPlayer(video, [{ videoPath: video.path, timeSec: 0 }], 0);
    },
    [openPlayer],
  );

  const openSearchHit = useCallback(
    (hit: NASVideoSearchHit, video: NASFileAsset) => {
      const hits = searchHits
        .filter((item) => item.video_path === hit.video_path)
        .map((item) => ({
          videoPath: item.video_path,
          timeSec: item.time_sec,
        }));
      const index = Math.max(
        0,
        hits.findIndex((item) => item.timeSec === hit.time_sec),
      );
      openPlayer(video, hits, index);
    },
    [openPlayer, searchHits],
  );

  const allTags = useMemo(() => {
    const counts = new Map<string, number>();
    for (const result of classifyResults) {
      const top = result.tags[0];
      if (top) counts.set(top.label, (counts.get(top.label) ?? 0) + 1);
    }
    return Array.from(counts.entries()).map(([label, count]) => ({
      label,
      count,
    }));
  }, [classifyResults]);

  const taggedVideos = useMemo(
    () =>
      selectedTag
        ? classifyResults
            .filter((result) => result.tags[0]?.label === selectedTag)
            .map((result) => resolveAsset(result.video_path))
            .filter((asset): asset is NASFileAsset => asset !== null)
        : [],
    [classifyResults, resolveAsset, selectedTag],
  );

  const renderVideoGrid = (assets: NASFileAsset[]) => (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-2">
      {assets.length > 0 ? (
        assets.map((asset) => (
          <VideoAssetTile
            key={asset.asset_id}
            asset={asset}
            copy={copy}
            onOpen={() => openAsset(asset)}
          />
        ))
      ) : (
        <div className="col-span-full px-4 py-12 text-center text-sm text-muted-foreground">
          {copy.videos.noResults}
        </div>
      )}
    </div>
  );

  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:py-0">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            {copy.videos.title}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {fill(copy.videos.subtitle, { count: videoFiles.length })}
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <ToolbarSearch
            label={copy.videos.searchLabel}
            query={query}
            setQuery={setQuery}
            runSearch={runSearch}
            isSearching={isSearching}
            manifest={manifest}
          />
          <Button
            size="sm"
            variant="secondary"
            className="h-8 rounded-md bg-card px-2 text-xs shadow-[var(--shadow-xs)]"
            onClick={rebuildIndex}
            disabled={isIndexing || !manifest}
          >
            <RefreshCwIcon
              className={cn("size-3.5", isIndexing && "animate-spin")}
            />
            {isIndexing ? copy.videos.indexing : copy.videos.indexAction}
          </Button>
          <Badge
            variant="outline"
            className="h-8 rounded-md border-border bg-card px-2.5 text-xs"
          >
            {copy.videos.badgeAllVideos}
          </Badge>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 border-b border-border bg-muted/50 px-3 py-2">
        {(
          [
            ["videos", copy.videos.tabVideos, FilmIcon],
            ["people", copy.videos.tabPeople, UsersIcon],
            ["tags", copy.videos.tabTags, TagIcon],
          ] as const
        ).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            aria-pressed={activeTab === value}
            onClick={() => setActiveTab(value)}
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors",
              activeTab === value
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-card",
            )}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
      </div>

      <main className="min-h-0 flex-1 overflow-y-auto bg-card px-4 py-4 lg:px-6">
        {activeTab === "videos" && (
          <>
            <div className="mb-2 flex items-center gap-2">
              <div className="flex min-w-0 flex-1 items-center gap-1 rounded-md border border-border bg-card px-2 shadow-[var(--shadow-xs)]">
                <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void runVideoSearch();
                  }}
                  placeholder={copy.videos.searchPlaceholder}
                  className="h-8 min-w-36 border-0 bg-transparent px-1 text-sm shadow-none focus-visible:ring-0"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 shrink-0 rounded-md px-2"
                  onClick={() => void runVideoSearch()}
                  disabled={isVideoSearching || !searchQuery.trim()}
                >
                  {isVideoSearching ? (
                    <RefreshCwIcon className="size-3.5 animate-spin" />
                  ) : (
                    <SearchIcon className="size-3.5" />
                  )}
                </Button>
              </div>
            </div>
            <div className="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
              <SparklesIcon className="size-3.5" />
              <span>{copy.videos.searchHint}</span>
            </div>
            {files.length === 0 && !hasSearched ? (
              <div className="rounded-lg border border-border bg-card px-4 py-12 text-center text-sm text-muted-foreground">
                {copy.videos.noIndex}
              </div>
            ) : hasSearched ? (
              <div className="space-y-4">
                {searchHits.length > 0 && (
                  <div>
                    <div className="mb-2 text-xs font-semibold text-muted-foreground">
                      {copy.videos.summary}
                    </div>
                    <div className="overflow-hidden rounded-lg border border-border bg-card shadow-[var(--shadow-xs)]">
                      {searchHits.map((hit) => {
                        const video = resolveAsset(hit.video_path);
                        return (
                          <button
                            key={`${hit.video_path}-${hit.time_sec}`}
                            type="button"
                            onClick={() => video && openSearchHit(hit, video)}
                            className="flex w-full items-center gap-3 border-b border-border/40 px-3 py-2.5 text-left last:border-b-0 hover:bg-muted"
                          >
                            <PlayIcon className="size-4 shrink-0 text-primary" />
                            <span className="min-w-0 flex-1 truncate text-sm font-medium">
                              {video ? video.name : basename(hit.video_path)}
                            </span>
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {formatSeconds(hit.time_sec)}
                            </span>
                            <Badge
                              variant="outline"
                              className="rounded-full border-border"
                            >
                              {Math.round(
                                Math.max(0, Math.min(1, hit.score)) * 100,
                              )}
                            </Badge>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {ocrHits.length > 0 && (
                  <div>
                    <div className="mb-2 text-xs font-semibold text-muted-foreground">
                      {copy.videos.ocr.label}
                    </div>
                    <div className="overflow-hidden rounded-lg border border-border bg-card shadow-[var(--shadow-xs)]">
                      {ocrHits.map((hit, index) => {
                        const video = resolveAsset(hit.video_path);
                        return (
                          <button
                            key={`${hit.video_path}-${hit.time_sec}-${index}`}
                            type="button"
                            onClick={() =>
                              video &&
                              openPlayer(
                                video,
                                [
                                  {
                                    videoPath: hit.video_path,
                                    timeSec: hit.time_sec,
                                  },
                                ],
                                0,
                              )
                            }
                            className="flex w-full items-start gap-3 border-b border-border/40 px-3 py-2.5 text-left last:border-b-0 hover:bg-muted"
                          >
                            <FileSearchIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium">
                                {video ? video.name : basename(hit.video_path)}{" "}
                                · {formatSeconds(hit.time_sec)}
                              </span>
                              <span className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                                {hit.text}
                              </span>
                            </span>
                            <Badge
                              variant="outline"
                              className="rounded-full border-border"
                            >
                              {Math.round(
                                Math.max(0, Math.min(1, hit.score)) * 100,
                              )}
                            </Badge>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {searchHits.length === 0 && ocrHits.length === 0 && (
                  <div className="rounded-lg border border-border bg-card px-4 py-10 text-center text-sm text-muted-foreground">
                    {copy.videos.noOcr}
                  </div>
                )}
              </div>
            ) : (
              <>
                {renderVideoGrid(files)}
                <div className="mt-6 text-xs text-muted-foreground">
                  {copy.videos.footerNote}
                </div>
              </>
            )}
          </>
        )}

        {activeTab === "people" && (
          <div className="space-y-3">
            {faceGroups.length > 0 ? (
              faceGroups.map((group) => (
                <div
                  key={group.person}
                  className="overflow-hidden rounded-lg border border-border bg-card shadow-[var(--shadow-xs)]"
                >
                  <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-3 py-2.5">
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                      <UserIcon className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold">
                        {copy.videos.peopleCount(group.person)}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {copy.videos.faceCount(group.count_faces)}
                      </div>
                    </div>
                  </div>
                  <div className="divide-y divide-black/[0.04]">
                    {group.appearances.map((appearance, index) => {
                      const video = resolveAsset(appearance.video_path);
                      return (
                        <button
                          key={`${appearance.video_path}-${appearance.time_sec}-${index}`}
                          type="button"
                          onClick={() =>
                            video &&
                            openPlayer(
                              video,
                              group.appearances.map((item) => ({
                                videoPath: item.video_path,
                                timeSec: item.time_sec,
                              })),
                              index,
                            )
                          }
                          className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-muted"
                        >
                          <PlayIcon className="size-4 shrink-0 text-primary" />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">
                            {video
                              ? video.name
                              : basename(appearance.video_path)}
                          </span>
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {formatSeconds(appearance.time_sec)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))
            ) : (
              <div className="rounded-lg border border-border bg-card px-4 py-12 text-center text-sm text-muted-foreground">
                {copy.videos.noFaces}
              </div>
            )}
          </div>
        )}

        {activeTab === "tags" && (
          <>
            <div className="mb-4 flex flex-wrap items-center gap-1.5">
              {allTags.length > 0 ? (
                allTags.map((tag) => (
                  <button
                    key={tag.label}
                    type="button"
                    aria-pressed={selectedTag === tag.label}
                    onClick={() =>
                      setSelectedTag(
                        selectedTag === tag.label ? null : tag.label,
                      )
                    }
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs transition-colors",
                      selectedTag === tag.label
                        ? "bg-foreground text-background"
                        : "border border-border bg-card hover:bg-muted",
                    )}
                  >
                    {tag.label}
                    <span className="ml-1 opacity-60">{tag.count}</span>
                  </button>
                ))
              ) : (
                <div className="w-full rounded-lg border border-border bg-card px-4 py-12 text-center text-sm text-muted-foreground">
                  {copy.videos.noTags}
                </div>
              )}
            </div>
            {selectedTag && renderVideoGrid(taggedVideos)}
          </>
        )}
      </main>

      {playerTarget && (
        <VideoPlayerDialog
          target={playerTarget}
          onClose={() => setPlayerTarget(null)}
          onPrev={() =>
            setPlayerTarget((target) =>
              target
                ? { ...target, index: Math.max(0, target.index - 1) }
                : target,
            )
          }
          onNext={() =>
            setPlayerTarget((target) =>
              target
                ? {
                    ...target,
                    index: Math.min(target.hits.length - 1, target.index + 1),
                  }
                : target,
            )
          }
          copy={copy}
        />
      )}
    </>
  );
}

function VideoAssetTile({
  asset,
  copy,
  onOpen,
}: {
  asset: NASFileAsset;
  copy: StorageCopy;
  onOpen: () => void;
}) {
  const coverUrl = getVideoCoverURL(asset.path, 0);
  const videoUrl = useNASAsset(
    `/v1/files/${encodeURIComponent(asset.asset_id)}/content`,
  );
  const [coverFailed, setCoverFailed] = useState(false);
  const showCover = !coverFailed && coverUrl;
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`${asset.name}\n${asset.path}`}
      className="group min-w-0 overflow-hidden rounded-lg bg-muted/40 text-left"
    >
      <span className="relative flex aspect-video w-full items-center justify-center overflow-hidden bg-black">
        {showCover ? (
          <img
            src={coverUrl}
            alt={asset.name}
            onError={() => setCoverFailed(true)}
            className="size-full object-cover transition-transform duration-base group-hover:scale-[1.02]"
          />
        ) : videoUrl ? (
          <video
            src={videoUrl}
            preload="metadata"
            muted
            playsInline
            className="size-full object-cover"
          />
        ) : (
          <FilmIcon className="size-7 text-muted-foreground" />
        )}
        <span className="absolute inset-0 grid place-items-center bg-black/0 transition-colors group-hover:bg-black/20">
          <span className="grid size-8 place-items-center rounded-full bg-white/90 text-foreground opacity-0 shadow-[var(--shadow-xs)] transition-opacity group-hover:opacity-100">
            <PlayIcon className="size-4" />
          </span>
        </span>
        <span className="absolute bottom-1.5 right-1.5 rounded bg-black/60 px-1.5 py-0.5 text-mini text-white">
          {copy.videos.duration}
        </span>
      </span>
      <div className="space-y-0.5 px-2 py-1.5">
        <div className="truncate text-xs font-medium">{asset.name}</div>
        <div className="truncate text-mini text-muted-foreground">
          {formatBytes(asset.size)}
        </div>
      </div>
    </button>
  );
}

function VideoPlayerDialog({
  target,
  onClose,
  onPrev,
  onNext,
  copy,
}: {
  target: VideoPlayerTarget;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  copy: StorageCopy;
}) {
  const videoUrl = useNASAsset(
    `/v1/files/${encodeURIComponent(target.video.asset_id)}/content`,
  );
  const videoRef = useRef<HTMLVideoElement>(null);
  const current = target.hits[target.index];
  const currentTimeSec = current?.timeSec;

  useEffect(() => {
    const el = videoRef.current;
    if (el && currentTimeSec !== undefined) el.currentTime = currentTimeSec;
  }, [target.index, currentTimeSec]);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-w-3xl border-border-default bg-card p-0 sm:max-w-3xl"
        showCloseButton={false}
      >
        <div className="p-4">
          <video
            key={target.video.asset_id}
            ref={videoRef}
            src={videoUrl ?? undefined}
            controls
            autoPlay
            muted
            playsInline
            className="aspect-video w-full rounded-lg bg-black"
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">
                {target.video.name}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {copy.videos.player.atTime(
                  formatSeconds(current?.timeSec ?? 0),
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                className="rounded-md bg-muted"
                onClick={onPrev}
                disabled={target.index <= 0}
              >
                {copy.videos.player.prev}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                className="rounded-md bg-muted"
                onClick={onNext}
                disabled={target.index >= target.hits.length - 1}
              >
                {copy.videos.player.next}
              </Button>
              <Button size="sm" variant="ghost" onClick={onClose}>
                {copy.videos.player.close}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function FileCard({
  item,
  onPreview,
}: {
  item: FileItem;
  onPreview?: () => void;
}) {
  const Icon = item.icon;
  return (
    <div className="group flex min-h-20 items-center gap-3 rounded-lg border border-border bg-card p-3 text-left shadow-[var(--shadow-xs)] transition-colors hover:border-border hover:bg-muted/30">
      <div
        className={cn(
          "flex size-11 shrink-0 items-center justify-center rounded-lg",
          toneClass(item.tone),
        )}
      >
        <Icon className="size-5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{item.name}</div>
        <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate">{item.path}</span>
          <span className="shrink-0">·</span>
          <span className="shrink-0">{item.updated}</span>
        </div>
      </div>
      <div className="hidden shrink-0 text-right text-xs text-muted-foreground xl:block">
        <div>{item.kind}</div>
        <div className="mt-1">{item.size}</div>
      </div>
      <QuickFileActions compact item={item} onPreview={onPreview} />
    </div>
  );
}

function FileManagerRow({
  item,
  onPreview,
}: {
  item: FileItem;
  onPreview: () => void;
}) {
  const Icon = item.icon;
  return (
    <div className="grid w-full grid-cols-[minmax(180px,1fr)_76px_104px] items-center gap-3 border-b border-border/40 px-3 py-3 text-left last:border-b-0 hover:bg-muted lg:grid-cols-[minmax(220px,1fr)_minmax(140px,220px)_76px_104px] xl:grid-cols-[minmax(240px,1fr)_minmax(180px,260px)_92px_120px_104px]">
      <span className="flex min-w-0 items-center gap-3">
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            toneClass(item.tone),
          )}
        >
          <Icon className="size-4" />
        </span>
        <span className="truncate text-sm font-medium">{item.name}</span>
      </span>
      <span className="hidden truncate text-xs text-muted-foreground lg:block">
        {item.path}
      </span>
      <span className="truncate text-xs text-muted-foreground">
        {item.size}
      </span>
      <span className="hidden truncate text-right text-xs text-muted-foreground xl:block">
        {item.updated}
      </span>
      <QuickFileActions item={item} onPreview={onPreview} />
    </div>
  );
}

function ImageAssetTile({
  item,
  asset,
  onPreview,
}: {
  item: FileItem;
  asset: NASFileAsset;
  onPreview: () => void;
}) {
  const Icon = item.icon;
  const imageUrl = useNASAsset(
    `/v1/files/${encodeURIComponent(asset.asset_id)}/content`,
  );
  const [imageFailed, setImageFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onPreview}
      title={`${item.name}\n${item.path}\n${item.size}\n${item.updated}`}
      className="group min-w-0 overflow-hidden rounded-lg bg-muted/40 text-left"
    >
      <span className="flex aspect-square w-full items-center justify-center overflow-hidden">
        {imageUrl && !imageFailed ? (
          <img
            src={imageUrl}
            alt={item.name}
            onError={() => setImageFailed(true)}
            className="size-full object-cover transition-transform duration-base group-hover:scale-[1.02]"
          />
        ) : (
          <Icon className="size-7 text-muted-foreground" />
        )}
      </span>
    </button>
  );
}

function QuickFileActions({
  compact = false,
  item,
  onPreview,
}: {
  compact?: boolean;
  item?: Pick<FileItem, "name" | "path">;
  onPreview?: () => void;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const openPath = async (path: string) => {
    if (window.echo?.desktop) {
      const result = await window.echo.desktop.openItem(path);
      if (!result.ok) throw new Error(result.error || "无法打开文件");
      return;
    }
    throw new Error("请在 EchoAI 桌面端打开本机文件");
  };
  const quoteInTask = () => {
    if (!item) return;
    window.location.hash =
      "/workspace/realtime/echo-assistant?agent=echo";
    window.setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent("echo:open-file", {
          detail: { path: item.path, sourceLabel: "知识库" },
        }),
      );
    }, 350);
  };
  const buttons = [
    {
      label: copy.preview.actionPreview,
      icon: EyeIcon,
      action: () => (onPreview ? onPreview() : item && openPath(item.path)),
    },
    {
      label: copy.preview.actionQuote,
      icon: MessageSquarePlusIcon,
      action: quoteInTask,
    },
    {
      label: copy.preview.actionLocate,
      icon: ExternalLinkIcon,
      action: () =>
        item && openPath(item.path.replace(/[\\/][^\\/]+$/, "") || item.path),
    },
  ];
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-end gap-1",
        compact && "gap-0.5",
      )}
    >
      {buttons.map((action) => {
        const Icon = action.icon;
        return (
          <button
            key={action.label}
            type="button"
            title={action.label}
            aria-label={action.label}
            disabled={!item}
            onClick={() => {
              Promise.resolve(action.action()).catch((error) =>
                toast.error(
                  error instanceof Error ? error.message : "操作失败",
                ),
              );
            }}
            className={cn(
              "grid place-items-center rounded-md text-muted-foreground transition-colors hover:bg-black/[0.06] hover:text-foreground",
              compact ? "size-7" : "size-8",
            )}
          >
            <Icon className={compact ? "size-3.5" : "size-4"} />
          </button>
        );
      })}
    </span>
  );
}

function FilePreviewDialog({
  item,
  onClose,
}: {
  item: FileItem | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={Boolean(item)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl overflow-hidden p-0">
        {item && (
          <div className="flex max-h-[76vh] flex-col">
            <div className="border-b border-border px-5 py-4">
              <DialogTitle className="truncate pr-8 text-base font-semibold">
                {item.name}
              </DialogTitle>
              <div className="mt-1 truncate text-xs text-muted-foreground">
                {item.path}
              </div>
            </div>
            <div className="min-h-0 overflow-y-auto bg-muted/25 p-5">
              <PreviewPanel
                title="文件预览"
                subtitle="确认文件信息后，可直接引用到任务或打开原文件。"
                item={item}
              />
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PreviewPanel({
  title,
  subtitle,
  item,
}: {
  title: string;
  subtitle: string;
  item: FileItem;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const Icon = item.icon;
  const quoteInTask = () => {
    window.location.hash =
      "/workspace/realtime/echo-assistant?agent=echo";
    window.setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent("echo:open-file", {
          detail: { path: item.path, sourceLabel: "知识库" },
        }),
      );
    }, 350);
  };
  const openItem = async () => {
    if (!window.echo?.desktop) {
      toast.error("请在 EchoAI 桌面端打开本机文件");
      return;
    }
    const result = await window.echo.desktop.openItem(item.path);
    if (!result.ok) toast.error(result.error || "无法打开文件");
  };
  return (
    <div className="flex min-h-full flex-col">
      <div className="rounded-lg bg-card p-4 shadow-[var(--shadow-xs)] ring-1 ring-border">
        <div className="text-sm font-semibold">{title}</div>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {subtitle}
        </p>
        <div className="mt-4 rounded-lg bg-muted/50 p-4">
          <div
            className={cn(
              "mx-auto flex size-20 items-center justify-center rounded-lg",
              toneClass(item.tone),
            )}
          >
            <Icon className="size-9" />
          </div>
          <div className="mt-4 text-center text-sm font-semibold">
            {item.name}
          </div>
          <div className="mt-1 truncate text-center text-xs text-muted-foreground">
            {item.path}
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-lg bg-card p-4 shadow-[var(--shadow-xs)] ring-1 ring-border">
        <div className="text-sm font-semibold">
          {copy.preview.sourceLocation}
        </div>
        <div className="mt-3 space-y-2 text-xs text-muted-foreground">
          <div className="flex justify-between gap-3">
            <span>{copy.preview.typeLabel}</span>
            <span className="text-foreground">{item.kind}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span>{copy.preview.updatedLabel}</span>
            <span className="text-foreground">{item.updated}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span>{copy.preview.sizeLabel}</span>
            <span className="text-foreground">{item.size}</span>
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-lg bg-card p-4 shadow-[var(--shadow-xs)] ring-1 ring-border">
        <div className="text-sm font-semibold">{copy.preview.snippetTitle}</div>
        <p className="mt-2 rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">
          {copy.preview.snippetDesc}
        </p>
      </div>

      <div className="mt-3 grid gap-2">
        <Button
          variant="secondary"
          className="justify-start rounded-lg bg-card shadow-[var(--shadow-xs)]"
          onClick={quoteInTask}
        >
          <FileSearchIcon className="size-4" />
          {copy.preview.quoteInChat}
        </Button>
        <Button
          variant="secondary"
          className="justify-start rounded-lg bg-card shadow-[var(--shadow-xs)]"
          onClick={() => void openItem()}
        >
          <FolderOpenIcon className="size-4" />
          {copy.preview.openLocation}
        </Button>
      </div>
    </div>
  );
}

function AppsView({
  apps,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
}: {
  apps: NASApp[];
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
}) {
  const { t } = useI18n();
  const copy = t.storage;
  const [category, setCategory] = useState<"all" | "office" | "system">("all");
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    item: AppItem;
    x: number;
    y: number;
  } | null>(null);
  const appItems = useMemo(
    () =>
      apps
        .filter((item) => category === "all" || item.category === category)
        .map((item) => mapNASApp(item, copy)),
    [apps, category, copy],
  );

  useEffect(() => {
    if (!contextMenu) return;
    const close = (event: PointerEvent) => {
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest("[data-app-context-menu]")
      )
        return;
      setContextMenu(null);
    };
    const closeOnScroll = () => setContextMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContextMenu(null);
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("resize", closeOnScroll);
    window.addEventListener("keydown", closeOnEscape);
    document.addEventListener("scroll", closeOnScroll, true);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("resize", closeOnScroll);
      window.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("scroll", closeOnScroll, true);
    };
  }, [contextMenu]);

  const launchApp = useCallback(async (item: AppItem) => {
    setSelectedAppId(item.id);
    setContextMenu(null);
    try {
      await openNASApp(item.id);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : `无法打开 ${item.name}`,
      );
    }
  }, []);

  const revealApp = useCallback(async (item: AppItem) => {
    setContextMenu(null);
    try {
      await revealNASApp(item.id);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : `无法定位 ${item.name}`,
      );
    }
  }, []);

  const copyAppPath = useCallback(async (item: AppItem) => {
    setContextMenu(null);
    try {
      await copyTextToClipboard(item.path);
      toast.success("应用路径已复制");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "复制失败");
    }
  }, []);
  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:py-0">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">
            {copy.apps.title}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {fill(copy.apps.subtitle, { count: appItems.length })}
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <ToolbarSearch
            label={copy.apps.searchLabel}
            query={query}
            setQuery={setQuery}
            runSearch={runSearch}
            isSearching={isSearching}
            manifest={manifest}
          />
          <Button
            size="sm"
            variant="ghost"
            aria-label={copy.toolbar.sortAria}
            className="size-8 rounded-md"
          >
            <SlidersHorizontalIcon className="size-4" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={copy.toolbar.listViewAria}
            className="size-8 rounded-md"
          >
            <LayoutListIcon className="size-4" />
          </Button>
        </div>
      </div>
      <main
        className="min-h-0 flex-1 overflow-y-auto bg-card px-5 py-5"
        onPointerDown={() => setSelectedAppId(null)}
        onContextMenu={(event) => {
          if (event.target === event.currentTarget) event.preventDefault();
        }}
      >
        <div className="mb-6 flex items-center gap-7 text-sm">
          {(
            [
              ["all", "全部应用"],
              ["office", "办公学习"],
              ["system", "系统应用"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={category === value}
              onClick={() => setCategory(value)}
              className={cn(
                "transition-colors hover:text-foreground",
                category === value
                  ? "font-semibold text-foreground"
                  : "text-muted-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(92px,1fr))] gap-x-5 gap-y-10">
          {appItems.length > 0 ? (
            appItems.map((item) => (
              <AppListRow
                key={item.id}
                item={item}
                selected={selectedAppId === item.id}
                onOpen={() => void launchApp(item)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setSelectedAppId(item.id);
                  setContextMenu({
                    item,
                    x: Math.min(event.clientX, window.innerWidth - 224),
                    y: Math.min(event.clientY, window.innerHeight - 164),
                  });
                }}
              />
            ))
          ) : (
            <div className="col-span-full px-4 py-10 text-center text-sm text-muted-foreground">
              {copy.apps.registeredSubtitle}
            </div>
          )}
        </div>
      </main>
      {contextMenu && (
        <div
          data-app-context-menu
          role="menu"
          aria-label={`${contextMenu.item.name} 操作`}
          className="fixed z-50 w-52 overflow-hidden rounded-xl border border-border bg-popover p-1.5 text-popover-foreground shadow-[var(--shadow-floating)]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onContextMenu={(event) => event.preventDefault()}
        >
          <div className="border-b border-border-subtle px-2.5 py-2">
            <div className="truncate text-xs font-semibold">
              {contextMenu.item.name}
            </div>
            <div className="mt-0.5 truncate text-mini text-muted-foreground">
              {contextMenu.item.path}
            </div>
          </div>
          <button
            type="button"
            role="menuitem"
            className="mt-1 flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-xs hover:bg-accent"
            onClick={() => void launchApp(contextMenu.item)}
          >
            <PlayIcon className="size-3.5" />
            打开
          </button>
          <button
            type="button"
            role="menuitem"
            className="flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-xs hover:bg-accent"
            onClick={() => void revealApp(contextMenu.item)}
          >
            <FolderSearchIcon className="size-3.5" />
            在访达中显示
          </button>
          <button
            type="button"
            role="menuitem"
            className="flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-xs hover:bg-accent"
            onClick={() => void copyAppPath(contextMenu.item)}
          >
            <CopyIcon className="size-3.5" />
            复制路径
          </button>
        </div>
      )}
    </>
  );
}

function AppListRow({
  item,
  selected,
  onOpen,
  onContextMenu,
}: {
  item: AppItem;
  selected: boolean;
  onOpen: () => void;
  onContextMenu: (event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  const Icon = item.icon;
  const iconUrl = useNASAsset(item.iconUrl);
  return (
    <button
      type="button"
      title={`${item.path}\n单击打开，右键查看更多操作`}
      aria-label={`打开 ${item.name}`}
      aria-pressed={selected}
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
      onContextMenu={onContextMenu}
      className={cn(
        "group flex min-w-0 flex-col items-center rounded-xl px-2 py-2 text-center outline-none transition-colors hover:bg-muted/45 focus-visible:ring-2 focus-visible:ring-ring/40",
        selected && "bg-accent/70",
      )}
    >
      <span className="flex size-14 items-center justify-center overflow-hidden rounded-xl bg-black/[0.025] transition-transform group-hover:-translate-y-0.5 group-active:scale-95">
        {iconUrl ? (
          <img src={iconUrl} alt="" className="size-14 object-contain" />
        ) : (
          <Icon className="size-7 text-muted-foreground" />
        )}
      </span>
      <span className="mt-2 line-clamp-2 min-h-8 max-w-24 text-xs leading-4">
        {item.name}
      </span>
    </button>
  );
}

function LocalDiskView({
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
}: {
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
}) {
  const fallbackFolders = useMemo(
    () => [
      disk("Applications", "/Applications", "文件夹", "142 项", AppWindowIcon),
      disk("Desktop", "~/Desktop", "文件夹", "12 项", FolderOpenIcon),
      disk("Documents", "~/Documents", "文件夹", "326 项", FileTextIcon),
      disk("Downloads", "~/Downloads", "文件夹", "58 项", ArchiveIcon),
      disk("Pictures", "~/Pictures", "文件夹", "8,426 项", FileImageIcon),
      disk("Public", "~/Public", "文件夹", "4 项", FolderIcon),
    ],
    [],
  );
  const [currentPath, setCurrentPath] = useState("/");
  const [entries, setEntries] = useState<DiskItem[]>(fallbackFolders);
  const [isLoading, setIsLoading] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setIsLoading(true);
      setBrowseError(null);
      try {
        const body = await listNASDirectory(currentPath);
        if (cancelled) return;
        setEntries(
          body.map((entry) => ({
            name: entry.name,
            path: entry.path,
            type: entry.type === "dir" ? "文件夹" : "文件",
            size: entry.type === "dir" ? "—" : formatBytes(entry.size ?? 0),
            icon: entry.type === "dir" ? FolderIcon : FileTextIcon,
            isDirectory: entry.type === "dir",
          })),
        );
      } catch (error) {
        if (!cancelled) {
          setBrowseError(
            error instanceof NASRequestTimeoutError
              ? "本地服务连接超时，已显示常用位置。"
              : error instanceof Error
                ? error.message
                : "目录读取失败",
          );
          if (currentPath === "/") setEntries(fallbackFolders);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [currentPath, fallbackFolders]);

  const pathParts = currentPath.split("/").filter(Boolean);
  const goUp = () => {
    if (pathParts.length > 1)
      setCurrentPath(`/${pathParts.slice(0, -1).join("/")}`);
  };

  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:px-3 lg:py-0">
        <div className="flex min-w-0 flex-wrap items-center text-sm">
          {pathParts.map((item, index, items) => (
            <span key={item} className="flex min-w-0 items-center">
              <button
                type="button"
                onClick={() =>
                  setCurrentPath(`/${pathParts.slice(0, index + 1).join("/")}`)
                }
                className={cn(
                  "max-w-32 truncate rounded px-1.5 py-1",
                  index === items.length - 1
                    ? "font-semibold text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {item}
              </button>
              {index < items.length - 1 && (
                <ChevronRightIcon className="mx-0.5 size-3 text-muted-foreground/60" />
              )}
            </span>
          ))}
          <button
            type="button"
            onClick={goUp}
            disabled={pathParts.length <= 1}
            className="ml-1 rounded px-1.5 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-30"
          >
            返回上一级
          </button>
          <span className="ml-1 text-xs text-muted-foreground">
            · {entries.length} 项
          </span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <ToolbarSearch
            label="在当前位置中搜索："
            query={query}
            setQuery={setQuery}
            runSearch={runSearch}
            isSearching={isSearching}
            manifest={manifest}
          />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <main className="min-h-0 min-w-0 flex-1 overflow-hidden bg-card">
          <div className="grid grid-cols-[minmax(0,1fr)_120px_96px_36px] gap-3 border-b border-border bg-muted/30 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span>名称</span>
            <span>类型</span>
            <span>项目</span>
            <span />
          </div>
          <div className="min-h-0 overflow-y-auto">
            {isLoading ? (
              <div className="px-4 py-12 text-center text-sm text-muted-foreground">
                正在读取目录…
              </div>
            ) : entries.length > 0 ? (
              entries.map((item) => (
                <LocalDiskEntryRow
                  key={item.path}
                  item={item}
                  onOpen={
                    item.isDirectory
                      ? () => setCurrentPath(item.path)
                      : undefined
                  }
                />
              ))
            ) : (
              <div className="px-4 py-12 text-center text-sm text-muted-foreground">
                当前目录为空
              </div>
            )}
          </div>
          <div className="border-t border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            {browseError ||
              (manifest
                ? "本地路径与索引已连接。"
                : "当前离线，可浏览常用位置。")}
          </div>
        </main>
      </div>
    </>
  );
}

function LocalDiskEntryRow({
  item,
  onOpen,
}: {
  item: DiskItem;
  onOpen?: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={!onOpen}
      className="grid w-full grid-cols-[minmax(0,1fr)_120px_96px_36px] items-center gap-3 border-b border-black/[0.035] px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted"
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground ring-1 ring-border">
          <Icon className="size-4" />
        </span>
        <span className="min-w-0">
          <span className="block truncate font-medium">{item.name}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {item.path}
          </span>
        </span>
      </span>
      <span className="truncate text-xs text-muted-foreground">
        {item.type}
      </span>
      <span className="truncate text-xs text-muted-foreground">
        {item.size}
      </span>
      <ChevronRightIcon className="ml-auto size-4 text-muted-foreground" />
    </button>
  );
}

function SourcesView({
  sources,
  stats,
  manifest,
  serviceError,
  isPickingFolder,
  isReconnecting,
  onPickFolder,
  onReconnect,
  onScan,
  onTogglePrivacy,
  isIndexing,
  policy,
  onRemoveSource,
}: {
  sources: NASSource[];
  stats: { files: number; chunks: number; sources: number };
  manifest: NASManifest | null;
  serviceError: string | null;
  isPickingFolder: boolean;
  isReconnecting: boolean;
  onPickFolder: () => void;
  onReconnect: () => void;
  onScan: () => void;
  onTogglePrivacy: () => void;
  isIndexing: boolean;
  policy: NASPolicy;
  onRemoveSource: (id: string) => Promise<void>;
}) {
  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-hidden bg-card">
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:py-0">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">授权目录</div>
          <div className="truncate text-xs text-muted-foreground">
            管理已授权目录、索引状态与扫描范围
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            className="h-8 rounded-md bg-black px-2.5 text-xs text-white hover:bg-black/85"
            onClick={onPickFolder}
            disabled={isPickingFolder}
          >
            <FolderPlusIcon className="size-3.5" />
            添加
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label="扫描队列"
            className="size-8 rounded-md"
            onClick={onScan}
            disabled={!manifest || isIndexing}
          >
            <RefreshCwIcon
              className={cn("size-4", isIndexing && "animate-spin")}
            />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label="隐私策略"
            className="size-8 rounded-md"
            onClick={onTogglePrivacy}
            title={
              policy.mode === "privacy" ? "切换为效率模式" : "切换为隐私模式"
            }
          >
            {policy.mode === "privacy" ? (
              <LockKeyholeIcon className="size-4" />
            ) : (
              <ShieldCheckIcon className="size-4" />
            )}
          </Button>
        </div>
      </div>

      <div className="grid shrink-0 gap-2 border-b border-border bg-muted/30 p-3 sm:grid-cols-3">
        <Metric label="授权目录" value={String(stats.sources)} />
        <Metric label="扫描文件" value={String(stats.files)} />
        <Metric label="索引片段" value={String(stats.chunks)} />
      </div>

      {!manifest && (
        <div className="flex shrink-0 flex-col gap-2 border-b border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="font-medium">下一步：重新连接本地知识库服务</div>
            <div className="mt-0.5 truncate text-warning/80">
              {serviceError || `当前未连接 ${getNASBaseURL()}`}
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 rounded-md border-warning/40 bg-card px-3 text-warning hover:bg-warning/10"
            onClick={onReconnect}
            disabled={isReconnecting}
          >
            <RefreshCwIcon
              className={cn("size-3.5", isReconnecting && "animate-spin")}
            />
            {isReconnecting ? "连接中" : "重新连接"}
          </Button>
        </div>
      )}

      <div className="flex shrink-0 items-center justify-end gap-3 border-b border-border px-3 py-2">
        <div className="hidden shrink-0 items-center gap-2 md:flex">
          <Badge
            variant="outline"
            className="rounded-md border-border bg-card text-xs"
          >
            本地索引
          </Badge>
          <Badge
            variant="outline"
            className="rounded-md border-border bg-card text-xs"
          >
            原文件不上传
          </Badge>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="hidden grid-cols-[minmax(260px,1fr)_92px_92px_120px] border-b border-border bg-muted/30 px-4 py-2 text-xs font-medium text-muted-foreground md:grid">
          <span>目录</span>
          <span>文件</span>
          <span>片段</span>
          <span className="text-right">状态</span>
        </div>
        {sources.length === 0 ? (
          <div className="flex min-h-[360px] flex-col items-center justify-center px-8 text-center">
            <div className="grid size-14 place-items-center rounded-lg bg-black text-white shadow-[var(--shadow-xs)]">
              <FolderPlusIcon className="size-7" />
            </div>
            <div className="mt-4 text-base font-semibold">
              {manifest
                ? "选择一个本机文件夹开始建索引"
                : "先恢复本地服务，再添加文件夹"}
            </div>
            <div className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
              {manifest
                ? "本地数据库会在本机解析文件、OCR 图片并生成向量索引。原文件不上传；只有你确认引用的片段会进入任务上下文。"
                : "离线时可以浏览常用位置，但无法扫描新目录。重新连接后再添加文件夹，索引会在本机生成。"}
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
              <Button
                className="rounded-md bg-black text-white hover:bg-black/85"
                onClick={manifest ? onPickFolder : onReconnect}
                disabled={manifest ? isPickingFolder : isReconnecting}
              >
                {manifest ? (
                  <FolderPlusIcon className="size-4" />
                ) : (
                  <RefreshCwIcon
                    className={cn("size-4", isReconnecting && "animate-spin")}
                  />
                )}
                {manifest
                  ? "添加文件夹"
                  : isReconnecting
                    ? "连接中"
                    : "重新连接"}
              </Button>
              <Button
                variant="secondary"
                className="rounded-md bg-muted"
                onClick={onTogglePrivacy}
              >
                <ShieldCheckIcon className="size-4" />
                查看隐私策略
              </Button>
            </div>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {sources.map((source) => (
              <SourceRow
                key={source.source_id}
                source={source}
                onRemove={() => void onRemoveSource(source.source_id)}
              />
            ))}
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span>隐私机制: 文件解析、OCR 与向量索引默认落在本机。</span>
          <span>
            扫描状态: {isIndexing ? "正在更新索引…" : "当前无运行任务"}
          </span>
        </div>
      </div>
    </main>
  );
}

function SearchResultsView({
  hits,
  query,
  setQuery,
  runSearch,
  isSearching,
  manifest,
  message,
  libraryLabel,
  onBack,
}: {
  hits: NASSearchHit[];
  query: string;
  setQuery: (value: string) => void;
  runSearch: () => void;
  isSearching: boolean;
  manifest: NASManifest | null;
  message?: string | null;
  libraryLabel: string;
  onBack: () => void;
}) {
  const hasHits = hits.length > 0;
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  useEffect(() => setSelectedPaths(new Set()), [hits]);
  const quoteSelected = () => {
    const selectedHits = hits.filter((hit) => selectedPaths.has(hit.path));
    if (selectedHits.length === 0) return;
    window.location.hash =
      "/workspace/realtime/echo-assistant?agent=echo";
    window.setTimeout(() => {
      selectedHits.forEach((hit) => {
        window.dispatchEvent(
          new CustomEvent("echo:open-file", {
            detail: { path: hit.path, sourceLabel: "知识库" },
          }),
        );
      });
    }, 350);
  };
  return (
    <>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border bg-muted/50 px-3 py-2 lg:h-[60px] lg:flex-row lg:items-center lg:justify-between lg:gap-3 lg:px-4 lg:py-0">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            size="sm"
            variant="ghost"
            className="h-8 shrink-0 rounded-lg bg-card px-2 shadow-[var(--shadow-xs)] ring-1 ring-border"
            onClick={onBack}
          >
            返回{libraryLabel}
          </Button>
          <div className="min-w-0">
            <div className="text-sm font-semibold">
              {hasHits ? "搜索结果" : "检索状态"}
            </div>
            <div className="mt-0.5 truncate text-xs text-muted-foreground">
              {hasHits
                ? `“${query}” · 本机索引命中 ${hits.length} 条，引用前不会进入任务上下文`
                : `“${query}” · 本机资料官未返回命中`}
            </div>
          </div>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <ToolbarSearch
            label="继续搜索："
            query={query}
            setQuery={setQuery}
            runSearch={runSearch}
            isSearching={isSearching}
            manifest={manifest}
          />
          <Button
            size="sm"
            className="h-9 shrink-0 rounded-lg bg-black px-3 text-white hover:bg-black/85"
            disabled={selectedPaths.size === 0}
            onClick={quoteSelected}
          >
            <MessageSquarePlusIcon className="size-4" />
            引用选中{selectedPaths.size > 0 ? ` (${selectedPaths.size})` : ""}
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/50 p-5">
        {hasHits ? (
          <div className="overflow-hidden rounded-lg bg-card shadow-[var(--shadow-xs)] ring-1 ring-border">
            {hits.map((hit, index) => (
              <div
                key={hit.chunk_id}
                className="flex w-full items-center gap-3 border-b border-border/40 px-4 py-3 text-left last:border-b-0 hover:bg-muted"
              >
                <button
                  type="button"
                  aria-label={`选择 ${hit.title || basename(hit.path)}`}
                  aria-pressed={selectedPaths.has(hit.path)}
                  onClick={() =>
                    setSelectedPaths((current) => {
                      const next = new Set(current);
                      if (next.has(hit.path)) next.delete(hit.path);
                      else next.add(hit.path);
                      return next;
                    })
                  }
                  className={cn(
                    "grid size-5 shrink-0 place-items-center rounded border text-[11px] transition-colors",
                    selectedPaths.has(hit.path)
                      ? "border-foreground bg-foreground text-background"
                      : "border-border bg-card hover:border-foreground/50",
                  )}
                >
                  {selectedPaths.has(hit.path) ? "✓" : ""}
                </button>
                <FileSearchIcon className="size-5 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">
                    {hit.title || basename(hit.path)}
                  </div>
                  <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                    {hit.snippet}
                  </div>
                  <div className="mt-1 truncate text-xs text-muted-foreground/75">
                    {hit.path}
                  </div>
                </div>
                <Badge variant="outline" className="rounded-full border-border">
                  #{index + 1}
                </Badge>
                <QuickFileActions
                  item={{
                    name: hit.title || basename(hit.path),
                    path: hit.path,
                  }}
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="flex min-h-[340px] flex-col items-center justify-center rounded-lg bg-card px-6 text-center shadow-[var(--shadow-xs)] ring-1 ring-border">
            <div className="grid size-14 place-items-center rounded-lg bg-warning/5 text-warning">
              <FileSearchIcon className="size-6" />
            </div>
            <div className="mt-4 text-base font-semibold">
              {message?.includes("not attached")
                ? "本机检索引擎尚未接入"
                : "没有找到匹配资料"}
            </div>
            <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
              {message ??
                "换一个关键词，或先在“授权目录”里添加文件夹并运行索引。"}
            </p>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                className="rounded-lg bg-muted"
                onClick={() => {
                  window.location.hash =
                    "/workspace/storage?surface=company&library=sources";
                }}
              >
                查看授权目录
              </Button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border-default bg-background/80 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}

function SourceRow({
  source,
  onRemove,
}: {
  source: NASSource;
  onRemove: () => void;
}) {
  const { confirm, confirmDialog } = useConfirmDialog();
  const isReady = source.status === "ready";
  const displayPath = isAbsolutePath(source.path) ? source.path : source.path;
  return (
    <div className="grid gap-2 px-4 py-3 text-sm md:grid-cols-[minmax(260px,1fr)_92px_92px_120px] md:items-center md:gap-3">
      <div className="flex min-w-0 items-center gap-3">
        <HardDriveIcon className="size-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="truncate font-medium">{source.display_name}</div>
          <div className="truncate text-xs text-muted-foreground">
            {displayPath}
          </div>
        </div>
      </div>
      <div className="flex items-center text-xs text-muted-foreground md:block">
        {source.file_count} 文件
      </div>
      <div className="flex items-center text-xs text-muted-foreground md:block">
        {source.chunk_count} 片段
      </div>
      <div className="flex items-center gap-2 md:justify-end">
        <Badge
          variant="outline"
          className={cn(
            "h-5 px-1.5 text-xs",
            isReady
              ? "border-success/55 bg-success/5 text-success"
              : "border-warning/55 bg-warning/5 text-warning",
          )}
        >
          {isReady ? "已就绪" : "待索引"}
        </Badge>
        <Button
          size="sm"
          variant="ghost"
          onClick={async () => {
            if (
              await confirm({
                title: "移除授权目录",
                description: `将移除「${source.display_name}」及其索引数据，此操作不可撤销。`,
                confirmLabel: "移除",
              })
            ) {
              onRemove();
            }
          }}
        >
          移除
        </Button>
      </div>
      {confirmDialog}
    </div>
  );
}

function toneClass(tone: string) {
  const classes: Record<string, string> = {
    blue: "bg-info/10 text-info",
    green: "bg-success/5 text-success",
    violet: "bg-chart-1/10 text-chart-1",
    amber: "bg-warning/5 text-warning",
    rose: "bg-destructive/5 text-destructive",
    zinc: "bg-muted text-muted-foreground",
  };
  return classes[tone] ?? tone;
}
