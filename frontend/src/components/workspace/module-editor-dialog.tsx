/** Sidebar placement uses the application catalog installation state. */
import { requestOpenEchoHub } from "@/core/apps/app-presentation";
import { CheckIcon, PlusIcon } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  MODULE_CATALOG,
  MODULE_GROUP_LABEL_KEYS,
  MODULE_GROUP_ORDER,
} from "@/core/modules/catalog";
import {
  setModuleEnabled,
  useEnabledModuleIds,
  useModuleAvailability,
} from "@/core/modules/enabled-modules";
import type { ModuleGroup } from "@/core/modules/types";
import { WORKBENCH_BUILTIN_APPS } from "@/core/workbench/apps";
import { cn } from "@/lib/utils";
import { useActiveAgentId } from "@/core/agents/active";
import { DEFAULT_PRIMARY_AGENT_ID } from "@/core/agents/persona-policy";
import { workspacePresetForAgent } from "@/core/workspace/workspace-presets";

export function ModuleEditorDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const activeAgentId = useActiveAgentId() ?? DEFAULT_PRIMARY_AGENT_ID;
  const preset = workspacePresetForAgent(activeAgentId);
  const enabledIds = useEnabledModuleIds(activeAgentId);
  const enabled = new Set(enabledIds);

  const label = (key: string) =>
    (t.sidebar as unknown as Record<string, string>)[key] ?? key;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="flex-row items-center justify-between space-y-0 border-b border-border-subtle px-5 py-3">
          <div className="min-w-0">
            <DialogTitle className="text-base">
              {t.sidebar.editModules}
            </DialogTitle>
            <DialogDescription className="mt-0.5 text-xs">
              {t.sidebar.editModulesHint} · {preset.direction}
            </DialogDescription>
          </div>
          <Button size="sm" onClick={() => onOpenChange(false)}>
            {t.sidebar.editModulesDone}
          </Button>
        </DialogHeader>

        <div className="max-h-[calc(80vh-4.5rem)] overflow-y-auto px-5 py-4">
          <p className="mb-4 text-xs text-muted-foreground">
            应用安装状态在桌面、独立窗口和工作台之间统一；侧栏固定按当前账号与角色保存。
            <button
              type="button"
              className="ml-2 text-primary hover:underline"
              onClick={() => {
                onOpenChange(false);
                requestOpenEchoHub();
              }}
            >
              前往应用中心安装或启用
            </button>
          </p>
          {MODULE_GROUP_ORDER.map((group) => (
            <ModuleGroupSection
              key={group}
              group={group}
              enabled={enabled}
              activeAgentId={activeAgentId}
              label={label}
              pinnedLabel={t.sidebar.modulePinned}
            />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ModuleGroupSection({
  group,
  enabled,
  activeAgentId,
  label,
  pinnedLabel,
}: {
  group: ModuleGroup;
  enabled: Set<string>;
  activeAgentId: string;
  label: (key: string) => string;
  pinnedLabel: string;
}) {
  const modules = MODULE_CATALOG.filter((m) => m.group === group);
  if (modules.length === 0) return null;

  return (
    <section className="mb-5 last:mb-0">
      <h3 className="mb-2 text-xs font-medium text-muted-foreground">
        {label(MODULE_GROUP_LABEL_KEYS[group])}
      </h3>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {modules.map((m) => {
          const isOn = enabled.has(m.id);
          return (
            <li key={m.id}>
              <ModuleCard
                moduleId={m.id}
                name={
                  WORKBENCH_BUILTIN_APPS.find((app) => app.moduleId === m.id)
                    ?.name ?? label(m.labelKey)
                }
                enabled={isOn}
                removable={m.removable}
                pinnedLabel={pinnedLabel}
                onToggle={() => setModuleEnabled(m.id, !isOn, activeAgentId)}
              />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ModuleCard({
  moduleId,
  name,
  enabled,
  removable,
  pinnedLabel,
  onToggle,
}: {
  moduleId: string;
  name: string;
  enabled: boolean;
  removable: boolean;
  pinnedLabel: string;
  onToggle: () => void;
}) {
  const availability = useModuleAvailability(moduleId);
  const remote = WORKBENCH_BUILTIN_APPS.some(
    (app) => app.moduleId === moduleId && app.delivery === "remote",
  );
  const unavailable = remote && availability !== true;
  // Pinned modules render as a plain, non-interactive row — the DingTalk
  // equivalent of the cards with no `+` badge.
  if (!removable) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-border-subtle bg-muted/40 px-3 py-2.5">
        <span className="min-w-0 truncate text-sm">{name}</span>
        <span className="shrink-0 text-micro text-muted-foreground">
          {pinnedLabel}
        </span>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={unavailable}
      title={unavailable ? "请先在应用中心安装并启用，再添加到侧栏" : undefined}
      aria-pressed={!unavailable && enabled}
      className={cn(
        "flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-left",
        "transition-[background-color,border-color] duration-fast",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50",
        enabled && !unavailable
          ? "border-primary/30 bg-primary/8 hover:bg-primary/12"
          : "border-border-subtle bg-card hover:border-border-default hover:bg-muted/45",
      )}
    >
      <span className="min-w-0 truncate text-sm">{name}</span>
      <span
        aria-hidden="true"
        className={cn(
          "ml-2 flex shrink-0 items-center justify-center",
          unavailable ? "text-muted-foreground" : "size-5 rounded-full border",
          enabled && !unavailable
            ? "border-primary/40 bg-primary text-primary-foreground"
            : "border-border-default text-muted-foreground",
        )}
      >
        {unavailable ? (
          <span className="whitespace-nowrap text-[10px]">需先安装或启用</span>
        ) : enabled ? (
          <CheckIcon className="size-3" />
        ) : (
          <PlusIcon className="size-3" />
        )}
      </span>
    </button>
  );
}
