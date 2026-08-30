import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  BookOpenIcon,
  FileTextIcon,
  Loader2Icon,
  RefreshCwIcon,
  SearchIcon,
  SparklesIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useGenerateWiki,
  useUpdateWiki,
  useWikiDocs,
  useWikiDocument,
  useWikiStatus,
} from "@/core/wiki/hooks";
import { useStreamdownPlugins } from "@/core/streamdown";
import { pickLocalDirectory } from "@/core/workspace/pick-local-directory";
import {
  activateProjectRoot,
  useActiveProjectRoot,
} from "@/core/workspace/use-active-project-root";
import { cn } from "@/lib/utils";

const LazyStreamdown = lazy(
  () => import("@/components/ai-elements/streamdown-host"),
);

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  return `${(size / 1024).toFixed(size >= 10 * 1024 ? 0 : 1)} KB`;
}

export function WikiPanel() {
  const plugins = useStreamdownPlugins();
  const projectRoot = useActiveProjectRoot();
  const status = useWikiStatus(projectRoot);
  const docs = useWikiDocs(projectRoot);
  const [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const selectedDoc = useWikiDocument(selectedPath, projectRoot);
  const generate = useGenerateWiki(projectRoot);
  const update = useUpdateWiki(projectRoot);

  const entries = useMemo(() => docs.data?.docs ?? [], [docs.data?.docs]);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return entries;
    return entries.filter(
      (entry) =>
        entry.name.toLocaleLowerCase().includes(normalized) ||
        entry.path.toLocaleLowerCase().includes(normalized),
    );
  }, [entries, query]);

  useEffect(() => {
    if (!selectedPath && entries[0]) setSelectedPath(entries[0].path);
    if (
      selectedPath &&
      entries.length &&
      !entries.some((item) => item.path === selectedPath)
    ) {
      setSelectedPath(entries[0]?.path ?? null);
    }
  }, [entries, selectedPath]);

  const runGeneration = async () => {
    const action = status.data?.exists ? update : generate;
    try {
      const result = await action.mutateAsync();
      if (result.error) throw new Error(result.error);
      toast.success(status.data?.exists ? "Wiki 已更新" : "Wiki 生成已开始");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Wiki 操作失败");
    }
  };

  const refresh = () => {
    void Promise.all([status.refetch(), docs.refetch(), selectedDoc.refetch()]);
  };

  const loading = status.isLoading || docs.isLoading;
  const failed = status.isError || docs.isError;
  const mutating = generate.isPending || update.isPending;
  const chooseProject = async () => {
    try {
      const path = await pickLocalDirectory();
      if (path) activateProjectRoot(path);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目目录选择失败");
    }
  };

  if (!projectRoot) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-muted/15 px-6 text-center">
        <div className="grid size-10 place-items-center rounded-xl bg-muted text-muted-foreground">
          <BookOpenIcon className="size-5" />
        </div>
        <div>
          <div className="text-sm font-semibold">选择一个项目</div>
          <div className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">
            Wiki 会保存在项目自己的 .echo-wiki 中，并统一使用同一套插件规范。
          </div>
        </div>
        <Button size="sm" onClick={() => void chooseProject()}>
          选择项目目录
        </Button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex min-h-64 items-center justify-center text-muted-foreground">
        <Loader2Icon className="size-5 animate-spin" />
      </div>
    );
  }

  if (failed) {
    return (
      <div className="flex min-h-64 flex-col items-center justify-center gap-2 text-center">
        <BookOpenIcon className="size-5 text-muted-foreground" />
        <div className="text-sm font-medium">暂时无法读取 Wiki</div>
        <Button variant="outline" size="sm" onClick={refresh}>
          重新加载
        </Button>
      </div>
    );
  }

  if (!status.data?.exists && entries.length === 0) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-muted/15 px-6 text-center">
        <div className="grid size-10 place-items-center rounded-xl bg-muted text-muted-foreground">
          <SparklesIcon className="size-5" />
        </div>
        <div>
          <div className="text-sm font-semibold">把项目整理成 Wiki</div>
          <div className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">
            自动梳理模块、关键流程和依赖关系，生成可搜索、可引用的结构化知识页。
          </div>
        </div>
        <Button
          size="sm"
          disabled={mutating}
          onClick={() => void runGeneration()}
        >
          {mutating ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <SparklesIcon className="size-3.5" />
          )}
          生成 Wiki
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex min-h-12 flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <div className="grid size-8 place-items-center rounded-lg bg-muted text-muted-foreground">
            <BookOpenIcon className="size-4" />
          </div>
          <div>
            <div className="text-sm font-semibold">项目 Wiki</div>
            <div className="text-mini text-muted-foreground">
              {entries.length} 篇知识页 · {status.data?.files_analyzed ?? 0}{" "}
              个文件
            </div>
            {projectRoot && (
              <div className="max-w-80 truncate text-micro text-muted-foreground/75">
                {projectRoot}
              </div>
            )}
          </div>
        </div>
        <Badge variant="outline" className="ml-1 h-5 text-micro">
          {status.data?.status === "current" ? "已同步" : "需要更新"}
        </Badge>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 text-xs text-muted-foreground"
            onClick={() => void chooseProject()}
          >
            切换项目
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label="刷新 Wiki"
            onClick={refresh}
          >
            <RefreshCwIcon
              className={cn(
                "size-3.5",
                (docs.isFetching || status.isFetching) && "animate-spin",
              )}
            />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            disabled={mutating}
            onClick={() => void runGeneration()}
          >
            {mutating ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            更新 Wiki
          </Button>
        </div>
      </div>

      <div className="grid min-h-[520px] flex-1 grid-cols-[260px_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-r border-border bg-muted/15">
          <div className="border-b border-border p-2">
            <div className="relative">
              <SearchIcon className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="搜索 Wiki"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索知识页"
                className="h-8 border-0 bg-background pl-8 text-xs shadow-none"
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
            {filtered.length === 0 ? (
              <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                没有匹配的知识页
              </div>
            ) : (
              filtered.map((entry) => {
                const folder = entry.path.includes("/")
                  ? entry.path.slice(0, entry.path.lastIndexOf("/"))
                  : "概览";
                return (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => setSelectedPath(entry.path)}
                    className={cn(
                      "mb-0.5 flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors",
                      selectedPath === entry.path
                        ? "bg-foreground text-background"
                        : "hover:bg-muted",
                    )}
                  >
                    <FileTextIcon className="mt-0.5 size-3.5 shrink-0 opacity-70" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">
                        {entry.name}
                      </span>
                      <span
                        className={cn(
                          "mt-0.5 block truncate text-micro",
                          selectedPath === entry.path
                            ? "text-background/65"
                            : "text-muted-foreground",
                        )}
                      >
                        {folder} · {formatSize(entry.size)}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        <article className="min-h-0 overflow-y-auto bg-background">
          {!selectedPath ? (
            <div className="flex h-full min-h-64 items-center justify-center text-xs text-muted-foreground">
              选择知识页查看
            </div>
          ) : selectedDoc.isLoading ? (
            <div className="flex min-h-64 items-center justify-center text-muted-foreground">
              <Loader2Icon className="size-5 animate-spin" />
            </div>
          ) : selectedDoc.isError ? (
            <div className="flex min-h-64 flex-col items-center justify-center gap-2">
              <div className="text-sm font-medium">知识页读取失败</div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void selectedDoc.refetch()}
              >
                重试
              </Button>
            </div>
          ) : (
            <div className="mx-auto max-w-4xl px-8 py-7">
              <div className="mb-5 border-b border-border pb-4">
                <div className="text-xs text-muted-foreground">
                  {selectedPath}
                </div>
              </div>
              <Suspense
                fallback={
                  <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
                }
              >
                <LazyStreamdown {...plugins} className="text-sm leading-7">
                  {selectedDoc.data?.content ?? ""}
                </LazyStreamdown>
              </Suspense>
            </div>
          )}
        </article>
      </div>
    </div>
  );
}
