/**
 * /workspace/reflex/edit · YAML rule editor (React port).
 *
 * Replaces the inline-HTML editor formerly served at
 * /admin/reflex/edit. Same backend (/api/reflex/rules-yaml GET +
 * POST, /api/reflex/test) but uses the workspace's CodeMirror host
 * for line numbers / find / undo · and respects the workspace
 * theme.
 *
 * Design rules
 * ------------
 * * Optimistic-lock save · pass ``expected_mtime`` so a second
 *   browser tab can't silently overwrite our edit.
 * * Pre-validate before save · the backend already rejects bad
 *   YAML; we surface its error inline so the editor doesn't have
 *   to re-implement the parser.
 * * Cmd/Ctrl+S = save+reload · matches the muscle memory operators
 *   already have from the inline-HTML version.
 * * No YAML lang extension currently · CodeMirror still gives line
 *   numbers + bracket matching + find/replace which is plenty for
 *   the small rule files (~60 lines typical). Adding
 *   ``@codemirror/legacy-modes/mode/yaml`` is the next step if
 *   the file grows past a few KB.
 */

import { swallow } from "@/core/utils/log";
import type { Extension } from "@codemirror/state";
import {
  ArrowLeftIcon,
  FileWarningIcon,
  PlayIcon,
  SaveIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { reflexFetch } from "../api";
import { ReflexCardEditor } from "./card-editor";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  customDarkTheme,
  customLightTheme,
} from "@/components/workspace/codemirror-config";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

const LazyCodeMirror = lazy(
  () => import("@/components/workspace/codemirror-host"),
);

type LoadResp = {
  ok: boolean;
  path?: string;
  content?: string;
  mtime?: number;
  size?: number;
  error?: string;
};

type SaveResp = {
  ok: boolean;
  rules_in_file?: number;
  reloaded?: boolean;
  rules_loaded?: number;
  reload_error?: string;
  new_mtime?: number;
  error?: string;
  actual_mtime?: number;
  expected_mtime?: number;
};

type TestFailure = {
  source_rule_id: string;
  input: string;
  reason: string;
  time?: string;
  actor?: string;
};
type TestResp = {
  total: number;
  passed: number;
  failed: number;
  failures: TestFailure[];
  error?: string;
  note?: string;
};

type StatusKind = "idle" | "ok" | "warn" | "err";

function localizeReflexError(error: string, fallback: string) {
  if (error.trim().toLowerCase() === "no rules file") {
    return "未找到规则文件，请先创建规则或从磁盘重新加载。";
  }
  return error || fallback;
}

export default function ReflexEditorPage() {
  const { t } = useI18n();
  const { resolvedTheme } = useTheme();
  const [content, setContent] = useState<string>("");
  const [path, setPath] = useState<string>("");
  const [mtime, setMtime] = useState<number>(0);
  const [statusMsg, setStatusMsg] = useState<string>(t.reflexEditor.statusIdle);
  const [statusKind, setStatusKind] = useState<StatusKind>("idle");
  const [test, setTest] = useState<TestResp | null>(null);
  const [mode, setMode] = useState<"card" | "yaml">("card");
  // CodeMirror extensions are loaded lazily to keep the initial JS
  // bundle small. Empty for plain text · adding `legacy-modes/yaml`
  // here would buy syntax highlighting at the cost of a few extra KB.
  const [extensions] = useState<Extension[]>([]);

  const setStatus = useCallback((msg: string, kind: StatusKind = "idle") => {
    setStatusMsg(msg);
    setStatusKind(kind);
  }, []);

  const loadFile = useCallback(async () => {
    setStatus(t.reflexEditor.statusLoading);
    try {
      const r: LoadResp = await reflexFetch<LoadResp>("/api/reflex/rules-yaml");
      if (!r.ok) {
        setStatus(
          t.reflexEditor.statusLoadFailed(
            localizeReflexError(r.error ?? "", t.reflexEditor.statusUnknown),
          ),
          "err",
        );
        return;
      }
      setContent(r.content ?? "");
      setPath(r.path ?? "");
      setMtime(r.mtime ?? 0);
      setStatus(t.reflexEditor.statusLoaded, "ok");
    } catch (e) {
      swallow(e);
      setStatus(
        e instanceof Error ? e.message : t.reflexEditor.statusFetchError,
        "err",
      );
    }
  }, [setStatus, t]);

  useEffect(() => {
    void loadFile();
  }, [loadFile]);

  const save = useCallback(
    async (reload: boolean) => {
      setStatus(t.reflexEditor.statusSaving);
      try {
        const r: SaveResp = await reflexFetch<SaveResp>(
          "/api/reflex/rules-yaml",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              content,
              expected_mtime: mtime,
              reload,
            }),
          },
        );
        if (!r.ok) {
          setStatus(
            t.reflexEditor.statusSaveFailed(
              r.error ?? t.reflexEditor.statusUnknown,
            ),
            "err",
          );
          return;
        }
        setMtime(r.new_mtime ?? mtime);
        let msg = t.reflexEditor.statusSaved(r.rules_in_file ?? 0);
        if (r.reloaded === true)
          msg += t.reflexEditor.statusReloaded(r.rules_loaded ?? 0);
        let kind: StatusKind = "ok";
        if (r.reloaded === false) {
          msg += t.reflexEditor.statusReloadFailed(r.reload_error ?? "?");
          kind = "warn";
        }
        setStatus(msg, kind);
      } catch (e) {
        swallow(e);
        setStatus(
          e instanceof Error ? e.message : t.reflexEditor.statusSaveError,
          "err",
        );
      }
    },
    [content, mtime, setStatus, t],
  );

  const runTests = useCallback(async () => {
    setStatus(t.reflexEditor.statusRunningTests);
    try {
      const r: TestResp = await reflexFetch<TestResp>("/api/reflex/test");
      setTest(r);
      if (r.error) {
        setStatus(t.reflexEditor.statusTestError(r.error), "err");
        return;
      }
      const head = t.reflexEditor.testSummary(r.passed, r.total, r.failed);
      setStatus(head, r.failed === 0 ? "ok" : "err");
    } catch (e) {
      swallow(e);
      setStatus(
        e instanceof Error ? e.message : t.reflexEditor.statusTestErrorFallback,
        "err",
      );
    }
  }, [setStatus, t]);

  // Cmd/Ctrl+S keyboard handler · matches the inline-HTML editor's
  // muscle memory. Captures globally so it works regardless of
  // CodeMirror's focus state.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  const cmTheme = resolvedTheme === "dark" ? customDarkTheme : customLightTheme;
  const initialLoadFailed = statusKind === "err" && !content && !path;

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="px-4 pb-4">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
          <section className="workspace-panel px-6 py-4">
            <div className="flex items-center gap-3">
              <Button asChild variant="ghost" size="sm">
                <Link to="/workspace/reflex">
                  <ArrowLeftIcon className="mr-2 size-4" />
                  {t.reflexEditor.backButton}
                </Link>
              </Button>
              <div className="flex-1">
                <h1 className="text-lg font-semibold">
                  {t.reflexEditor.pageTitle}
                </h1>
                <div className="text-xs text-muted-foreground">
                  {path}
                  {mtime > 0 && (
                    <>
                      {" · "}
                      <span className="font-mono">
                        {t.reflexEditor.mtimePrefix(
                          new Date(mtime * 1000).toLocaleString(),
                        )}
                      </span>
                    </>
                  )}
                </div>
              </div>
              <StatusBadge msg={statusMsg} kind={statusKind} />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-md border border-border-default p-0.5">
                <button
                  onClick={() => setMode("card")}
                  className={cn(
                    "rounded px-3 py-1 text-xs transition-colors",
                    mode === "card"
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted/40",
                  )}
                >
                  {t.reflexEditor.modeCard}
                </button>
                <button
                  onClick={() => setMode("yaml")}
                  className={cn(
                    "rounded px-3 py-1 text-xs transition-colors",
                    mode === "yaml"
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted/40",
                  )}
                >
                  {t.reflexEditor.modeYaml}
                </button>
              </div>
              {!initialLoadFailed && mode === "yaml" && (
                <>
                  <Button variant="outline" size="sm" onClick={loadFile}>
                    {t.reflexEditor.reloadFromDisk}
                  </Button>
                  <Button variant="outline" size="sm" onClick={runTests}>
                    <PlayIcon className="mr-2 size-4" />
                    {t.reflexEditor.runTestsButton}
                  </Button>
                  <Button size="sm" onClick={() => save(true)}>
                    <SaveIcon className="mr-2 size-4" />
                    {t.reflexEditor.saveAndReload}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => save(false)}>
                    {t.reflexEditor.saveNoReload}
                  </Button>
                  <span className="ml-auto text-xs text-muted-foreground">
                    <kbd className="rounded border border-border-default bg-background/60 px-1.5 py-0.5 font-mono text-xs">
                      Ctrl/⌘+S
                    </kbd>{" "}
                    {t.reflexEditor.keyboardHintSuffix}
                  </span>
                </>
              )}
            </div>
          </section>

          {test && (
            <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <FileWarningIcon className="size-4" />
                  {t.reflexEditor.testResultsCard}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {test.error ? (
                  <div className="text-sm text-destructive">
                    {t.reflexEditor.errorPrefix(test.error)}
                  </div>
                ) : test.note ? (
                  <div className="text-sm text-muted-foreground">
                    {test.note}
                  </div>
                ) : (
                  <div className="space-y-1 text-sm">
                    <div
                      className={cn(
                        "font-medium",
                        test.failed === 0 ? "text-success" : "text-destructive",
                      )}
                    >
                      {t.reflexEditor.testSummary(
                        test.passed,
                        test.total,
                        test.failed,
                      )}
                    </div>
                    {test.failures.map((f, i) => (
                      <div
                        key={`${f.source_rule_id}-${i}`}
                        className="font-mono text-xs text-destructive"
                      >
                        {t.reflexEditor.testFailureRow(
                          f.source_rule_id,
                          JSON.stringify(f.input),
                          f.reason,
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {initialLoadFailed ? (
            <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
              <CardContent className="flex flex-col items-center gap-3 px-4 py-12 text-center">
                <FileWarningIcon className="size-8 text-destructive" />
                <div className="flex flex-wrap justify-center gap-2">
                  <Button variant="outline" size="sm" onClick={loadFile}>
                    {t.reflexEditor.reloadFromDisk}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : mode === "yaml" ? (
            <Card className="workspace-panel border-white/40 shadow-none dark:border-white/10">
              <CardContent className="p-2">
                <Suspense
                  fallback={
                    <div className="px-4 py-8 text-sm text-muted-foreground">
                      {t.reflexEditor.loadingEditor}
                    </div>
                  }
                >
                  <LazyCodeMirror
                    value={content}
                    onChange={setContent}
                    theme={cmTheme}
                    extensions={extensions}
                    basicSetup={{
                      lineNumbers: true,
                      foldGutter: true,
                      highlightActiveLine: true,
                      bracketMatching: true,
                    }}
                    className="min-h-[60vh] font-mono text-sm"
                  />
                </Suspense>
              </CardContent>
            </Card>
          ) : (
            <ReflexCardEditor
              key={mtime}
              onSwitchToYaml={() => setMode("yaml")}
              onSavedExternally={() => void loadFile()}
            />
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function StatusBadge({ msg, kind }: { msg: string; kind: StatusKind }) {
  const cls = {
    idle: "bg-muted/40 text-muted-foreground",
    ok: "bg-success/15 text-success",
    warn: "bg-warning/15 text-warning",
    err: "bg-destructive/15 text-destructive",
  }[kind];
  return (
    <span
      className={cn("rounded-md px-3 py-1 font-mono text-xs", cls)}
      title={msg}
    >
      {msg}
    </span>
  );
}
