/* Implementation note. */
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { FileTextIcon } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { ErrorState, LoadingState } from "@/components/ui/state";
import { cn } from "@/lib/utils";

const LazyStreamdown = lazy(
  () => import("@/components/ai-elements/streamdown-host"),
);

interface DocMeta {
  id: string;
  path: string;
}

// Implementation note.
type DocGroupKey = "entry" | "diagrams" | "topics" | "coreOrgans";

const DOC_GROUPS: { titleKey: DocGroupKey; ids: string[] }[] = [
  { titleKey: "entry", ids: ["readme", "core-path"] },
  { titleKey: "diagrams", ids: ["high-res-map", "high-res-mermaid"] },
  {
    titleKey: "topics",
    ids: ["chat-modes", "react-self-evo", "organ-tiering", "module-map"],
  },
  {
    titleKey: "coreOrgans",
    ids: [
      "organ-cerebrum",
      "organ-ganglia",
      "organ-beak",
      "organ-hearts",
      "organ-chromatophores",
    ],
  },
];

export default function ArchitecturePage() {
  const { t } = useI18n();
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [activeId, setActiveId] = useState<string>("core-path");
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  // Implementation note.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${getBackendBaseURL()}/api/architecture/docs`);
        if (!r.ok) throw new Error(`list failed: ${r.statusText}`);
        const data = (await r.json()) as { docs: DocMeta[] };
        if (!cancelled) setDocs(data.docs);
      } catch (e) {
        swallow(e);
        if (!cancelled) setError(e instanceof Error ? e.message : "unknown");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Implementation note.
  useEffect(() => {
    let cancelled = false;
    if (!activeId) return undefined;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const r = await fetch(
          `${getBackendBaseURL()}/api/architecture/docs/${encodeURIComponent(activeId)}`,
        );
        if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
        const data = (await r.json()) as { content: string };
        if (!cancelled) setContent(data.content);
      } catch (e) {
        swallow(e);
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "load failed");
          setContent("");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, reloadToken]);

  const availableIds = useMemo(() => new Set(docs.map((d) => d.id)), [docs]);
  const reloadActiveDocument = () => {
    if (!activeId) return;
    setReloadToken((current) => current + 1);
  };

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="p-0">
        <div className="flex flex-col md:flex-row w-full h-full min-h-0">
          {/* Implementation note. */}
          <aside
            className={cn(
              "shrink-0 border-r border-border-subtle bg-muted/20",
              "md:w-64 md:h-full overflow-y-auto",
            )}
          >
            <div className="px-4 py-3 border-b border-border-subtle">
              <h1 className="text-sm font-bold">{t.architecture.title}</h1>
              <p className="mt-0.5 text-xs text-muted-foreground leading-snug">
                {t.architecture.subtitle}
              </p>
            </div>
            <nav className="p-2 space-y-3">
              {DOC_GROUPS.map((grp) => (
                <div key={grp.titleKey}>
                  <div className="px-2 py-1 text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                    {t.architecture.groups[grp.titleKey]}
                  </div>
                  <ul className="mt-0.5 space-y-0.5">
                    {grp.ids
                      .filter((id) => availableIds.has(id))
                      .map((id) => (
                        <li key={id}>
                          <button
                            type="button"
                            onClick={() => setActiveId(id)}
                            className={cn(
                              "w-full text-left rounded px-2 py-1.5 text-xs",
                              "hover:bg-muted/60 transition-colors truncate",
                              id === activeId
                                ? "bg-primary/10 text-foreground font-medium"
                                : "text-muted-foreground",
                            )}
                            title={docs.find((d) => d.id === id)?.path ?? id}
                          >
                            {(t.architecture.docs as Record<string, string>)[
                              id
                            ] ?? id}
                          </button>
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
            </nav>
          </aside>

          {/* Implementation note. */}
          <main className="flex-1 min-w-0 overflow-y-auto">
            <div className="max-w-4xl mx-auto px-6 py-6">
              {loading && (
                <LoadingState
                  title={t.architecture.loading}
                  variant="skeleton"
                  className="rounded-lg"
                />
              )}
              {error && (
                <ErrorState
                  title={t.architecture.loadFailed}
                  detail={error}
                  actionLabel={t.architecture.retry}
                  onAction={reloadActiveDocument}
                />
              )}
              {!loading && !error && content && (
                <Suspense
                  fallback={
                    <LoadingState
                      title={t.architecture.rendering}
                      className="min-h-[180px]"
                    />
                  }
                >
                  <LazyStreamdown
                    className="prose prose-sm dark:prose-invert max-w-none"
                    parseIncompleteMarkdown={false}
                  >
                    {content}
                  </LazyStreamdown>
                </Suspense>
              )}
              {!loading && !error && !content && (
                <Empty className="min-h-[260px]">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <FileTextIcon />
                    </EmptyMedia>
                    <EmptyTitle>{t.architecture.emptyTitle}</EmptyTitle>
                    <EmptyDescription>
                      {t.architecture.emptyDescription}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </div>
          </main>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
