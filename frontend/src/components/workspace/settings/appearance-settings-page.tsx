import {
  CheckIcon,
  MonitorSmartphoneIcon,
  MoonIcon,
  SunIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import {
  useCallback,
  useMemo,
  useRef,
  type ComponentType,
  type PointerEvent,
  type SVGProps,
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
  type Palette,
} from "@/hooks/use-appearance";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

/** Palettes with a fixed swatch — excludes "custom", which setPalette rejects. */
type NamedPalette = Exclude<Palette, "custom">;

const LANGUAGE_OPTIONS = [
  { value: "en-US", label: "English" },
  { value: "zh-CN", label: "简体中文" },
  { value: "ja-JP", label: "日本語" },
  { value: "ko-KR", label: "한국어" },
] satisfies { value: Locale; label: string }[];

export default function AppearanceSettingsPage() {
  const { t, locale, changeLocale } = useI18n();
  const { theme, setTheme, systemTheme } = useTheme();
  const currentTheme = (theme ?? "system") as "system" | "light" | "dark";
  const {
    cornerScale,
    density,
    palette,
    customColor,
    setCornerScale,
    setDensity,
    setPalette,
    setCustomColor,
  } = useAppearance();

  // Two groups by character: bright/warm ("柔和") vs low-chroma/deep ("沉稳").
  // Swatch hexes are the computed light-mode --primary of each [data-theme].
  const paletteGroups = useMemo(
    () =>
      [
        {
          id: "soft",
          label: t.settings.appearance.paletteGroupSoft,
          options: [
            {
              id: "rouge" as NamedPalette,
              label: t.settings.appearance.paletteRose,
              description: t.settings.appearance.paletteRoseDescription,
              swatch: "#d85164",
            },
            {
              id: "apricot" as NamedPalette,
              label: t.settings.appearance.paletteApricot,
              description: t.settings.appearance.paletteApricotDescription,
              swatch: "#bd5223",
            },
            {
              id: "violet" as NamedPalette,
              label: t.settings.appearance.paletteViolet,
              description: t.settings.appearance.paletteVioletDescription,
              swatch: "#9e4eab",
            },
            {
              id: "mint" as NamedPalette,
              label: t.settings.appearance.paletteMint,
              description: t.settings.appearance.paletteMintDescription,
              swatch: "#008557",
            },
          ],
        },
        {
          id: "deep",
          label: t.settings.appearance.paletteGroupDeep,
          options: [
            {
              id: "steel" as NamedPalette,
              label: t.settings.appearance.paletteSteel,
              description: t.settings.appearance.paletteSteelDescription,
              swatch: "#4461be",
            },
            {
              id: "teal" as NamedPalette,
              label: t.settings.appearance.paletteTeal,
              description: t.settings.appearance.paletteTealDescription,
              swatch: "#377684",
            },
            {
              id: "emerald" as NamedPalette,
              label: t.settings.appearance.paletteEmerald,
              description: t.settings.appearance.paletteEmeraldDescription,
              swatch: "#167a69",
            },
            {
              id: "amber" as NamedPalette,
              label: t.settings.appearance.paletteAmber,
              description: t.settings.appearance.paletteAmberDescription,
              swatch: "#af5331",
            },
          ],
        },
      ] satisfies {
        id: string;
        label: string;
        options: {
          id: NamedPalette;
          label: string;
          description: string;
          swatch: string;
        }[];
      }[],
    [
      t.settings.appearance.paletteAmber,
      t.settings.appearance.paletteAmberDescription,
      t.settings.appearance.paletteApricot,
      t.settings.appearance.paletteApricotDescription,
      t.settings.appearance.paletteEmerald,
      t.settings.appearance.paletteEmeraldDescription,
      t.settings.appearance.paletteGroupDeep,
      t.settings.appearance.paletteGroupSoft,
      t.settings.appearance.paletteMint,
      t.settings.appearance.paletteMintDescription,
      t.settings.appearance.paletteRose,
      t.settings.appearance.paletteRoseDescription,
      t.settings.appearance.paletteSteel,
      t.settings.appearance.paletteSteelDescription,
      t.settings.appearance.paletteTeal,
      t.settings.appearance.paletteTealDescription,
      t.settings.appearance.paletteViolet,
      t.settings.appearance.paletteVioletDescription,
    ],
  );

  const themeOptions = useMemo(
    () => [
      {
        id: "system",
        label: t.settings.appearance.system,
        description: t.settings.appearance.systemDescription,
        icon: MonitorSmartphoneIcon,
      },
      {
        id: "light",
        label: t.settings.appearance.light,
        description: t.settings.appearance.lightDescription,
        icon: SunIcon,
      },
      {
        id: "dark",
        label: t.settings.appearance.dark,
        description: t.settings.appearance.darkDescription,
        icon: MoonIcon,
      },
    ],
    [
      t.settings.appearance.dark,
      t.settings.appearance.darkDescription,
      t.settings.appearance.light,
      t.settings.appearance.lightDescription,
      t.settings.appearance.system,
      t.settings.appearance.systemDescription,
    ],
  );

  return (
    <div className="space-y-6">
      <SettingsSection
        title={t.settings.appearance.themeTitle}
        description={t.settings.appearance.themeDescription}
      >
        <div className="grid grid-cols-3 gap-2">
          {themeOptions.map((option) => (
            <ThemePreviewCard
              key={option.id}
              icon={option.icon}
              label={option.label}
              description={option.description}
              active={currentTheme === option.id}
              mode={option.id as "system" | "light" | "dark"}
              systemTheme={systemTheme}
              onSelect={(value) => setTheme(value)}
            />
          ))}
        </div>
      </SettingsSection>

      <Separator className="my-1" />

      <SettingsSection
        title={t.settings.appearance.paletteTitle}
        description={t.settings.appearance.paletteDescription}
      >
        <div className="space-y-3">
          {paletteGroups.map((group) => (
            <div key={group.id} className="flex flex-wrap items-center gap-2">
              <span className="w-10 shrink-0 text-xs text-muted-foreground">
                {group.label}
              </span>
              {group.options.map((option) => (
                <PaletteSwatchButton
                  key={option.id}
                  label={option.label}
                  description={option.description}
                  active={palette === option.id}
                  swatch={option.swatch}
                  onSelect={() => setPalette(option.id)}
                />
              ))}
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <PaletteSwatchButton
            label={t.settings.appearance.paletteCustom}
            active={palette === "custom"}
            swatch={customColor}
            onSelect={() => setCustomColor(customColor)}
          />
          <label
            className={cn(
              "relative ml-1 inline-flex size-8 shrink-0 cursor-pointer items-center",
              "justify-center rounded-full border border-dashed text-muted-foreground",
              "transition-colors hover:border-primary/50 hover:text-foreground",
            )}
            title={t.settings.appearance.paletteCustomHint}
          >
            <input
              type="color"
              aria-label={t.settings.appearance.paletteCustom}
              className="absolute inset-0 cursor-pointer opacity-0"
              value={customColor}
              onChange={(event) => setCustomColor(event.target.value)}
            />
            <span aria-hidden="true" className="font-mono text-xs leading-none">
              +
            </span>
          </label>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {t.settings.appearance.paletteCustomHint}
          <span className="ml-1.5 font-mono uppercase">{customColor}</span>
        </p>
      </SettingsSection>

      <Separator className="my-1" />

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

/** Compact palette picker: a color dot with a check when active. */
function PaletteSwatchButton({
  label,
  description,
  active,
  swatch,
  onSelect,
}: {
  label: string;
  description?: string;
  active: boolean;
  swatch: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      aria-label={label}
      title={description ? `${label} · ${description}` : label}
      className={cn(
        "relative inline-flex size-8 shrink-0 items-center justify-center rounded-full",
        "border transition-all hover:scale-105",
        active
          ? "border-primary ring-2 ring-primary/35 ring-offset-1 ring-offset-background"
          : "border-black/10 hover:border-primary/40 dark:border-white/15",
      )}
      style={{ backgroundColor: swatch }}
    >
      {active ? (
        <CheckIcon
          aria-hidden="true"
          className="size-4 text-white drop-shadow-[0_1px_1px_rgba(0,0,0,0.45)]"
          strokeWidth={3}
        />
      ) : null}
      <span className="sr-only">{label}</span>
    </button>
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

function ThemePreviewCard({
  icon: Icon,
  label,
  description,
  active,
  mode,
  systemTheme,
  onSelect,
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  label: string;
  description: string;
  active: boolean;
  mode: "system" | "light" | "dark";
  systemTheme?: string;
  onSelect: (mode: "system" | "light" | "dark") => void;
}) {
  const previewMode =
    mode === "system" ? (systemTheme === "dark" ? "dark" : "light") : mode;
  const previewIsDark = previewMode === "dark";
  const previewFrameClass =
    previewMode === "dark"
      ? "border-neutral-800 bg-neutral-950 text-neutral-200"
      : "border-border bg-white text-foreground";
  const previewTopbarClass =
    previewMode === "dark"
      ? "border-white/10 bg-neutral-900"
      : "border-border bg-muted";
  const previewSidebarClass =
    previewMode === "dark"
      ? "border-white/10 bg-[linear-gradient(180deg,#171717_0%,#101010_100%)]"
      : "border-border bg-muted/85";
  const previewCanvasClass =
    previewMode === "dark" ? "bg-neutral-900" : "bg-white";
  const activeDotClass = previewMode === "dark" ? "bg-success" : "bg-success";
  return (
    <button
      type="button"
      onClick={() => onSelect(mode)}
      aria-pressed={active}
      className={cn(
        "group flex h-full min-w-0 flex-col gap-2 rounded-lg border p-2 text-left transition-all sm:p-3",
        active
          ? "border-primary ring-primary/30 shadow-[var(--shadow-xs)] ring-2"
          : "hover:border-border hover:shadow-[var(--shadow-xs)]",
      )}
    >
      <div className="flex min-w-0 items-center gap-1.5 sm:items-start sm:gap-3">
        <div className="hidden rounded-lg bg-muted p-1.5 sm:block">
          <Icon className="size-4" />
        </div>
        <div className="min-w-0 space-y-1">
          <div className="truncate text-xs font-semibold leading-none sm:text-sm">
            {label}
          </div>
          <p className="hidden text-xs leading-snug text-muted-foreground sm:block">
            {description}
          </p>
        </div>
      </div>
      <div
        className={cn(
          "relative aspect-[4/3] overflow-hidden rounded-md border text-xs transition-colors sm:aspect-auto sm:rounded-lg",
          previewFrameClass,
        )}
      >
        <div
          className={cn(
            "flex items-center gap-1 border-b px-1.5 py-1.5 sm:gap-2 sm:px-3 sm:py-2",
            previewTopbarClass,
          )}
        >
          <div className={cn("h-2 w-2 rounded-full", activeDotClass)} />
          <div className="h-2 w-10 rounded-md bg-current/20" />
          <div className="h-2 w-6 rounded-md bg-current/15" />
        </div>
        <div className="grid h-full grid-cols-[18px_minmax(0,1fr)] sm:grid-cols-[32px_minmax(0,1fr)]">
          <div
            className={cn(
              "flex min-h-12 flex-col gap-1 border-r px-1 py-1.5 sm:min-h-[72px] sm:gap-1.5 sm:px-2 sm:py-2",
              previewSidebarClass,
            )}
          >
            <div
              className={cn("size-2 rounded-full sm:size-3", activeDotClass)}
            />
            <div className="h-2 w-4 rounded-full bg-current/18" />
            <div className="h-2 w-4 rounded-full bg-current/14" />
            <div className="mt-auto h-2 w-4 rounded-full bg-current/12" />
          </div>
          <div
            className={cn(
              "grid grid-cols-1 gap-1 p-1.5 sm:grid-cols-[1fr_76px] sm:gap-2 sm:px-2.5 sm:py-2.5",
              previewCanvasClass,
            )}
          >
            <div className="space-y-2">
              <div className="h-2.5 w-3/4 rounded-md bg-current/15" />
              <div className="h-2.5 w-1/2 rounded-md bg-current/10" />
              <div
                className={cn(
                  "h-6 rounded-md border bg-current/5 sm:h-9 sm:rounded-lg",
                  previewIsDark
                    ? "border-white/10 bg-white/[0.03]"
                    : "border-border bg-white",
                )}
              />
            </div>
            <div className="hidden space-y-2 sm:block">
              <div className="flex items-center gap-2">
                <div className="h-8 w-8 rounded-lg bg-current/10" />
                <div className="space-y-2">
                  <div className="h-2 w-10 rounded-md bg-current/15" />
                  <div className="h-2 w-7 rounded-md bg-current/10" />
                </div>
              </div>
              <div
                className={cn(
                  "flex flex-col gap-1 rounded-lg border border-dashed p-2",
                  previewIsDark ? "border-white/10" : "border-border",
                )}
              >
                <div className="h-2 w-3/5 rounded-md bg-current/15" />
                <div className="h-2 w-2/5 rounded-md bg-current/10" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </button>
  );
}
