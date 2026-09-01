/**
 * Echo OS NAS 文件管理器(原生路线)。
 *
 * 浏览宿主存储、拖放上传、下载/复制、删除→回收站、回收站恢复/清空。
 * 删除一律走回收站(硬约束),物理删除仅"清空回收站"一条显式路径。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AppWindowIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  Clock3Icon,
  ChevronRightIcon,
  CloudIcon,
  CopyIcon,
  DatabaseIcon,
  DownloadIcon,
  FileIcon,
  FolderIcon,
  Grid2X2Icon,
  HardDriveIcon,
  HomeIcon,
  ListIcon,
  Loader2Icon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  PauseIcon,
  PlayIcon,
  RotateCcwIcon,
  SearchIcon,
  Trash2Icon,
  UploadCloudIcon,
  XIcon,
} from "lucide-react";

import {
  copyEntry,
  downloadFile,
  emptyTrash,
  FileServiceUnavailableError,
  formatSize,
  listDir,
  listTrash,
  restoreTrash,
  trashEntry,
  uploadFile,
  ResumableUploadController,
  type FileEntry,
  type TrashEntry,
} from "@/appliance/files";
import { requestHighRiskApproval } from "@/appliance/approval";
import { resolveAgentAppUrl } from "@/appliance/agent-workspace";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  answerStorage,
  searchStorage,
  type StorageHit,
} from "@/appliance/storage";

type FinderSidebarTarget =
  | "recent"
  | "airdrop"
  | "applications"
  | "home"
  | "desktop"
  | "downloads"
  | "nas";

const FINDER_TARGET_LABELS: Record<FinderSidebarTarget, string> = {
  recent: "最近使用",
  airdrop: "隔空投送",
  applications: "应用程序",
  home: "个人",
  desktop: "桌面",
  downloads: "下载",
  nas: "NAS",
};

function siblingCopyPath(entry: FileEntry): string {
  const slash = entry.path.lastIndexOf("/");
  const parent = slash >= 0 ? entry.path.slice(0, slash + 1) : "";
  const name = slash >= 0 ? entry.path.slice(slash + 1) : entry.path;
  if (entry.kind === "dir") return `${parent}${name} 副本`;
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return `${parent}${name} 副本`;
  return `${parent}${name.slice(0, dot)} 副本${name.slice(dot)}`;
}

function Breadcrumb({
  path,
  onNavigate,
}: {
  path: string;
  onNavigate: (p: string) => void;
}) {
  const parts = path ? path.split("/") : [];
  return (
    <div className="flex min-w-0 items-center gap-1 overflow-x-auto text-sm">
      <button
        type="button"
        onClick={() => onNavigate("")}
        className="shrink-0 rounded-md px-2 py-0.5 font-medium text-slate-700 transition hover:bg-slate-900/8"
      >
        NAS
      </button>
      {parts.map((part, i) => {
        const sub = parts.slice(0, i + 1).join("/");
        return (
          <span key={sub} className="flex shrink-0 items-center gap-1">
            <ChevronRightIcon className="size-3.5 text-slate-400" />
            <button
              type="button"
              onClick={() => onNavigate(sub)}
              className="rounded-md px-2 py-0.5 text-slate-700 transition hover:bg-slate-900/8"
            >
              {part}
            </button>
          </span>
        );
      })}
    </div>
  );
}

export function FileManager({
  onClose,
  onOpenSettings,
  onOpenSystemFiles,
}: {
  onClose: () => void;
  onOpenSettings?: () => void;
  onOpenSystemFiles?: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadControlRef = useRef<ResumableUploadController | null>(null);
  const downloadAbortRef = useRef<AbortController | null>(null);
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileServiceUnavailable, setFileServiceUnavailable] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [transfer, setTransfer] = useState<{
    kind: "upload" | "download";
    name: string;
    percent: number;
    paused?: boolean;
  } | null>(null);
  const [showTrash, setShowTrash] = useState(false);
  const [sidebarTarget, setSidebarTarget] =
    useState<FinderSidebarTarget>("nas");
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [emptyApprovalOpen, setEmptyApprovalOpen] = useState(false);
  const [showAiPanel, setShowAiPanel] = useState(false);
  const [aiQuery, setAiQuery] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [aiResults, setAiResults] = useState<{
    answer?: string;
    hits: StorageHit[];
    unavailable: boolean;
    message?: string;
  } | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    listDir(path)
      .then((r) => {
        setEntries(r.entries);
        setFileServiceUnavailable(false);
      })
      .catch((e) => {
        if (e instanceof FileServiceUnavailableError) {
          setEntries([]);
          setFileServiceUnavailable(true);
          return;
        }
        setFileServiceUnavailable(false);
        setError(e instanceof Error ? e.message : "读取失败");
      })
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    if (!showTrash && sidebarTarget === "nas") refresh();
  }, [refresh, showTrash, sidebarTarget]);

  const selectSidebarTarget = (target: FinderSidebarTarget) => {
    setSidebarTarget(target);
    setShowTrash(false);
    setPath("");
    setEntries([]);
    setError(null);
    setFileServiceUnavailable(false);
  };

  const refreshTrash = useCallback(() => {
    listTrash()
      .then((r) => setTrash(r.entries))
      .catch(() => setTrash([]));
  }, []);

  useEffect(() => {
    if (showTrash) refreshTrash();
  }, [showTrash, refreshTrash]);

  const onDelete = async (entry: FileEntry) => {
    setBusy(`delete:${entry.path}`);
    try {
      await trashEntry(entry.path);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(null);
    }
  };

  const onUpload = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || showTrash || fileServiceUnavailable) return;
      setError(null);
      const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
      let completedBytes = 0;
      const control = new ResumableUploadController();
      uploadControlRef.current = control;
      try {
        for (const file of files) {
          setTransfer({ kind: "upload", name: file.name, percent: 0 });
          await uploadFile(
            path,
            file,
            (progress) => {
              const currentBytes = progress.total > 0 ? progress.loaded : 0;
              const percent =
                totalBytes > 0
                  ? Math.round(
                      ((completedBytes + currentBytes) / totalBytes) * 100,
                    )
                  : progress.percent;
              setTransfer((current) => ({
                kind: "upload",
                name: file.name,
                percent: Math.min(100, percent),
                paused: current?.paused,
              }));
            },
            { control },
          );
          completedBytes += file.size;
        }
        refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : "上传失败");
      } finally {
        uploadControlRef.current = null;
        setTransfer(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [fileServiceUnavailable, path, refresh, showTrash],
  );

  const onDownload = async (entry: FileEntry) => {
    const controller = new AbortController();
    downloadAbortRef.current = controller;
    setBusy(`download:${entry.path}`);
    setError(null);
    setTransfer({ kind: "download", name: entry.name, percent: 0 });
    try {
      await downloadFile(
        entry.path,
        entry.name,
        (progress) => {
          setTransfer({
            kind: "download",
            name: entry.name,
            percent: progress.percent,
          });
        },
        { signal: controller.signal },
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "下载失败");
    } finally {
      downloadAbortRef.current = null;
      setBusy(null);
      setTransfer(null);
    }
  };

  const onCopy = async (entry: FileEntry) => {
    setBusy(`copy:${entry.path}`);
    setError(null);
    try {
      await copyEntry(entry.path, siblingCopyPath(entry));
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "复制失败");
    } finally {
      setBusy(null);
    }
  };

  const onRestore = async (id: string) => {
    setBusy(id);
    try {
      await restoreTrash(id);
      refreshTrash();
    } finally {
      setBusy(null);
    }
  };

  const onEmpty = () => setEmptyApprovalOpen(true);

  const confirmEmpty = async (password: string) => {
    setBusy("__empty__");
    try {
      const approval = await requestHighRiskApproval(
        "files.trash.empty",
        "recycle-bin",
        password,
      );
      await emptyTrash(approval.approvalToken);
      refreshTrash();
      setEmptyApprovalOpen(false);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="absolute inset-0 z-[70]" data-desktop-interactive>
      <button
        type="button"
        className="absolute inset-0 bg-transparent"
        onClick={onClose}
        aria-label="关闭文件管理器"
      />
      <section className="mac-finder-window" data-liquid-surface="ultra-thick">
        <header className="mac-finder-toolbar">
          <div className="mac-finder-traffic">
            <button
              type="button"
              className="mac-traffic-light close"
              onClick={onClose}
              aria-label="关闭"
            />
            <span className="mac-traffic-light minimize" />
            <span className="mac-traffic-light zoom" />
          </div>
          <div className="mac-finder-navigation">
            <button
              type="button"
              onClick={() => setPath("")}
              disabled={!path || showTrash || sidebarTarget !== "nas"}
              aria-label="后退"
            >
              <ArrowLeftIcon />
            </button>
            <button type="button" disabled aria-label="前进">
              <ArrowRightIcon />
            </button>
          </div>
          <div className="mac-finder-location">
            {showTrash ? (
              <h2>
                <Trash2Icon />
                回收站
              </h2>
            ) : sidebarTarget === "nas" ? (
              <Breadcrumb path={path} onNavigate={setPath} />
            ) : (
              <h2>
                <FolderIcon />
                {FINDER_TARGET_LABELS[sidebarTarget]}
              </h2>
            )}
          </div>
          <div className="mac-finder-actions">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={
                showTrash ||
                sidebarTarget !== "nas" ||
                fileServiceUnavailable ||
                transfer?.kind === "upload"
              }
              aria-label="上传文件"
              title="上传文件"
            >
              <UploadCloudIcon />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(event) =>
                void onUpload(Array.from(event.currentTarget.files ?? []))
              }
            />
            <button type="button" aria-label="图标显示">
              <Grid2X2Icon />
            </button>
            <button type="button" aria-label="列表显示" className="is-active">
              <ListIcon />
            </button>
            <button
              type="button"
              onClick={() => setShowAiPanel((value) => !value)}
              className={showAiPanel ? "is-active" : ""}
              aria-label="AI 问答"
              title="AI 问答"
            >
              <MessageSquareIcon />
            </button>
            <button
              type="button"
              onClick={() =>
                window.open(
                  resolveAgentAppUrl("/workspace/storage"),
                  "_blank",
                  "noopener,noreferrer",
                )
              }
              aria-label="文件管家"
              title="在文件管家中管理文档库"
            >
              <DatabaseIcon />
            </button>
            <button
              type="button"
              onClick={() => {
                setSidebarTarget("nas");
                setPath("");
                setError(null);
                setShowTrash((value) => !value);
              }}
              className={showTrash ? "is-active" : ""}
              aria-label={showTrash ? "返回文件" : "回收站"}
            >
              <Trash2Icon />
            </button>
            <button type="button" aria-label="更多">
              <MoreHorizontalIcon />
            </button>
          </div>
        </header>

        <div className="mac-finder-main">
          <aside className="mac-finder-sidebar">
            <p>个人收藏</p>
            <button
              type="button"
              className={sidebarTarget === "recent" ? "is-selected" : ""}
              onClick={() => selectSidebarTarget("recent")}
            >
              <Clock3Icon className="text-[#0a84ff]" />
              最近使用
            </button>
            <button
              type="button"
              className={sidebarTarget === "airdrop" ? "is-selected" : ""}
              onClick={() => selectSidebarTarget("airdrop")}
            >
              <CloudIcon className="text-[#0a84ff]" />
              隔空投送
            </button>
            <button
              type="button"
              className={sidebarTarget === "applications" ? "is-selected" : ""}
              onClick={() => selectSidebarTarget("applications")}
            >
              <AppWindowIcon className="text-[#0a84ff]" />
              应用程序
            </button>
            <button
              type="button"
              className={sidebarTarget === "home" ? "is-selected" : ""}
              onClick={() => selectSidebarTarget("home")}
            >
              <HomeIcon className="text-[#0a84ff]" />
              个人
            </button>
            <button
              type="button"
              className={sidebarTarget === "desktop" ? "is-selected" : ""}
              onClick={() => selectSidebarTarget("desktop")}
            >
              <FolderIcon className="text-[#0a84ff]" />
              桌面
            </button>
            <button
              type="button"
              className={sidebarTarget === "downloads" ? "is-selected" : ""}
              onClick={() => selectSidebarTarget("downloads")}
            >
              <DownloadIcon className="text-[#0a84ff]" />
              下载
            </button>
            <p>位置</p>
            {onOpenSystemFiles && (
              <button type="button" onClick={onOpenSystemFiles}>
                <HardDriveIcon />
                Echo HD
              </button>
            )}
            <button
              type="button"
              className={sidebarTarget === "nas" ? "is-selected" : ""}
              onClick={() => {
                setSidebarTarget("nas");
                setShowTrash(false);
                setPath("");
                setEntries([]);
                setError(null);
                setFileServiceUnavailable(false);
              }}
            >
              <DatabaseIcon />
              NAS
            </button>
            <p>标签</p>
            <button type="button">
              <span className="mac-tag-dot bg-red-500" />
              重要
            </button>
            <button type="button">
              <span className="mac-tag-dot bg-blue-500" />
              项目
            </button>
          </aside>

          <div
            className="mac-finder-content"
            onDragOver={(event) => {
              if (
                showTrash ||
                sidebarTarget !== "nas" ||
                fileServiceUnavailable
              )
                return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
              setDragActive(true);
            }}
            onDragLeave={(event) => {
              const next = event.relatedTarget;
              if (
                !(next instanceof Node) ||
                !event.currentTarget.contains(next)
              ) {
                setDragActive(false);
              }
            }}
            onDrop={(event) => {
              if (
                showTrash ||
                sidebarTarget !== "nas" ||
                fileServiceUnavailable
              )
                return;
              event.preventDefault();
              setDragActive(false);
              void onUpload(Array.from(event.dataTransfer.files));
            }}
          >
            {showAiPanel && (
              <AiDocPanel
                query={aiQuery}
                onQueryChange={setAiQuery}
                loading={aiLoading}
                results={aiResults}
                onSearch={async (q, mode) => {
                  setAiLoading(true);
                  setAiResults(null);
                  try {
                    if (mode === "answer") {
                      const r = await answerStorage(q);
                      setAiResults({
                        answer: r.answer,
                        hits: r.citations || [],
                        unavailable: !r.available,
                        message: r.error,
                      });
                    } else {
                      const r = await searchStorage(q);
                      setAiResults({
                        hits: r.hits,
                        unavailable: !r.available,
                        message: r.message || r.error,
                      });
                    }
                  } finally {
                    setAiLoading(false);
                  }
                }}
              />
            )}

            {error && <div className="mac-finder-error">{error}</div>}

            {transfer && (
              <div className="mac-finder-transfer" role="status">
                {transfer.kind === "upload" ? (
                  <UploadCloudIcon />
                ) : (
                  <DownloadIcon />
                )}
                <span>
                  {transfer.kind === "upload" ? "正在上传" : "正在下载"} ·{" "}
                  {transfer.name}
                </span>
                <div className="mac-finder-transfer-progress">
                  <i style={{ width: `${transfer.percent}%` }} />
                </div>
                <strong>
                  {transfer.percent > 0 ? `${transfer.percent}%` : "…"}
                </strong>
                {transfer.kind === "upload" && (
                  <div className="mac-finder-transfer-actions">
                    <button
                      type="button"
                      className="rounded-md p-1 text-slate-500 hover:bg-slate-900/8"
                      aria-label={transfer.paused ? "继续上传" : "暂停上传"}
                      onClick={() => {
                        const control = uploadControlRef.current;
                        if (!control) return;
                        if (control.isPaused) control.resume();
                        else control.pause();
                        setTransfer((current) =>
                          current
                            ? { ...current, paused: control.isPaused }
                            : current,
                        );
                      }}
                    >
                      {transfer.paused ? (
                        <PlayIcon className="size-3.5" />
                      ) : (
                        <PauseIcon className="size-3.5" />
                      )}
                    </button>
                    <button
                      type="button"
                      className="rounded-md p-1 text-slate-500 hover:bg-red-500/10 hover:text-red-600"
                      aria-label="取消上传"
                      onClick={() => uploadControlRef.current?.cancel()}
                    >
                      <XIcon className="size-3.5" />
                    </button>
                  </div>
                )}
                {transfer.kind === "download" && (
                  <div className="mac-finder-transfer-actions">
                    <button
                      type="button"
                      className="rounded-md p-1 text-slate-500 hover:bg-red-500/10 hover:text-red-600"
                      aria-label="取消下载"
                      onClick={() => downloadAbortRef.current?.abort()}
                    >
                      <XIcon className="size-3.5" />
                    </button>
                  </div>
                )}
              </div>
            )}

            <div
              className={`mac-finder-list${dragActive ? " is-drop-target" : ""}`}
            >
              {showTrash ? (
                <TrashView
                  trash={trash}
                  busy={busy}
                  onRestore={onRestore}
                  onEmpty={onEmpty}
                />
              ) : sidebarTarget !== "nas" ? (
                <div
                  className="mac-finder-empty mac-finder-local-empty"
                  role="status"
                >
                  <FolderIcon
                    className="size-14 text-slate-400"
                    strokeWidth={1.25}
                  />
                  <strong>{FINDER_TARGET_LABELS[sidebarTarget]}</strong>
                  <span>
                    此位置由宿主系统管理，当前桌面会话未接入本地文件桥接。
                  </span>
                  {onOpenSystemFiles && (
                    <button
                      type="button"
                      onClick={onOpenSystemFiles}
                      className="mt-3 rounded-md bg-slate-900/8 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-900/12"
                    >
                      打开系统文件
                    </button>
                  )}
                </div>
              ) : loading ? (
                <div className="mac-finder-empty">
                  <Loader2Icon className="size-5 animate-spin" />
                </div>
              ) : fileServiceUnavailable ? (
                <div className="mac-finder-empty" role="status">
                  <HardDriveIcon
                    className="size-14 text-slate-400"
                    strokeWidth={1.25}
                  />
                  <strong>NAS 文件服务尚未启用</strong>
                  <span>
                    完成存储配置后即可浏览、上传和管理此设备上的文件。
                  </span>
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={refresh}
                      className="rounded-md bg-slate-900/8 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-900/12"
                    >
                      重试连接
                    </button>
                    {onOpenSettings && (
                      <button
                        type="button"
                        onClick={onOpenSettings}
                        className="rounded-md bg-[#0a84ff] px-3 py-1.5 text-xs font-medium text-white transition hover:bg-[#0077ed]"
                      >
                        打开系统设置
                      </button>
                    )}
                  </div>
                </div>
              ) : entries.length === 0 ? (
                <div className="mac-finder-empty">
                  <FolderIcon
                    className="size-14 text-[#63b6ee]"
                    strokeWidth={1.25}
                  />
                  <strong>此文件夹为空</strong>
                  <span>拖移文件到此处即可存储在 NAS 中</span>
                </div>
              ) : (
                <ul className="mac-finder-table">
                  <li className="mac-finder-table-head">
                    <span>名称</span>
                    <span>大小</span>
                    <span>种类</span>
                  </li>
                  {entries.map((entry) => (
                    <li key={entry.path} className="group">
                      <button
                        type="button"
                        onDoubleClick={() => {
                          if (entry.kind === "dir") setPath(entry.path);
                          else void onDownload(entry);
                        }}
                      >
                        {entry.kind === "dir" ? (
                          <FolderIcon className="text-[#5eb6ed]" />
                        ) : (
                          <FileIcon className="text-slate-400" />
                        )}
                        <span>{entry.name}</span>
                      </button>
                      <span>
                        {entry.kind === "file" ? formatSize(entry.size) : "--"}
                      </span>
                      <span>{entry.kind === "dir" ? "文件夹" : "文稿"}</span>
                      <div className="mac-finder-row-actions">
                        {entry.kind === "file" && (
                          <button
                            type="button"
                            onClick={() => void onDownload(entry)}
                            disabled={busy !== null}
                            title="下载"
                            aria-label={`下载 ${entry.name}`}
                          >
                            {busy === `download:${entry.path}` ? (
                              <Loader2Icon className="animate-spin" />
                            ) : (
                              <DownloadIcon />
                            )}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => void onCopy(entry)}
                          disabled={busy !== null}
                          title="复制副本"
                          aria-label={`复制 ${entry.name}`}
                        >
                          {busy === `copy:${entry.path}` ? (
                            <Loader2Icon className="animate-spin" />
                          ) : (
                            <CopyIcon />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => void onDelete(entry)}
                          disabled={busy !== null}
                          title="移入回收站"
                          aria-label={`删除 ${entry.name}`}
                          className="mac-finder-delete"
                        >
                          {busy === `delete:${entry.path}` ? (
                            <Loader2Icon className="animate-spin" />
                          ) : (
                            <Trash2Icon />
                          )}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {dragActive && (
                <div className="mac-finder-drop-overlay">
                  <UploadCloudIcon />
                  <strong>松开以上传到 {path || "NAS"}</strong>
                  <span>重名文件不会被覆盖</span>
                </div>
              )}
            </div>
            <footer className="mac-finder-status">
              {sidebarTarget !== "nas"
                ? "本地位置"
                : fileServiceUnavailable
                  ? "NAS 服务未连接"
                  : showTrash
                    ? `${trash.length} 个项目`
                    : `${entries.length} 个项目`}
              <span>
                {sidebarTarget !== "nas"
                  ? "等待系统文件桥接"
                  : fileServiceUnavailable
                    ? "等待配置"
                    : "本地安全存储"}
              </span>
            </footer>
          </div>
        </div>
      </section>
      <HighRiskApprovalDialog
        open={emptyApprovalOpen}
        title="永久清空回收站？"
        description={`将永久删除回收站中的 ${trash.length} 个项目，此操作无法撤销。`}
        targetLabel="回收站 · 物理删除"
        confirmLabel="永久删除"
        destructive
        onCancel={() => setEmptyApprovalOpen(false)}
        onConfirm={confirmEmpty}
      />
    </div>
  );
}

function AiDocPanel({
  query,
  onQueryChange,
  loading,
  results,
  onSearch,
}: {
  query: string;
  onQueryChange: (q: string) => void;
  loading: boolean;
  results: {
    answer?: string;
    hits: StorageHit[];
    unavailable: boolean;
    message?: string;
  } | null;
  onSearch: (q: string, mode: "search" | "answer") => Promise<void>;
}) {
  const [mode, setMode] = useState<"search" | "answer">("search");
  return (
    <div className="border-b border-slate-900/8 bg-slate-50/60 px-5 py-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && query.trim() && !loading) {
                void onSearch(query.trim(), mode);
              }
            }}
            placeholder="问我关于你文档库的问题…"
            className="h-8 w-full rounded-lg border border-slate-300 bg-white pl-8 pr-3 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-blue-400"
          />
        </div>
        <button
          type="button"
          onClick={() => setMode(mode === "search" ? "answer" : "search")}
          className="inline-flex h-8 items-center rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
          title={mode === "search" ? "切换为智能回答" : "切换为仅检索"}
        >
          {mode === "search" ? "检索" : "回答"}
        </button>
        <button
          type="button"
          disabled={!query.trim() || loading}
          onClick={() => void onSearch(query.trim(), mode)}
          className="inline-flex h-8 items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <MessageSquareIcon className="size-3.5" />
          )}
          {loading ? "思考中…" : mode === "search" ? "检索" : "问答"}
        </button>
      </div>

      {results && (
        <div className="mt-3 space-y-2">
          {results.unavailable && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              {results.message ||
                "本地文档库未运行。请先启动 Echo Storage 服务。"}
            </div>
          )}
          {results.answer && (
            <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs leading-relaxed text-slate-700">
              <span className="mb-1 block text-[10px] font-medium text-slate-400">
                回答
              </span>
              {results.answer}
            </div>
          )}
          {results.hits.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-white p-2">
              <div className="mb-1 text-[10px] font-medium text-slate-400">
                {results.answer ? "引用来源" : "检索结果"}
              </div>
              <ul className="space-y-1.5">
                {results.hits.map((hit, i) => (
                  <li
                    key={`${hit.path}-${i}`}
                    className="rounded-md bg-slate-50 px-2 py-1.5 text-xs"
                  >
                    <div className="font-medium text-slate-700">
                      {hit.title || hit.path}
                    </div>
                    {hit.snippet && (
                      <div className="mt-0.5 line-clamp-2 text-slate-500">
                        {hit.snippet}
                      </div>
                    )}
                    {hit.path && (
                      <div className="mt-0.5 text-[10px] text-slate-400">
                        {hit.path}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!results.unavailable &&
            results.hits.length === 0 &&
            !results.answer && (
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
                未找到相关文档。
              </div>
            )}
        </div>
      )}
    </div>
  );
}

function TrashView({
  trash,
  busy,
  onRestore,
  onEmpty,
}: {
  trash: TrashEntry[];
  busy: string | null;
  onRestore: (id: string) => void;
  onEmpty: () => void;
}) {
  if (trash.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">
        回收站是空的
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs text-slate-500">
          {trash.length} 项 · 恢复回原位置,或永久删除
        </p>
        <button
          type="button"
          onClick={onEmpty}
          disabled={busy === "__empty__"}
          className="inline-flex h-7 items-center gap-1.5 rounded-lg bg-red-600 px-2.5 text-xs font-medium text-white transition hover:bg-red-700 disabled:opacity-50"
        >
          {busy === "__empty__" ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <Trash2Icon className="size-3" />
          )}
          清空回收站
        </button>
      </div>
      <ul className="grid grid-cols-1 gap-1">
        {trash.map((entry) => (
          <li
            key={entry.id}
            className="flex items-center gap-3 rounded-xl px-3 py-2 transition hover:bg-slate-900/5"
          >
            {entry.kind === "dir" ? (
              <FolderIcon className="size-5 shrink-0 text-amber-500" />
            ) : (
              <FileIcon className="size-5 shrink-0 text-slate-400" />
            )}
            <span className="min-w-0 flex-1 truncate text-sm">
              {entry.name}
              <span className="ml-2 text-xs text-slate-400">
                {entry.original}
              </span>
            </span>
            <button
              type="button"
              onClick={() => onRestore(entry.id)}
              disabled={busy === entry.id}
              title="恢复"
              className="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
            >
              {busy === entry.id ? (
                <Loader2Icon className="size-3 animate-spin" />
              ) : (
                <RotateCcwIcon className="size-3" />
              )}
              恢复
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
