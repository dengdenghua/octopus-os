import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";

import { ArmsEditor } from "./arms-editor";

interface Props {
  agentId: string;
  agentDisplayName?: string;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initialTab?: "arms" | "skills" | "permissions" | "routing";
}

export function AgentArmsDialog({
  agentId,
  agentDisplayName,
  open,
  onOpenChange,
  initialTab,
}: Props) {
  const { t } = useI18n();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-hidden rounded-sm border-border bg-background p-0 sm:max-w-5xl">
        <div className="relative overflow-hidden border-b border-border bg-card/90 px-5 py-4">
          <div className="pointer-events-none absolute inset-0 opacity-40 [background-image:linear-gradient(90deg,hsl(var(--border)/0.35)_1px,transparent_1px),linear-gradient(180deg,hsl(var(--border)/0.28)_1px,transparent_1px)] [background-size:28px_28px]" />
          <div className="pointer-events-none absolute left-0 top-0 h-5 w-5 border-l border-t border-primary/60" />
          <div className="pointer-events-none absolute bottom-0 right-0 h-5 w-5 border-b border-r border-primary/45" />
          <DialogHeader className="relative">
            <DialogTitle>
              {t.armsDialog.titlePrefix} {agentDisplayName || agentId}
            </DialogTitle>
            <DialogDescription>{t.armsDialog.description}</DialogDescription>
          </DialogHeader>
        </div>
        <div className="relative max-h-[calc(88vh-96px)] overflow-y-auto bg-background px-5 py-4 before:pointer-events-none before:absolute before:inset-0 before:opacity-30 before:[background-image:linear-gradient(90deg,hsl(var(--border)/0.35)_1px,transparent_1px),linear-gradient(180deg,hsl(var(--border)/0.22)_1px,transparent_1px)] before:[background-size:36px_36px]">
          <div className="relative">
            {open && <ArmsEditor agentId={agentId} initialTab={initialTab} />}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
