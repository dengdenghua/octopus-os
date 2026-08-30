import {
  AlertCircleIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  ClockIcon,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { swallow } from "@/core/utils/log";
import { SettingsSection } from "./settings-section";

interface CronJob {
  name: string;
  command: string;
  cron_expression?: string;
  last_run?: string;
  last_status?: string;
  last_output?: string;
}

export function CronSettingsPage() {
  const { t } = useI18n();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCommand, setNewCommand] = useState("");
  const [newCron, setNewCron] = useState("0 * * * *");
  const [fieldErrors, setFieldErrors] = useState<{
    name?: string;
    command?: string;
    cron?: string;
  }>({});
  const [submitting, setSubmitting] = useState(false);
  const [taskToDelete, setTaskToDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const nameId = useId();
  const commandId = useId();
  const cronId = useId();

  const validate = useCallback(
    (values: { name: string; command: string; cron: string }) => {
      const errors: { name?: string; command?: string; cron?: string } = {};
      const name = values.name.trim();
      const command = values.command.trim();
      const cron = values.cron.trim();
      if (!name) errors.name = t.cronSettings.nameRequired;
      if (!command) errors.command = t.cronSettings.commandRequired;
      if (!cron) {
        errors.cron = t.cronSettings.cronRequired;
      } else if (!/^\S+\s+\S+\s+\S+\s+\S+\s+\S+$/.test(cron)) {
        errors.cron = t.cronSettings.cronInvalid;
      }
      return errors;
    },
    [
      t.cronSettings.commandRequired,
      t.cronSettings.cronInvalid,
      t.cronSettings.cronRequired,
      t.cronSettings.nameRequired,
    ],
  );

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/cron/`, {
        credentials: "include",
        headers: authHeaders(),
      });
      if (res.status === 401 || res.status === 403) {
        setJobs([]);
        setNeedsAuth(true);
        setLoadError(null);
        return;
      }
      if (!res.ok) {
        throw new Error(`Failed to load cron jobs: ${res.status}`);
      }
      const data = await res.json();
      setJobs(Array.isArray(data) ? data : []);
      setNeedsAuth(false);
      setLoadError(null);
    } catch (error) {
      swallow(error);
      setLoadError(
        error instanceof Error ? error.message : t.cronSettings.loadFailed,
      );
    } finally {
      setLoading(false);
    }
  }, [t.cronSettings.loadFailed]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  const handleAdd = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = { name: newName, command: newCommand, cron: newCron };
    const errors = validate(values);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/cron/`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        credentials: "include",
        body: JSON.stringify({
          name: values.name.trim(),
          command: values.command.trim(),
          cron_expression: values.cron.trim(),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Create failed: ${res.status}`);
      }
      setNewName("");
      setNewCommand("");
      setNewCron("0 * * * *");
      setFieldErrors({});
      setShowAdd(false);
      toast.success(t.cronSettings.createSuccess);
      void fetchJobs();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.cronSettings.createFailed,
      );
    } finally {
      setSubmitting(false);
    }
  };

  const doDeleteTask = async (name: string) => {
    setDeleting(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/cron/${encodeURIComponent(name)}`,
        {
          method: "DELETE",
          credentials: "include",
          headers: authHeaders(),
        },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Delete failed: ${res.status}`);
      }
      toast.success(t.cronSettings.deleteSuccess);
      void fetchJobs();
      return true;
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.cronSettings.deleteFailed,
      );
      return false;
    } finally {
      setDeleting(false);
    }
  };

  const handleDelete = (name: string) => {
    setTaskToDelete(name);
  };

  const statusLabel = (status: string) => {
    switch (status.trim().toLowerCase()) {
      case "success":
      case "completed":
        return t.taskBoard.completed;
      case "failed":
      case "error":
        return t.taskBoard.failed;
      case "running":
        return t.taskBoard.running;
      case "queued":
      case "pending":
        return t.taskBoard.queued;
      default:
        return status;
    }
  };

  return (
    <div className="space-y-4">
      <SettingsSection
        title={t.cronSettings.title}
        description={t.cronSettings.description}
      >
        <div className="mb-3 flex items-center justify-end">
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1.5"
            disabled={loading}
            onClick={fetchJobs}
          >
            {loading ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            {t.taskBoard.refresh}
          </Button>
        </div>

        <div className="space-y-2">
          {loading && jobs.length === 0 ? (
            <div
              role="status"
              className="flex items-center justify-center rounded-lg border border-border-default bg-card/40 py-8 text-sm text-muted-foreground"
            >
              <Loader2Icon className="mr-2 size-4 animate-spin" />
              {t.common.loading}
            </div>
          ) : needsAuth ? (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-lg border border-warning/20 bg-warning/5 px-3 py-3 text-sm text-warning"
            >
              <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
              {t.cronSettings.needsAuth}
            </div>
          ) : loadError ? (
            <div
              role="alert"
              className="flex flex-col items-start justify-between gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-3 text-sm text-destructive sm:flex-row sm:items-center"
            >
              <span className="flex items-center gap-2">
                <AlertCircleIcon className="size-4 shrink-0" />
                {t.cronSettings.loadFailed}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-full px-2 sm:w-auto"
                onClick={fetchJobs}
              >
                {t.taskBoard.refresh}
              </Button>
            </div>
          ) : (
            jobs.map((job) => (
              <div
                key={job.name}
                className="flex min-w-0 flex-col items-stretch justify-between gap-3 rounded-lg border p-3 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <ClockIcon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 truncate font-medium">
                      {job.name}
                    </span>
                    {job.cron_expression && (
                      <code className="max-w-full truncate rounded bg-muted/60 px-1.5 py-0.5 text-xs text-muted-foreground">
                        {job.cron_expression}
                      </code>
                    )}
                  </div>
                  <div className="mt-1 break-all pl-6 text-xs text-muted-foreground">
                    {job.command}
                  </div>
                  {job.last_status && (
                    <div className="mt-0.5 pl-6 text-xs text-muted-foreground">
                      {t.cronSettings.last}: {statusLabel(job.last_status)}
                    </div>
                  )}
                  {job.last_output && (
                    <div className="mt-0.5 line-clamp-3 break-all pl-6 font-mono text-mini text-muted-foreground/70">
                      {job.last_output}
                    </div>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="self-end sm:self-auto"
                  onClick={() => handleDelete(job.name)}
                  aria-label={t.cronSettings.deleteTask(job.name)}
                >
                  <Trash2Icon className="size-4 text-destructive" />
                </Button>
              </div>
            ))
          )}
          {!loading && !needsAuth && !loadError && jobs.length === 0 && (
            <div className="text-sm text-muted-foreground text-center py-4">
              {t.cronSettings.noTasks}
            </div>
          )}
        </div>

        {showAdd ? (
          <form
            className="mt-3 space-y-4 rounded-lg border p-3 sm:p-4"
            onSubmit={handleAdd}
          >
            <div className="space-y-1.5">
              <Label htmlFor={nameId}>{t.cronSettings.jobName}</Label>
              <Input
                id={nameId}
                autoFocus
                placeholder={t.cronSettings.jobNamePlaceholder}
                value={newName}
                onChange={(e) => {
                  setNewName(e.target.value);
                  if (fieldErrors.name) {
                    setFieldErrors((prev) => ({ ...prev, name: undefined }));
                  }
                }}
                aria-invalid={!!fieldErrors.name}
                aria-describedby={
                  fieldErrors.name ? `${nameId}-error` : undefined
                }
              />
              {fieldErrors.name && (
                <p id={`${nameId}-error`} className="text-xs text-destructive">
                  {fieldErrors.name}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={commandId}>{t.cronSettings.commandToRun}</Label>
              <Input
                id={commandId}
                placeholder={t.cronSettings.commandPlaceholder}
                value={newCommand}
                onChange={(e) => {
                  setNewCommand(e.target.value);
                  if (fieldErrors.command) {
                    setFieldErrors((prev) => ({ ...prev, command: undefined }));
                  }
                }}
                aria-invalid={!!fieldErrors.command}
                aria-describedby={
                  fieldErrors.command ? `${commandId}-error` : undefined
                }
              />
              {fieldErrors.command && (
                <p
                  id={`${commandId}-error`}
                  className="text-xs text-destructive"
                >
                  {fieldErrors.command}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={cronId}>{t.cronSettings.cronExpression}</Label>
              <Input
                id={cronId}
                placeholder={t.cronSettings.cronPlaceholder}
                value={newCron}
                onChange={(e) => {
                  setNewCron(e.target.value);
                  if (fieldErrors.cron) {
                    setFieldErrors((prev) => ({ ...prev, cron: undefined }));
                  }
                }}
                aria-invalid={!!fieldErrors.cron}
                aria-describedby={
                  fieldErrors.cron
                    ? `${cronId}-hint ${cronId}-error`
                    : `${cronId}-hint`
                }
              />
              <p
                id={`${cronId}-hint`}
                className="text-xs leading-5 text-muted-foreground"
              >
                {t.cronSettings.cronHint}
              </p>
              {fieldErrors.cron && (
                <p id={`${cronId}-error`} className="text-xs text-destructive">
                  {fieldErrors.cron}
                </p>
              )}
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <Button type="submit" size="sm" disabled={submitting}>
                {submitting ? (
                  <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
                ) : null}
                {submitting ? t.common.loading : t.cronSettings.create}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                type="button"
                onClick={() => {
                  setShowAdd(false);
                  setFieldErrors({});
                }}
                disabled={submitting}
              >
                {t.cronSettings.cancel}
              </Button>
            </div>
          </form>
        ) : (
          <Button
            variant="outline"
            size="sm"
            className="mt-2"
            disabled={needsAuth}
            onClick={() => setShowAdd(true)}
          >
            <PlusIcon className="size-4 mr-1" /> {t.cronSettings.addTask}
          </Button>
        )}
      </SettingsSection>

      <Dialog
        open={taskToDelete !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && !deleting) setTaskToDelete(null);
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 shadow-xl sm:max-w-[360px]"
        >
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-base">
              {t.cronSettings.deleteConfirmTitle}
            </DialogTitle>
            <DialogDescription className="text-caption leading-5">
              {taskToDelete
                ? t.cronSettings.deleteConfirmDescription(taskToDelete)
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              disabled={deleting}
              onClick={() => setTaskToDelete(null)}
              className="inline-flex h-8 items-center justify-center rounded-md border border-border bg-background px-3 text-caption font-medium text-foreground/80 transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-60"
            >
              {t.common.cancel}
            </button>
            <button
              type="button"
              disabled={deleting}
              onClick={async () => {
                if (!taskToDelete) return;
                const target = taskToDelete;
                const deleted = await doDeleteTask(target);
                if (deleted) setTaskToDelete(null);
              }}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-destructive/25 bg-destructive/[0.07] px-3 text-caption font-medium text-destructive transition-colors hover:border-destructive/35 hover:bg-destructive/[0.11] disabled:pointer-events-none disabled:opacity-60"
            >
              {deleting ? (
                <span className="size-3 animate-spin rounded-full border border-current border-t-transparent" />
              ) : (
                <Trash2Icon className="size-3.5" />
              )}
              {t.common.delete}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
