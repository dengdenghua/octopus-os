import {
  CheckIcon,
  ChevronsUpDownIcon,
  DnaIcon,
  GlobeIcon,
  Loader2Icon,
  PlusIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type MouseEvent,
} from "react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import {
  BROWSER_HOME_URL,
  type BrowserTab,
  useBrowserStore,
} from "./browser-store";

const TAB_LIST_THRESHOLD = 6;

function TabIcon({ tab }: { tab: BrowserTab }) {
  if (tab.crash) {
    return <TriangleAlertIcon className="size-3.5 shrink-0 text-warning" />;
  }
  if (tab.isLoading) {
    return <Loader2Icon className="size-3.5 shrink-0 animate-spin" />;
  }
  if (tab.url === BROWSER_HOME_URL) {
    return <DnaIcon className="size-3.5 shrink-0 text-primary" />;
  }
  if (tab.favicon) {
    return (
      <img
        src={tab.favicon}
        alt=""
        className="size-3.5 shrink-0"
        onError={(event) => (event.currentTarget.style.display = "none")}
      />
    );
  }
  return <GlobeIcon className="size-3.5 shrink-0 opacity-60" />;
}

export function TabBar() {
  const { t } = useI18n();
  const tb = t.browser.tabBar;
  const { state, openTab, closeTab, activateTab, reorderTab } =
    useBrowserStore();
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [tabListOpen, setTabListOpen] = useState(false);
  const tabElements = useRef(new Map<string, HTMLDivElement>());

  const pinnedHomeTab = useMemo(
    () => state.tabs.find((tab) => tab.url === BROWSER_HOME_URL) ?? null,
    [state.tabs],
  );
  const pageTabs = useMemo(
    () => state.tabs.filter((tab) => tab.id !== pinnedHomeTab?.id),
    [pinnedHomeTab?.id, state.tabs],
  );
  const crowded = pageTabs.length > 8;

  useEffect(() => {
    const activeElement = state.activeId
      ? tabElements.current.get(state.activeId)
      : undefined;
    activeElement?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [state.activeId]);

  const handleAuxClick = (event: MouseEvent, id: string) => {
    if (event.button === 1) {
      event.preventDefault();
      closeTab(id);
    }
  };

  const handleDragStart = (event: DragEvent, id: string) => {
    setDragId(id);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", id);
  };

  const handleDrop = (event: DragEvent, targetId: string) => {
    event.preventDefault();
    if (!dragId || dragId === targetId) return;
    const fromIndex = state.tabs.findIndex((tab) => tab.id === dragId);
    const toIndex = state.tabs.findIndex((tab) => tab.id === targetId);
    if (fromIndex >= 0 && toIndex >= 0) reorderTab(fromIndex, toIndex);
    setDragId(null);
    setDragOverId(null);
  };

  const renderTab = (tab: BrowserTab, fixed = false) => {
    const active = state.activeId === tab.id;
    const isHomeTab = tab.url === BROWSER_HOME_URL;
    const tabLabel = isHomeTab
      ? fixed
        ? tb.homeTabShort
        : t.browser.newTabPage
      : tab.title || tab.url;
    return (
      <div
        key={tab.id}
        ref={(element) => {
          if (element) tabElements.current.set(tab.id, element);
          else tabElements.current.delete(tab.id);
        }}
        data-testid={fixed ? "browser-home-tab" : "browser-page-tab"}
        data-active={active}
        draggable={!fixed}
        onDragStart={(event) => handleDragStart(event, tab.id)}
        onDragOver={(event) => {
          if (fixed) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          setDragOverId(tab.id);
        }}
        onDrop={(event) => handleDrop(event, tab.id)}
        onDragEnd={() => {
          setDragId(null);
          setDragOverId(null);
        }}
        onClick={() => activateTab(tab.id)}
        onAuxClick={(event) => handleAuxClick(event, tab.id)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            activateTab(tab.id);
          }
        }}
        role="button"
        tabIndex={0}
        aria-label={tabLabel}
        className={cn(
          "group relative flex h-7 cursor-pointer items-center gap-1 overflow-hidden rounded-md px-2 text-mini transition-[background-color,border-color,box-shadow,color,transform] after:absolute after:inset-x-2 after:bottom-0 after:h-[2px] after:scale-x-0 after:rounded-full after:bg-primary after:transition-transform",
          fixed
            ? "w-[76px] shrink-0"
            : crowded
              ? "min-w-[64px] max-w-[132px]"
              : "min-w-[84px] max-w-[160px]",
          active
            ? "bg-card/90 text-foreground shadow-[var(--shadow-xs)] after:scale-x-100"
            : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
          !fixed && dragOverId === tab.id && dragId !== tab.id
            ? "ring-2 ring-primary ring-offset-0"
            : null,
        )}
        style={
          {
            flex: fixed ? "0 0 76px" : crowded ? "1 1 112px" : "1 1 152px",
            WebkitAppRegion: "no-drag",
          } as CSSProperties
        }
        title={tab.title || tab.url}
      >
        <TabIcon tab={tab} />
        <span className="min-w-0 flex-1 truncate">{tabLabel}</span>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            closeTab(tab.id);
          }}
          className="grid size-3.5 shrink-0 place-items-center rounded text-muted-foreground/60 opacity-0 transition-opacity hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100 data-[active=true]:opacity-100"
          data-active={active}
          title={tb.close}
          aria-label={`${tb.close} ${tabLabel}`}
        >
          <XIcon className="size-2.5" />
        </button>
      </div>
    );
  };

  return (
    <div
      className="flex h-7 min-w-0 flex-1 items-center gap-0.5 bg-transparent"
      style={{ WebkitAppRegion: "drag" } as CSSProperties}
      data-testid="browser-tab-bar"
    >
      {pinnedHomeTab ? renderTab(pinnedHomeTab, true) : null}

      <div
        className="flex h-7 min-w-0 flex-1 items-center gap-0.5 overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        data-testid="browser-scrollable-tabs"
        onWheel={(event) => {
          if (event.deltaY === 0) return;
          event.currentTarget.scrollLeft += event.deltaY;
        }}
      >
        {pageTabs.map((tab) => renderTab(tab))}
      </div>

      {state.tabs.length >= TAB_LIST_THRESHOLD ? (
        <button
          type="button"
          onClick={() => setTabListOpen(true)}
          className="relative grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
          style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
          title={`${t.browser.tabs.label} · ${state.tabs.length}`}
          aria-label={`${t.browser.tabs.label} · ${state.tabs.length}`}
          data-testid="browser-all-tabs-trigger"
        >
          <ChevronsUpDownIcon className="size-3.5" />
          <span className="absolute -right-0.5 -top-0.5 min-w-3 rounded-full bg-primary px-0.5 text-center text-[8px] leading-3 text-primary-foreground">
            {state.tabs.length > 99 ? "99+" : state.tabs.length}
          </span>
        </button>
      ) : null}

      <button
        type="button"
        onClick={() => openTab()}
        className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
        style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
        title={tb.newTab}
        aria-label={tb.newTab}
        data-testid="browser-new-tab"
      >
        <PlusIcon className="size-3.5" />
      </button>

      <CommandDialog
        open={tabListOpen}
        onOpenChange={setTabListOpen}
        title={t.browser.tabs.label}
        description={t.browser.searchPlaceholder}
        className="sm:max-w-[520px]"
      >
        <CommandInput placeholder={t.browser.searchPlaceholder} />
        <CommandList>
          <CommandEmpty>{t.browser.empty.noMatch}</CommandEmpty>
          <CommandGroup
            heading={`${t.browser.tabs.label} · ${state.tabs.length}`}
          >
            {state.tabs.map((tab) => {
              const isActive = state.activeId === tab.id;
              const label =
                tab.id === pinnedHomeTab?.id
                  ? tb.homeTabShort
                  : tab.url === BROWSER_HOME_URL
                    ? t.browser.newTabPage
                    : tab.title || tab.url;
              return (
                <CommandItem
                  key={tab.id}
                  value={`${label} ${tab.url}`}
                  onSelect={() => {
                    activateTab(tab.id);
                    setTabListOpen(false);
                  }}
                  className="group/tab"
                >
                  <TabIcon tab={tab} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{label}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">
                      {tab.url}
                    </span>
                  </span>
                  {isActive ? (
                    <CheckIcon className="size-3.5 text-primary" />
                  ) : null}
                  <button
                    type="button"
                    className="grid size-6 shrink-0 place-items-center rounded-md opacity-0 transition-opacity hover:bg-foreground/10 group-hover/tab:opacity-100"
                    aria-label={`${tb.close} ${label}`}
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      closeTab(tab.id);
                    }}
                  >
                    <XIcon className="size-3" />
                  </button>
                </CommandItem>
              );
            })}
          </CommandGroup>
        </CommandList>
      </CommandDialog>
    </div>
  );
}
