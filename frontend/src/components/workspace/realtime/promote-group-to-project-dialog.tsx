import { useEffect, useState } from "react";
import { FolderKanbanIcon, Loader2Icon } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import { type Project, usePromoteGroupToProject } from "@/core/projects/hooks";
import { isIMEComposing } from "@/lib/ime";

export interface PromoteGroupToProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadId: string;
  defaultName?: string;
  onPromoted?: (project: Project) => void | Promise<void>;
}

export function PromoteGroupToProjectDialog({
  open,
  onOpenChange,
  threadId,
  defaultName = "",
  onPromoted,
}: PromoteGroupToProjectDialogProps) {
  const { t } = useI18n();
  const copy = t.promoteProjectDialog;
  const promote = usePromoteGroupToProject();
  const [name, setName] = useState(defaultName);
  const [goal, setGoal] = useState("");

  useEffect(() => {
    if (!open) return;
    setName(defaultName.trim());
    setGoal("");
  }, [defaultName, open, threadId]);

  const canSubmit =
    Boolean(threadId.trim() && name.trim() && goal.trim()) &&
    !promote.isPending;

  const handleSubmit = () => {
    if (!canSubmit) return;
    promote.mutate(
      {
        threadId,
        name: name.trim(),
        goal: goal.trim(),
      },
      {
        onSuccess: async ({ project }) => {
          toast.success(copy.success);
          onOpenChange(false);
          await onPromoted?.(project);
        },
        onError: () => toast.error(copy.failed),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <span className="mb-1 grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
            <FolderKanbanIcon className="size-4.5" aria-hidden="true" />
          </span>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-1">
          <div className="space-y-2">
            <Label htmlFor="promote-group-project-name">{copy.nameLabel}</Label>
            <Input
              id="promote-group-project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={copy.namePlaceholder}
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="promote-group-project-goal">{copy.goalLabel}</Label>
            <Textarea
              id="promote-group-project-goal"
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder={copy.goalPlaceholder}
              className="min-h-24 resize-none"
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  (event.metaKey || event.ctrlKey) &&
                  !isIMEComposing(event)
                ) {
                  event.preventDefault();
                  handleSubmit();
                }
              }}
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={promote.isPending}
          >
            {copy.cancel}
          </Button>
          <Button type="button" onClick={handleSubmit} disabled={!canSubmit}>
            {promote.isPending ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : null}
            {promote.isPending ? copy.submitting : copy.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
