import { useEffect, useMemo, useState } from "react";
import { ClipboardListIcon, Loader2Icon } from "lucide-react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useCreateTeamTask } from "@/core/team-tasks";
import type { TaskAssignee } from "@/core/team-tasks";
import type { Team } from "@/core/teams";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface MetaSkillSummary {
  name: string;
  description?: string;
  display_name?: string;
  step_count?: number;
}

interface CreateTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roomId: string | null | undefined;
  team: Team | null;
}

const FREEFORM_TEMPLATE = "__freeform__";

export function CreateTaskDialog({
  open,
  onOpenChange,
  roomId,
  team,
}: CreateTaskDialogProps) {
  const { t } = useI18n();
  const createTask = useCreateTeamTask();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [sopTemplate, setSopTemplate] = useState(FREEFORM_TEMPLATE);
  const [selectedAssignees, setSelectedAssignees] = useState<string[]>([]);
  const [packs, setPacks] = useState<MetaSkillSummary[]>([]);
  const [packsLoading, setPacksLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPacksLoading(true);
    fetch(`${getBackendBaseURL()}/api/meta-skills`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`meta-skills ${res.status}`);
        return (await res.json()) as { packs?: MetaSkillSummary[] };
      })
      .then((data) => {
        if (!cancelled) setPacks(data.packs ?? []);
      })
      .catch(() => {
        if (!cancelled) setPacks([]);
      })
      .finally(() => {
        if (!cancelled) setPacksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const agentOptions = useMemo(
    () =>
      (team?.members ?? []).map((member) => ({
        ref: member.name,
        label: member.display_name ?? member.name,
        description: member.description,
        icon: member.icon,
      })),
    [team?.members],
  );

  const canSubmit =
    Boolean(roomId) && title.trim().length > 0 && !createTask.isPending;

  const toggleAssignee = (ref: string) => {
    setSelectedAssignees((prev) =>
      prev.includes(ref) ? prev.filter((item) => item !== ref) : [...prev, ref],
    );
  };

  const reset = () => {
    setTitle("");
    setDescription("");
    setSopTemplate(FREEFORM_TEMPLATE);
    setSelectedAssignees([]);
  };

  const handleSubmit = async () => {
    if (!roomId || !canSubmit) return;
    const assignees: TaskAssignee[] = selectedAssignees.map((ref) => ({
      kind: "agent",
      ref,
    }));
    try {
      await createTask.mutateAsync({
        room_id: roomId,
        title: title.trim(),
        description: description.trim(),
        sop_template: sopTemplate === FREEFORM_TEMPLATE ? "" : sopTemplate,
        assignees,
      });
      toast.success(t.collab.createTask.toastCreated);
      reset();
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t.collab.createTask.toastFailed);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent className="overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="border-b border-border-default px-5 py-4">
          <DialogTitle className="flex items-center gap-2">
            <ClipboardListIcon className="size-5" />
            {t.collab.createTask.title}
          </DialogTitle>
          <DialogDescription>
            {t.collab.createTask.description}
          </DialogDescription>
        </DialogHeader>

        <div className="grid max-h-[66vh] gap-4 overflow-y-auto px-5 py-4">
          <div className="grid gap-2">
            <Label htmlFor="team-task-title">
              {t.collab.createTask.taskTitleLabel}
            </Label>
            <Input
              id="team-task-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={t.collab.createTask.titlePlaceholder}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="team-task-description">
              {t.collab.createTask.descriptionLabel}
            </Label>
            <Textarea
              id="team-task-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder={t.collab.createTask.descriptionPlaceholder}
              className="min-h-24 resize-none"
            />
          </div>

          <div className="grid gap-2">
            <Label>{t.collab.createTask.sopLabel}</Label>
            <Select value={sopTemplate} onValueChange={setSopTemplate}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={FREEFORM_TEMPLATE}>
                  {t.collab.createTask.autoMatchFreeform}
                </SelectItem>
                {packs.map((pack) => (
                  <SelectItem key={pack.name} value={pack.name}>
                    {pack.name}
                    {pack.step_count ? ` · ${pack.step_count} 步` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {packsLoading && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2Icon className="size-3 animate-spin" />
                {t.collab.createTask.loadingPacks}
              </span>
            )}
          </div>

          {agentOptions.length > 0 && (
            <div className="grid gap-2">
              <Label>{t.collab.createTask.assigneeLabel}</Label>
              <div className="grid grid-cols-2 gap-2">
                {agentOptions.map((agent) => {
                  const selected = selectedAssignees.includes(agent.ref);
                  return (
                    <button
                      key={agent.ref}
                      type="button"
                      onClick={() => toggleAssignee(agent.ref)}
                      className={cn(
                        "flex min-w-0 items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors",
                        selected
                          ? "border-primary/40 bg-primary/10 text-foreground"
                          : "border-border-default bg-muted/10 hover:bg-muted/40",
                      )}
                    >
                      <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-border-default bg-background text-sm">
                        {agent.icon?.trim() || agent.label.charAt(0)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">
                          {agent.label}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {agent.ref}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="border-t border-border-default px-5 py-3">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t.collab.createTask.cancel}
          </Button>
          <Button onClick={() => void handleSubmit()} disabled={!canSubmit}>
            {createTask.isPending && (
              <Loader2Icon className="mr-2 size-4 animate-spin" />
            )}
            {t.collab.createTask.create}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
