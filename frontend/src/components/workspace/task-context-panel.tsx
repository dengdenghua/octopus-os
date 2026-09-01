"use client";

import {
  BookOpenIcon,
  FileEditIcon,
  FolderOpenIcon,
  TerminalIcon,
} from "lucide-react";
import { useMemo } from "react";
import type { LiveToolEvent } from "./live-tool-timeline";
import { cn } from "@/lib/utils";
import {
  isEditToolName,
  isReadToolName,
  isShellToolName,
  isWriteToolName,
  shellCommandFromInput,
} from "./tool-name-groups";

interface TaskContextPanelProps {
  liveToolEvents: LiveToolEvent[];
  className?: string;
}

interface FileEntry {
  path: string;
  action: "read" | "write" | "edit";
  tool: string;
}

interface CommandEntry {
  command: string;
  status: "running" | "done" | "error";
}

function extractPath(input?: Record<string, unknown>): string | undefined {
  if (!input) return undefined;
  for (const key of ["path", "file_path", "filepath"]) {
    const v = input[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return undefined;
}

export function TaskContextPanel({
  liveToolEvents,
  className,
}: TaskContextPanelProps) {
  const { files, commands } = useMemo(() => {
    const fileMap = new Map<string, FileEntry>();
    const cmds: CommandEntry[] = [];

    for (const event of liveToolEvents) {
      if (event.parentToolUseId) continue;

      if (isShellToolName(event.name)) {
        const cmd = shellCommandFromInput(event.input, event.name);
        if (cmd) {
          cmds.push({
            command: cmd,
            status:
              event.status === "running"
                ? "running"
                : event.status === "error"
                  ? "error"
                  : "done",
          });
        }
        continue;
      }

      const path = extractPath(event.input);
      if (!path) continue;

      if (isWriteToolName(event.name)) {
        fileMap.set(path, { path, action: "write", tool: event.name });
      } else if (isEditToolName(event.name)) {
        fileMap.set(path, { path, action: "edit", tool: event.name });
      } else if (isReadToolName(event.name) && !fileMap.has(path)) {
        fileMap.set(path, { path, action: "read", tool: event.name });
      }
    }

    return {
      files: [...fileMap.values()],
      commands: cmds,
    };
  }, [liveToolEvents]);

  const reads = files.filter((f) => f.action === "read");
  const writes = files.filter(
    (f) => f.action === "write" || f.action === "edit",
  );

  if (files.length === 0 && commands.length === 0) return null;

  return (
    <div
      className={cn(
        "workspace-panel-subtle rounded-lg border border-border-default p-3 my-3",
        className,
      )}
    >
      <div className="text-xs font-semibold text-muted-foreground mb-2">
        Task Context
      </div>

      {reads.length > 0 && (
        <Section
          icon={<BookOpenIcon className="size-3 text-info" />}
          label={`Read (${reads.length})`}
        >
          {reads.map((f) => (
            <FileRow key={f.path} path={f.path} />
          ))}
        </Section>
      )}

      {writes.length > 0 && (
        <Section
          icon={<FileEditIcon className="size-3 text-info" />}
          label={`Modified (${writes.length})`}
        >
          {writes.map((f) => (
            <FileRow key={f.path} path={f.path} />
          ))}
        </Section>
      )}

      {commands.length > 0 && (
        <Section
          icon={<TerminalIcon className="size-3 text-success" />}
          label={`Commands (${commands.length})`}
        >
          {commands.slice(-8).map((c, i) => (
            <div key={i} className="flex items-center gap-1.5 py-0.5">
              <span
                className={cn(
                  "size-1.5 rounded-full shrink-0",
                  c.status === "done"
                    ? "bg-success"
                    : c.status === "error"
                      ? "bg-destructive"
                      : "bg-info animate-pulse",
                )}
              />
              <span className="text-xs font-mono text-muted-foreground truncate">
                {c.command}
              </span>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-2 last:mb-0">
      <div className="flex items-center gap-1.5 mb-1">
        {icon}
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {label}
        </span>
      </div>
      <div className="ml-4">{children}</div>
    </div>
  );
}

function FileRow({ path }: { path: string }) {
  const name = path.split(/[/\\]/).pop() || path;
  const dir = path.split(/[/\\]/).slice(0, -1).join("/");
  return (
    <div className="flex items-center gap-1.5 py-0.5">
      <FolderOpenIcon className="size-2.5 text-muted-foreground/50 shrink-0" />
      <span className="text-xs font-medium truncate">{name}</span>
      {dir && (
        <span className="text-xs text-muted-foreground/50 truncate">
          {dir}
        </span>
      )}
    </div>
  );
}
