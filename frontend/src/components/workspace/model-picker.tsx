import { ChevronDownIcon, PlusIcon, SparklesIcon } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import { DEFAULT_CONTEXT_WINDOW_TOKENS } from "@/core/models/context-window";
import type { ReasoningEffort } from "@/core/threads";
import { cn } from "@/lib/utils";

/** Minimal slice of the backend model shape used by the picker. */
export interface PickerModel {
  id?: string | null;
  name: string;
  display_name?: string | null;
  source_display_name?: string | null;
  description?: string | null;
  entry_id?: string | null;
  selection_id?: string | null;
  model?: string | null;
  supports_thinking?: boolean;
  supports_vision?: boolean;
  supports_tool_use?: boolean;
  supports_reasoning_effort?: boolean;
  /** UI effort tiers this model genuinely accepts. undefined/null = full
   *  default set; [] = no meaningful effort control (picker hides it). */
  reasoning_efforts?: ReasoningEffort[] | null;
  context_window?: number | null;
  context_profile?: string | null;
  [key: string]: unknown;
}

function selectionValue(model: PickerModel): string {
  return model.selection_id || model.entry_id || model.name;
}

function isOpenCodeZenFreeModel(model: PickerModel | undefined): boolean {
  return model?.entry_id === "opencode-zen";
}

function deduplicatePickerModels(models: PickerModel[]): PickerModel[] {
  const seen = new Set<string>();
  return models.filter((model) => {
    if (model.context_profile === "1m") return false;
    const key = selectionValue(model);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function longContextSelectionValue(model: PickerModel): string {
  // Older catalogs have no selection_id; their routable 1M alias is the
  // ``variant::1m`` name, not the entry id (which means the default profile).
  return model.selection_id || model.name;
}

function modelFamilyKey(model: PickerModel): string {
  if (model.entry_id && model.model) {
    return `${model.entry_id}\u0000${model.model}`;
  }
  return model.name.replace(/::1m$/, "");
}

function modelMatchesValue(
  model: PickerModel,
  value: string | null | undefined,
): boolean {
  if (!value) return false;
  return [
    model.selection_id,
    model.entry_id,
    model.name,
    model.model,
    model.id,
  ].includes(value);
}

function contextSelectionValue(model: PickerModel): string {
  return model.context_profile === "1m"
    ? longContextSelectionValue(model)
    : selectionValue(model);
}

function contextWindowTokens(model: PickerModel): number {
  const explicit = Number(model.context_window);
  if (Number.isFinite(explicit) && explicit > 0) return Math.floor(explicit);
  return model.context_profile === "1m"
    ? 1_000_000
    : DEFAULT_CONTEXT_WINDOW_TOKENS;
}

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return `${Number.isInteger(millions) ? millions : millions.toFixed(1)}M`;
  }
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return tokens.toLocaleString();
}

export function ModelContextSetting({
  models,
  selected,
  value,
  disabled,
  onChange,
  className,
}: {
  models: PickerModel[];
  selected: PickerModel | undefined;
  value?: string | null;
  disabled?: boolean;
  onChange: (value: string) => void;
  className?: string;
}) {
  const { t } = useI18n();
  const options = useMemo(() => {
    if (!selected) return [];
    const family = modelFamilyKey(selected);
    const seen = new Set<string>();
    return models
      .filter((model) => modelFamilyKey(model) === family)
      .filter((model) => {
        const key = contextSelectionValue(model);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort(
        (left, right) => contextWindowTokens(left) - contextWindowTokens(right),
      );
  }, [models, selected]);

  // This is a quick *choice*, not a model-spec readout. Keep the existing
  // picker unchanged for models that only expose one context window.
  if (!selected || options.length < 2) return null;

  const current =
    options.find((model) => modelMatchesValue(model, value)) ??
    options.find(
      (model) => model.context_profile === selected.context_profile,
    ) ??
    options[0]!;
  const currentTokens = contextWindowTokens(current);
  return (
    <section
      data-testid="model-context-setting"
      className={cn("space-y-1", className)}
    >
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-muted-foreground/80">
          {t.modelPicker.contextLength}
        </span>
        <span className="tabular-nums text-foreground/80">
          {formatContextWindow(currentTokens)}
        </span>
      </div>
      <div
        role="radiogroup"
        aria-label={t.modelPicker.contextLength}
        className="grid gap-0.5 rounded-md bg-muted/40 p-0.5"
        style={{
          gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))`,
        }}
      >
        {options.map((model) => {
          const optionValue = contextSelectionValue(model);
          const optionTokens = contextWindowTokens(model);
          const optionExpanded = model.context_profile === "1m";
          const optionSelected = model === current;
          const profileLabel = optionExpanded
            ? t.modelPicker.contextMax
            : t.modelPicker.contextStandard;
          const formatted = formatContextWindow(optionTokens);
          return (
            <button
              key={optionValue}
              type="button"
              role="radio"
              aria-label={`${profileLabel} · ${formatted}`}
              aria-checked={optionSelected}
              disabled={disabled}
              onClick={(event) => {
                event.preventDefault();
                if (!optionSelected) onChange(optionValue);
              }}
              className={cn(
                "flex h-7 min-w-0 items-center justify-center gap-1 rounded px-1.5 text-xs transition-colors",
                optionSelected
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "text-muted-foreground hover:text-foreground",
                "disabled:cursor-not-allowed disabled:opacity-45",
              )}
            >
              <span className="truncate">{profileLabel}</span>
              <span className="shrink-0 tabular-nums text-[11px] opacity-70">
                {formatted}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

interface OfficialMeta {
  key: string;
  displayName: string;
  id: string;
  multiplier: string;
  recommended: boolean;
}

/**
 * Echo Mix — the built-in mixture-of-agents virtual model. Surfaces in the
 * Official tab when the backend advertises ``echo-mix`` via /api/llm-models.
 */
const MIX_META: OfficialMeta = {
  key: "echo-mix",
  id: "echo-mix",
  displayName: "mix",
  multiplier: "Mix",
  recommended: true,
};

const REASONING_EFFORT_OPTIONS: ReasoningEffort[] = [
  "off",
  "low",
  "medium",
  "high",
  "xhigh",
];

/** Rough strength scale used to map an unsupported effort onto the nearest
 *  tier a provider genuinely accepts (the wire value may differ). */
const EFFORT_STRENGTH: Record<ReasoningEffort, number> = {
  off: 0,
  minimal: 1,
  low: 2,
  medium: 3,
  high: 4,
  xhigh: 5,
  max: 6,
};

function resolveEffectiveEffort(
  current: ReasoningEffort,
  offered: ReasoningEffort[],
): ReasoningEffort {
  if (offered.includes(current)) return current;
  const base = EFFORT_STRENGTH[current] ?? 0;
  // Prefer the smallest offered tier at or above the selection (the backend
  // promotes below-high efforts for DeepSeek-style providers); otherwise the
  // largest offered tier.
  let candidate: ReasoningEffort | undefined;
  for (const tier of offered) {
    if ((EFFORT_STRENGTH[tier] ?? 0) >= base) {
      candidate = tier;
      break;
    }
  }
  if (candidate) return candidate;
  return offered[offered.length - 1] ?? "high";
}

function reasoningEffortLabel(
  effort: ReasoningEffort,
  t: Translations,
): string {
  switch (effort) {
    case "off":
      return t.inputBox.reasoningEffortOff;
    case "minimal":
      return t.inputBox.reasoningEffortMinimal;
    case "low":
      return t.inputBox.reasoningEffortLow;
    case "medium":
      return t.inputBox.reasoningEffortMedium;
    case "high":
      return t.inputBox.reasoningEffortHigh;
    case "xhigh":
      return t.inputBox.reasoningEffortXHigh;
    case "max":
      return t.inputBox.reasoningEffortMax;
  }
}

function ReasoningEffortSetting({
  value,
  disabled,
  efforts,
  onChange,
}: {
  value?: ReasoningEffort;
  disabled?: boolean;
  efforts?: ReasoningEffort[] | null;
  onChange: (effort: ReasoningEffort) => void;
}) {
  const { t } = useI18n();
  const rawCurrent = value === "max" ? "xhigh" : (value ?? "medium");
  // An explicitly empty set means this model has no meaningful effort control
  // (adaptive / unsupported thinking) — hide it rather than show fake tiers.
  if (efforts && efforts.length === 0) return null;
  const offered =
    efforts && efforts.length > 0 ? efforts : REASONING_EFFORT_OPTIONS;
  const effective = resolveEffectiveEffort(rawCurrent, offered);
  const mapped = effective !== rawCurrent;
  const title = t.inputBox.reasoningEffort;

  return (
    <div className="mx-1 mt-1 border-t border-border-default pt-1">
      <div className="mb-0.5 flex items-center justify-between px-1">
        <span className="text-xs font-medium text-muted-foreground/70">
          {title}
        </span>
        <span className="text-xs text-muted-foreground">
          {t.inputBox.reasoningEffortCurrent(
            reasoningEffortLabel(effective, t),
          )}
        </span>
      </div>
      {mapped && (
        <div className="mb-1 px-1 text-[11px] text-muted-foreground/70">
          {t.inputBox.reasoningEffortMapped(
            reasoningEffortLabel(rawCurrent, t),
            reasoningEffortLabel(effective, t),
          )}
        </div>
      )}
      <div
        role="radiogroup"
        aria-label={title}
        className="grid gap-0.5 rounded-md bg-muted/35 p-0.5"
        style={{
          gridTemplateColumns: `repeat(${offered.length}, minmax(0, 1fr))`,
        }}
      >
        {offered.map((effort) => {
          const selected = effort === effective;
          return (
            <button
              key={effort}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={(event) => {
                event.preventDefault();
                onChange(effort);
              }}
              className={cn(
                "h-5 rounded-md px-1 text-xs transition-colors",
                selected
                  ? "bg-background text-foreground shadow-[var(--shadow-xs)]"
                  : "text-muted-foreground hover:text-foreground",
                "disabled:cursor-not-allowed disabled:opacity-45",
              )}
            >
              {reasoningEffortLabel(effort, t)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Single row in the list. The primary action and any trailing action are
 * sibling buttons so each option remains valid, independently focusable HTML.
 * `right` is reserved for passive content that belongs to the primary action.
 */
function PickerRow({
  label,
  right,
  trailingAction,
  badge,
  selected,
  disabled,
  onSelect,
}: {
  label: ReactNode;
  right?: ReactNode;
  trailingAction?: ReactNode;
  badge?: ReactNode;
  selected?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      className={cn(
        // Match sidebar NavRow language: h-8, opacity-based emphasis,
        // monochrome. No color accent — selection reads via opacity and
        // a 2px leading bar the way active nav items do.
        "group/row relative flex h-7 w-full items-stretch rounded-md text-xs opacity-75 transition-[opacity,background-color]",
        "hover:bg-muted/40 hover:opacity-100 focus-within:bg-muted/40 focus-within:opacity-100",
        disabled &&
          "cursor-not-allowed opacity-35 hover:opacity-35 hover:bg-transparent",
        selected &&
          !disabled &&
          "opacity-100 bg-[color:color-mix(in_oklch,var(--sidebar-accent)_55%,transparent)] before:absolute before:left-0 before:top-1 before:bottom-1 before:w-[2px] before:rounded-r before:bg-primary/70",
      )}
    >
      <button
        type="button"
        disabled={disabled}
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 text-left",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset",
          trailingAction && "rounded-r-none pr-1",
          disabled && "cursor-not-allowed",
        )}
      >
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate">
          <span className="truncate">{label}</span>
          {badge}
        </span>
        {right !== undefined && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground/70 transition-colors group-hover/row:text-muted-foreground">
            {right}
          </span>
        )}
      </button>
      {trailingAction}
    </div>
  );
}

export interface ModelPickerProps {
  models: PickerModel[];
  value?: string | null;
  onChange: (name: string) => void;
  reasoningEffort?: ReasoningEffort;
  reasoningEffortDisabled?: boolean;
  onReasoningEffortChange?: (effort: ReasoningEffort) => void;
  /** Render prop for a custom trigger. */
  renderTrigger?: (selected: PickerModel | undefined) => ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function ModelPicker({
  models,
  value,
  onChange,
  reasoningEffort,
  reasoningEffortDisabled,
  onReasoningEffortChange,
  renderTrigger,
  open: controlledOpen,
  onOpenChange,
}: ModelPickerProps) {
  const { t } = useI18n();
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = onOpenChange ?? setUncontrolledOpen;

  // "auto" is a UI-only sentinel — the backend reads it as "let the
  // ModelRouter middleware pick per-task". We resolve it to a synthetic
  // PickerModel so the trigger + highlighted row can render a label.
  const isAutoMode = (value ?? "").trim().toLowerCase() === "auto";
  const selected = useMemo(() => {
    if (isAutoMode) {
      return {
        name: "auto",
        display_name: t.modelPicker.autoModelLabel,
        description: t.modelPicker.autoModelDescription,
      };
    }
    if (value) {
      const matched = models.find(
        (m) =>
          m.name === value ||
          m.model === value ||
          ("id" in m && m.id === value) ||
          m.entry_id === value ||
          m.selection_id === value,
      );
      // A stored selection the current catalog no longer advertises (a
      // removed/renamed custom model, or the list still loading) must stay
      // visible as-is — silently snapping to the first row (Mix) would make
      // the picker lie about what the thread actually uses after a reload.
      return (
        matched ?? {
          name: value,
          display_name: value,
          unavailable: true,
        }
      );
    }
    return models[0];
  }, [
    isAutoMode,
    value,
    models,
    t.modelPicker.autoModelLabel,
    t.modelPicker.autoModelDescription,
  ]);

  const officialMetas = useMemo(() => {
    // Surface the built-in Mix model in the Official tab only when the
    // backend actually advertises it (via /api/llm-models).
    const hasMix = models.some(
      (m) => m.name === MIX_META.id || m.model === MIX_META.id,
    );
    return hasMix ? [MIX_META] : [];
  }, [models]);

  const selectedMeta = useMemo(() => {
    if (!selected) return null;
    return (
      officialMetas.find(
        (meta) =>
          meta.id === selected.name ||
          meta.id === selected.model ||
          meta.displayName === selected.display_name,
      ) ?? null
    );
  }, [officialMetas, selected]);

  /**
   * One flat list, in the order the backend returned.
   *
   * The dropdown used to split Official / Custom across tabs. With a handful
   * of configured endpoints that cost two clicks to reach a neighbouring
   * model and hid the selected row behind whichever tab opened by default.
   * Unconfigured official rows are dropped here rather than rendered grey —
   * they are a settings concern, and a column of unclickable placeholders is
   * the bulk of what made this panel feel heavy.
   *
   * ``::1m`` variants are folded into the selected model's context-length
   * control, so one model still occupies one line without hiding Max mode.
   */
  const flatEntries = useMemo(() => deduplicatePickerModels(models), [models]);

  const handleSelect = (name: string) => {
    onChange(name);
    setOpen(false);
  };

  // Clean up model name: remove trailing question marks and whitespace
  const cleanModelName = (name: string | undefined | null): string => {
    if (!name) return "";
    return name.replace(/\?+$/, "").trim();
  };

  // The trigger keeps the Auto sparkles + label + chevron (the
  // model-name + multiplier header and the in-list Auto row were the
  // red-box duplicates that got removed). The Auto toggle now lives
  // exclusively on the Auto row at the top of the dropdown panel
  // — one control, one backing state, two visible surfaces.
  const selectedDisplayLabel = isAutoMode
    ? t.modelPicker.autoModelLabel
    : cleanModelName(selectedMeta?.displayName) ||
      cleanModelName(selected?.display_name) ||
      cleanModelName(selected?.name) ||
      t.modelPicker.selectModel;

  const triggerButton = (
    <button
      type="button"
      data-testid="model-picker-trigger"
      className={cn(
        "inline-flex h-8 min-w-0 items-center gap-1 rounded-lg border border-transparent",
        "bg-transparent px-2 py-1 text-xs text-muted-foreground transition outline-none",
        "hover:border-border-default hover:bg-muted/60 hover:text-foreground",
        "data-[state=open]:bg-muted data-[state=open]:text-foreground",
      )}
      aria-label={t.modelPicker.selectModel}
      title={selectedDisplayLabel}
    >
      {isAutoMode && <SparklesIcon className="size-3 shrink-0 text-info" />}
      <span className="truncate max-w-[var(--text-truncate-md)]">
        <span
          className={cn(
            isOpenCodeZenFreeModel(selected) &&
              "text-emerald-600 dark:text-emerald-400",
          )}
        >
          {selectedDisplayLabel}
        </span>
      </span>
      <ChevronDownIcon className="size-3 opacity-60" />
    </button>
  );

  // Default trigger composition: the trigger button alone is
  // wrapped by DropdownMenuTrigger asChild. The Auto switch used
  // to sit to the left of the trigger, but it duplicates the Auto
  // row at the top of the dropdown panel — same backing state, same
  // "auto" sentinel — so the inline switch is redundant and removed.
  const defaultTriggerContainer = (
    <DropdownMenuTrigger asChild>{triggerButton}</DropdownMenuTrigger>
  );

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      {renderTrigger ? (
        <DropdownMenuTrigger asChild>
          {renderTrigger(selected)}
        </DropdownMenuTrigger>
      ) : (
        defaultTriggerContainer
      )}
      <DropdownMenuContent
        data-testid="model-picker-menu"
        align="end"
        side="top"
        sideOffset={6}
        className="w-56 p-0"
      >
        {/* Auto row lives above the tabs so the user can flip into
            smart-routing mode without scrolling through the model
            list. The trigger button is the canonical model label;
            this row only restates the Auto state with a sparkles
            icon + 智能 badge so the option stays discoverable. */}
        <div className="p-1 pb-0">
          <PickerRow
            label={
              <span className="inline-flex items-center gap-1.5">
                <SparklesIcon className="size-3 shrink-0 text-info" />
                {t.modelPicker.autoModelLabel}
              </span>
            }
            right={
              <span className="rounded border border-info/40 px-1 py-0 text-xs text-info">
                {t.modelPicker.autoModelBadge}
              </span>
            }
            selected={isAutoMode}
            onSelect={() => handleSelect("auto")}
          />
        </div>

        {onReasoningEffortChange && (
          <ReasoningEffortSetting
            value={reasoningEffort}
            disabled={reasoningEffortDisabled}
            efforts={selected?.reasoning_efforts}
            onChange={onReasoningEffortChange}
          />
        )}
        <ModelContextSetting
          models={models}
          selected={selected}
          value={value}
          disabled={reasoningEffortDisabled}
          onChange={onChange}
          className="mx-1 mt-1 border-t border-border-default px-0.5 pt-1"
        />

        <div className="p-1 pt-0.5">
          <div className="flex flex-col gap-0.5">
            {flatEntries.length === 0 ? (
              <div className="px-2 py-4 text-center text-xs text-muted-foreground">
                {t.modelPicker.noCustomModels}
              </div>
            ) : (
              flatEntries.map((m) => {
                // selection_id identifies endpoint + upstream variant +
                // context profile. Legacy catalogs fall back to entry/name.
                const selectKey = selectionValue(m);
                const selectedFamily =
                  !isAutoMode &&
                  selected &&
                  modelFamilyKey(m) === modelFamilyKey(selected);
                return (
                  <PickerRow
                    key={selectKey}
                    label={
                      <span
                        className={cn(
                          isOpenCodeZenFreeModel(m) &&
                            "text-emerald-600 dark:text-emerald-400",
                        )}
                      >
                        {m.display_name || m.name}
                      </span>
                    }
                    selected={Boolean(selectedFamily)}
                    onSelect={() => handleSelect(selectKey)}
                  />
                );
              })
            )}
          </div>
          <div className="mt-1.5 border-t pt-1.5">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                // Settings dialog hosts the "models" page where the
                // user manages api_base / api_key entries.
                window.dispatchEvent(
                  new CustomEvent("echo:open-settings", {
                    detail: { tab: "models" },
                  }),
                );
              }}
              className={cn(
                "flex w-full items-center justify-center gap-1.5 rounded-md",
                "px-2 py-1.5 text-xs text-muted-foreground transition",
                "hover:bg-accent hover:text-foreground",
              )}
            >
              <PlusIcon className="size-3.5" />
              {t.modelPicker.addModel}
            </button>
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
