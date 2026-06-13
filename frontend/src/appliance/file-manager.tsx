/**
 * Octopus OS NAS 文件管理器(原生路线)。
 *
 * 浏览宿主存储、进入文件夹、删除→回收站、回收站恢复/清空。
 * 删除一律走回收站(硬约束),物理删除仅"清空回收站"一条显式路径。
 */

import { useCallback, useEffect, useState } from "react";
import {
  ChevronRightIcon,
  FileIcon,
  FolderIcon,
  Loader2Icon,
  RotateCcwIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";

import {
  emptyTrash,
  formatSize,
  listDir,
  listTrash,
  restoreTrash,
  trashEntry,
  type FileEntry,
  type TrashEntry,
} from "@/appliance/files";

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

export function FileManager({ onClose }: { onClose: () => void }) {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showTrash, setShowTrash] = useState(false);
  const [trash, setTrash] = useState<TrashEntry[]>([]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    listDir(path)
      .then((r) => setEntries(r.entries))
      .catch((e) => setError(e instanceof Error ? e.message : "读取失败"))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    if (!showTrash) refresh();
  }, [refresh, showTrash]);

  const refreshTrash = useCallback(() => {
    listTrash()
      .then((r) => setTrash(r.entries))
      .catch(() => setTrash([]));
  }, []);

  useEffect(() => {
    if (showTrash) refreshTrash();
  }, [showTrash, refreshTrash]);

  const onDelete = async (entry: FileEntry) => {
    setBusy(entry.path);
    try {
      await trashEntry(entry.path);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
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

  const onEmpty = async () => {
    setBusy("__empty__");
    try {
      await emptyTrash();
      refreshTrash();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="absolute inset-0 z-30">
      <div
        className="absolute inset-0 bg-black/20 backdrop-blur-sm"
        onClick={onClose}
      />
      <section className="absolute inset-x-6 bottom-24 top-12 mx-auto flex max-w-[820px] flex-col rounded-[26px] border border-white/35 bg-white/75 text-slate-800 shadow-2xl shadow-black/25 backdrop-blur-2xl">
        {/* header */}
        <div className="flex items-center justify-between gap-3 border-b border-slate-900/8 px-5 py-3.5">
          {showTrash ? (
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <Trash2Icon className="size-4" /> 回收站
            </h2>
          ) : (
            <Breadcrumb path={path} onNavigate={setPath} />
          )}
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => setShowTrash((v) => !v)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
            >
              {showTrash ? (
                <FolderIcon className="size-3.5" />
              ) : (
                <Trash2Icon className="size-3.5" />
              )}
              {showTrash ? "返回文件" : "回收站"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="grid size-8 place-items-center rounded-full bg-slate-900/8 text-slate-600 transition hover:bg-slate-900/14"
              aria-label="关闭"
            >
              <XIcon className="size-4" />
            </button>
          </div>
        </div>

        {error && (
          <div className="mx-5 mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}

        {/* body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {showTrash ? (
            <TrashView
              trash={trash}
              busy={busy}
              onRestore={onRestore}
              onEmpty={onEmpty}
            />
          ) : loading ? (
            <div className="flex h-full items-center justify-center text-slate-400">
              <Loader2Icon className="size-5 animate-spin" />
            </div>
          ) : entries.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">
              此文件夹为空
            </div>
          ) : (
            <ul className="grid grid-cols-1 gap-1">
              {entries.map((entry) => (
                <li
                  key={entry.path}
                  className="group flex items-center gap-3 rounded-xl px-3 py-2 transition hover:bg-slate-900/5"
                >
                  <button
                    type="button"
                    disabled={entry.kind !== "dir"}
                    onClick={() => entry.kind === "dir" && setPath(entry.path)}
                    className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-default"
                  >
                    {entry.kind === "dir" ? (
                      <FolderIcon className="size-5 shrink-0 text-amber-500" />
                    ) : (
                      <FileIcon className="size-5 shrink-0 text-slate-400" />
                    )}
                    <span className="truncate text-sm">{entry.name}</span>
                    {entry.kind === "file" && (
                      <span className="ml-auto shrink-0 text-xs text-slate-400">
                        {formatSize(entry.size)}
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(entry)}
                    disabled={busy === entry.path}
                    title="移入回收站"
                    className="grid size-7 shrink-0 place-items-center rounded-lg text-slate-400 opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 disabled:opacity-50"
                  >
                    {busy === entry.path ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <Trash2Icon className="size-3.5" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
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
