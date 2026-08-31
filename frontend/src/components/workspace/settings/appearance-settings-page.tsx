import {
  useCallback,
  useRef,
  type PointerEvent,
} from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { isLocale, type Locale } from "@/core/i18n/locale";
import { useI18n } from "@/core/i18n/hooks";
import {
  useAppearance,
  type CornerScale,
  type Density,
} from "@/hooks/use-appearance";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

const LANGUAGE_OPTIONS = [
  { value: "en-US", label: "English" },
  { value: "zh-CN", label: "简体中文" },
  { value: "ja-JP", label: "日本語" },
  { value: "ko-KR", label: "한국어" },
] satisfies { value: Locale; label: string }[];

export default function AppearanceSettingsPage() {
  const { t, locale, changeLocale } = useI18n();
  const {
    cornerScale,
    density,
    setCornerScale,
    setDensity,
  } = useAppearance();

  return (
    <div className="space-y-6">
      {/* Language remains a general application preference. Conversation
          density now has its own destination in Settings. */}
      <div className="divide-y rounded-lg border">
        <SettingRow
          title={t.settings.appearance.languageTitle}
          description={t.settings.appearance.languageDescription}
        >
          <Select
            value={locale}
            onValueChange={(value) => {
              if (isLocale(value)) {
                changeLocale(value);
              }
            }}
          >
            <SelectTrigger
              aria-label={t.settings.appearance.languageTitle}
              className="w-full sm:w-[200px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGE_OPTIONS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
      </div>

      <Separator />

      {/* Both step sliders sit side by side on wide screens. */}
      <div className="grid gap-6 lg:grid-cols-2">
        <SettingsSection
          title={t.settings.appearance.cornerRadiusTitle}
          description={t.settings.appearance.cornerRadiusDescription}
        >
          <AppearanceStepSlider<CornerScale>
            label={t.settings.appearance.cornerRadiusTitle}
            value={cornerScale}
            onChange={setCornerScale}
            showHeader={false}
            options={[
              {
                value: 0.5,
                label: t.settings.appearance.cornerCrisp,
                preview: "0.25rem",
              },
              {
                value: 0.75,
                label: t.settings.appearance.cornerSoft,
                preview: "0.375rem",
              },
              {
                value: 1,
                label: t.settings.appearance.cornerDefault,
                preview: "0.5rem",
              },
              {
                value: 1.25,
                label: t.settings.appearance.cornerRound,
                preview: "0.625rem",
              },
              {
                value: 1.5,
                label: t.settings.appearance.cornerPill,
                preview: "0.75rem",
              },
            ]}
          />
        </SettingsSection>

        <SettingsSection
          title={t.settings.appearance.uiDensityTitle}
          description={t.settings.appearance.uiDensityDescription}
        >
          <AppearanceStepSlider<Density>
            label={t.settings.appearance.uiDensityTitle}
            value={density}
            onChange={setDensity}
            showHeader={false}
            options={[
              {
                value: "relaxed",
                label: t.settings.appearance.densityRelaxed,
                preview: "16px",
              },
              {
                value: "comfortable",
                label: t.settings.appearance.densityComfortable,
                preview: "15px",
              },
              {
                value: "compact",
                label: t.settings.appearance.densityCompact,
                preview: "14px",
              },
              {
                value: "dense",
                label: t.settings.appearance.densityDense,
                preview: "13px",
              },
              {
                value: "ultradense",
                label: t.settings.appearance.densityUltraDense,
                preview: "12.5px",
              },
            ]}
          />
        </SettingsSection>
      </div>
    </div>
  );
}

/** One-line setting: label + description on the left, control on the right. */
function SettingRow({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0 space-y-0.5">
        <div className="text-sm font-medium">{title}</div>
        <p className="text-xs leading-snug text-muted-foreground">
          {description}
        </p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

type AppearanceStepValue = string | number;

type AppearanceStepOption<TValue extends AppearanceStepValue> = {
  value: TValue;
  label: string;
  description?: string;
  preview?: string;
};

function AppearanceStepSlider<TValue extends AppearanceStepValue>({
  label,
  onChange,
  options,
  showHeader = true,
  value,
}: {
  label: string;
  onChange: (value: TValue) => void;
  options: AppearanceStepOption<TValue>[];
  showHeader?: boolean;
  value: TValue;
}) {
  const activeIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  const fallbackOption: AppearanceStepOption<TValue> = {
    value,
    label,
    description: "",
  };
  const active = options[activeIndex] ?? options[0] ?? fallbackOption;
  const activeDetail = active.description ?? active.preview ?? "";
  const progress =
    options.length > 1 ? (activeIndex / (options.length - 1)) * 100 : 0;
  const trackRef = useRef<HTMLDivElement>(null);
  const updateFromIndex = useCallback(
    (index: number) => {
      const next = options[index];
      if (next) onChange(next.value);
    },
    [onChange, options],
  );
  const updateFromInputValue = (rawValue: string) => {
    updateFromIndex(Number(rawValue));
  };
  const updateFromPointer = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const track = trackRef.current;
      if (!track || options.length < 1) return;

      const rect = track.getBoundingClientRect();
      const ratio = Math.min(
        1,
        Math.max(0, (event.clientX - rect.left) / rect.width),
      );
      updateFromIndex(Math.round(ratio * (options.length - 1)));
    },
    [options, updateFromIndex],
  );

  return (
    <div className="rounded-lg border bg-muted/20 px-4 py-3">
      {showHeader ? (
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-medium">{label}</div>
            {activeDetail ? (
              <div className="mt-1 text-xs text-muted-foreground">
                {activeDetail}
              </div>
            ) : null}
          </div>
          <div className="rounded-full border bg-background/75 px-2.5 py-1 text-xs font-medium shadow-[var(--shadow-xs)]">
            <span>{active.label}</span>
            {active.preview ? (
              <span className="ml-1 text-muted-foreground">
                {active.preview}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
      <div
        className="relative px-1"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          updateFromPointer(event);
        }}
        onPointerMove={(event) => {
          if (event.buttons === 1) updateFromPointer(event);
        }}
        ref={trackRef}
      >
        <input
          aria-label={label}
          className="octo-appearance-step-slider"
          aria-valuetext={[active.label, activeDetail]
            .filter(Boolean)
            .join(": ")}
          max={options.length - 1}
          min={0}
          onChange={(event) => updateFromInputValue(event.currentTarget.value)}
          onInput={(event) => updateFromInputValue(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Home") {
              event.preventDefault();
              updateFromIndex(0);
            }
            if (event.key === "End") {
              event.preventDefault();
              updateFromIndex(options.length - 1);
            }
            if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
              event.preventDefault();
              updateFromIndex(Math.max(0, activeIndex - 1));
            }
            if (event.key === "ArrowRight" || event.key === "ArrowUp") {
              event.preventDefault();
              updateFromIndex(Math.min(options.length - 1, activeIndex + 1));
            }
          }}
          step={1}
          style={{
            background: `linear-gradient(90deg, color-mix(in oklch, var(--primary) 72%, white 18%) 0 ${progress}%, color-mix(in oklch, var(--muted) 72%, transparent) ${progress}% 100%)`,
          }}
          type="range"
          value={activeIndex}
        />
        <div className="pointer-events-none absolute inset-x-1 top-1/2 flex -translate-y-1/2 justify-between">
          {options.map((option, index) => (
            <span
              aria-hidden="true"
              className={cn(
                "size-2.5 rounded-full border border-background shadow-[var(--shadow-xs)]",
                index <= activeIndex ? "bg-primary" : "bg-muted-foreground/30",
              )}
              key={option.value}
            />
          ))}
        </div>
      </div>
      <div
        className="mt-2 grid gap-1 text-xs"
        style={{
          gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))`,
        }}
      >
        {options.map((option, index) => (
          <button
            aria-pressed={index === activeIndex}
            className={cn(
              "min-w-0 rounded-md px-1 py-1 text-center leading-none transition-colors",
              index === activeIndex
                ? "bg-primary/12 text-primary"
                : "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
            )}
            key={option.value}
            onClick={() => updateFromIndex(index)}
            type="button"
          >
            <span className="block truncate">{option.label}</span>
            {option.preview ? (
              <span className="mt-0.5 block truncate text-xs font-normal text-muted-foreground">
                {option.preview}
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}
