import {
  ChevronDownIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
} from "lucide-react";

import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import type { PermissionMode } from "@/core/permissions";
import { useI18n } from "@/core/i18n/hooks";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const PERMISSION_OPTIONS: PermissionMode[] = [
  "default",
  "acceptEdits",
  "bypassPermissions",
];

interface PermissionIndicatorProps {
  mode: PermissionMode;
  onModeChange: (mode: PermissionMode) => void;
  className?: string;
  compact?: boolean;
}

const PERMISSION_TRIGGER_TONE =
  "border-transparent bg-transparent text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground";

export function PermissionIndicator({
  mode,
  onModeChange,
  className,
  compact = false,
}: PermissionIndicatorProps) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const labels: Record<PermissionMode, { label: string; description: string }> =
    {
      default: {
        label: t.chatInputBox.permissionModeDefault,
        description: t.chatInputBox.permissionModeDefaultDesc,
      },
      acceptEdits: {
        label: t.chatInputBox.permissionModeAcceptEdits,
        description: t.chatInputBox.permissionModeAcceptEditsDesc,
      },
      bypassPermissions: {
        label: t.chatInputBox.permissionModeBypass,
        description: t.chatInputBox.permissionModeBypassDesc,
      },
      plan: {
        label: t.chatInputBox.permissionModePlan,
        description: t.chatInputBox.permissionModePlanDesc,
      },
    };
  const current = labels[mode] ?? labels.default;
  const isBypassMode = mode === "bypassPermissions";

  const handleModeChange = async (value: string) => {
    const nextMode = value as PermissionMode;
    if (nextMode === mode) return;
    if (nextMode === "bypassPermissions") {
      const accepted = await confirm({
        title: t.chatInputBox.permissionModeBypassConfirmTitle,
        description: t.chatInputBox.permissionModeBypassConfirmDesc,
        confirmLabel: t.chatInputBox.permissionModeBypassConfirmAction,
        destructive: false,
      });
      if (!accepted) return;
    }
    onModeChange(nextMode);
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            data-testid="permission-mode-trigger"
            className={cn(
              "flex items-center gap-1.5 text-xs font-medium transition-colors duration-base",
              isBypassMode
                ? "h-8 rounded-lg px-1.5 text-muted-foreground hover:bg-muted/55 hover:text-foreground"
                : compact
                  ? "h-8 rounded-lg px-1.5 text-muted-foreground hover:bg-muted/55 hover:text-foreground"
                  : cn("h-8 rounded-lg px-2.5", PERMISSION_TRIGGER_TONE),
              className,
            )}
            title={`${t.chatInputBox.permissionModeLabel}: ${current.description}`}
            aria-label={`${t.chatInputBox.permissionModeLabel}: ${current.label}`}
          >
            {isBypassMode ? (
              <ShieldAlertIcon className="size-3.5 text-warning" />
            ) : (
              <ShieldCheckIcon className="size-3 opacity-75" />
            )}
            <span>{current.label}</span>
            <ChevronDownIcon className="size-3 opacity-35" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          data-testid="permission-mode-menu"
          side="top"
          align="start"
          className="w-64"
        >
          <DropdownMenuLabel className="text-xs text-muted-foreground">
            {t.chatInputBox.permissionModeLabel}
          </DropdownMenuLabel>
          <DropdownMenuRadioGroup value={mode} onValueChange={handleModeChange}>
            {PERMISSION_OPTIONS.map((option) => {
              const item = labels[option];
              return (
                <DropdownMenuRadioItem
                  key={option}
                  data-testid={`permission-mode-option-${option}`}
                  value={option}
                  className={cn(
                    "items-start py-2 text-left",
                    option === "bypassPermissions" &&
                      "text-warning focus:bg-warning/10 focus:text-warning dark:focus:text-warning",
                  )}
                  aria-label={`${item.label}: ${item.description}`}
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-xs font-medium leading-5">
                      {option === "bypassPermissions" && (
                        <ShieldAlertIcon className="size-3.5" />
                      )}
                      {item.label}
                    </span>
                    <span className="block text-xs leading-4 text-muted-foreground">
                      {item.description}
                    </span>
                  </span>
                </DropdownMenuRadioItem>
              );
            })}
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      {confirmDialog}
    </>
  );
}
