import { Loader2Icon } from "lucide-react";
import { useEffect, useState } from "react";
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
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

export interface AutomationTemplate {
  id: string;
  title: string;
  description: string;
  tags: string[];
  topic: string;
  cadence: string;
  schedule_time: string;
  schedule_day?: string;
  instructions?: string;
}

interface AutomationCreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  presetTemplate?: AutomationTemplate | null;
  onCreated?: () => void;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export function AutomationCreateDialog({
  open,
  onOpenChange,
  presetTemplate,
  onCreated,
}: AutomationCreateDialogProps) {
  const { t } = useI18n();
  const [displayName, setDisplayName] = useState("");
  const [topic, setTopic] = useState("");
  const [cadence, setCadence] = useState("每天");
  const [scheduleTime, setScheduleTime] = useState("09:00");
  const [scheduleDay, setScheduleDay] = useState("1");
  const [instructions, setInstructions] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const resetForm = () => {
    setDisplayName("");
    setTopic("");
    setCadence("每天");
    setScheduleTime("09:00");
    setScheduleDay("1");
    setInstructions("");
  };

  useEffect(() => {
    if (open) {
      if (presetTemplate) {
        setDisplayName(presetTemplate.title);
        setTopic(presetTemplate.topic);
        if (presetTemplate.cadence.includes("每日")) {
          setCadence("每天");
        } else if (presetTemplate.cadence.includes("每周")) {
          setCadence("每周");
        } else if (presetTemplate.cadence.includes("高频") || presetTemplate.cadence.toLowerCase().includes("hour")) {
          setCadence("每小时");
        } else {
          setCadence("每天");
        }
        setScheduleTime(presetTemplate.schedule_time || "09:00");
        setScheduleDay(presetTemplate.schedule_day || "1");
        setInstructions(presetTemplate.instructions || "");
      } else {
        resetForm();
      }
    }
  }, [open, presetTemplate]);

  const handleSubmit = async () => {
    if (!displayName.trim()) {
      toast.error(t.intelligence.nameRequired);
      return;
    }
    if (!topic.trim()) {
      toast.error(t.intelligence.topicRequired);
      return;
    }

    setSubmitting(true);
    try {
      const keywords = topic
        .split(/[,，]/)
        .map((k) => k.trim())
        .filter((k) => k.length > 0);

      const body: Record<string, unknown> = {
        topic: topic.trim(),
        display_name: displayName.trim(),
        keywords,
        cadence,
        enabled: true,
      };

      if (cadence !== "每小时") {
        body.schedule_time = scheduleTime;
      }
      if (cadence === "每周") {
        body.schedule_day = scheduleDay;
      }
      if (instructions.trim()) {
        body.instructions = instructions.trim();
      }

      await apiFetch("/api/intelligence/subscriptions", {
        method: "POST",
        body: JSON.stringify(body),
      });

      toast.success(t.intelligence.createTaskSuccess);
      onCreated?.();
      onOpenChange(false);
      resetForm();
    } catch {
      toast.error(t.intelligence.createTaskFailed);
    } finally {
      setSubmitting(false);
    }
  };

  const weekdayOptions = [
    { value: "1", label: t.intelligencePanel.weekdayMonday },
    { value: "2", label: t.intelligencePanel.weekdayTuesday },
    { value: "3", label: t.intelligencePanel.weekdayWednesday },
    { value: "4", label: t.intelligencePanel.weekdayThursday },
    { value: "5", label: t.intelligencePanel.weekdayFriday },
    { value: "6", label: t.intelligencePanel.weekdaySaturday },
    { value: "7", label: t.intelligencePanel.weekdaySunday },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 rounded-lg p-6 sm:max-w-[480px]">
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-base">{t.intelligence.createTaskTitle}</DialogTitle>
          <DialogDescription className="text-xs leading-5">
            {t.intelligence.createTaskDescription}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label className="text-xs font-medium text-foreground">
              {t.intelligence.taskNameLabel}
            </Label>
            <Input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t.intelligence.taskNamePlaceholder}
              className="text-sm rounded-md"
            />
          </div>

          <div className="space-y-2">
            <Label className="text-xs font-medium text-foreground">
              {t.intelligence.topicLabel}
            </Label>
            <Textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder={t.intelligence.topicPlaceholder}
              rows={2}
              className="text-sm rounded-md resize-none"
            />
          </div>

          <div className="space-y-2">
            <Label className="text-xs font-medium text-foreground">
              {t.intelligence.cadenceLabel}
            </Label>
            <Select value={cadence} onValueChange={setCadence}>
              <SelectTrigger className="w-full text-sm rounded-md">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="每天">{t.intelligencePanel.cadenceDaily}</SelectItem>
                <SelectItem value="每周">{t.intelligencePanel.cadenceWeekly}</SelectItem>
                <SelectItem value="每小时">{t.intelligence.cadenceHourly}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {cadence !== "每小时" && (
            <div className="space-y-2">
              <Label className="text-xs font-medium text-foreground">
                {t.intelligence.scheduleTimeLabel}
              </Label>
              <Input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="text-sm rounded-md w-full"
              />
            </div>
          )}

          {cadence === "每周" && (
            <div className="space-y-2">
              <Label className="text-xs font-medium text-foreground">
                {t.intelligence.scheduleDayLabel}
              </Label>
              <Select value={scheduleDay} onValueChange={setScheduleDay}>
                <SelectTrigger className="w-full text-sm rounded-md">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {weekdayOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="space-y-2">
            <Label className="text-xs font-medium text-foreground">
              {t.intelligence.instructionsLabel}
            </Label>
            <Textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={t.intelligence.instructionsPlaceholder}
              rows={2}
              className="text-sm rounded-md resize-none"
            />
          </div>
        </div>

        <DialogFooter className="flex flex-row justify-end gap-2 sm:justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-md"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            {t.common.cancel}
          </Button>
          <Button
            type="button"
            variant="default"
            size="sm"
            className="rounded-md"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? (
              <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
            ) : null}
            {t.intelligence.createTask}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
