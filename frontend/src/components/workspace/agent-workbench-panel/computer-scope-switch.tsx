import { useI18n } from "@/core/i18n/hooks";

export function ComputerScopeSwitch({
  subLabel,
  onOpenMain,
}: {
  subLabel: string;
  onOpenMain: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="border-b border-border-subtle px-3 pt-2">
      <div className="flex min-w-0 items-center gap-4 text-xs font-medium">
        <span className="min-w-0 truncate border-b-2 border-foreground pb-2 text-foreground">
          {subLabel}
        </span>
        <button
          type="button"
          onClick={onOpenMain}
          className="min-w-0 truncate border-b-2 border-transparent pb-2 text-muted-foreground transition-colors hover:border-border hover:text-foreground"
          title={t.agentWorkbenchPanel.switchToMainComputer}
        >
          {t.agentWorkbenchPanel.mainComputer}
        </button>
      </div>
    </div>
  );
}
