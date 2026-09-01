/**
 * Personal-space work mode selector.
 *
 * The project ModeSelector only applies once a workspace folder is bound. This is
 * its personal-space counterpart — the agent's own sandbox/conversation space:
 *   - general:  normal agent (default)
 *   - build:    the agent actively produces runnable artifacts in its own sandbox
 *   - research: reuses the existing deep-research behaviour (the backend treats
 *               personal_mode="research" as a research turn)
 *
 * Labels are kept self-contained here (not in the shared i18n bundle) so this
 * feature stays decoupled from concurrently-edited locale files.
 */

import {
  ChevronDownIcon,
  FlaskConicalIcon,
  HammerIcon,
  SparklesIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export type PersonalMode = "general" | "build" | "research";

type PanelRect = {
  left: number;
  width: number;
  maxHeight: number;
  top?: number;
  bottom?: number;
};

type ModeMeta = {
  name: PersonalMode;
  icon: typeof SparklesIcon;
  activeTone: string;
};

type Labels = { label: string; desc: string };

const PERSONAL_MODES: [ModeMeta, ModeMeta, ModeMeta] = [
  {
    name: "general",
    icon: SparklesIcon,
    activeTone:
      "bg-info/15 text-info dark:bg-info/40 dark:text-info ring-1 ring-info/20",
  },
  {
    name: "build",
    icon: HammerIcon,
    activeTone:
      "bg-warning/15 text-warning dark:bg-warning/40 dark:text-warning ring-1 ring-warning/20",
  },
  {
    name: "research",
    icon: FlaskConicalIcon,
    activeTone:
      "bg-chart-1/15 text-chart-1 dark:bg-chart-1/40 dark:text-chart-1 ring-1 ring-chart-1/20",
  },
];

const LABELS: Record<
  PersonalMode,
  Record<"zh" | "en" | "ja" | "ko", Labels>
> = {
  general: {
    zh: { label: "通用", desc: "日常对话与任务" },
    en: { label: "General", desc: "Everyday chat and tasks" },
    ja: { label: "汎用", desc: "日常の対話とタスク" },
    ko: { label: "일반", desc: "일상 대화와 작업" },
  },
  build: {
    zh: { label: "构建", desc: "在自己工作区造可运行成果" },
    en: { label: "Build", desc: "Make runnable artifacts in its own space" },
    ja: { label: "構築", desc: "自分の作業領域で動く成果物を作る" },
    ko: { label: "빌드", desc: "자체 작업 공간에서 산출물 생성" },
  },
  research: {
    zh: { label: "研究", desc: "多源深度调研出报告" },
    en: { label: "Research", desc: "Deep multi-source research report" },
    ja: { label: "研究", desc: "多ソースの深いリサーチ報告" },
    ko: { label: "리서치", desc: "다중 소스 심층 리서치 보고서" },
  },
};

function labelsFor(mode: PersonalMode, locale: string): Labels {
  const lang = (locale || "en").slice(0, 2).toLowerCase();
  const byLang = LABELS[mode];
  if (lang === "zh") return byLang.zh;
  if (lang === "ja") return byLang.ja;
  if (lang === "ko") return byLang.ko;
  return byLang.en;
}

const PANEL_WIDTH = 300;
const PANEL_GAP = 6;
const PANEL_MARGIN = 12;
const PANEL_MIN_HEIGHT = 140;

interface PersonalModeSelectorProps {
  mode: PersonalMode;
  chromeless?: boolean;
  labelOverrides?: Partial<Record<PersonalMode, string>>;
  onModeChange: (mode: PersonalMode) => void;
  className?: string;
}

export function PersonalModeSelector({
  mode,
  chromeless = false,
  labelOverrides,
  onModeChange,
  className,
}: PersonalModeSelectorProps) {
  const { locale } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listboxRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const triggerId = `${baseId}-trigger`;
  const listboxId = `${baseId}-listbox`;
  const [panelRect, setPanelRect] = useState<PanelRect | null>(null);

  const activeOption =
    PERSONAL_MODES.find((o) => o.name === mode) ?? PERSONAL_MODES[0];
  const ActiveIcon = activeOption.icon;
  const activeLabels = labelsFor(activeOption.name, locale);

  useEffect(() => {
    if (!expanded) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        panelRef.current &&
        !panelRef.current.contains(target) &&
        !menuRef.current?.contains(target)
      ) {
        // Keyboard path leaves focus inside the popup; hand it back to
        // the trigger instead of stranding it on <body>.
        if (menuRef.current?.contains(document.activeElement)) {
          triggerRef.current?.focus();
        }
        setExpanded(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [expanded]);

  const updatePanelPosition = useCallback(() => {
    const trigger = panelRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const width = Math.min(PANEL_WIDTH, viewportWidth - PANEL_MARGIN * 2);
    const left = Math.min(
      Math.max(PANEL_MARGIN, rect.right - width),
      viewportWidth - PANEL_MARGIN - width,
    );
    const spaceBelow = viewportHeight - rect.bottom - PANEL_MARGIN;
    const spaceAbove = rect.top - PANEL_MARGIN;
    const openUp = spaceAbove > spaceBelow;
    const maxHeight = Math.max(
      PANEL_MIN_HEIGHT,
      openUp ? spaceAbove - PANEL_GAP : spaceBelow - PANEL_GAP,
    );
    setPanelRect({
      left,
      width,
      maxHeight,
      ...(openUp
        ? { bottom: viewportHeight - rect.top + PANEL_GAP }
        : { top: rect.bottom + PANEL_GAP }),
    });
  }, []);

  useEffect(() => {
    if (!expanded) {
      setPanelRect(null);
      return;
    }
    updatePanelPosition();
    window.addEventListener("resize", updatePanelPosition);
    window.addEventListener("scroll", updatePanelPosition, true);
    return () => {
      window.removeEventListener("resize", updatePanelPosition);
      window.removeEventListener("scroll", updatePanelPosition, true);
    };
  }, [expanded, updatePanelPosition]);

  const handlePick = useCallback(
    (next: PersonalMode) => {
      onModeChange(next);
      setExpanded(false);
      triggerRef.current?.focus();
    },
    [onModeChange],
  );

  const closeAndRefocusTrigger = useCallback(() => {
    setExpanded(false);
    triggerRef.current?.focus();
  }, []);

  // The popup is portaled to the end of <body>, so DOM tab order never reaches
  // it from the trigger. Move focus onto the selected option as soon as the
  // listbox mounts; closing paths hand focus back to the trigger.
  const setListboxNode = useCallback((node: HTMLDivElement | null) => {
    listboxRef.current = node;
    if (!node) return;
    const selected = node.querySelector<HTMLButtonElement>(
      '[role="option"][aria-selected="true"]',
    );
    (
      selected ?? node.querySelector<HTMLButtonElement>('[role="option"]')
    )?.focus();
  }, []);

  const handlePopupKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        closeAndRefocusTrigger();
        return;
      }
      if (e.key === "Tab") {
        // Portaled popup: walk its own focusables so future non-option
        // controls stay keyboard-reachable; close when tabbing past an
        // end (mirrors mode-selector.tsx).
        const focusables = Array.from(
          menuRef.current?.querySelectorAll<HTMLButtonElement>(
            "button:not([disabled])",
          ) ?? [],
        );
        const current = focusables.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        const next = current + (e.shiftKey ? -1 : 1);
        e.preventDefault();
        e.stopPropagation();
        if (current >= 0 && next >= 0 && next < focusables.length) {
          focusables[next]?.focus();
          return;
        }
        closeAndRefocusTrigger();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
      const options = Array.from(
        listboxRef.current?.querySelectorAll<HTMLButtonElement>(
          '[role="option"]',
        ) ?? [],
      );
      if (options.length === 0) return;
      e.preventDefault();
      const current = options.indexOf(
        document.activeElement as HTMLButtonElement,
      );
      let next: number;
      if (e.key === "Home") next = 0;
      else if (e.key === "End") next = options.length - 1;
      else if (e.key === "ArrowDown")
        next = current < 0 ? 0 : Math.min(current + 1, options.length - 1);
      else next = current < 0 ? options.length - 1 : Math.max(current - 1, 0);
      options[next]?.focus();
    },
    [closeAndRefocusTrigger],
  );

  return (
    <div ref={panelRef} className={cn("relative", className)}>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        aria-expanded={expanded}
        aria-haspopup="listbox"
        aria-controls={expanded ? listboxId : undefined}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (!expanded && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            setExpanded(true);
          } else if (expanded && e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            setExpanded(false);
          }
        }}
        className={cn(
          "group flex items-center gap-1.5 text-xs font-medium text-muted-foreground shadow-none transition-colors duration-base",
          chromeless
            ? "h-8 rounded-lg px-1.5 hover:bg-muted/55 hover:text-foreground"
            : "h-8 rounded-lg border border-transparent bg-transparent px-2 hover:border-border-subtle hover:bg-muted/55 hover:text-foreground",
        )}
        title={activeLabels.desc}
      >
        <ActiveIcon className="size-3" />
        <span className="max-w-[72px] truncate">
          {labelOverrides?.[mode]?.trim() || activeLabels.label}
        </span>
        <ChevronDownIcon className="size-3 opacity-35 transition-opacity group-hover:opacity-60" />
      </button>

      {expanded && panelRect && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={menuRef}
              className="fixed z-[100] overflow-hidden rounded-lg border bg-background shadow-xl ring-1 ring-border/30"
              style={{
                left: `${panelRect.left}px`,
                width: `${panelRect.width}px`,
                maxHeight: `${panelRect.maxHeight}px`,
                top:
                  panelRect.top !== undefined
                    ? `${panelRect.top}px`
                    : undefined,
                bottom:
                  panelRect.bottom !== undefined
                    ? `${panelRect.bottom}px`
                    : undefined,
              }}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={handlePopupKeyDown}
            >
              <div className="max-h-[inherit] overflow-y-auto">
                <div
                  ref={setListboxNode}
                  id={listboxId}
                  role="listbox"
                  aria-labelledby={triggerId}
                  className="space-y-1 p-2"
                >
                  {PERSONAL_MODES.map((option) => {
                    const Icon = option.icon;
                    const labels = labelsFor(option.name, locale);
                    return (
                      <button
                        key={option.name}
                        type="button"
                        role="option"
                        aria-selected={mode === option.name}
                        onClick={() => handlePick(option.name)}
                        className={cn(
                          "flex w-full items-center gap-2 px-3 py-2 text-xs transition-colors",
                          mode === option.name
                            ? option.activeTone
                            : "text-muted-foreground hover:bg-muted",
                        )}
                        title={labels.desc}
                      >
                        <Icon className="size-4 shrink-0" />
                        <div className="flex min-w-0 items-center gap-2 text-left">
                          <span className="font-semibold">
                            {labelOverrides?.[option.name]?.trim() ||
                              labels.label}
                          </span>
                          <span className="truncate text-xs opacity-70">
                            {labels.desc}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
