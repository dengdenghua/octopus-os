import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  AtSign,
  Award,
  Banknote,
  BarChart3,
  Beaker,
  Binary,
  BookMarked,
  BookOpen,
  Bot,
  Boxes,
  Brain,
  BriefcaseBusiness,
  Brush,
  Bug,
  Calculator,
  CalendarCheck,
  Camera,
  CheckCircle2,
  CheckSquare,
  ClipboardCheck,
  Clock,
  Cloud,
  CloudCog,
  Code2,
  Cog,
  Coins,
  Compass,
  Component,
  Container,
  Cpu,
  CreditCard,
  Database,
  DollarSign,
  Eye,
  FileBadge,
  FileBarChart,
  FileCheck,
  FileCode,
  FileSpreadsheet,
  FileText,
  Film,
  Fingerprint,
  Flag,
  FlaskConical,
  GitBranch,
  GitCommit,
  Globe,
  GraduationCap,
  Handshake,
  HardDrive,
  Hash,
  Image,
  Inbox,
  Key,
  Layers,
  LayoutDashboard,
  LayoutTemplate,
  Library,
  Lightbulb,
  LineChart,
  ListTodo,
  Loader2,
  Lock,
  Mail,
  Medal,
  Megaphone,
  MessagesSquare,
  Mic,
  Microscope,
  Milestone,
  Monitor,
  Music,
  Network,
  Package,
  Palette,
  PenLine,
  Pencil,
  PencilRuler,
  PieChart,
  PiggyBank,
  Plus,
  Presentation,
  Puzzle,
  Radar,
  Scale,
  ScanLine,
  Scroll,
  ScrollText,
  Search,
  SearchCheck,
  Send,
  Server,
  Settings2,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShoppingBag,
  ShoppingCart,
  Sparkles,
  Star,
  Store,
  Table,
  Tag,
  Target,
  Terminal,
  TestTube,
  Timer,
  TrendingUp,
  Type,
  UserCheck,
  Users,
  Video,
  Wallet,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { useEnableSkill, useSkills } from "@/core/skills/hooks";
import { cn } from "@/lib/utils";
import {
  type LocalSkill,
  LOCAL_SKILL_CATEGORIES,
  StoreErrorState,
  classifyLocalSkill,
  searchableSkillText,
  useLocalSkillCategoryLabel,
} from "./store-utils";

const CATEGORY_ICON_POOL: Record<string, LucideIcon[]> = {
  "browser-search": [SearchCheck, Globe, Compass, Radar, ScanLine],
  "agent-tools": [Bot, Cpu, Puzzle, Workflow, Brain],
  "webapp-frontend": [Layers, LayoutDashboard, Component, Monitor, Globe],
  "backend-api": [Server, Database, Network, CloudCog, GitBranch],
  "code-quality": [Code2, GitCommit, Bug, ShieldCheck, FileCode],
  "devops-cloud": [Cloud, Server, Container, HardDrive, Terminal],
  "office-docs": [
    FileText,
    FileSpreadsheet,
    FileCheck,
    ClipboardCheck,
    ScrollText,
  ],
  "slides-report": [Presentation, Monitor, LayoutTemplate, Image, Star],
  "chart-viz": [BarChart3, FileBarChart, LineChart, PieChart, Activity],
  "writing-editing": [PenLine, Pencil, PencilRuler, Type, BookOpen],
  "marketing-copy": [Megaphone, Sparkles, Tag, Target, Send],
  "seo-growth": [TrendingUp, Search, ArrowUpRight, Hash, Eye],
  ecommerce: [ShoppingCart, ShoppingBag, Store, CreditCard, Package],
  "market-product": [
    BriefcaseBusiness,
    Handshake,
    TrendingUp,
    ShoppingCart,
    Target,
    Medal,
  ],
  "project-goal": [CheckCircle2, Flag, Target, Milestone, ListTodo],
  "finance-stock": [Wallet, Banknote, DollarSign, Coins, TrendingUp],
  "finance-model": [Calculator, FileSpreadsheet, Banknote, PiggyBank, Coins],
  "data-stats": [BarChart3, FileBarChart, Table, Binary, Activity],
  "data-insight": [LineChart, Activity, Eye, Lightbulb, Microscope],
  "academic-paper": [GraduationCap, BookOpen, BookMarked, Library, Scroll],
  "deep-research": [SearchCheck, Microscope, FlaskConical, Beaker, TestTube],
  "education-coach": [GraduationCap, BookOpen, Lightbulb, Star, Award],
  "hr-career": [Users, UserCheck, BriefcaseBusiness, Award, Medal],
  "email-comms": [Mail, Send, MessagesSquare, Inbox, AtSign],
  "legal-compliance": [Scale, Shield, ShieldCheck, ScrollText, FileBadge],
  "security-audit": [ShieldAlert, Lock, Fingerprint, Key, Bug],
  "design-creative": [Palette, Brush, PencilRuler, Image, Layers],
  "media-audio-video": [Video, Film, Music, Mic, Camera],
  "personal-productivity": [CheckSquare, Timer, Clock, ListTodo, CalendarCheck],
  other: [Wrench, Cog, Settings2, Puzzle, Sparkles],
};

function hashString(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (h << 5) - h + str.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h);
}

function getSkillIcon(category: string, skillName: string): LucideIcon {
  const pool = CATEGORY_ICON_POOL[category] ??
    CATEGORY_ICON_POOL.other ?? [Wrench];
  return pool[hashString(skillName) % pool.length] ?? pool[0] ?? Wrench;
}

type LocalSkillDirectoryPanelProps = {
  searchQuery?: string;
  allButtonPosition?: "start" | "end";
  onDirectorySelect?: () => void;
  onSkillPacksSelect?: () => void;
  skillPacksContent?: ReactNode;
  skillPacksSelected?: boolean;
};

export function LocalSkillDirectoryPanel({
  searchQuery: externalSearchQuery,
  allButtonPosition = "start",
  onDirectorySelect,
  onSkillPacksSelect,
  skillPacksContent,
  skillPacksSelected = false,
}: LocalSkillDirectoryPanelProps = {}) {
  const { t } = useI18n();
  const categoryLabel = useLocalSkillCategoryLabel();
  const { skills, isLoading, isFetching, error, refetch } = useSkills();
  const { mutate: setSkillEnabled, isPending } = useEnableSkill();
  const [query, setQuery] = useState(externalSearchQuery ?? "");
  const [category, setCategory] = useState("all");
  const showInternalSearch = externalSearchQuery === undefined;
  const [showInternalSkills, setShowInternalSkills] = useState(false);

  useEffect(() => {
    if (externalSearchQuery !== undefined) setQuery(externalSearchQuery);
  }, [externalSearchQuery]);

  const allDomainSkills = useMemo(() => {
    return (skills as LocalSkill[])
      .filter((skill) => (skill.kind ?? "domain") === "domain")
      .map((skill) => ({ ...skill, localCategory: classifyLocalSkill(skill) }))
      .sort((a, b) => {
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
  }, [skills]);

  const localSkills = useMemo(() => {
    if (showInternalSkills) return allDomainSkills;
    return allDomainSkills.filter(
      (skill) => (skill.market_visibility ?? "market") === "market",
    );
  }, [allDomainSkills, showInternalSkills]);

  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const skill of localSkills) {
      counts.set(
        skill.localCategory,
        (counts.get(skill.localCategory) ?? 0) + 1,
      );
    }
    return counts;
  }, [localSkills]);

  const visibleSkills = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return localSkills.filter((skill) => {
      if (category !== "all" && skill.localCategory !== category) return false;
      if (!needle) return true;
      return searchableSkillText(skill).includes(needle);
    });
  }, [category, localSkills, query]);

  const activeLabel =
    category === "all" ? t.unifiedStore.skills.all : categoryLabel(category);
  const showSkillPacks = Boolean(skillPacksContent && skillPacksSelected);
  const hiddenSkillCount = Math.max(
    0,
    allDomainSkills.length - localSkills.length,
  );

  const handleCategorySelect = (nextCategory: string) => {
    setCategory(nextCategory);
    onDirectorySelect?.();
  };

  const allButton = (
    <Button
      size="sm"
      variant={!showSkillPacks && category === "all" ? "secondary" : "ghost"}
      className="h-8 shrink-0 px-3 text-xs"
      onClick={() => handleCategorySelect("all")}
    >
      {t.agentWorld.categories.all}
    </Button>
  );

  if (isLoading) {
    return (
      <div className="flex min-h-[360px] items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        {t.unifiedStore.skills.loading}
      </div>
    );
  }

  if (error) {
    return (
      <StoreErrorState
        title={t.localSkillDirectory.errorTitle}
        detail={error.message}
        retryLabel={t.localSkillDirectory.retryLabel}
        retrying={isFetching}
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
      {showInternalSearch && !showSkillPacks && (
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-center">
          <div className="relative w-full lg:max-w-[560px]">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label={t.unifiedStore.skills.searchAria}
              className="h-9 border-border bg-background pl-10 text-sm shadow-none"
              placeholder={t.unifiedStore.skills.searchPlaceholder}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {skillPacksContent && (
          <Button
            size="sm"
            variant={showSkillPacks ? "secondary" : "ghost"}
            className="h-8 shrink-0 px-3 text-xs"
            onClick={onSkillPacksSelect}
          >
            <Boxes className="mr-1.5 size-3.5" />
            {t.metaSkills.title}
          </Button>
        )}
        {allButtonPosition === "start" && allButton}
        {LOCAL_SKILL_CATEGORIES.map((item) => {
          const count = categoryCounts.get(item.key) ?? 0;
          if (!count) return null;
          return (
            <Button
              key={item.key}
              size="sm"
              variant={
                !showSkillPacks && category === item.key ? "secondary" : "ghost"
              }
              className="h-8 shrink-0 px-3 text-xs"
              onClick={() => handleCategorySelect(item.key)}
            >
              {categoryLabel(item.key)}
              <span className="ml-1 text-muted-foreground">{count}</span>
            </Button>
          );
        })}
        {(categoryCounts.get("other") ?? 0) > 0 && (
          <Button
            size="sm"
            variant={
              !showSkillPacks && category === "other" ? "secondary" : "ghost"
            }
            className="h-8 shrink-0 px-3 text-xs"
            onClick={() => handleCategorySelect("other")}
          >
            {t.unifiedStore.skills.other}
            <span className="ml-1 text-muted-foreground">
              {categoryCounts.get("other")}
            </span>
          </Button>
        )}
        {allButtonPosition === "end" && allButton}
      </div>

      {showSkillPacks ? (
        <div>{skillPacksContent}</div>
      ) : (
        <>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {category === "all"
                ? t.unifiedStore.skills.totalCount(visibleSkills.length)
                : t.unifiedStore.skills.visibleCount(
                    activeLabel,
                    visibleSkills.length,
                  )}
            </span>
            {hiddenSkillCount > 0 && (
              <button
                type="button"
                className="px-2 py-1 transition-colors hover:bg-muted hover:text-foreground"
                onClick={() => setShowInternalSkills((value) => !value)}
              >
                {showInternalSkills
                  ? t.localSkillDirectory.hideInternalSkills
                  : t.localSkillDirectory.showInternalSkills(hiddenSkillCount)}
              </button>
            )}
          </div>

          {visibleSkills.length ? (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-3">
              {visibleSkills.map((skill) => {
                const SkillIcon = getSkillIcon(skill.localCategory, skill.name);
                return (
                  <article
                    key={skill.name}
                    className={cn(
                      "group flex min-w-0 flex-col rounded-lg border border-border bg-card p-3.5 shadow-none transition-colors hover:bg-accent/30",
                      !skill.enabled && "bg-muted/15 text-muted-foreground",
                    )}
                  >
                    <div className="flex min-w-0 items-start gap-3">
                      <div
                        className={cn(
                          "flex size-12 shrink-0 items-center justify-center border shadow-none",
                          skill.enabled
                            ? "border-border bg-primary/10 text-primary"
                            : "border-border bg-muted text-muted-foreground",
                        )}
                      >
                        <SkillIcon className="size-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                          <h3 className="truncate text-sm font-semibold leading-5 text-foreground">
                            {skill.name}
                          </h3>
                        </div>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          <Badge
                            variant="outline"
                            className="text-xs font-normal"
                          >
                            {categoryLabel(skill.localCategory)}
                          </Badge>
                          {skill.has_tests && (
                            <Badge
                              variant="outline"
                              className="gap-1 text-xs font-normal"
                            >
                              <ShieldCheck className="size-3" />
                              {t.localSkillDirectory.verified}
                            </Badge>
                          )}
                          {(skill.market_visibility ?? "market") !==
                            "market" && (
                            <Badge
                              variant="secondary"
                              className="text-xs font-normal"
                              title={
                                skill.canonical_skill
                                  ? `${skill.market_reason ?? t.localSkillDirectory.marketReasonMerged}：${skill.canonical_skill}`
                                  : (skill.market_reason ??
                                    t.localSkillDirectory.internalSkill)
                              }
                            >
                              {skill.market_visibility === "duplicate"
                                ? t.localSkillDirectory.visibilityDuplicate
                                : skill.market_visibility === "provider"
                                  ? t.localSkillDirectory.visibilityProvider
                                  : skill.market_visibility === "specialized"
                                    ? t.localSkillDirectory
                                        .visibilitySpecialized
                                    : skill.market_visibility === "deprecated"
                                      ? t.localSkillDirectory
                                          .visibilityDeprecated
                                      : t.localSkillDirectory
                                          .visibilityInternal}
                            </Badge>
                          )}
                        </div>
                      </div>
                    </div>
                    <p className="mt-3 line-clamp-2 flex-1 text-sm leading-5 text-muted-foreground">
                      {skill.description || t.unifiedStore.skills.noDescription}
                    </p>
                    <div className="mt-3 flex items-center justify-end gap-3 border-t border-border pt-2.5">
                      <Button
                        type="button"
                        size="sm"
                        aria-label={t.unifiedStore.skills.toggleSkillAria(
                          skill.enabled,
                          skill.name,
                        )}
                        disabled={isPending}
                        onClick={() =>
                          setSkillEnabled({
                            skillName: skill.name,
                            enabled: !skill.enabled,
                          })
                        }
                        variant="outline"
                        className={cn(
                          "h-7 gap-1.5 px-2.5 text-xs font-medium shadow-none transition-colors disabled:opacity-60",
                          skill.enabled
                            ? "text-primary hover:bg-primary/10"
                            : "text-foreground hover:bg-accent",
                        )}
                      >
                        {skill.enabled ? (
                          <>
                            <CheckCircle2 className="size-3.5" />
                            {t.localSkillDirectory.enabled}
                          </>
                        ) : (
                          <>
                            <Plus className="size-3.5" />
                            {t.localSkillDirectory.enable}
                          </>
                        )}
                      </Button>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-muted/10 p-8 text-center text-sm text-muted-foreground">
              {t.unifiedStore.skills.noMatch(query || activeLabel)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
