import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Code2,
  Download,
  FolderPlus,
  Languages,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Star,
  Store,
  Trash2,
  Wand2,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import type { BrowserExtensionInfo } from "@/types/electron";

type ExtensionCategory =
  | "featured"
  | "efficiency"
  | "research"
  | "security"
  | "development"
  | "comingSoon";

type ExtensionListing = {
  id: string;
  name: string;
  tagline: string;
  category: Exclude<ExtensionCategory, "featured">;
  icon: typeof Sparkles;
  accent: string;
  rating: string;
  installs: string;
  status?: "coming-soon";
  tags: string[];
};

const STORAGE_KEY = "echo-browser-extension-marketplace";

const categories: ExtensionCategory[] = [
  "featured",
  "efficiency",
  "research",
  "security",
  "development",
  "comingSoon",
];

const catalog: ExtensionListing[] = [
  {
    id: "echo-page-agent",
    name: "Page Agent",
    tagline: "Turn the current page into executable task context",
    category: "efficiency",
    icon: Wand2,
    accent: "from-sky-400 to-blue-600",
    rating: "4.9",
    installs: "12K",
    tags: ["Automation", "Web Operation", "AI"],
  },
  {
    id: "research-clipper",
    name: "Research Clipper",
    tagline: "Collect web snippets, sources, and screenshots",
    category: "research",
    icon: BookOpen,
    accent: "from-success to-teal-600",
    rating: "4.8",
    installs: "8.4K",
    tags: ["Library", "Citation", "Screenshot"],
  },
  {
    id: "shield-lite",
    name: "Shield Lite",
    tagline: "Lightweight blocking of intrusive elements and tracking scripts",
    category: "security",
    icon: ShieldCheck,
    accent: "from-destructive to-destructive",
    rating: "4.7",
    installs: "18K",
    tags: ["Privacy", "Blocking", "Security"],
  },
  {
    id: "cookie-vault",
    name: "Cookie Vault",
    tagline: "Save isolated sessions for test accounts",
    category: "security",
    icon: LockKeyhole,
    accent: "from-warning to-orange-500",
    rating: "4.6",
    installs: "5.2K",
    tags: ["Session", "Testing", "Isolation"],
  },
  {
    id: "translator-lens",
    name: "Translator Lens",
    tagline: "Hover-translate selected text and page regions",
    category: "efficiency",
    icon: Languages,
    accent: "from-violet-400 to-fuchsia-600",
    rating: "4.8",
    installs: "21K",
    tags: ["Translation", "Reading", "Hover"],
  },
  {
    id: "dom-inspector",
    name: "DOM Inspector",
    tagline: "Directly inspect elements, selectors, and accessibility tree",
    category: "development",
    icon: Code2,
    accent: "from-muted-foreground to-foreground",
    rating: "4.9",
    installs: "9.1K",
    tags: ["Debug", "Selector", "Accessibility"],
  },
  {
    id: "visual-recorder",
    name: "Visual Recorder",
    tagline: "Record click paths and generate automation steps",
    category: "comingSoon",
    icon: Sparkles,
    accent: "from-cyan-400 to-indigo-600",
    rating: "New",
    installs: "Beta",
    status: "coming-soon",
    tags: ["Recording", "Playback", "Workflow"],
  },
];

const featuredListing = catalog[0]!;

const readInstalled = () => {
  if (typeof window === "undefined") return new Set<string>();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as string[]) : [];
    return new Set(parsed);
  } catch (e) {
    swallow(e);
    return new Set<string>();
  }
};

export function ExtensionMarketplace({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const em = t.browser.extensionMarketplace;

  const categoryLabels = useMemo<Record<ExtensionCategory, string>>(
    () => ({
      featured: em.categoryFeatured,
      efficiency: em.categoryEfficiency,
      research: em.categoryResearch,
      security: em.categorySecurity,
      development: em.categoryDevelopment,
      comingSoon: em.categoryComingSoon,
    }),
    [em],
  );

  const taglineLabels = useMemo<Record<string, string>>(
    () => ({
      "echo-page-agent": em.taglinePageAgent,
      "research-clipper": em.taglineResearchClipper,
      "shield-lite": em.taglineShieldLite,
      "cookie-vault": em.taglineCookieVault,
      "translator-lens": em.taglineTranslatorLens,
      "dom-inspector": em.taglineDomInspector,
      "visual-recorder": em.taglineVisualRecorder,
    }),
    [em],
  );

  const tagLabels = useMemo<Record<string, string[]>>(
    () => ({
      "echo-page-agent": em.tagsPageAgent,
      "research-clipper": em.tagsResearchClipper,
      "shield-lite": em.tagsShieldLite,
      "cookie-vault": em.tagsCookieVault,
      "translator-lens": em.tagsTranslatorLens,
      "dom-inspector": em.tagsDomInspector,
      "visual-recorder": em.tagsVisualRecorder,
    }),
    [em],
  );

  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<ExtensionCategory>("featured");
  const [selectedId, setSelectedId] = useState(featuredListing.id);
  const [installed, setInstalled] = useState<Set<string>>(() =>
    readInstalled(),
  );
  const [browserExtensions, setBrowserExtensions] = useState<
    BrowserExtensionInfo[]
  >([]);
  const [extensionError, setExtensionError] = useState<string | null>(null);
  const [extensionBusy, setExtensionBusy] = useState(false);
  const { confirm, confirmDialog } = useConfirmDialog();
  const isElectron =
    typeof window !== "undefined" && window.echo?.isElectron === true;

  const refreshBrowserExtensions = useCallback(async () => {
    if (!window.echo?.extensions) {
      setBrowserExtensions([]);
      return;
    }
    const result = await window.echo.extensions.list();
    if (result.ok) {
      setBrowserExtensions(result.extensions);
      setExtensionError(null);
    } else {
      setExtensionError(result.error || em.errorListFailed);
    }
  }, [em]);

  const installBrowserExtension = async () => {
    if (!window.echo?.extensions) return;
    setExtensionBusy(true);
    try {
      const result = await window.echo.extensions.installFromFolder();
      if (!result.ok && !result.canceled) {
        setExtensionError(result.error || em.errorInstallFailed);
      }
      await refreshBrowserExtensions();
    } finally {
      setExtensionBusy(false);
    }
  };

  const setBrowserExtensionEnabled = async (id: string, enabled: boolean) => {
    if (!window.echo?.extensions) return;
    setExtensionBusy(true);
    try {
      const result = await window.echo.extensions.setEnabled(id, enabled);
      if (!result.ok) setExtensionError(result.error || em.errorStatusFailed);
      await refreshBrowserExtensions();
    } finally {
      setExtensionBusy(false);
    }
  };

  const removeBrowserExtension = async (id: string) => {
    if (!window.echo?.extensions) return;
    const ok = await confirm({
      title: t.common.delete,
      description: em.confirmRemove,
    });
    if (!ok) return;
    setExtensionBusy(true);
    try {
      const result = await window.echo.extensions.remove(id);
      if (!result.ok) setExtensionError(result.error || em.errorRemoveFailed);
      await refreshBrowserExtensions();
    } finally {
      setExtensionBusy(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    setInstalled(readInstalled());
    void refreshBrowserExtensions();
  }, [open, refreshBrowserExtensions]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onOpenChange, open]);

  const visibleListings = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return catalog.filter((item) => {
      const matchesCategory =
        category === "featured" ||
        item.category === category ||
        (category === "comingSoon" && item.status === "coming-soon");
      const matchesQuery =
        !normalizedQuery ||
        [item.name, item.tagline, item.category, ...item.tags]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery);
      return matchesCategory && matchesQuery;
    });
  }, [category, query]);

  useEffect(() => {
    if (!visibleListings.length) return;
    if (!visibleListings.some((item) => item.id === selectedId)) {
      setSelectedId(visibleListings[0]!.id);
    }
  }, [selectedId, visibleListings]);

  const selected: ExtensionListing =
    catalog.find((item) => item.id === selectedId) ??
    visibleListings[0] ??
    featuredListing;
  const isInstalled = installed.has(selected.id);

  const install = (id: string) => {
    const listing = catalog.find((item) => item.id === id);
    if (!listing || listing.status === "coming-soon") return;
    setInstalled((current) => {
      const next = new Set(current);
      next.add(id);
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
      return next;
    });
  };

  if (!open) return null;

  const SelectedIcon = selected.icon;
  const selectedTagline = taglineLabels[selected.id] ?? selected.tagline;
  const selectedTags = tagLabels[selected.id] ?? selected.tags;

  return (
    <div className="fixed inset-0 z-50 bg-background/72 backdrop-blur-xl">
      <div className="flex h-full flex-col overflow-hidden">
        <header className="flex shrink-0 items-center gap-3 border-b border-border-default bg-background/72 px-5 py-4 shadow-[var(--shadow-xs)]">
          <div className="flex size-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-[var(--shadow-xs)]">
            <Store className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-foreground">
              {em.title}
            </h2>
            <p className="text-xs text-muted-foreground">{em.subtitle}</p>
          </div>
          <Button
            disabled={!isElectron || extensionBusy}
            variant="secondary"
            onClick={installBrowserExtension}
          >
            <FolderPlus className="size-4" />
            {em.installLocal}
          </Button>
          <Button
            aria-label={em.refreshAriaLabel}
            disabled={!isElectron || extensionBusy}
            size="icon"
            variant="ghost"
            onClick={refreshBrowserExtensions}
          >
            <RefreshCw
              className={cn("size-4", extensionBusy && "animate-spin")}
            />
          </Button>
          <Button
            aria-label={em.closeAriaLabel}
            size="icon"
            variant="ghost"
            onClick={() => onOpenChange(false)}
          >
            <X className="size-4" />
          </Button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(360px,480px)_1fr]">
          <aside className="flex min-h-0 flex-col border-b border-border-default bg-muted/18 lg:border-b-0 lg:border-r">
            <div className="space-y-3 border-b border-border-default p-4">
              <label className="flex h-10 items-center gap-2 rounded-2xl border border-border-strong bg-background/86 px-3 shadow-[var(--shadow-xs)] focus-within:border-primary/40 focus-within:ring-4 focus-within:ring-primary/10">
                <Search className="size-4 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={em.searchPlaceholder}
                  className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                />
              </label>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {categories.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setCategory(item)}
                    className={cn(
                      "h-8 shrink-0 rounded-full border px-3 text-xs font-medium transition",
                      category === item
                        ? "border-primary/30 bg-primary text-primary-foreground shadow-[var(--shadow-xs)]"
                        : "border-border-default bg-background/76 text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    {categoryLabels[item]}
                  </button>
                ))}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              <div className="grid gap-2">
                {visibleListings.map((item) => {
                  const Icon = item.icon;
                  const active = selected.id === item.id;
                  const itemInstalled = installed.has(item.id);
                  const itemTagline = taglineLabels[item.id] ?? item.tagline;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      className={cn(
                        "group grid grid-cols-[44px_1fr_auto] items-center gap-3 rounded-2xl border p-3 text-left transition",
                        active
                          ? "border-primary/35 bg-background shadow-[var(--shadow-xs)] ring-4 ring-primary/10"
                          : "border-transparent bg-background/58 hover:border-border-default hover:bg-background/90",
                      )}
                    >
                      <span
                        className={cn(
                          "flex size-11 items-center justify-center rounded-2xl bg-gradient-to-br text-white shadow-[var(--shadow-xs)]",
                          item.accent,
                        )}
                      >
                        <Icon className="size-5" />
                      </span>
                      <span className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm font-semibold text-foreground">
                            {item.name}
                          </span>
                          {itemInstalled && (
                            <CheckCircle2 className="size-3.5 shrink-0 text-primary" />
                          )}
                        </span>
                        <span className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                          {itemTagline}
                        </span>
                      </span>
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Star className="size-3 fill-current" />
                        {item.rating}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </aside>

          <main className="min-h-0 overflow-y-auto bg-background">
            <section className="border-b border-border-default px-5 py-5 md:px-8">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-foreground">
                    {em.installedExtensions}
                  </h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {isElectron ? em.electronSupported : em.webPreviewOnly}
                  </p>
                </div>
                <Badge variant={isElectron ? "secondary" : "outline"}>
                  {isElectron ? "Electron" : em.webPreview}
                </Badge>
              </div>

              {extensionError && (
                <div className="mb-3 flex items-start gap-2 rounded-2xl border border-destructive/30 bg-destructive/8 px-3 py-2 text-xs text-destructive">
                  <AlertCircle className="mt-0.5 size-4 shrink-0" />
                  <span>{extensionError}</span>
                </div>
              )}

              {browserExtensions.length ? (
                <div className="grid gap-2">
                  {browserExtensions.map((item) => (
                    <div
                      key={item.id}
                      className="grid grid-cols-[1fr_auto] items-center gap-3 rounded-2xl border border-border-default bg-muted/18 p-3"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-semibold">
                            {item.name}
                          </span>
                          <Badge variant={item.enabled ? "default" : "outline"}>
                            {item.enabled ? em.enabled : em.disabled}
                          </Badge>
                        </div>
                        <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                          {item.description || item.path}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          disabled={extensionBusy}
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            setBrowserExtensionEnabled(item.id, !item.enabled)
                          }
                        >
                          {item.enabled ? em.disabled : em.enabled}
                        </Button>
                        <Button
                          aria-label={em.removeAriaLabel(item.name)}
                          disabled={extensionBusy}
                          size="icon-sm"
                          variant="ghost"
                          onClick={() => removeBrowserExtension(item.id)}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-border-strong bg-muted/14 p-5 text-sm text-muted-foreground">
                  {isElectron ? em.noExtensionsElectron : em.noExtensionsWeb}
                </div>
              )}
            </section>

            <section className="relative overflow-hidden border-b border-border-default">
              <div
                className={cn(
                  "absolute inset-0 opacity-18 blur-3xl bg-gradient-to-br",
                  selected.accent,
                )}
              />
              <div className="relative px-5 py-6 md:px-8 md:py-8">
                <div className="flex flex-col gap-5 md:flex-row md:items-start">
                  <div
                    className={cn(
                      "flex size-20 items-center justify-center rounded-4xl bg-gradient-to-br text-white shadow-xl",
                      selected.accent,
                    )}
                  >
                    <SelectedIcon className="size-9" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <Badge variant="secondary">
                        {categoryLabels[selected.category as ExtensionCategory]}
                      </Badge>
                      {selected.status === "coming-soon" ? (
                        <Badge variant="outline">{em.comingSoonBadge}</Badge>
                      ) : isInstalled ? (
                        <Badge variant="default">{em.installedBadge}</Badge>
                      ) : (
                        <Badge variant="outline">{em.installableBadge}</Badge>
                      )}
                    </div>
                    <h3 className="text-3xl font-semibold text-foreground">
                      {selected.name}
                    </h3>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                      {selectedTagline}
                    </p>
                  </div>
                  <Button
                    disabled={selected.status === "coming-soon" || isInstalled}
                    onClick={() => install(selected.id)}
                    className="mt-1 self-start"
                  >
                    {isInstalled ? (
                      <CheckCircle2 className="size-4" />
                    ) : (
                      <Download className="size-4" />
                    )}
                    {selected.status === "coming-soon"
                      ? em.categoryComingSoon
                      : isInstalled
                        ? em.installedBadge
                        : em.install}
                  </Button>
                </div>
              </div>
            </section>

            <section className="grid gap-5 px-5 py-6 md:px-8">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="rounded-2xl border border-border-default bg-muted/24 p-4">
                  <div className="text-xs text-muted-foreground">
                    {em.rating}
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-xl font-semibold">
                    <Star className="size-4 fill-current text-warning" />
                    {selected.rating}
                  </div>
                </div>
                <div className="rounded-2xl border border-border-default bg-muted/24 p-4">
                  <div className="text-xs text-muted-foreground">
                    {em.installs}
                  </div>
                  <div className="mt-2 text-xl font-semibold">
                    {selected.installs}
                  </div>
                </div>
                <div className="rounded-2xl border border-border-default bg-muted/24 p-4">
                  <div className="text-xs text-muted-foreground">
                    {em.status}
                  </div>
                  <div className="mt-2 text-xl font-semibold">
                    {selected.status === "coming-soon"
                      ? em.comingSoonBadge
                      : isInstalled
                        ? em.installedBadge
                        : em.installableBadge}
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-border-default bg-muted/18 p-5">
                <h4 className="text-sm font-semibold text-foreground">
                  {em.capabilityTags}
                </h4>
                <div className="mt-3 flex flex-wrap gap-2">
                  {selectedTags.map((tag) => (
                    <Badge key={tag} variant="outline">
                      {tag}
                    </Badge>
                  ))}
                </div>
              </div>
            </section>
          </main>
        </div>
      </div>
      {confirmDialog}
    </div>
  );
}
