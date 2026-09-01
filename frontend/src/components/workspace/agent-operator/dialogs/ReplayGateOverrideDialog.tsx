import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { GateStat } from "../../replay-panel";
import type { ReplayGateOverridePrompt } from "../shared";
import { useOperatorCopy } from "../use-operator-copy";

export function ReplayGateOverrideDialog({
  prompt,
  reason,
  busy,
  onCancel,
  onReasonChange,
  onConfirm,
}: {
  prompt: ReplayGateOverridePrompt | null;
  reason: string;
  busy: boolean;
  onCancel: () => void;
  onReasonChange: (value: string) => void;
  onConfirm: () => void;
}) {
  const to = useOperatorCopy();
  const gate = prompt?.gate ?? null;
  return (
    <Dialog open={!!prompt} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{to("Replay gate blocked apply")}</DialogTitle>
          <DialogDescription>
            {to(
              "Promotion was stopped because replay gate did not pass. Override only when you have reviewed the failing cases.",
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2">
          <div className="text-sm font-medium text-destructive">
            {prompt?.message ?? to("Replay gate did not pass")}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {gate?.reason || to("No reason provided")}
          </div>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-center font-mono text-xs">
            <GateStat label={to("cases")} value={gate?.summary.total ?? 0} />
            <GateStat label={to("pass")} value={gate?.summary.passed ?? 0} />
            <GateStat label={to("fail")} value={gate?.summary.failed ?? 0} />
            <GateStat
              label={to("low")}
              value={gate?.summary.below_min_score ?? 0}
            />
          </div>
        </div>
        <Textarea
          aria-label={to("Override reason")}
          value={reason}
          onChange={(event) => onReasonChange(event.target.value)}
          placeholder={to(
            "Record why this replay gate override is acceptable.",
          )}
          className="min-h-24 resize-none text-sm"
        />
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            {to("Cancel")}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={busy || !reason.trim()}
          >
            {to("Override gate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
