import {
  ChevronDownIcon,
  ChevronRightIcon,
  FileEditIcon,
  FileIcon,
  FilePlusIcon,
  FileXIcon,
  RotateCcwIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { canAccessOperatorControlPlane } from "@/core/auth/control-plane-access";
import type { FileOpEvent } from "@/core/observability/api";
import { useFileOpStream } from "@/core/observability/file-ops";
import { useOptionalAuth } from "@/providers/AuthProvider";
import { cn } from "@/lib/utils";

import { HunkHeader } from "./diff-editor/hunk-actions";
import { parseUnifiedDiff, type DiffHunk } from "./diff-editor/utils";

interface ChangesPanelProps {
  className?: string;
  onFileClick?: (path: string) => void;
}

export function ChangesPanel({ className, onFileClick }: ChangesPanelProps) {
  const { t } = useI18n();
  const auth = useOptionalAuth();
  const events = useFileOpStream({
    limit: 50,
    enabled: canAccessOperatorControlPlane(
      auth?.authStatus ?? null,
      auth?.user ?? null,
    ),
  });

  const uniqueFiles = new Map<string, FileOpEvent>();
  for (const e of events) {
    uniqueFiles.set(e.path, e);
  }
  const files = [...uniqueFiles.values()].reverse();

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2">
          <FileEditIcon className="size-4 text-primary" />
          <span className="text-sm font-medium">{t.changesPanel.title}</span>
          {files.length > 0 && (
            <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
              {files.length}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {files.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-4 text-muted-foreground/50">
            <FileIcon className="mb-2 size-8 opacity-30" />
            <span className="text-xs">{t.changesPanel.empty}</span>
            <span className="mt-1 text-xs opacity-60">
              {t.changesPanel.emptyHint}
            </span>
          </div>
        ) : (
          <ul className="divide-y divide-border/30">
            {files.map((event) => (
              <ChangeRow
                key={`${event.ts}-${event.path}`}
                event={event}
                onFileClick={onFileClick}
                t={t}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ChangeRow({
  event,
  onFileClick,
  t,
}: {
  event: FileOpEvent;
  onFileClick?: (path: string) => void;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const [expanded, setExpanded] = useState(false);
  const [accepted, setAccepted] = useState<Record<string, boolean | null>>({});
  const [busyHunks, setBusyHunks] = useState<Record<string, boolean>>({});
  const [reverting, setReverting] = useState(false);
  const hasDiff = !!event.diff && event.diff.length > 0;
  const fileName = event.path.split(/[/\\]/).pop() || event.path;
  const dirPath = event.path.split(/[/\\]/).slice(0, -1).join("/");
  const hunks = useMemo(() => parseUnifiedDiff(event.diff ?? ""), [event.diff]);

  const handleRevert = async () => {
    setReverting(true);
    try {
      const base = getBackendBaseURL();
      const response = event.diff
        ? await fetch(`${base}/api/fs/revert-diff`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              path: event.path,
              diff: event.diff,
              delete_empty: event.action === "create",
            }),
          })
        : await fetch(`${base}/api/fs/revert`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: event.path }),
          });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response));
      }
      notifyWorkspaceChanged();
      toast.success(t.codeMode.fileReverted);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.codeMode.failedToRevertFile,
      );
    } finally {
      setReverting(false);
    }
  };

  const handleAcceptHunk = (hunkId: string) => {
    setAccepted((prev) => ({ ...prev, [hunkId]: true }));
    toast.success(t.codeMode.hunkAccepted);
  };

  const handleRejectHunk = async (hunk: DiffHunk) => {
    if (busyHunks[hunk.id]) return;
    setBusyHunks((prev) => ({ ...prev, [hunk.id]: true }));
    try {
      const base = getBackendBaseURL();
      const response = await fetch(`${base}/api/fs/revert-diff`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: event.path,
          diff: hunkToUnifiedDiff(event.path, hunk),
        }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response));
      }
      setAccepted((prev) => ({ ...prev, [hunk.id]: false }));
      notifyWorkspaceChanged();
      toast.success(t.codeMode.hunkReverted);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.codeMode.failedToRevertHunk,
      );
    } finally {
      setBusyHunks((prev) => ({ ...prev, [hunk.id]: false }));
    }
  };

  return (
    <li>
      <div className="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-muted/50">
        <button
          type="button"
          onClick={() => hasDiff && setExpanded((v) => !v)}
          disabled={!hasDiff}
          className={cn(
            "shrink-0",
            hasDiff ? "cursor-pointer" : "cursor-default",
          )}
        >
          {hasDiff ? (
            expanded ? (
              <ChevronDownIcon className="size-3 text-muted-foreground" />
            ) : (
              <ChevronRightIcon className="size-3 text-muted-foreground" />
            )
          ) : (
            <span className="size-3" />
          )}
        </button>
        <ActionIcon action={event.action} className="size-3.5 shrink-0" />
        <button
          type="button"
          onClick={() => onFileClick?.(event.path)}
          className="min-w-0 flex-1 text-left"
        >
          <div className="truncate text-xs font-medium transition-colors hover:text-primary">
            {fileName}
          </div>
          {dirPath && (
            <div className="truncate text-xs text-muted-foreground/60">
              {dirPath}
            </div>
          )}
        </button>
        {hunks.length > 0 && (
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
            {t.codeMode.hunks(hunks.length)}
          </span>
        )}
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {formatDelta(event.bytes_delta)}
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            void handleRevert();
          }}
          disabled={reverting}
          className="shrink-0 rounded p-0.5 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive"
          title={t.codeMode.revertToLastCommit}
        >
          <RotateCcwIcon
            className={cn("size-3", reverting && "animate-spin")}
          />
        </button>
      </div>
      {expanded && event.diff && (
        <div className="mx-3 mb-2 overflow-hidden rounded border border-border-subtle bg-background">
          {hunks.length > 0 ? (
            <div className="max-h-[var(--panel-height-lg)] overflow-y-auto">
              {hunks.map((hunk) => (
                <HunkBlock
                  key={hunk.id}
                  filePath={event.path}
                  hunk={{ ...hunk, accepted: accepted[hunk.id] ?? null }}
                  busy={busyHunks[hunk.id] ?? false}
                  onAccept={handleAcceptHunk}
                  onReject={handleRejectHunk}
                />
              ))}
            </div>
          ) : (
            <pre
              className={cn(
                "max-h-[var(--panel-height-md)] overflow-x-auto overflow-y-auto px-2 py-1.5",
                "whitespace-pre text-xs font-mono leading-snug",
              )}
            >
              {event.diff}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}

function HunkBlock({
  filePath,
  hunk,
  busy,
  onAccept,
  onReject,
}: {
  filePath: string;
  hunk: DiffHunk;
  busy: boolean;
  onAccept: (hunkId: string) => void;
  onReject: (hunk: DiffHunk) => void;
}) {
  return (
    <div
      className={cn(
        "border-t border-border-subtle first:border-t-0",
        busy && "pointer-events-none opacity-60",
      )}
    >
      <HunkHeader
        hunk={hunk}
        filePath={filePath}
        onAcceptHunk={(_filePath, hunkId) => onAccept(hunkId)}
        onRejectHunk={() => onReject(hunk)}
      />
      <div className="overflow-x-auto max-w-full">
        <table className="w-full border-collapse text-xs font-mono leading-snug">
          <tbody>
            {hunk.lines.map((line, index) => (
              <tr
                key={`${hunk.id}-${index}`}
                className={cn(
                  line.type === "add" && "bg-success/8",
                  line.type === "remove" && "bg-destructive/8",
                )}
              >
                <td className="w-10 select-none border-r border-border-subtle px-2 py-0.5 text-right text-muted-foreground/60">
                  {line.oldLineNumber ?? ""}
                </td>
                <td className="w-10 select-none border-r border-border-subtle px-2 py-0.5 text-right text-muted-foreground/60">
                  {line.newLineNumber ?? ""}
                </td>
                <td
                  className={cn(
                    "px-2 py-0.5 whitespace-pre",
                    line.type === "add" && "text-success",
                    line.type === "remove" && "text-destructive",
                  )}
                >
                  <span className="mr-2 inline-block w-2 text-muted-foreground/60">
                    {line.type === "add"
                      ? "+"
                      : line.type === "remove"
                        ? "-"
                        : " "}
                  </span>
                  {line.content || "​"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function hunkToUnifiedDiff(filePath: string, hunk: DiffHunk): string {
  const normalizedPath = filePath.replace(/\\/g, "/");
  const lines = [
    `--- a/${normalizedPath}`,
    `+++ b/${normalizedPath}`,
    hunk.header,
    ...hunk.lines.map((line) => {
      const marker =
        line.type === "add" ? "+" : line.type === "remove" ? "-" : " ";
      return `${marker}${line.content}`;
    }),
  ];
  return `${lines.join("\n")}\n`;
}

function notifyWorkspaceChanged() {
  window.dispatchEvent(new CustomEvent("echo:workspace-changed"));
}

async function responseErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return payload.detail;
    if (payload?.detail?.error) return String(payload.detail.error);
  } catch (e) {
    swallow(e);
  }
  return response.statusText || `Request failed (${response.status})`;
}

function ActionIcon({
  action,
  className,
}: {
  action: FileOpEvent["action"];
  className?: string;
}) {
  const Icon =
    action === "create"
      ? FilePlusIcon
      : action === "delete"
        ? FileXIcon
        : action === "edit"
          ? FileEditIcon
          : FileIcon;
  return <Icon className={className} />;
}

function formatDelta(delta: number): string {
  if (delta === 0) return "·";
  const sign = delta > 0 ? "+" : "";
  const abs = Math.abs(delta);
  if (abs < 1024) return `${sign}${delta}B`;
  if (abs < 1024 * 1024) return `${sign}${(delta / 1024).toFixed(1)}K`;
  return `${sign}${(delta / 1024 / 1024).toFixed(1)}M`;
}
