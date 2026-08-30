/**
 * Teach & Repeat Panel
 *
 * Provides the full UI for the workflow record/replay system:
 *   - Recording indicator and controls (start/stop)
 *   - Workflow library browser with search
 *   - Replay dialog with parameter inputs
 *   - Replay progress and results view
 *   - Template editor (rename, delete, duplicate)
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  CircleDotIcon,
  CopyIcon,
  Loader2Icon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  Trash2Icon,
  XCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleIcon,
  AlertTriangleIcon,
  WrenchIcon,
} from "lucide-react";

import {
  deleteTemplate,
  duplicateTemplate,
  getRecordingStatus,
  listTemplates,
  replayAdaptive,
  replayTemplate,
  startRecording,
  stopRecording,
} from "@/core/teach-repeat/api";
import type {
  RecordingStatus,
  ReplayResult,
  StepResult,
  WorkflowTemplateListItem,
  WorkflowParam,
} from "@/core/teach-repeat/types";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    completed:
      "bg-success/10 text-success dark:bg-success/30 dark:text-success",
    adapted:
      "bg-warning/10 text-warning dark:bg-warning/30 dark:text-warning",
    failed: "bg-destructive/10 text-destructive dark:bg-destructive/30 dark:text-destructive",
    running: "bg-primary/10 text-primary dark:bg-primary/30 dark:text-primary",
    pending: "bg-muted text-muted-foreground dark:bg-muted dark:text-muted-foreground/70",
    success:
      "bg-success/10 text-success dark:bg-success/30 dark:text-success",
    skipped: "bg-muted text-muted-foreground dark:bg-muted dark:text-muted-foreground/70",
  };

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-lg px-2 py-0.5 text-xs font-medium",
        styles[status] ?? styles.pending,
      )}
    >
      {status}
    </span>
  );
}

function StepResultIcon({ status }: { status: string }) {
  switch (status) {
    case "success":
      return <CheckCircle2Icon className="size-3.5 text-success" />;
    case "adapted":
      return <WrenchIcon className="size-3.5 text-warning" />;
    case "failed":
      return <XCircleIcon className="size-3.5 text-destructive" />;
    case "skipped":
      return <CircleIcon className="size-3.5 text-muted-foreground/70" />;
    default:
      return <CircleDotIcon className="size-3.5 text-muted-foreground/70" />;
  }
}

// ---------------------------------------------------------------------------
// Recording Indicator
// ---------------------------------------------------------------------------

function RecordingIndicator({
  status,
  onStop,
}: {
  status: RecordingStatus;
  onStop: () => void;
}) {
  const { t } = useI18n();
  if (!status.recording) return null;

  return (
    <div className="bg-destructive/10 border-destructive/30 flex items-center gap-2 rounded-lg border px-3 py-2">
      <span className="bg-destructive size-2.5 animate-pulse rounded-lg" />
      <span className="text-destructive text-sm font-medium">
        {t.teachRepeat.recording}: {status.name}
      </span>
      <span className="text-muted-foreground text-xs">
        ({status.step_count} {t.teachRepeat.steps})
      </span>
      <button
        type="button"
        onClick={onStop}
        className="text-destructive hover:text-destructive/80 ml-auto text-xs underline"
      >
        {t.teachRepeat.stop}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Start Recording Dialog
// ---------------------------------------------------------------------------

function StartRecordingDialog({
  threadId,
  onStarted,
  onCancel,
}: {
  threadId: string;
  onStarted: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    if (!name.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await startRecording({
        thread_id: threadId,
        name: name.trim(),
        description: description.trim(),
      });
      onStarted();
    } catch (e) {
      swallow(e);
      setError(e instanceof Error ? e.message : "Failed to start recording");
    }
    setLoading(false);
  };

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">{t.teachRepeat.startRecording}</h3>
      <p className="text-muted-foreground text-xs">
        {t.teachRepeat.startRecordingDesc}
      </p>
      <div className="space-y-2">
        <Input
          type="text"
          placeholder={t.teachRepeat.workflowNamePlaceholder}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="border-input bg-background w-full rounded-lg border px-3 py-1.5 text-sm"
          aria-label={t.teachRepeat.workflowNamePlaceholder}
          autoFocus
        />
        <Textarea
          placeholder={t.teachRepeat.descriptionOptional}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="border-input bg-background w-full rounded-lg border px-3 py-1.5 text-sm"
        />
      </div>
      {error && <p className="text-destructive text-xs">{error}</p>}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void handleStart()}
          disabled={!name.trim() || loading}
          className="bg-destructive text-destructive-foreground hover:bg-destructive/90 inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50"
        >
          {loading ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <CircleDotIcon className="size-3" />
          )}
          {t.teachRepeat.startRecording}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-muted-foreground hover:text-foreground text-xs"
        >
          {t.common.cancel}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Replay Dialog
// ---------------------------------------------------------------------------

function ReplayDialog({
  template,
  onReplay,
  onCancel,
}: {
  template: WorkflowTemplateListItem & { params?: WorkflowParam[] };
  onReplay: (params: Record<string, unknown>, adaptive: boolean) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const params = useMemo<WorkflowParam[]>(
    () => template.params ?? [],
    [template.params],
  );

  // Pre-fill defaults
  useEffect(() => {
    const defaults: Record<string, string> = {};
    for (const p of params) {
      if (p.default_value) defaults[p.name] = p.default_value;
    }
    setParamValues(defaults);
  }, [params]);

  const hasParams = params.length > 0;

  const handleReplay = (adaptive: boolean) => {
    const parsed: Record<string, unknown> = {};
    for (const p of params) {
      const raw = paramValues[p.name] ?? p.default_value ?? "";
      if (p.param_type === "number") parsed[p.name] = Number(raw);
      else if (p.param_type === "boolean") parsed[p.name] = raw === "true";
      else if (p.param_type === "json") {
        try {
          parsed[p.name] = JSON.parse(raw);
        } catch (e) {
          swallow(e);
          parsed[p.name] = raw;
        }
      } else {
        parsed[p.name] = raw;
      }
    }
    onReplay(parsed, adaptive);
  };

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">Replay: {template.name}</h3>
      <p className="text-muted-foreground text-xs">
        {template.description || t.teachRepeat.noDescription}
      </p>
      {hasParams && (
        <div className="space-y-2">
          <p className="text-xs font-medium">Parameters</p>
          {params.map((p) => (
            <div key={p.name} className="space-y-0.5">
              <label className="text-muted-foreground text-xs">
                {p.name}
                {p.required && (
                  <span className="text-destructive ml-0.5">*</span>
                )}
                {p.description && (
                  <span className="ml-1 opacity-60">({p.description})</span>
                )}
              </label>
              {p.param_type === "boolean" ? (
                <select
                  value={paramValues[p.name] ?? "false"}
                  onChange={(e) =>
                    setParamValues({ ...paramValues, [p.name]: e.target.value })
                  }
                  className="border-input bg-background w-full rounded-lg border px-3 py-1.5 text-sm"
                >
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  type={p.param_type === "number" ? "number" : "text"}
                  placeholder={p.default_value ?? `Enter ${p.name}`}
                  value={paramValues[p.name] ?? ""}
                  onChange={(e) =>
                    setParamValues({ ...paramValues, [p.name]: e.target.value })
                  }
                  aria-label={p.name}
                  className="border-input bg-background w-full rounded-lg border px-3 py-1.5 text-sm"
                />
              )}
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => handleReplay(false)}
          className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium"
        >
          <PlayIcon className="size-3" />
          {t.teachRepeat.replay}
        </button>
        <button
          type="button"
          onClick={() => handleReplay(true)}
          className="bg-secondary text-secondary-foreground hover:bg-secondary/80 inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium"
        >
          <WrenchIcon className="size-3" />
          {t.teachRepeat.adaptiveReplay}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-muted-foreground hover:text-foreground text-xs"
        >
          {t.common.cancel}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Replay Results View
// ---------------------------------------------------------------------------

function ReplayResultsView({
  result,
  onClose,
}: {
  result: ReplayResult;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">
            {t.teachRepeat.replayResults}
          </h3>
          <StatusBadge status={result.status} />
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="text-muted-foreground hover:text-foreground"
        >
          <XCircleIcon className="size-4" />
        </button>
      </div>

      <div className="text-muted-foreground text-xs">
        {result.step_results.length} steps in {result.total_duration_ms}ms
      </div>

      {result.error && (
        <div className="bg-destructive/10 text-destructive rounded-lg px-3 py-2 text-xs">
          {result.error}
        </div>
      )}

      <div className="space-y-1">
        {result.step_results.map((sr: StepResult) => (
          <div key={sr.step_id} className="rounded-lg border text-xs">
            <button
              type="button"
              onClick={() => toggle(sr.step_id)}
              className="hover:bg-muted/50 flex w-full items-center gap-2 px-2 py-1.5 text-left"
            >
              {expanded.has(sr.step_id) ? (
                <ChevronDownIcon className="size-3" />
              ) : (
                <ChevronRightIcon className="size-3" />
              )}
              <StepResultIcon status={sr.status} />
              <span className="flex-1 truncate">{sr.step_id}</span>
              <span className="text-muted-foreground">{sr.duration_ms}ms</span>
            </button>
            {expanded.has(sr.step_id) && (
              <div className="border-t px-3 py-2 space-y-1">
                {sr.output && (
                  <p className="text-muted-foreground whitespace-pre-wrap break-all">
                    {sr.output.slice(0, 500)}
                  </p>
                )}
                {sr.error && <p className="text-destructive">{sr.error}</p>}
                {sr.adaptation_note && (
                  <p className="text-warning flex items-center gap-1">
                    <AlertTriangleIcon className="size-3" />
                    {sr.adaptation_note}
                  </p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Template Library
// ---------------------------------------------------------------------------

function TemplateLibrary({
  templates,
  loading,
  onRefresh,
  onSelect,
  onDuplicate,
  onDelete,
}: {
  templates: WorkflowTemplateListItem[];
  loading: boolean;
  onRefresh: () => void;
  onSelect: (t: WorkflowTemplateListItem) => void;
  onDuplicate: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useI18n();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return templates;
    const q = search.toLowerCase();
    return templates.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q),
    );
  }, [templates, search]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="border-input bg-background flex flex-1 items-center gap-1.5 rounded-lg border px-2 py-1">
          <SearchIcon className="text-muted-foreground size-3.5" />
          <input
            type="text"
            placeholder={t.teachRepeat.searchWorkflows}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label={t.teachRepeat.searchWorkflows}
            className="w-full bg-transparent text-sm outline-none"
          />
        </div>
        <button
          type="button"
          onClick={onRefresh}
          aria-label="Refresh"
          className="text-muted-foreground hover:text-foreground"
          title="Refresh"
        >
          {loading ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <RefreshCwIcon className="size-3.5" />
          )}
        </button>
      </div>

      {filtered.length === 0 && (
        <p className="text-muted-foreground py-6 text-center text-xs">
          {templates.length === 0
            ? t.teachRepeat.noWorkflows
            : t.teachRepeat.noMatchingWorkflows}
        </p>
      )}

      <div className="max-h-[50vh] space-y-1 overflow-y-auto">
        {filtered.map((t) => (
          <div
            key={t.id}
            className="hover:bg-muted/50 group flex items-center gap-2 rounded-lg border px-3 py-2"
          >
            <div
              role="button"
              tabIndex={0}
              className="min-w-0 flex-1 cursor-pointer"
              onClick={() => onSelect(t)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                onSelect(t);
              }}
            >
              <div className="flex items-center gap-1.5">
                <span className="truncate text-sm font-medium">{t.name}</span>
                {t.tags.length > 0 && (
                  <span className="bg-muted rounded px-1 py-0.5 text-xs">
                    {t.tags[0]}
                  </span>
                )}
              </div>
              <div className="text-muted-foreground text-xs">
                {t.step_count} steps
                {t.param_count > 0 && ` / ${t.param_count} params`}
                {t.use_count > 0 && ` / used ${t.use_count}x`}
              </div>
            </div>
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(t);
                }}
                aria-label="Replay"
                title="Replay"
                className="text-muted-foreground hover:text-foreground rounded p-1"
              >
                <PlayIcon className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDuplicate(t.id);
                }}
                aria-label="Duplicate"
                title="Duplicate"
                className="text-muted-foreground hover:text-foreground rounded p-1"
              >
                <CopyIcon className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(t.id);
                }}
                aria-label="Delete"
                title="Delete"
                className="text-muted-foreground hover:text-destructive rounded p-1"
              >
                <Trash2Icon className="size-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Panel
// ---------------------------------------------------------------------------

type PanelView =
  | "library"
  | "start-recording"
  | "replay-dialog"
  | "replay-results";

interface TeachRepeatPanelProps {
  threadId: string;
  className?: string;
}

export function TeachRepeatPanel({
  threadId,
  className,
}: TeachRepeatPanelProps) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [view, setView] = useState<PanelView>("library");
  const [templates, setTemplates] = useState<WorkflowTemplateListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState<RecordingStatus>({
    recording: false,
    step_count: 0,
    name: "",
  });
  const [selectedTemplate, setSelectedTemplate] =
    useState<WorkflowTemplateListItem | null>(null);
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);

  // -- Data fetching --------------------------------------------------------

  const refreshTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listTemplates({ limit: 100 });
      setTemplates(data.templates);
    } catch (e) {
      swallow(e);
    }
    setLoading(false);
  }, []);

  const refreshRecordingStatus = useCallback(async () => {
    if (!threadId) return;
    try {
      const status = await getRecordingStatus(threadId);
      setRecordingStatus(status);
    } catch (e) {
      swallow(e);
    }
  }, [threadId]);

  useEffect(() => {
    void refreshTemplates();
    void refreshRecordingStatus();
  }, [refreshTemplates, refreshRecordingStatus]);

  // Poll recording status while recording is active
  useEffect(() => {
    if (!recordingStatus.recording) return;
    const interval = setInterval(() => void refreshRecordingStatus(), 3000);
    return () => clearInterval(interval);
  }, [recordingStatus.recording, refreshRecordingStatus]);

  // -- Handlers -------------------------------------------------------------

  const handleStopRecording = async () => {
    try {
      await stopRecording({ thread_id: threadId, use_llm: true });
      await refreshRecordingStatus();
      await refreshTemplates();
    } catch (e) {
      swallow(e);
    }
  };

  const handleReplay = async (
    params: Record<string, unknown>,
    adaptive: boolean,
  ) => {
    if (!selectedTemplate) return;
    setReplayLoading(true);
    setView("replay-results");
    try {
      const result = adaptive
        ? await replayAdaptive(selectedTemplate.id, { params })
        : await replayTemplate(selectedTemplate.id, { params });
      setReplayResult(result);
    } catch (e) {
      swallow(e);
      setReplayResult({
        workflow_id: selectedTemplate.id,
        status: "failed",
        step_results: [],
        params_used: params,
        total_duration_ms: 0,
        error: e instanceof Error ? e.message : "Replay failed",
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      });
    }
    setReplayLoading(false);
  };

  const handleDuplicate = async (id: string) => {
    try {
      await duplicateTemplate(id);
      await refreshTemplates();
    } catch (e) {
      swallow(e);
    }
  };

  const handleDelete = async (id: string) => {
    const template = templates.find((tpl) => tpl.id === id);
    if (
      !(await confirm({
        title: t.teachRepeat.deleteConfirmTitle,
        description: template
          ? t.teachRepeat.deleteConfirmDescription(template.name)
          : t.teachRepeat.deleteConfirmDescriptionUnknown,
        confirmLabel: t.common.delete,
      }))
    )
      return;
    try {
      await deleteTemplate(id);
      setTemplates((prev) => prev.filter((t) => t.id !== id));
    } catch (e) {
      swallow(e);
    }
  };

  // -- Render ---------------------------------------------------------------

  return (
    <div className={cn("space-y-3 p-3", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <div className="flex size-6 items-center justify-center rounded-lg bg-primary/10">
            <CircleDotIcon className="text-primary size-3.5" />
          </div>
          <span className="text-sm font-semibold">{t.teachRepeat.title}</span>
        </div>
        {view !== "library" && view !== "start-recording" && (
          <button
            onClick={() => {
              setView("library");
              setSelectedTemplate(null);
              setReplayResult(null);
            }}
            className="text-muted-foreground hover:text-foreground text-xs"
          >
            {t.teachRepeat.backToLibrary}
          </button>
        )}
      </div>

      {/* Recording indicator */}
      <RecordingIndicator
        status={recordingStatus}
        onStop={() => void handleStopRecording()}
      />

      {/* Main content */}
      {view === "library" && (
        <>
          {!recordingStatus.recording && (
            <button
              onClick={() => setView("start-recording")}
              className="bg-destructive/10 text-destructive hover:bg-destructive/20 inline-flex w-full items-center justify-center gap-1.5 rounded-lg py-2 text-xs font-medium"
            >
              <PlusIcon className="size-3.5" />
              {t.teachRepeat.newRecording}
            </button>
          )}
          <TemplateLibrary
            templates={templates}
            loading={loading}
            onRefresh={() => void refreshTemplates()}
            onSelect={(t) => {
              setSelectedTemplate(t);
              setView("replay-dialog");
            }}
            onDuplicate={(id) => void handleDuplicate(id)}
            onDelete={(id) => void handleDelete(id)}
          />
        </>
      )}

      {view === "start-recording" && (
        <StartRecordingDialog
          threadId={threadId}
          onStarted={() => {
            void refreshRecordingStatus();
            setView("library");
          }}
          onCancel={() => setView("library")}
        />
      )}

      {view === "replay-dialog" && selectedTemplate && (
        <ReplayDialog
          template={selectedTemplate}
          onReplay={handleReplay}
          onCancel={() => {
            setView("library");
            setSelectedTemplate(null);
          }}
        />
      )}

      {view === "replay-results" && (
        <>
          {replayLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2Icon className="text-primary size-6 animate-spin" />
              <span className="text-muted-foreground ml-2 text-sm">
                {t.teachRepeat.replayingWorkflow}
              </span>
            </div>
          )}
          {!replayLoading && replayResult && (
            <ReplayResultsView
              result={replayResult}
              onClose={() => {
                setView("library");
                setReplayResult(null);
                setSelectedTemplate(null);
              }}
            />
          )}
        </>
      )}
      {confirmDialog}
    </div>
  );
}
