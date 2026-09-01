import {
  ActivityIcon,
  BoxesIcon,
  BrainCircuitIcon,
  CableIcon,
  CpuIcon,
  DnaIcon,
  FolderIcon,
  GlobeIcon,
  KeyboardIcon,
  MessageSquarePlusIcon,
  NetworkIcon,
  PlugIcon,
  RadarIcon,
  SettingsIcon,
  SparklesIcon,
  StethoscopeIcon,
  StoreIcon,
  UsersIcon,
  ZapIcon,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { emitOpenSettings } from "@/core/events";
import { useI18n } from "@/core/i18n/hooks";
import { BROWSER_WORKSPACE_ROUTE } from "@/core/workspace/sidebar-routing";
import { useGlobalShortcuts } from "@/hooks/use-global-shortcuts";

export function CommandPalette() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [isMac, setIsMac] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleNewChat = useCallback(() => {
    navigate("/workspace/realtime/new");
    setOpen(false);
  }, [navigate]);

  const handleOpenSettings = useCallback(() => {
    setOpen(false);
    emitOpenSettings();
  }, []);

  const handleShowShortcuts = useCallback(() => {
    setOpen(false);
    setShortcutsOpen(true);
  }, []);

  const shortcuts = useMemo(
    () => [
      { key: "k", meta: true, action: () => setOpen((o) => !o) },
      { key: "n", meta: true, shift: true, action: handleNewChat },
      { key: ",", meta: true, action: handleOpenSettings },
      { key: "/", meta: true, action: handleShowShortcuts },
    ],
    [handleNewChat, handleOpenSettings, handleShowShortcuts],
  );

  useGlobalShortcuts(shortcuts);

  // Listen for Ctrl+Shift+P dispatched by useWorkspaceShortcuts
  useEffect(() => {
    const handler = () => setOpen((o) => !o);
    window.addEventListener("echo:command-palette", handler);
    return () => window.removeEventListener("echo:command-palette", handler);
  }, []);

  useEffect(() => {
    setIsMac(navigator.userAgent.includes("Mac"));
  }, []);
  const metaKey = isMac ? "⌘" : "Ctrl+";
  const shiftKey = isMac ? "⇧" : "Shift+";

  // Workspace pages exposed to command-palette search. Each entry
  // ships an icon and a list of keywords so users can hop directly
  // to a route by typing either its label, its path segment, or a
  // related term (e.g. "monitor" finds Observability). Rendered as
  // a single CommandGroup below the actions group; the underlying
  // cmdk library filters both groups from the same input value.
  const handleNavigate = useCallback(
    (to: string) => {
      navigate(to);
      setOpen(false);
    },
    [navigate],
  );
  const PAGE_ITEMS = useMemo(
    () => [
      {
        id: "new-chat",
        to: "/workspace/realtime/new",
        label: t.sidebar.newChat,
        icon: MessageSquarePlusIcon,
        keywords: "chat new realtime conversation",
      },
      {
        id: "agents",
        to: "/workspace/agents",
        label: t.agents.title,
        icon: UsersIcon,
        keywords: "agent team",
      },
      {
        id: "skills",
        to: "/workspace/agents?surface=chat&tab=skills",
        label: `${t.sidebar.navHR} · ${t.plugins.tabSkillMarket}`,
        icon: SparklesIcon,
        keywords: "skill",
      },
      {
        id: "plugins",
        to: "/workspace/agents?surface=chat&tab=plugins",
        label: `${t.sidebar.navHR} · ${t.metaSkills.title}`,
        icon: BoxesIcon,
        keywords: "skill pack meta workflow template plugin",
      },
      {
        id: "hub",
        to: "/workspace/agents?surface=chat",
        label: t.sidebar.navHR,
        icon: StoreIcon,
        keywords: "hub agents roles market store shop asset center",
      },
      {
        id: "channels",
        to: "/workspace/channels",
        label: t.channels.title,
        icon: CableIcon,
        keywords: "channel connector messaging",
      },
      {
        id: "mcp",
        to: "/workspace/settings?section=tools",
        label: t.sidebar.navMcp,
        icon: PlugIcon,
        keywords: "mcp integration",
      },
      {
        id: "architecture",
        to: "/workspace/architecture",
        label: t.architecture.title,
        icon: NetworkIcon,
        keywords: "architecture docs design",
      },
      {
        id: "observability",
        to: "/workspace/observability",
        label: t.observabilityPage.pageTitle,
        icon: ActivityIcon,
        keywords: "observability monitoring health",
      },
      {
        id: "diagnostics",
        to: "/workspace/observability",
        label: t.sidebar.diagnostics,
        icon: StethoscopeIcon,
        keywords: "diagnostics debug troubleshoot",
      },
      {
        id: "intelligence",
        to: "/workspace/intelligence",
        label: t.intelligence.title,
        icon: ZapIcon,
        keywords: "intelligence subscription automation schedule",
      },
      {
        id: "knowledge",
        to: "/workspace/knowledge",
        label: t.sidebar.navKnowledgeGraph,
        icon: BrainCircuitIcon,
        keywords: "knowledge base memory wiki files",
      },
      {
        id: "desktop-organizer",
        to: "/workspace/desktop-organizer",
        label: t.sidebar.navDesktopOrganizer,
        icon: FolderIcon,
        keywords: "desktop organizer folder",
      },
      {
        id: "evolution",
        to: "/workspace/evolution",
        label: t.evolutionDashboard.title,
        icon: DnaIcon,
        keywords: "evolution dna self",
      },
      {
        id: "reflex",
        to: "/workspace/reflex",
        label: t.reflexPage.pageTitle,
        icon: RadarIcon,
        keywords: "reflex rule monitor",
      },
      {
        id: "browser",
        to: BROWSER_WORKSPACE_ROUTE,
        label: t.sidebar.navBrowserSurface,
        icon: GlobeIcon,
        keywords: "browser ai tabs history bookmarks copilot",
      },
      {
        id: "computer",
        to: "/workspace/computer",
        label: t.agentWorkbench.computerView,
        icon: CpuIcon,
        keywords: "computer desktop automation",
      },
    ],
    [t],
  );

  if (!mounted) return null;

  return (
    <>
      {open && (
        <CommandDialog
          open={open}
          onOpenChange={setOpen}
          title={t.shortcuts.openCommandPalette}
          description={t.shortcuts.commandPaletteDescription}
        >
          <CommandInput placeholder={t.shortcuts.searchActions} />
          <CommandList>
            <CommandEmpty>{t.shortcuts.noResults}</CommandEmpty>
            <CommandGroup heading={t.shortcuts.actions}>
              <CommandItem onSelect={handleNewChat}>
                <MessageSquarePlusIcon className="mr-2 h-4 w-4" />
                {t.sidebar.newChat}
                <CommandShortcut>
                  {metaKey}
                  {shiftKey}N
                </CommandShortcut>
              </CommandItem>
              <CommandItem onSelect={handleOpenSettings}>
                <SettingsIcon className="mr-2 h-4 w-4" />
                {t.common.settings}
                <CommandShortcut>{metaKey},</CommandShortcut>
              </CommandItem>
              <CommandItem onSelect={handleShowShortcuts}>
                <KeyboardIcon className="mr-2 h-4 w-4" />
                {t.shortcuts.keyboardShortcuts}
                <CommandShortcut>{metaKey}/</CommandShortcut>
              </CommandItem>
              <CommandItem
                onSelect={() => {
                  navigate("/workspace/evolution");
                  setOpen(false);
                }}
              >
                <DnaIcon className="mr-2 h-4 w-4" />
                {t.evolutionDashboard.title}
              </CommandItem>
            </CommandGroup>
            <CommandGroup heading={t.shortcuts.pages}>
              {PAGE_ITEMS.map((item) => {
                const Icon = item.icon;
                return (
                  <CommandItem
                    key={item.id}
                    value={`${item.label} ${item.to} ${item.keywords}`}
                    onSelect={() => handleNavigate(item.to)}
                  >
                    <Icon className="mr-2 h-4 w-4" />
                    {item.label}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </CommandDialog>
      )}

      <Dialog open={shortcutsOpen} onOpenChange={setShortcutsOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t.shortcuts.keyboardShortcuts}</DialogTitle>
            <DialogDescription>
              {t.shortcuts.keyboardShortcutsDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            {[
              { keys: `${metaKey}K`, label: t.shortcuts.openCommandPalette },
              {
                keys: `${metaKey}${shiftKey}P`,
                label: t.shortcuts.openCommandPalette,
              },
              { keys: `${metaKey}${shiftKey}N`, label: t.sidebar.newChat },
              { keys: `${metaKey}B`, label: t.shortcuts.toggleSidebar },
              { keys: `${metaKey}J`, label: t.shortcuts.focusChatInput },
              { keys: `${metaKey}\\`, label: t.shortcuts.toggleRightPanel },
              { keys: `${metaKey},`, label: t.common.settings },
              {
                keys: `${metaKey}/`,
                label: t.shortcuts.keyboardShortcuts,
              },
            ].map(({ keys, label }) => (
              <div key={keys} className="flex items-center justify-between">
                <span className="text-muted-foreground">{label}</span>
                <kbd className="bg-muted text-muted-foreground rounded px-2 py-0.5 font-mono text-xs">
                  {keys}
                </kbd>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
