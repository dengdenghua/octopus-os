import {
  ActivityIcon,
  BellIcon,
  InfoIcon,
  BrainIcon,
  CpuIcon,
  LogOutIcon,
  PaletteIcon,
  ShieldIcon,
  UserIcon,
  ZapIcon,
  CreditCardIcon,
  ServerIcon,
  SettingsIcon,
  SearchIcon,
  XIcon,
  Globe2Icon,
  MessageSquareTextIcon,
  MonitorIcon,
} from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Suspense, lazy } from "react";
import { getSettingsUxCopy } from "./settings-ux-copy";
import AppearanceSettingsPage from "./appearance-settings-page";
import type { SettingsSection } from "./settings-sections";

export {
  SETTINGS_SECTIONS,
  normalizeSettingsSection,
  type SettingsSection,
} from "./settings-sections";

// Lazy load settings pages. Each ``import()`` is kept as a reusable factory
// so we can preload *all* chunks the moment the dialog opens — see
// ``preloadSettingsPages`` below. Without preloading, switching to a tab
// that hasn't been visited in this session triggered a fresh chunk
// download + Suspense fallback, which users correctly perceived as "every
// tab reloads".
const importAbout = () =>
  import("@/components/workspace/settings/about-settings-page");
const importAccount = () =>
  import("@/components/workspace/settings/account-settings-page");
const importMemory = () =>
  import("@/components/workspace/settings/memory-settings-page");
const importNotification = () =>
  import("@/components/workspace/settings/notification-settings-page");
const importModel = () =>
  import("@/components/workspace/settings/model-settings-page");
const importSubscription = () =>
  import("@/components/workspace/settings/subscription-settings-page");
const importPrivacy = () =>
  import("@/components/workspace/settings/privacy-settings-page");
const importAutomation = () =>
  import("@/components/workspace/settings/automation-settings-page");
const importAutomationSecurity = () =>
  import("@/components/workspace/settings/automation-security-settings-page");
const importBrowserAutomation = () =>
  import("@/components/workspace/settings/browser-automation-settings-page");
const importDesktopAutomation = () =>
  import("@/components/workspace/settings/desktop-automation-settings-page");
const importConversation = () =>
  import("@/components/workspace/settings/conversation-settings-page");
const importMcp = () =>
  import("@/components/workspace/settings/mcp-settings-page").then((mod) => ({
    default: mod.McpSettingsPage,
  }));

const AboutSettingsPage = lazy(importAbout);
const AccountSettingsPage = lazy(importAccount);
const MemorySettingsPage = lazy(importMemory);
const NotificationSettingsPage = lazy(importNotification);
const ModelSettingsPage = lazy(importModel);
const SubscriptionSettingsPage = lazy(importSubscription);
const PrivacySettingsPage = lazy(importPrivacy);
const AutomationSecuritySettingsPage = lazy(importAutomationSecurity);
const BrowserAutomationSettingsPage = lazy(importBrowserAutomation);
const DesktopAutomationSettingsPage = lazy(importDesktopAutomation);
const ConversationSettingsPage = lazy(importConversation);
const McpSettingsPage = lazy(importMcp);

// Run every chunk import in parallel the first time the dialog opens.
// Browsers dedupe the ``import()`` calls against cache, so repeated opens
// are no-ops. We deliberately swallow errors — a failed preload doesn't
// break anything; the per-tab Suspense fallback will still catch it on
// actual navigation.
let preloadStarted = false;
function preloadSettingsPages(): void {
  if (preloadStarted) return;
  preloadStarted = true;
  [
    importAbout,
    importAccount,
    importMemory,
    importNotification,
    importModel,
    importSubscription,
    importPrivacy,
    importAutomation,
    importAutomationSecurity,
    importBrowserAutomation,
    importDesktopAutomation,
    importConversation,
    importMcp,
  ].forEach((fn) => {
    fn().catch((e) => {
      swallow(e);
    });
  });
}

import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { useAuth } from "@/providers/AuthProvider";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";
import { octApi } from "@/core/oct/api";

type SettingsDialogProps = React.ComponentProps<typeof Dialog> & {
  defaultSection?: SettingsSection;
};

// Persist user-chosen dialog size across sessions so we don't reset on every
// open. localStorage is fine — the preference is per-browser, non-sensitive.
const DIALOG_SIZE_KEY = "echo_settings_dialog_size";
const MIN_W = 560;
const MIN_H = 360;

function readSavedSize(): { w: number; h: number } | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(DIALOG_SIZE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { w?: unknown; h?: unknown };
    if (typeof parsed.w === "number" && typeof parsed.h === "number") {
      return { w: parsed.w, h: parsed.h };
    }
  } catch (e) {
    swallow(e);
  }
  return null;
}

function isPlaceholderUsername(username?: string | null): boolean {
  const value = username?.trim().toLowerCase();
  return !value || value === "anonymous" || value === "__anonymous__";
}

function getAccountDisplayName(
  user: {
    mobile?: string;
    email?: string;
    username?: string;
    actor_id?: string;
  } | null,
): string | null {
  if (!user) return null;
  return (
    user.mobile ||
    user.email ||
    (!isPlaceholderUsername(user.username) ? user.username : "") ||
    user.actor_id ||
    null
  );
}

export function SettingsDialog(props: SettingsDialogProps) {
  const { defaultSection = "appearance", ...dialogProps } = props;
  const { t, locale } = useI18n();
  const settingsUxCopy = getSettingsUxCopy(locale);
  const navigate = useNavigate();
  const { user, logout, authStatus, isLoading } = useAuth();
  const accountName = getAccountDisplayName(user);
  const queryClient = useQueryClient();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(defaultSection);

  // Null = "let the Tailwind default sizing apply". Once the user drags the
  // corner handle we switch to an inline width/height so DialogContent grows
  // symmetrically around its translate-[-50%,-50%] center anchor.
  const [size, setSize] = useState<{ w: number; h: number } | null>(() =>
    readSavedSize(),
  );
  const resizeStartRef = useRef<{
    x: number;
    y: number;
    w: number;
    h: number;
  } | null>(null);
  const contentScrollRootRef = useRef<HTMLDivElement>(null);
  const sectionScrollRef = useRef<HTMLDivElement>(null);
  const [sectionScrollEdges, setSectionScrollEdges] = useState({
    before: false,
    after: false,
  });

  const updateSectionScrollEdges = useCallback(() => {
    const element = sectionScrollRef.current;
    if (!element) return;
    const maxScrollLeft = Math.max(
      0,
      element.scrollWidth - element.clientWidth,
    );
    setSectionScrollEdges({
      before: element.scrollLeft > 2,
      after: element.scrollLeft < maxScrollLeft - 2,
    });
  }, []);

  useEffect(() => {
    if (!dialogProps.open) return;
    // Each settings page can be much taller than the dialog. Never carry a
    // previous page's vertical position into the newly selected page: that
    // would make its heading appear clipped and suggests the tab failed to
    // switch. Wait one frame so the new Suspense child has mounted first.
    const frame = window.requestAnimationFrame(() => {
      const viewport = contentScrollRootRef.current?.querySelector<HTMLElement>(
        '[data-slot="scroll-area-viewport"]',
      );
      if (viewport) viewport.scrollTop = 0;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeSection, dialogProps.open]);

  const onResizeStart = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    const content = (e.currentTarget as HTMLElement).closest(
      '[data-slot="dialog-content"]',
    ) as HTMLElement | null;
    if (!content) return;
    const rect = content.getBoundingClientRect();
    resizeStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      w: rect.width,
      h: rect.height,
    };
    const onMove = (ev: MouseEvent) => {
      const start = resizeStartRef.current;
      if (!start) return;
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      // Dialog is center-anchored, so corner drag of dx px moves the right
      // edge by dx — we need to grow width by 2*dx to keep the corner under
      // the cursor.
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const nextW = Math.max(MIN_W, Math.min(vw - 32, start.w + dx * 2));
      const nextH = Math.max(MIN_H, Math.min(vh - 32, start.h + dy * 2));
      setSize({ w: nextW, h: nextH });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      resizeStartRef.current = null;
      // Persist the last value we pushed into state. We read via a setter
      // callback to avoid a stale closure over `size`.
      setSize((latest) => {
        if (latest) {
          try {
            window.localStorage.setItem(
              DIALOG_SIZE_KEY,
              JSON.stringify(latest),
            );
          } catch (e) {
            swallow(e, "storage");
          }
        }
        return latest;
      });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  useEffect(() => {
    // When opening the dialog, ensure the active section follows the caller's intent.
    // This allows triggers like "About" to open the dialog directly on that page.
    if (dialogProps.open) {
      setActiveSection(defaultSection);
      // Let the requested page paint first, then warm the remaining tabs while
      // the browser is idle. Starting every settings chunk synchronously made
      // the first dialog open compete with its own rendering work.
      const preload = () => preloadSettingsPages();
      const idleWindow = window as Window & {
        requestIdleCallback?: (
          callback: IdleRequestCallback,
          options?: IdleRequestOptions,
        ) => number;
        cancelIdleCallback?: (handle: number) => void;
      };
      const idleHandle = idleWindow.requestIdleCallback?.(preload, {
        timeout: 2_000,
      });
      const timerHandle =
        idleHandle === undefined ? window.setTimeout(preload, 500) : undefined;

      // Prefetch the oct account bridge; individual account tabs can fetch
      // their own optional data when opened.
      queryClient.prefetchQuery({
        queryKey: ["account", "oct"],
        queryFn: () => octApi.get().catch(() => null),
        staleTime: 30_000,
      });

      return () => {
        if (idleHandle !== undefined) {
          idleWindow.cancelIdleCallback?.(idleHandle);
        }
        if (timerHandle !== undefined) window.clearTimeout(timerHandle);
      };
    }
  }, [defaultSection, dialogProps.open, queryClient]);

  const handleLogout = async () => {
    try {
      await logout();
      toast.success(t.auth.logoutSuccess);
      dialogProps.onOpenChange?.(false);
      navigate("/");
    } catch {
      toast.error(t.auth.logoutFailed);
    }
  };

  const sections = useMemo(() => {
    type SectionGroup = "account" | "workspace" | "capabilities" | "system";
    type Section = {
      id: SettingsSection;
      label: string;
      icon: React.ComponentType<{ className?: string }>;
      keywords: string[];
      group: SectionGroup;
      disabled?: boolean;
      disabledReason?: string;
    };

    const toolsLabel = locale.toLowerCase().startsWith("zh")
      ? "工具与集成"
      : locale.toLowerCase().startsWith("ja")
        ? "ツールと連携"
        : locale.toLowerCase().startsWith("ko")
          ? "도구 및 통합"
          : "Tools & integrations";
    const automationSecurityLabel = locale.toLowerCase().startsWith("zh")
      ? "执行与安全"
      : locale.toLowerCase().startsWith("ja")
        ? "自動化とセキュリティ"
        : locale.toLowerCase().startsWith("ko")
          ? "자동화 및 보안"
          : "Automation & security";

    const all: Section[] = [
      {
        id: "account",
        group: "account",
        label: t.settings.sections.account,
        icon: SettingsIcon,
        keywords: [
          "account",
          "user",
          "login",
          "profile",
          ...t.settings.dialog.sectionKeywords.account,
        ],
      },
      {
        id: "subscription",
        group: "account",
        label: t.settings.sections.subscription,
        icon: CreditCardIcon,
        keywords: [
          "subscription",
          "billing",
          "plan",
          "usage",
          ...t.settings.dialog.sectionKeywords.subscription,
        ],
      },
      {
        id: "appearance",
        group: "workspace",
        label: t.settings.sections.general,
        icon: PaletteIcon,
        keywords: [
          "appearance",
          "theme",
          "material",
          "glass",
          "density",
          "language",
          ...t.settings.dialog.sectionKeywords.appearance,
        ],
      },
      {
        id: "conversation",
        group: "workspace",
        label: t.settings.sections.conversation,
        icon: MessageSquareTextIcon,
        keywords: [
          "conversation",
          "chat",
          "detail level",
          "font size",
          "对话",
          "细节",
          "字号",
          ...t.settings.dialog.sectionKeywords.appearance,
        ],
      },
      {
        id: "models",
        group: "workspace",
        label: t.settings.model.title,
        icon: CpuIcon,
        keywords: [
          "model",
          "llm",
          "api",
          "provider",
          ...t.settings.dialog.sectionKeywords.models,
        ],
      },
      {
        id: "memory",
        group: "workspace",
        label: t.settings.sections.memory,
        icon: BrainIcon,
        keywords: [
          "memory",
          "knowledge",
          ...t.settings.dialog.sectionKeywords.memory,
        ],
      },
      {
        id: "notification",
        group: "workspace",
        label: t.settings.sections.notification,
        icon: BellIcon,
        keywords: [
          "notification",
          "alert",
          ...t.settings.dialog.sectionKeywords.notification,
        ],
      },
      {
        id: "tools",
        group: "capabilities",
        label: toolsLabel,
        icon: ServerIcon,
        keywords: [
          "tools",
          "integrations",
          "mcp",
          "tool",
          "server",
          ...t.settings.dialog.sectionKeywords.mcp,
        ],
      },
      {
        id: "browserAutomation",
        group: "capabilities",
        label: locale.toLowerCase().startsWith("zh")
          ? "浏览器自动化"
          : "Browser automation",
        icon: Globe2Icon,
        keywords: [
          "browser",
          "relay",
          "extension",
          "link",
          "浏览器",
          "扩展",
          "链接",
          ...t.settings.dialog.sectionKeywords.automation,
        ],
      },
      {
        id: "desktopAutomation",
        group: "capabilities",
        label: locale.toLowerCase().startsWith("zh")
          ? "桌面自动化"
          : "Desktop automation",
        icon: MonitorIcon,
        keywords: [
          "desktop",
          "computer",
          "screen recording",
          "accessibility",
          "桌面",
          "电脑",
          "屏幕录制",
          "辅助功能",
          ...t.settings.dialog.sectionKeywords.automation,
        ],
      },
      {
        id: "automationSecurity",
        group: "capabilities",
        label: automationSecurityLabel,
        icon: ZapIcon,
        keywords: [
          "automation",
          "security",
          "sandbox",
          "execution",
          "permission",
          "browser",
          "desktop",
          "approval",
          "沙箱",
          "执行",
          "权限",
          "自动化",
          ...t.settings.dialog.sectionKeywords.automation,
          ...t.settings.dialog.sectionKeywords.sandbox,
        ],
      },
      {
        id: "privacy",
        group: "capabilities",
        label: t.settings.sections.privacy,
        icon: ShieldIcon,
        keywords: [
          "privacy",
          "permission",
          "security",
          ...t.settings.dialog.sectionKeywords.privacy,
        ],
      },
      {
        id: "observability",
        group: "system",
        label: t.settings.sections.observability,
        icon: ActivityIcon,
        keywords: [
          "observability",
          "diagnostics",
          "logs",
          ...t.settings.dialog.sectionKeywords.observability,
        ],
      },
      {
        id: "about",
        group: "system",
        label: t.settings.sections.about,
        icon: InfoIcon,
        keywords: [
          "about",
          "version",
          "help",
          ...t.settings.dialog.sectionKeywords.about,
        ],
      },
    ];

    return all;
  }, [
    t.settings.sections.account,
    t.settings.sections.subscription,
    t.settings.sections.general,
    t.settings.sections.conversation,
    t.settings.model.title,
    t.settings.sections.memory,
    locale,
    t.settings.sections.notification,
    t.settings.sections.about,
    t.settings.sections.observability,
    t.settings.sections.privacy,
    t.settings.dialog.sectionKeywords,
  ]);
  const [settingsQuery, setSettingsQuery] = useState("");
  const normalizedSettingsQuery = settingsQuery.trim().toLowerCase();
  const visibleSections = useMemo(() => {
    if (!normalizedSettingsQuery) return sections;
    return sections.filter((section) =>
      [section.id, section.label, ...section.keywords]
        .join(" ")
        .toLowerCase()
        .includes(normalizedSettingsQuery),
    );
  }, [normalizedSettingsQuery, sections]);
  const hasSettingsResults = visibleSections.length > 0;

  useEffect(() => {
    if (!dialogProps.open) return;
    const frame = window.requestAnimationFrame(updateSectionScrollEdges);
    window.addEventListener("resize", updateSectionScrollEdges);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updateSectionScrollEdges);
    if (sectionScrollRef.current) observer?.observe(sectionScrollRef.current);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updateSectionScrollEdges);
      observer?.disconnect();
    };
  }, [dialogProps.open, updateSectionScrollEdges, visibleSections.length]);

  const sectionGroupLabels = useMemo(() => {
    const isZh = locale.toLowerCase().startsWith("zh");
    const isJa = locale.toLowerCase().startsWith("ja");
    const isKo = locale.toLowerCase().startsWith("ko");
    return {
      account: isZh
        ? "账户与方案"
        : isJa
          ? "アカウントとプラン"
          : isKo
            ? "계정 및 요금제"
            : "Account & plan",
      workspace: isZh
        ? "体验与工作方式"
        : isJa
          ? "体験とワークフロー"
          : isKo
            ? "경험 및 작업 방식"
            : "Experience & workflow",
      capabilities: isZh
        ? "能力与安全"
        : isJa
          ? "機能とセキュリティ"
          : isKo
            ? "기능 및 보안"
            : "Capabilities & security",
      system: isZh
        ? "支持与诊断"
        : isJa
          ? "サポートと診断"
          : isKo
            ? "지원 및 진단"
            : "Support & diagnostics",
    } as const;
  }, [locale]);

  useEffect(() => {
    if (!dialogProps.open || !normalizedSettingsQuery || !hasSettingsResults) {
      return;
    }
    if (visibleSections.some((section) => section.id === activeSection)) {
      return;
    }
    const next = visibleSections.find((section) => !section.disabled);
    if (next) setActiveSection(next.id);
  }, [
    activeSection,
    dialogProps.open,
    hasSettingsResults,
    normalizedSettingsQuery,
    visibleSections,
  ]);

  return (
    <Dialog
      {...dialogProps}
      onOpenChange={(open) => props.onOpenChange?.(open)}
    >
      <DialogContent
        closeLabel={t.common.close}
        className="flex h-[min(760px,calc(100vh-2rem))] max-h-[calc(100vh-2rem)] flex-col sm:max-w-5xl md:max-w-6xl"
        style={
          size
            ? {
                width: size.w,
                height: size.h,
                maxWidth: "calc(100vw - 2rem)",
                maxHeight: "calc(100vh - 2rem)",
              }
            : undefined
        }
        aria-describedby={undefined}
      >
        <DialogHeader className="gap-0">
          {/* Inline the title and description so the header is one row instead
              of two. The description is redundant context once the dialog is
              open — we keep a muted copy for screen readers / first-time users. */}
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <DialogTitle className="leading-none">
              {t.settings.title}
            </DialogTitle>
            <p className="text-muted-foreground text-xs leading-none">
              {t.settings.description}
            </p>
          </div>
          {!isLoading && (user || authStatus?.enabled) ? (
            <div className="mt-2 flex items-center justify-between rounded-lg border bg-muted/30 px-2.5 py-1.5">
              <div className="flex min-w-0 items-center gap-2.5">
                <div className="relative flex size-7 items-center justify-center border border-border-default bg-background text-muted-foreground shadow-[var(--shadow-xs)]">
                  <UserIcon className="size-3.5" />
                  <span className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full border border-background bg-success" />
                </div>
                <div className="min-w-0 leading-tight">
                  <p className="text-sm font-medium">
                    {accountName ?? t.auth.notLoggedIn}
                  </p>
                  <p className="text-muted-foreground truncate text-xs">
                    {user?.email && user.email !== accountName
                      ? user.email
                      : t.auth.currentAccount}
                  </p>
                </div>
              </div>
              {user ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2.5 text-xs"
                  onClick={handleLogout}
                >
                  <LogOutIcon className="mr-1 size-3.5" />
                  {t.auth.logout}
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-2.5 text-xs"
                  onClick={() => {
                    dialogProps.onOpenChange?.(false);
                    navigate("/login");
                  }}
                >
                  <LogOutIcon className="mr-1 size-3.5" />
                  {t.auth.loginAccount}
                </Button>
              )}
            </div>
          ) : null}
        </DialogHeader>
        <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-2 md:grid-cols-[196px_1fr] md:grid-rows-[1fr]">
          <nav className="bg-sidebar flex min-h-0 flex-col overflow-hidden rounded-lg border p-1 md:max-h-none md:p-1.5">
            <div className="relative mb-2 hidden md:block">
              <SearchIcon className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2" />
              <Input
                value={settingsQuery}
                onChange={(event) => setSettingsQuery(event.target.value)}
                placeholder={t.settings.dialog.searchPlaceholder}
                aria-label={t.settings.dialog.searchPlaceholder}
                className="h-7 rounded-md pl-8 pr-8 text-xs"
              />
              {settingsQuery ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="absolute right-0.5 top-1/2 size-7 -translate-y-1/2 opacity-60 hover:opacity-100"
                  onClick={() => setSettingsQuery("")}
                  aria-label={t.settings.dialog.clearSearch}
                >
                  <XIcon className="size-3.5" />
                </Button>
              ) : null}
            </div>
            <div className="text-muted-foreground mb-0.5 hidden items-center justify-between px-1.5 text-[11px] font-medium uppercase md:flex">
              <span>{t.settings.dialog.sectionsLabel}</span>
              {normalizedSettingsQuery ? (
                <span>
                  {t.settings.dialog.resultsCount(visibleSections.length)}
                </span>
              ) : null}
            </div>
            <div className="relative min-h-0 flex-1">
              <div
                ref={sectionScrollRef}
                data-testid="settings-section-scroll"
                onScroll={updateSectionScrollEdges}
                className="h-full min-h-0 overflow-x-auto overflow-y-hidden overscroll-x-contain scroll-smooth [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:overflow-x-hidden md:overflow-y-auto md:[scrollbar-width:thin]"
              >
                <ul className="flex w-max min-w-full flex-nowrap gap-0.5 pr-6 md:block md:w-full md:min-w-0 md:space-y-1 md:pr-0">
                  {visibleSections.map(
                    (
                      {
                        id,
                        label,
                        icon: Icon,
                        group,
                        disabled,
                        disabledReason,
                      },
                      index,
                    ) => {
                      const active = activeSection === id;
                      return (
                        <Fragment key={id}>
                          {index === 0 ||
                          visibleSections[index - 1]?.group !== group ? (
                            <li
                              aria-hidden="true"
                              className="hidden items-center gap-2 px-2 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80 first:pt-0 md:flex"
                            >
                              <span className="h-px flex-1 bg-border-subtle" />
                              <span>{sectionGroupLabels[group]}</span>
                              <span className="h-px flex-1 bg-border-subtle" />
                            </li>
                          ) : null}
                          <li className="shrink-0 md:w-full">
                            <button
                              type="button"
                              onClick={() => {
                                if (disabled) return;
                                setActiveSection(id as SettingsSection);
                              }}
                              disabled={disabled}
                              title={disabled ? disabledReason : undefined}
                              aria-disabled={disabled || undefined}
                              aria-current={active ? "page" : undefined}
                              className={cn(
                                // Keep the nav quiet: one active tint and a
                                // slim leading accent are enough for hierarchy.
                                "group/sec relative flex h-9 w-auto min-w-max items-center gap-1.5 rounded-md px-2.5 text-xs transition-[opacity,background-color] md:h-auto md:min-h-0 md:w-full md:gap-2 md:py-1.5 md:text-sm",
                                disabled
                                  ? "cursor-not-allowed opacity-40"
                                  : "opacity-75 hover:opacity-100 hover:bg-muted/50",
                                active &&
                                  "bg-primary/10 text-primary opacity-100 after:absolute after:bottom-0 after:left-2.5 after:right-2.5 after:h-[2px] after:rounded-t after:bg-primary/75 md:bg-[color:color-mix(in_oklch,var(--sidebar-accent)_70%,transparent)] md:text-foreground md:after:hidden md:before:absolute md:before:bottom-1.5 md:before:left-0 md:before:top-1.5 md:before:w-[2px] md:before:rounded-r md:before:bg-primary/70",
                              )}
                            >
                              <Icon className="size-4" />
                              <span className="flex-1 truncate text-left">
                                {label}
                              </span>
                              {disabled && (
                                <span className="rounded border border-border-default px-1 py-0.5 text-xs font-medium leading-none text-muted-foreground">
                                  {t.common.guest}
                                </span>
                              )}
                            </button>
                          </li>
                        </Fragment>
                      );
                    },
                  )}
                </ul>
                {!hasSettingsResults ? (
                  <div className="text-muted-foreground px-2 py-8 text-center text-xs">
                    {t.settings.dialog.noSearchResultsTitle}
                  </div>
                ) : null}
              </div>
              {sectionScrollEdges.before ? (
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-y-0 left-0 z-10 w-7 bg-gradient-to-r from-sidebar via-sidebar/85 to-transparent md:hidden"
                />
              ) : null}
              {sectionScrollEdges.after ? (
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-y-0 right-0 z-10 w-7 bg-gradient-to-l from-sidebar via-sidebar/85 to-transparent md:hidden"
                />
              ) : null}
            </div>
          </nav>
          <ScrollArea
            ref={contentScrollRootRef}
            className="h-full min-h-0 min-w-0 rounded-lg border after:pointer-events-none after:absolute after:inset-x-1 after:bottom-1 after:z-10 after:h-5 after:rounded-b-md after:bg-gradient-to-t after:from-background/85 after:to-transparent [&_[data-slot=scroll-area-viewport]>div]:!block [&_[data-slot=scroll-area-viewport]>div]:!w-full [&_[data-slot=scroll-area-viewport]>div]:!min-w-0"
          >
            <div className="w-full min-w-0 max-w-full space-y-4 overflow-x-hidden p-3 sm:p-4">
              {/* Each tab gets its own Suspense boundary so switching
                  to an uncached tab doesn't blank out the currently
                  mounted one. Combined with preloadSettingsPages() this
                  effectively removes the "every tab reloads" flicker
                  users were seeing. */}
              {!hasSettingsResults ? (
                <Empty className="min-h-[420px] border-0">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <SearchIcon />
                    </EmptyMedia>
                    <EmptyTitle>
                      {t.settings.dialog.noSearchResultsTitle}
                    </EmptyTitle>
                    <EmptyDescription>
                      {t.settings.dialog.noSearchResultsDescription}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : null}
              {hasSettingsResults && activeSection === "account" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <AccountSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "subscription" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <SubscriptionSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "appearance" && (
                <AppearanceSettingsPage />
              )}
              {hasSettingsResults && activeSection === "conversation" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <ConversationSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "models" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <ModelSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "memory" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <MemorySettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "tools" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <McpSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "automationSecurity" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <AutomationSecuritySettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "browserAutomation" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <BrowserAutomationSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "desktopAutomation" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <DesktopAutomationSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "privacy" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <PrivacySettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "notification" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <NotificationSettingsPage />
                </Suspense>
              )}
              {hasSettingsResults && activeSection === "observability" && (
                <section
                  aria-labelledby="settings-observability-title"
                  className="space-y-6"
                >
                  <header className="space-y-1.5">
                    <h2
                      id="settings-observability-title"
                      className="text-lg font-semibold tracking-tight"
                    >
                      {settingsUxCopy.observability.title}
                    </h2>
                    <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
                      {settingsUxCopy.observability.description}
                    </p>
                  </header>
                  <div className="grid gap-3 sm:grid-cols-3">
                    {[
                      {
                        icon: ActivityIcon,
                        title: settingsUxCopy.observability.activityTitle,
                        description:
                          settingsUxCopy.observability.activityDescription,
                      },
                      {
                        icon: ZapIcon,
                        title: settingsUxCopy.observability.tracesTitle,
                        description:
                          settingsUxCopy.observability.tracesDescription,
                      },
                      {
                        icon: SettingsIcon,
                        title: settingsUxCopy.observability.healthTitle,
                        description:
                          settingsUxCopy.observability.healthDescription,
                      },
                    ].map((item) => {
                      const Icon = item.icon;
                      return (
                        <div
                          key={item.title}
                          className="rounded-lg border border-border bg-muted/20 p-4"
                        >
                          <Icon
                            aria-hidden="true"
                            className="mb-3 size-4 text-primary"
                          />
                          <h3 className="text-sm font-medium">{item.title}</h3>
                          <p className="mt-1 text-xs leading-5 text-muted-foreground">
                            {item.description}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                  <Button
                    className="w-full sm:w-auto"
                    onClick={() => {
                      dialogProps.onOpenChange?.(false);
                      navigate("/workspace/observability");
                    }}
                  >
                    {settingsUxCopy.observability.openDashboard}
                  </Button>
                </section>
              )}
              {hasSettingsResults && activeSection === "about" && (
                <Suspense fallback={<SettingsPageSkeleton />}>
                  <AboutSettingsPage />
                </Suspense>
              )}
            </div>
          </ScrollArea>
        </div>
        {/* Resize handle — sits in the bottom-right corner. We keep the
            clickable area a bit bigger than the visual glyph so it's easy
            to grab without overlapping close-button or scrollbars. */}
        <div
          onMouseDown={onResizeStart}
          // Keyboard fallback: arrow keys grow/shrink the dialog so
          // keyboard-only and screen-reader users can resize too.
          // Shift = 4× step. Home/End snaps to min/default.
          onKeyDown={(e) => {
            const step = e.shiftKey ? 64 : 16;
            const current = size ?? {
              w: MIN_W + 200,
              h: MIN_H + 200,
            };
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const maxW = vw - 32;
            const maxH = vh - 32;
            let nextW = current.w;
            let nextH = current.h;
            switch (e.key) {
              case "ArrowRight":
                nextW = Math.min(maxW, current.w + step);
                break;
              case "ArrowLeft":
                nextW = Math.max(MIN_W, current.w - step);
                break;
              case "ArrowDown":
                nextH = Math.min(maxH, current.h + step);
                break;
              case "ArrowUp":
                nextH = Math.max(MIN_H, current.h - step);
                break;
              case "Home":
                nextW = MIN_W;
                nextH = MIN_H;
                break;
              case "End":
                nextW = Math.min(maxW, 1280);
                nextH = Math.min(maxH, 800);
                break;
              default:
                return;
            }
            e.preventDefault();
            const next = { w: nextW, h: nextH };
            setSize(next);
            try {
              window.localStorage.setItem(
                DIALOG_SIZE_KEY,
                JSON.stringify(next),
              );
            } catch (err) {
              swallow(err, "storage");
            }
          }}
          role="separator"
          aria-orientation="vertical"
          aria-label={t.settings.dialog.dragToResize}
          aria-valuenow={size ? Math.round(size.w) : undefined}
          aria-valuemin={MIN_W}
          aria-valuemax={
            typeof window !== "undefined" ? window.innerWidth - 32 : undefined
          }
          title={t.settings.dialog.dragToResize}
          tabIndex={0}
          className="absolute bottom-0 right-0 z-50 hidden size-5 cursor-nwse-resize items-end justify-end rounded-sm p-1 text-muted-foreground/40 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 md:flex"
        >
          <svg viewBox="0 0 10 10" className="size-2.5" aria-hidden="true">
            <path
              d="M0 10 L10 0 M4 10 L10 4 M8 10 L10 8"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
              fill="none"
            />
          </svg>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// Skeleton loader for settings pages
function SettingsPageSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-96" />
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    </div>
  );
}
