/**
 * 「编辑侧栏」面板 — 选择哪些模块显示在侧栏。
 *
 * 交互对标钉钉的侧栏编辑面板：按业务分组的网格，可选项右上角 `+` / `✓`
 * 切换，常驻项不提供按钮，右上角「完成」退出。
 *
 * 注意：这里**不下载任何东西**。所有模块都已在包内，页面均为 `lazy()`，
 * 未显示的入口对应 chunk 自然不会被请求。远端加载是另一条更重的路径
 * （见 docs/architecture/blocks.md §2）。
 */
import { CheckIcon, PlusIcon } from "lucide-react";

import {
  Dialog,
  DialogContent,
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
} from "@/core/modules/enabled-modules";
import type { ModuleGroup } from "@/core/modules/types";
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
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t.sidebar.editModulesHint} · {preset.direction}
            </p>
          </div>
          <Button size="sm" onClick={() => onOpenChange(false)}>
            {t.sidebar.editModulesDone}
          </Button>
        </DialogHeader>

        <div className="max-h-[calc(80vh-4.5rem)] overflow-y-auto px-5 py-4">
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
                name={label(m.labelKey)}
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
  name,
  enabled,
  removable,
  pinnedLabel,
  onToggle,
}: {
  name: string;
  enabled: boolean;
  removable: boolean;
  pinnedLabel: string;
  onToggle: () => void;
}) {
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
      aria-pressed={enabled}
      className={cn(
        "flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-left",
        "transition-[background-color,border-color] duration-fast",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50",
        enabled
          ? "border-primary/30 bg-primary/8 hover:bg-primary/12"
          : "border-border-subtle bg-card hover:border-border-default hover:bg-muted/45",
      )}
    >
      <span className="min-w-0 truncate text-sm">{name}</span>
      <span
        aria-hidden="true"
        className={cn(
          "ml-2 flex size-5 shrink-0 items-center justify-center rounded-full border",
          enabled
            ? "border-primary/40 bg-primary text-primary-foreground"
            : "border-border-default text-muted-foreground",
        )}
      >
        {enabled ? (
          <CheckIcon className="size-3" />
        ) : (
          <PlusIcon className="size-3" />
        )}
      </span>
    </button>
  );
}
