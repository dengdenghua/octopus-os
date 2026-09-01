import { XIcon, FileIcon } from "lucide-react";
import { useCallback, useRef, useEffect } from "react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";

export interface EditorTab {
  path: string;
  label: string;
  isDirty?: boolean;
}

interface EditorTabsProps {
  tabs: EditorTab[];
  activeTab: string | null;
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
  className?: string;
}

function langIcon(path: string) {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "TS",
    tsx: "TX",
    js: "JS",
    jsx: "JX",
    py: "PY",
    rs: "RS",
    go: "GO",
    java: "JA",
    json: "{}",
    md: "MD",
    css: "CS",
    html: "<>",
    sql: "SQ",
    sh: "SH",
    yml: "YM",
    yaml: "YM",
    toml: "TM",
    xml: "XM",
    vue: "VU",
    svelte: "SV",
  };
  return map[ext] ?? null;
}

export function EditorTabs({
  tabs,
  activeTab,
  onSelect,
  onClose,
  className,
}: EditorTabsProps) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (activeRef.current) {
      activeRef.current.scrollIntoView({
        block: "nearest",
        inline: "nearest",
        behavior: "smooth",
      });
    }
  }, [activeTab]);

  const handleClose = useCallback(
    (e: React.MouseEvent, path: string) => {
      e.stopPropagation();
      onClose(path);
    },
    [onClose],
  );

  const handleMiddleClick = useCallback(
    (e: React.MouseEvent, path: string) => {
      if (e.button === 1) {
        e.preventDefault();
        onClose(path);
      }
    },
    [onClose],
  );

  if (tabs.length === 0) return null;

  return (
    <div
      className={cn(
        "flex items-center border-b border-border-default bg-muted/20 overflow-hidden",
        className,
      )}
    >
      <div
        ref={scrollRef}
        className="flex items-center overflow-x-auto scrollbar-none flex-1 min-w-0"
      >
        {tabs.map((tab) => {
          const isActive = tab.path === activeTab;
          const icon = langIcon(tab.path);
          return (
            <button
              key={tab.path}
              ref={isActive ? activeRef : undefined}
              type="button"
              onClick={() => onSelect(tab.path)}
              onMouseDown={(e) => handleMiddleClick(e, tab.path)}
              className={cn(
                "group relative flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium whitespace-nowrap border-r border-border-subtle transition-colors shrink-0 max-w-[var(--text-truncate-lg)]",
                isActive
                  ? "bg-background text-foreground"
                  : "text-muted-foreground hover:bg-muted/40 hover:text-foreground",
              )}
            >
              {/* PLACEHOLDER_BOTTOM_BORDER */}
              {isActive && (
                <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-primary" />
              )}
              {icon ? (
                <span
                  className={cn(
                    "text-xs font-bold rounded px-0.5 shrink-0",
                    isActive ? "text-primary" : "text-muted-foreground/60",
                  )}
                >
                  {icon}
                </span>
              ) : (
                <FileIcon className="size-3 shrink-0 text-muted-foreground/60" />
              )}
              <span className="truncate">{tab.label}</span>
              {tab.isDirty && (
                <span className="size-1.5 rounded-full bg-primary shrink-0" />
              )}
              <button
                type="button"
                aria-label={t.editorTabs.closeTabAria(tab.label)}
                onClick={(e) => handleClose(e, tab.path)}
                className={cn(
                  "ml-0.5 rounded p-0.5 shrink-0 transition-colors",
                  isActive
                    ? "hover:bg-muted text-muted-foreground hover:text-foreground"
                    : "opacity-0 group-hover:opacity-100 hover:bg-muted text-muted-foreground hover:text-foreground",
                )}
              >
                <XIcon className="size-2.5" />
              </button>
            </button>
          );
        })}
      </div>
    </div>
  );
}
