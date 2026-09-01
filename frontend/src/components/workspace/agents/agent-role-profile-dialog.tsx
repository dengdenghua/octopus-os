import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronRight,
  CircuitBoard,
  ImagePlus,
  Link,
  Loader2,
  Plus,
  Save,
  Shield,
  Sparkles,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import type { EvolutionStoryChange } from "@/core/evolution/api";
import { useEvolutionOverview, useSkillPerformance, useEvolutionStory } from "@/core/evolution/hooks";
import {
  calculateLevel,
  calculateXP,
  calculateStars,
  transformToSkills,
} from "@/components/workspace/evolution-dashboard/game-data-transformer";
import { calculateAbilityScores } from "@/components/workspace/evolution-dashboard/ability-radar-chart";
import { AbilityRadarChart } from "@/components/workspace/evolution-dashboard/ability-radar-chart";
import { SkillTree } from "@/components/workspace/evolution-dashboard/skill-tree";
import { GrowthTimeline } from "@/components/workspace/evolution-dashboard/growth-timeline";
import type { TimelineEvent } from "@/components/workspace/evolution-dashboard/growth-timeline";
import { toast } from "sonner";

import { AuthenticatedImage } from "@/components/ui/authenticated-image";
import { Button } from "@/components/ui/button";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  useAgent,
  useGenerateAgentVisuals,
  useUpdateAgent,
} from "@/core/agents/hooks";
import { installAgent } from "@/core/agents/agent-world-api";
import {
  useAgentToolRegistry,
  useArms,
  useCapabilityPermissions,
  useSaveAgentToolRegistry,
} from "@/core/agents/tool-registry-hooks";
import type { AgentWorldAgent } from "@/core/agents/types";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import { cn } from "@/lib/utils";

import { AgentArmsDialog } from "./agent-arms-dialog";

interface AgentRoleProfileDialogProps {
  agent: AgentWorldAgent | null;
  agents?: AgentWorldAgent[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstallChange?: () => void;
  onSelectAgent?: (agent: AgentWorldAgent) => void;
  onCreateAgent?: () => void;
}

type EditableAgentConfig = {
  description: string;
  model: string;
  soul: string;
  arms: string[];
  extraAffinity: string;
  privateSkills: string;
};

function makeCodeName(agent: AgentWorldAgent): string {
  return agent.name
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 3).toUpperCase())
    .join("-");
}

function makeUid(agent: AgentWorldAgent): string {
  const seed = Array.from(agent.id || agent.name).reduce(
    (sum, char) => sum + char.charCodeAt(0),
    0,
  );
  return `${makeCodeName(agent).slice(0, 3) || "AGT"}-${String(seed % 90_000).padStart(5, "0")}`;
}

function parseList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function serializeList(items: string[] | undefined): string {
  return (items ?? []).join(", ");
}

function sameList(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((item, index) => item === right[index]);
}

function uniqueList(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter(Boolean)));
}

type AgentCharacterProfile = {
  epithet: string;
  quote: string;
  intro: string;
  background: string;
  age: string;
  personality: string;
  temperament: string;
  currentState: string;
  visualKeywords: string[];
  prompt: string;
};

type VisualPromptGroup = {
  id: string;
  label: string;
  options: { id: string; label: string }[];
};

type RoleProfileNotes = {
  bestFor: string[];
  boundaries: string[];
};

const MAX_VISUAL_REFERENCE_IMAGES = 3;

const VISUAL_PROMPT_OPTION_PROMPTS: Record<string, string> = {
  "game-character":
    "premium game HUD character illustration, polished live2D-adjacent key art, readable silhouette",
  "clean-anime":
    "clean anime-inspired linework, elegant facial rendering, refined hair shapes, controlled highlights",
  "semi-real":
    "semi-realistic character concept art, natural proportions, cinematic but not photorealistic, not painterly",
  "full-body":
    "full body visible, standing turnaround pose, entire character contained inside canvas, figure reads large in frame",
  "safe-headroom":
    "full head visible with extra top margin, do not crop hair or head, preserve footroom too",
  "avatar-ready":
    "face clear and centered enough for a Zero-style large-face avatar crop from the front view",
  "three-view-consistency":
    "front side and back views must keep the same face, hairstyle, outfit, colors, proportions, and facial appeal",
  transparent:
    "transparent background, isolated character, no scenery, no colored backdrop, no infographic layout",
  "soft-shadow":
    "subtle grounding shadow only when necessary, no environment, suitable for UI overlay",
  "high-resolution":
    "high resolution, crisp edges, detailed outfit materials, sharp eyes, clean hands, no blur",
  "no-artifacts":
    "avoid extra fingers, distorted hands, asymmetrical eyes, duplicate limbs, detached accessories, messy text, watermark",
};

const DEFAULT_VISUAL_PROMPT_OPTION_IDS = [
  "game-character",
  "full-body",
  "safe-headroom",
  "avatar-ready",
  "three-view-consistency",
  "transparent",
  "high-resolution",
  "no-artifacts",
];

function buildVisualPrompt(
  basePrompt: string,
  selectedIds: string[],
  customPrompt: string,
): string {
  const optionPrompts = selectedIds
    .map((id) => VISUAL_PROMPT_OPTION_PROMPTS[id])
    .filter(Boolean);
  return [
    basePrompt,
    "Use agent-visual-kit metaskill workflow.",
    "Generate three high-definition character turnaround views plus a separate square avatar for this Agent.",
    "Visual target: premium Echo Hub agent art, calm readable pose, attractive face, role-first costume language.",
    "Composition rule: body should read large in the Hub, while the avatar should behave like Zero with the face filling most of the icon.",
    ...optionPrompts,
    customPrompt.trim() ? `user additions: ${customPrompt.trim()}` : "",
  ]
    .filter(Boolean)
    .join("; ");
}

function parseReferenceImageInput(value: string): string[] {
  return value
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function readImageFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
      } else {
        reject(new Error("Failed to read image"));
      }
    });
    reader.addEventListener("error", () => reject(reader.error));
    reader.readAsDataURL(file);
  });
}

function buildRoleProfileNotes(
  agent: AgentWorldAgent,
  t: Translations,
): RoleProfileNotes {
  const key = `${agent.name} ${agent.display_name}`.toLowerCase();
  if (key.includes("coder")) {
    return {
      bestFor: t.agentRoleProfile.coderBestFor,
      boundaries: t.agentRoleProfile.coderBoundaries,
    };
  }
  if (key.includes("market_researcher") || key.includes("research")) {
    return {
      bestFor: t.agentRoleProfile.researcherBestFor,
      boundaries: t.agentRoleProfile.researcherBoundaries,
    };
  }
  if (key.includes("vibe") || key.includes("growth")) {
    return {
      bestFor: t.agentRoleProfile.growthBestFor,
      boundaries: t.agentRoleProfile.growthBoundaries,
    };
  }
  if (key.includes("ecommerce") || key.includes("commerce")) {
    return {
      bestFor: t.agentRoleProfile.ecommerceBestFor,
      boundaries: t.agentRoleProfile.ecommerceBoundaries,
    };
  }
  if (key.includes("aoi")) {
    return {
      bestFor: t.agentRoleProfile.aoiBestFor,
      boundaries: t.agentRoleProfile.aoiBoundaries,
    };
  }
  return {
    bestFor: t.agentRoleProfile.defaultBestFor,
    boundaries: t.agentRoleProfile.defaultBoundaries,
  };
}

function pickCategoryValue<T>(
  values: Record<string, T>,
  category: AgentWorldAgent["category"],
  fallback: T,
): T {
  return values[category] ?? fallback;
}

function buildCharacterProfile(
  agent: AgentWorldAgent,
  t: Translations,
): AgentCharacterProfile {
  const custom = agent.character_profile;
  if (custom) {
    const visualKeywords =
      custom.visual_keywords && custom.visual_keywords.length > 0
        ? custom.visual_keywords
        : (t.agentConfig.characterVisualKeywords.assistant ?? []);
    const detailLines = [
      custom.background ? `background: ${custom.background}` : "",
      custom.personality ? `personality: ${custom.personality}` : "",
      custom.temperament ? `temperament: ${custom.temperament}` : "",
      custom.likes?.length ? `likes: ${custom.likes.join("; ")}` : "",
      custom.dislikes?.length ? `dislikes: ${custom.dislikes.join("; ")}` : "",
      custom.quirks?.length ? `quirks: ${custom.quirks.join("; ")}` : "",
      custom.tone?.length ? `tone: ${custom.tone.join("; ")}` : "",
      custom.appearance?.length
        ? `appearance: ${custom.appearance.join("; ")}`
        : "",
      custom.interaction?.length
        ? `interaction: ${custom.interaction.join("; ")}`
        : "",
      custom.current_state?.length
        ? `current state: ${custom.current_state.join("; ")}`
        : "",
      custom.emotion_list?.length
        ? `available emotions: ${custom.emotion_list.join(", ")}`
        : "",
    ].filter(Boolean);

    return {
      epithet:
        custom.epithet ??
        t.agentConfig.characterEpithets[agent.category] ??
        agent.display_name,
      quote:
        custom.quote ?? t.agentConfig.characterQuotes[agent.category] ?? "",
      intro:
        custom.intro ??
        custom.background ??
        descriptionOrFallback(
          agent.description,
          t.agentConfig.characterDefaultOrigin,
        ),
      background:
        custom.background ??
        descriptionOrFallback(
          agent.description,
          t.agentConfig.characterDefaultOrigin,
        ),
      age:
        custom.apparent_age ??
        t.agentConfig.characterAgeArchetypes[agent.category] ??
        t.agentConfig.characterAgeArchetypes.assistant ??
        "",
      personality:
        custom.personality ??
        t.agentConfig.characterPersonalities[agent.category] ??
        t.agentConfig.characterPersonalities.assistant ??
        "",
      temperament:
        custom.temperament ??
        t.agentConfig.characterTemperaments[agent.category] ??
        t.agentConfig.characterTemperaments.assistant ??
        "",
      currentState: custom.current_state?.[0] ?? "",
      visualKeywords,
      prompt: [
        `character name: ${agent.display_name}`,
        custom.gender ? `gender: ${custom.gender}` : "",
        `character epithet: ${custom.epithet ?? agent.display_name}`,
        custom.quote ? `signature line: ${custom.quote}` : "",
        custom.intro ? `readable character intro: ${custom.intro}` : "",
        ...detailLines,
        `visual keywords: ${visualKeywords.join(", ")}`,
      ]
        .filter(Boolean)
        .join("; "),
    };
  }

  const role = t.agentConfig.categoryRoles[agent.category] ?? agent.category;
  const type = t.agentConfig.categoryTypes[agent.category] ?? agent.category;
  const faction = agent.is_official
    ? t.agentConfig.officialFaction
    : t.agentConfig.authorFaction(agent.author);
  const fallbackAge = t.agentConfig.characterAgeArchetypes.assistant ?? "";
  const fallbackPersonality =
    t.agentConfig.characterPersonalities.assistant ?? "";
  const fallbackTemperament =
    t.agentConfig.characterTemperaments.assistant ?? "";
  const fallbackVisualKeywords =
    t.agentConfig.characterVisualKeywords.assistant ?? [];
  const fallbackEpithet = t.agentConfig.characterEpithets.assistant ?? role;
  const fallbackQuote = t.agentConfig.characterQuotes.assistant ?? "";
  const age = pickCategoryValue(
    t.agentConfig.characterAgeArchetypes,
    agent.category,
    fallbackAge,
  );
  const personality = pickCategoryValue(
    t.agentConfig.characterPersonalities,
    agent.category,
    fallbackPersonality,
  );
  const temperament = pickCategoryValue(
    t.agentConfig.characterTemperaments,
    agent.category,
    fallbackTemperament,
  );
  const visualKeywords = pickCategoryValue(
    t.agentConfig.characterVisualKeywords,
    agent.category,
    fallbackVisualKeywords,
  );
  const epithet = pickCategoryValue(
    t.agentConfig.characterEpithets,
    agent.category,
    fallbackEpithet,
  );
  const quote = pickCategoryValue(
    t.agentConfig.characterQuotes,
    agent.category,
    fallbackQuote,
  );
  const background = t.agentConfig.characterBackground(
    agent.display_name,
    role,
    type,
    faction,
    agent.description,
  );
  const intro = t.agentConfig.characterIntro(
    agent.display_name,
    role,
    type,
    faction,
    descriptionOrFallback(
      agent.description,
      t.agentConfig.characterDefaultOrigin,
    ),
    personality,
    temperament,
  );
  const prompt = [
    `character epithet: ${epithet}`,
    `signature line: ${quote}`,
    `readable character intro: ${intro}`,
    `character background: ${background}`,
    `apparent age: ${age}`,
    `personality: ${personality}`,
    `temperament: ${temperament}`,
    `visual keywords: ${visualKeywords.join(", ")}`,
  ].join("; ");

  return {
    epithet,
    quote,
    intro,
    background,
    age,
    personality,
    temperament,
    currentState: "",
    visualKeywords,
    prompt,
  };
}

function descriptionOrFallback(description: string, fallback: string): string {
  const trimmed = description.trim();
  if (!trimmed) return fallback;
  const fallbackUsesCjk = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/.test(
    fallback,
  );
  const descriptionCjkCount = (
    trimmed.match(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g) ?? []
  ).length;
  const descriptionLatinCount = (trimmed.match(/[A-Za-z]/g) ?? []).length;
  if (fallbackUsesCjk && descriptionLatinCount > descriptionCjkCount * 3) {
    return fallback;
  }
  return trimmed;
}

function FieldLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <label className="block text-xs font-medium text-white">
      {label}
      {hint ? (
        <span className="ml-2 font-normal text-muted-foreground">{hint}</span>
      ) : null}
    </label>
  );
}

function AgentCoreVisual({
  agent,
  agents = [],
  codeName: _codeName,
  uid: _uid,
  onSelectAgent,
  onCreateAgent,
}: {
  agent: AgentWorldAgent;
  agents?: AgentWorldAgent[];
  codeName: string;
  uid: string;
  onSelectAgent?: (agent: AgentWorldAgent) => void;
  onCreateAgent?: () => void;
}) {
  const { t } = useI18n();
  const [view, setView] = useState<"front" | "side" | "back">("front");
  const [visualPromptOpen, setVisualPromptOpen] = useState(false);
  const [selectedVisualPromptIds, setSelectedVisualPromptIds] = useState<
    string[]
  >(DEFAULT_VISUAL_PROMPT_OPTION_IDS);
  const [customVisualPrompt, setCustomVisualPrompt] = useState("");
  const [referenceImageInput, setReferenceImageInput] = useState("");
  const [uploadedReferenceImages, setUploadedReferenceImages] = useState<
    string[]
  >([]);
  const generateVisuals = useGenerateAgentVisuals();
  const characterProfile = useMemo(
    () => buildCharacterProfile(agent, t),
    [agent, t],
  );
  const finalVisualPrompt = useMemo(
    () =>
      buildVisualPrompt(
        characterProfile.prompt,
        selectedVisualPromptIds,
        customVisualPrompt,
      ),
    [characterProfile.prompt, customVisualPrompt, selectedVisualPromptIds],
  );
  const referenceImages = useMemo(
    () =>
      uniqueList([
        ...parseReferenceImageInput(referenceImageInput),
        ...uploadedReferenceImages,
      ]).slice(0, MAX_VISUAL_REFERENCE_IMAGES),
    [referenceImageInput, uploadedReferenceImages],
  );
  const viewOptions = [
    ["front", t.agentConfig.viewFront],
    ["side", t.agentConfig.viewSide],
    ["back", t.agentConfig.viewBack],
  ] as const;
  const visualPromptGroups = useMemo<VisualPromptGroup[]>(
    () => [
      {
        id: "style",
        label: t.agentRoleProfile.visualPromptGroupStyle,
        options: [
          {
            id: "game-character",
            label: t.agentRoleProfile.visualPromptOptionGameCharacter,
          },
          {
            id: "clean-anime",
            label: t.agentRoleProfile.visualPromptOptionCleanAnime,
          },
          {
            id: "semi-real",
            label: t.agentRoleProfile.visualPromptOptionSemiReal,
          },
        ],
      },
      {
        id: "composition",
        label: t.agentRoleProfile.visualPromptGroupComposition,
        options: [
          {
            id: "full-body",
            label: t.agentRoleProfile.visualPromptOptionFullBody,
          },
          {
            id: "safe-headroom",
            label: t.agentRoleProfile.visualPromptOptionSafeHeadroom,
          },
          {
            id: "avatar-ready",
            label: t.agentRoleProfile.visualPromptOptionAvatarReady,
          },
          {
            id: "three-view-consistency",
            label: t.agentRoleProfile.visualPromptOptionThreeViewConsistency,
          },
        ],
      },
      {
        id: "background",
        label: t.agentRoleProfile.visualPromptGroupBackground,
        options: [
          {
            id: "transparent",
            label: t.agentRoleProfile.visualPromptOptionTransparent,
          },
          {
            id: "soft-shadow",
            label: t.agentRoleProfile.visualPromptOptionSoftShadow,
          },
        ],
      },
      {
        id: "quality",
        label: t.agentRoleProfile.visualPromptGroupQuality,
        options: [
          {
            id: "high-resolution",
            label: t.agentRoleProfile.visualPromptOptionHighResolution,
          },
          {
            id: "no-artifacts",
            label: t.agentRoleProfile.visualPromptOptionNoArtifacts,
          },
        ],
      },
    ],
    [t],
  );
  const visualUrls = agent.visual_urls ?? {};
  const activeGeneratedVisual = visualUrls[view] ?? null;
  const activeAvatar = agent.avatar_url
    ? withAgentAvatarVersion(agent.avatar_url)
    : null;
  const activeVisual = activeGeneratedVisual ?? activeAvatar;
  const isAvatarOnly = Boolean(activeVisual && !activeGeneratedVisual);
  const switchAgents = agents.length > 0 ? agents : [agent];
  const activeSwitchAgentRef = useRef<HTMLButtonElement | null>(null);
  const switchAgentEdgeSpacer = "calc(50% - 1.375rem)";

  useEffect(() => {
    const activeButton = activeSwitchAgentRef.current;
    if (!activeButton) return;
    const frame = window.requestAnimationFrame(() => {
      activeButton.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "center",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [agent.id, switchAgents.length]);

  function toggleVisualPromptOption(id: string) {
    setSelectedVisualPromptIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  }

  async function handleReferenceImageUpload(
    event: ChangeEvent<HTMLInputElement>,
  ) {
    const files = Array.from(event.currentTarget.files ?? [])
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, MAX_VISUAL_REFERENCE_IMAGES);
    event.currentTarget.value = "";
    if (files.length === 0) return;
    try {
      const dataUrls = await Promise.all(files.map(readImageFileAsDataUrl));
      setUploadedReferenceImages((current) =>
        uniqueList([...current, ...dataUrls]).slice(
          0,
          MAX_VISUAL_REFERENCE_IMAGES,
        ),
      );
    } catch {
      toast.error(t.agentRoleProfile.imageReadFailed);
    }
  }

  function removeReferenceImage(image: string) {
    setUploadedReferenceImages((current) =>
      current.filter((item) => item !== image),
    );
    setReferenceImageInput((current) =>
      parseReferenceImageInput(current)
        .filter((item) => item !== image)
        .join("\n"),
    );
  }

  async function handleGenerateVisuals() {
    try {
      await generateVisuals.mutateAsync({
        name: agent.name,
        provider: "agnes",
        stylePrompt: finalVisualPrompt,
        referenceImages,
      });
      setVisualPromptOpen(false);
      toast.success(t.agentConfig.visualGenerateSuccess);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(t.agentConfig.visualGenerateFailed(message));
    }
  }

  return (
    <section className="relative min-h-0 overflow-hidden">
      <div className="pointer-events-none absolute inset-0 opacity-[0.16] [background-image:radial-gradient(circle_at_48%_62%,hsl(var(--primary)/0.12),transparent_30%),linear-gradient(90deg,rgba(255,255,255,0.055)_1px,transparent_1px),linear-gradient(180deg,rgba(255,255,255,0.035)_1px,transparent_1px)] [background-size:100%_100%,34px_34px,34px_34px]" />
      <div className="pointer-events-none absolute left-[11%] top-[28%] text-7xl font-black uppercase tracking-normal text-white/[0.035] 2xl:text-8xl">
        {t.agentConfig.visualWatermark}
      </div>
      <div className="pointer-events-none absolute bottom-[44%] left-[15%] text-sm font-mono uppercase tracking-[0.55em] text-white/[0.045]">
        {t.agentConfig.visualLoadoutLabel}
      </div>
      <div className="absolute right-8 top-8 z-20 rounded-sm border border-primary/20 bg-black/18 px-2 py-1 font-mono text-xs uppercase tracking-[0.32em] text-primary/80">
        REC
      </div>
      <div className="absolute right-5 top-1/2 z-20 flex -translate-y-1/2 flex-col gap-3">
        {viewOptions.map(([key, label]) => (
          <button
            key={key}
            className={cn(
              "group relative h-[74px] w-[62px] rounded-sm border bg-black/18 p-1 text-left transition xl:h-[92px] xl:w-[78px]",
              view === key
                ? "border-[#f4e86f] shadow-[0_0_18px_rgba(244,232,111,0.16)]"
                : "border-white/10 opacity-45 hover:opacity-85",
            )}
            type="button"
            onClick={() => setView(key)}
          >
            <span className="absolute left-2 top-2 z-10 font-mono text-xs uppercase tracking-[0.25em] text-muted-foreground">
              {key}
            </span>
            <span className="flex h-full items-end justify-center overflow-hidden rounded-sm bg-white/[0.035] pb-1">
              {visualUrls[key] ? (
                <AuthenticatedImage
                  alt={`${agent.display_name} ${key}`}
                  className="h-[54px] w-full object-contain xl:h-[70px]"
                  src={visualUrls[key]}
                />
              ) : (
                <Bot className="mb-5 size-6 text-muted-foreground" />
              )}
            </span>
            <span className="absolute bottom-2 right-2 font-mono text-xs uppercase text-muted-foreground">
              {label}
            </span>
          </button>
        ))}
        <Button
          aria-label={t.agentConfig.visualGenerateAction}
          className="h-8 w-[62px] rounded-sm border-primary/35 bg-black/15 px-1 text-xs xl:w-[78px]"
          disabled={generateVisuals.isPending}
          title={t.agentConfig.visualGenerateAction}
          variant="outline"
          onClick={() => setVisualPromptOpen(true)}
        >
          {generateVisuals.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ImagePlus className="size-3.5" />
          )}
        </Button>
      </div>
      <div className="absolute left-4 top-4 z-20 hidden items-center gap-2 rounded-sm border border-white/10 bg-black/15 px-3 py-2">
        <span className="h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_12px_hsl(var(--primary)/0.75)]" />
        <span className="font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
          {t.agentConfig.visualTitle}
        </span>
      </div>
      <div className="relative z-10 h-full min-h-[520px] px-4 pb-8 pt-8 xl:min-h-[620px] xl:px-6 xl:pb-10 xl:pt-10">
        <div className="relative flex h-full min-h-0 items-end justify-center overflow-hidden pr-20 xl:pr-24">
          <div className="absolute bottom-[60px] h-16 w-[62%] rounded-[50%] border border-[#f4e86f]/25 bg-[#f4e86f]/10 shadow-[0_0_34px_rgba(244,232,111,0.12)]" />
          <div className="absolute bottom-[100px] left-[10%] right-[18%] h-px bg-white/10" />
          <div className="relative z-10 flex h-[500px] w-full max-w-[460px] items-end justify-center xl:h-[640px] xl:max-w-[580px]">
            {activeVisual ? (
              <div
                className={cn(
                  "flex w-full items-end justify-center",
                  isAvatarOnly ? "pb-24 xl:pb-28" : "h-full",
                )}
              >
                <AuthenticatedImage
                  key={activeVisual}
                  alt={`${agent.display_name} ${view}`}
                  className={cn(
                    "object-contain drop-shadow-2xl",
                    isAvatarOnly
                      ? "h-[300px] w-[300px] rounded-md border border-white/10 bg-white/95 object-center p-0 mix-blend-normal xl:h-[360px] xl:w-[360px]"
                      : "max-h-full w-full object-center",
                  )}
                  fallback={
                    <div className="mb-24 flex size-32 items-center justify-center rounded-sm border border-primary/35 bg-background text-6xl shadow-[var(--shadow-xs)]">
                      {agent.icon || (
                        <Bot className="size-16 text-muted-foreground" />
                      )}
                    </div>
                  }
                  src={activeVisual}
                />
              </div>
            ) : (
              <div className="mb-28 flex flex-col items-center justify-center gap-3 text-muted-foreground">
                <div className="flex size-32 items-center justify-center rounded-sm border border-primary/35 bg-background text-6xl shadow-[var(--shadow-xs)]">
                  {agent.icon || <Bot className="size-16" />}
                </div>
                <span className="font-mono text-xs uppercase tracking-eyebrow">
                  {t.agentConfig.visualMissing}
                </span>
              </div>
            )}
            <div className="absolute bottom-[52px] left-[6%] right-[6%] h-9 border-y border-[#111]/30 bg-[#f4e86f] text-center font-mono text-xs font-semibold uppercase tracking-[0.55em] text-[#232323] shadow-[0_0_24px_rgba(244,232,111,0.18)]">
              <div className="flex h-full items-center justify-center gap-4">
                <span className="h-px w-10 bg-[#232323]/50" />
                {t.agentConfig.visualSystemOnline}
                <span className="h-px w-10 bg-[#232323]/50" />
              </div>
            </div>
            <div className="absolute -bottom-2 left-1/2 z-30 flex max-w-[92%] -translate-x-1/2 scroll-px-3 items-center gap-2 overflow-x-auto rounded-sm border border-white/10 bg-black/35 px-2 py-2 shadow-[0_12px_34px_rgba(0,0,0,0.28)] backdrop-blur [scrollbar-width:none]">
              <span
                aria-hidden="true"
                className="block shrink-0"
                style={{
                  minWidth: switchAgentEdgeSpacer,
                  width: switchAgentEdgeSpacer,
                }}
              />
              {switchAgents.map((candidate) => {
                const active = candidate.id === agent.id;
                const avatar = candidate.avatar_url
                  ? withAgentAvatarVersion(candidate.avatar_url)
                  : null;
                return (
                  <button
                    key={candidate.id}
                    ref={active ? activeSwitchAgentRef : undefined}
                    type="button"
                    aria-label={t.agentRoleProfile.switchToAgent(
                      candidate.display_name,
                    )}
                    title={candidate.display_name}
                    className={cn(
                      "relative flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-sm border bg-white/[0.04] text-sm transition",
                      active
                        ? "border-[#f4e86f] shadow-[0_0_18px_rgba(244,232,111,0.22)]"
                        : "border-white/12 opacity-68 hover:border-white/28 hover:opacity-100",
                    )}
                    onClick={() => {
                      if (!active) onSelectAgent?.(candidate);
                    }}
                  >
                    {avatar ? (
                      <AuthenticatedImage
                        alt={candidate.display_name}
                        className="size-full bg-white object-cover object-top"
                        src={avatar}
                      />
                    ) : (
                      <span className="text-lg leading-none">
                        {candidate.icon || <Bot className="size-4" />}
                      </span>
                    )}
                    {active ? (
                      <span className="absolute inset-x-1 bottom-1 h-0.5 rounded-full bg-[#f4e86f]" />
                    ) : null}
                  </button>
                );
              })}
              {onCreateAgent ? (
                <button
                  type="button"
                  aria-label={t.agentWorld.newAgent}
                  title={t.agentWorld.newAgent}
                  className="group relative flex size-11 shrink-0 items-center justify-center rounded-sm border border-dashed border-[#f4e86f]/45 bg-[#f4e86f]/8 text-[#f4e86f] transition hover:border-[#f4e86f]/85 hover:bg-[#f4e86f]/14"
                  onClick={onCreateAgent}
                >
                  <Plus className="size-4 transition-transform group-hover:scale-110" />
                </button>
              ) : null}
              <span
                aria-hidden="true"
                className="block shrink-0"
                style={{
                  minWidth: switchAgentEdgeSpacer,
                  width: switchAgentEdgeSpacer,
                }}
              />
            </div>
          </div>
        </div>
      </div>
      <Dialog open={visualPromptOpen} onOpenChange={setVisualPromptOpen}>
        <DialogContent className="max-h-[88vh] overflow-hidden rounded-sm border-white/10 bg-[#191919] p-0 text-white shadow-2xl sm:max-w-2xl">
          <div className="relative overflow-hidden border-b border-white/10 bg-[#202020]/92 px-5 py-4">
            <div className="pointer-events-none absolute inset-0 opacity-25 [background-image:linear-gradient(90deg,rgba(255,255,255,0.06)_1px,transparent_1px),linear-gradient(180deg,rgba(255,255,255,0.04)_1px,transparent_1px)] [background-size:28px_28px]" />
            <DialogTitle className="relative flex items-center gap-2 text-base">
              <ImagePlus className="size-4 text-primary" />
              {t.agentRoleProfile.generateVisualPromptTitle}
            </DialogTitle>
            <DialogDescription className="relative mt-1 text-xs text-white/48">
              {t.agentRoleProfile.generateVisualPromptDescription}
            </DialogDescription>
          </div>

          <div className="max-h-[calc(88vh-148px)] overflow-y-auto p-5">
            <div className="grid gap-3">
              {visualPromptGroups.map((group) => (
                <div
                  key={group.id}
                  className="rounded-sm border border-white/8 bg-black/18 p-3"
                >
                  <div className="mb-2 font-mono text-xs uppercase tracking-eyebrow text-white/44">
                    {group.label}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {group.options.map((option) => {
                      const active = selectedVisualPromptIds.includes(
                        option.id,
                      );
                      return (
                        <button
                          key={option.id}
                          type="button"
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs leading-5 transition",
                            active
                              ? "border-[#f4e86f]/45 bg-[#f4e86f]/12 text-white"
                              : "border-white/10 bg-white/[0.025] text-white/52 hover:border-white/22 hover:text-white/82",
                          )}
                          onClick={() => toggleVisualPromptOption(option.id)}
                        >
                          <span
                            className={cn(
                              "size-1.5 rounded-full",
                              active ? "bg-[#f4e86f]" : "bg-white/18",
                            )}
                          />
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}

              <div className="rounded-sm border border-white/8 bg-black/18 p-3">
                <div className="mb-2 font-mono text-xs uppercase tracking-eyebrow text-white/44">
                  {t.agentRoleProfile.customAdditions}
                </div>
                <Textarea
                  className="min-h-[92px] resize-none border-white/10 bg-black/24 text-sm leading-6 text-white placeholder:text-white/28"
                  placeholder={t.agentRoleProfile.customPromptPlaceholder}
                  value={customVisualPrompt}
                  onChange={(event) =>
                    setCustomVisualPrompt(event.target.value)
                  }
                />
              </div>

              <div className="rounded-sm border border-white/8 bg-black/18 p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="font-mono text-xs uppercase tracking-eyebrow text-white/44">
                    {t.agentRoleProfile.referenceImages}
                  </div>
                  <span className="text-xs text-white/36">
                    {t.agentRoleProfile.referenceImagesHint(
                      MAX_VISUAL_REFERENCE_IMAGES,
                    )}
                  </span>
                </div>
                <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                  <Textarea
                    className="min-h-[58px] resize-none border-white/10 bg-black/24 text-xs leading-5 text-white placeholder:text-white/28"
                    placeholder={
                      t.agentRoleProfile.referenceImageUrlPlaceholder
                    }
                    value={referenceImageInput}
                    onChange={(event) =>
                      setReferenceImageInput(event.target.value)
                    }
                  />
                  <label className="inline-flex h-[58px] cursor-pointer items-center justify-center gap-2 rounded-sm border border-white/12 bg-white/[0.04] px-3 text-xs text-white/72 transition hover:border-[#f4e86f]/45 hover:text-white">
                    <Upload className="size-3.5" />
                    {t.agentRoleProfile.upload}
                    <input
                      accept="image/*"
                      className="sr-only"
                      multiple
                      type="file"
                      onChange={(event) =>
                        void handleReferenceImageUpload(event)
                      }
                    />
                  </label>
                </div>
                {referenceImages.length > 0 ? (
                  <div className="mt-3 flex gap-2 overflow-x-auto pb-1 [scrollbar-width:thin]">
                    {referenceImages.map((image, index) => (
                      <div
                        key={`${image.slice(0, 48)}-${index}`}
                        className="relative size-16 shrink-0 overflow-hidden rounded-sm border border-white/10 bg-white/[0.035]"
                      >
                        <img
                          alt={t.agentRoleProfile.referenceImageAlt(index)}
                          className="size-full object-cover"
                          src={image}
                        />
                        <button
                          aria-label={t.agentRoleProfile.removeReferenceImage}
                          className="absolute right-1 top-1 flex size-5 items-center justify-center rounded-sm bg-black/62 text-white/78 transition hover:text-white"
                          type="button"
                          onClick={() => removeReferenceImage(image)}
                        >
                          <X className="size-3" />
                        </button>
                        {!image.startsWith("data:") ? (
                          <span className="absolute bottom-1 left-1 rounded-sm bg-black/62 px-1 text-xs text-white/68">
                            <Link className="inline size-2.5" />
                          </span>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>

              <div className="rounded-sm border border-white/8 bg-white/[0.025] p-3">
                <div className="mb-2 font-mono text-xs uppercase tracking-eyebrow text-white/36">
                  Prompt Preview
                </div>
                {referenceImages.length > 0 ? (
                  <div className="mb-2 text-xs text-[#f4e86f]/78">
                    {t.agentRoleProfile.referenceImagesGenerateHint(
                      referenceImages.length,
                    )}
                  </div>
                ) : null}
                <p className="max-h-28 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-white/48 [scrollbar-width:thin]">
                  {finalVisualPrompt}
                </p>
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-white/10 bg-[#202020]/92 px-5 py-3">
            <Button
              className="h-8 rounded-sm border-white/12 bg-black/20 px-3 text-xs text-white/76 hover:bg-white/8 hover:text-white"
              type="button"
              variant="outline"
              onClick={() => {
                setSelectedVisualPromptIds(DEFAULT_VISUAL_PROMPT_OPTION_IDS);
                setCustomVisualPrompt("");
                setReferenceImageInput("");
                setUploadedReferenceImages([]);
              }}
            >
              {t.agentRoleProfile.reset}
            </Button>
            <div className="flex items-center gap-2">
              <Button
                className="h-8 rounded-sm border-white/12 bg-black/20 px-3 text-xs text-white/76 hover:bg-white/8 hover:text-white"
                type="button"
                variant="outline"
                onClick={() => setVisualPromptOpen(false)}
              >
                {t.agentRoleProfile.cancel}
              </Button>
              <Button
                className="h-8 rounded-sm bg-[#f4e86f] px-4 text-xs text-[#232323] hover:bg-[#fff27c]"
                disabled={generateVisuals.isPending}
                type="button"
                onClick={() => void handleGenerateVisuals()}
              >
                {generateVisuals.isPending ? (
                  <Loader2 className="mr-1 size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="mr-1 size-3.5" />
                )}
                {t.agentRoleProfile.generateThreeViews}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export function AgentRoleProfileDialog({
  agent,
  agents = [],
  open,
  onOpenChange,
  onInstallChange,
  onSelectAgent,
  onCreateAgent,
}: AgentRoleProfileDialogProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [armsOpen, setArmsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [configExpanded, setConfigExpanded] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [installingPack, setInstallingPack] = useState(false);
  const [armsInitialTab, setArmsInitialTab] = useState<
    "arms" | "skills" | "permissions" | "routing"
  >("arms");
  const [activeBottomTab, setActiveBottomTab] = useState<
    "overview" | "growth" | "radar" | "skills"
  >("overview");
  const [form, setForm] = useState<EditableAgentConfig>({
    description: "",
    model: "",
    soul: "",
    arms: [],
    extraAffinity: "",
    privateSkills: "",
  });

  const localAgentName = open && agent?.is_installed ? agent.name : null;
  const agentQuery = useAgent(localAgentName);
  const armsQuery = useArms();
  const registryQuery = useAgentToolRegistry(localAgentName);
  const permissionsQuery = useCapabilityPermissions();
  const updateAgent = useUpdateAgent();
  const saveRegistry = useSaveAgentToolRegistry(agent?.name ?? "");

  // Fetch evolution data
  const evolutionQuery = useEvolutionOverview();
  const evolutionData = evolutionQuery.data;
  const skillPerformanceQuery = useSkillPerformance();
  const skillPerformances = skillPerformanceQuery.data;
  const evolutionStoryQuery = useEvolutionStory();
  const evolutionStory = evolutionStoryQuery.data;

  const fullAgent = agentQuery.agent;
  const registry = registryQuery.data;
  const characterProfile = useMemo(
    () => (agent ? buildCharacterProfile(agent, t) : null),
    [agent, t],
  );

  // Transform evolution data for tabs
  const tabData = useMemo(() => {
    if (!evolutionData || !skillPerformances) return null;

    const skills = transformToSkills(skillPerformances);
    const abilityScores = calculateAbilityScores(evolutionData, skillPerformances, "general");

    // Generate timeline events from evolution story
    const timelineEvents: TimelineEvent[] = [];

    if (evolutionStory?.changes) {
      evolutionStory.changes.slice(0, 10).forEach((change: EvolutionStoryChange, idx: number) => {
        timelineEvents.push({
          id: `event-${idx}`,
          type: change.kind === 'skill' ? 'skill' :
                change.kind === 'rule' ? 'rule' : 'achievement',
          timestamp: new Date(Date.now() - idx * 86400000).toISOString(),
          title: change.title || '未知事件',
          description: change.content || change.kind,
        });
      });
    }

    return {
      skills,
      abilityScores,
      timelineEvents,
    };
  }, [evolutionData, skillPerformances, evolutionStory]);

  const meta = useMemo(() => {
    if (!agent) return null;
    return {
      codeName: makeCodeName(agent),
      uid: makeUid(agent),
      role: t.agentConfig.categoryRoles[agent.category],
      type: t.agentConfig.categoryTypes[agent.category],
      faction: agent.is_official
        ? t.agentConfig.officialFaction
        : t.agentConfig.authorFaction(agent.author),
    };
  }, [agent, t]);

  const serverState = useMemo<EditableAgentConfig | null>(() => {
    if (!agent) return null;
    const fallbackPrivateSkills =
      agent.private_skills ?? agent.key_skills ?? [];
    return {
      description: fullAgent?.description ?? agent.description ?? "",
      model: fullAgent?.model ?? agent.model ?? "",
      soul: fullAgent?.soul ?? agent.soul ?? "",
      arms: registry?.arms ?? fullAgent?.tool_groups ?? agent.tool_groups ?? [],
      extraAffinity: serializeList(
        registry?.extra_affinity ?? agent.extra_affinity,
      ),
      privateSkills: serializeList(
        registry?.private_skills?.length
          ? registry.private_skills
          : fallbackPrivateSkills,
      ),
    };
  }, [agent, fullAgent, registry]);

  useEffect(() => {
    if (open && serverState) {
      setForm(serverState);
    }
  }, [open, serverState]);

  if (!agent || !meta || !characterProfile) return null;

  const desiredExtraAffinity = parseList(form.extraAffinity);
  const desiredPrivateSkills = parseList(form.privateSkills);
  const agentDirty =
    !!serverState &&
    (form.description !== serverState.description ||
      form.model !== serverState.model ||
      form.soul !== serverState.soul);
  const registryDirty =
    !!serverState &&
    (!sameList(form.arms, serverState.arms) ||
      form.extraAffinity.trim() !== serverState.extraAffinity.trim() ||
      form.privateSkills.trim() !== serverState.privateSkills.trim());
  const isDirty = agentDirty || registryDirty;
  const canAssembleCapabilityPack =
    !agent.is_installed && desiredPrivateSkills.length > 0;
  const isLoading =
    agentQuery.isLoading || armsQuery.isLoading || registryQuery.isLoading;
  const isSaving = updateAgent.isPending || saveRegistry.isPending;
  const desiredArmSet = new Set(form.arms);
  const desiredSkillSet = new Set(desiredPrivateSkills);
  const selectedArmSkillSet = new Set<string>();
  for (const arm of armsQuery.data ?? []) {
    if (!desiredArmSet.has(arm.arm_id)) continue;
    for (const skill of arm.skills) selectedArmSkillSet.add(skill);
  }
  const permissionEffectiveCount =
    permissionsQuery.data?.filter((permission) => {
      if (!permission.enabled) return false;
      if (permission.id === "builtin" || permission.id === "memory") {
        return true;
      }
      return permission.skill_names.some(
        (skill) => selectedArmSkillSet.has(skill) || desiredSkillSet.has(skill),
      );
    }).length ?? 0;
  const permissionTotalCount = permissionsQuery.data?.length ?? 0;
  const permissionSummary = permissionTotalCount
    ? t.armsEditor.permissionEffectiveCount(
        permissionEffectiveCount,
        permissionTotalCount,
      )
    : t.agentConfig.guarded;
  const roleLabel = meta.role ?? agent.category;
  const typeLabel = meta.type ?? agent.category;
  const identityRows = [
    characterProfile.age
      ? [t.agentConfig.characterAgeLabel, characterProfile.age]
      : null,
    characterProfile.temperament
      ? [t.agentConfig.characterTemperamentLabel, characterProfile.temperament]
      : null,
    [typeLabel, characterProfile.currentState || roleLabel],
  ].filter(Boolean) as Array<[string, string]>;
  const profileNotes = buildRoleProfileNotes(agent, t);
  const personaTags = uniqueList([
    roleLabel,
    characterProfile.temperament,
    ...characterProfile.visualKeywords.slice(0, 3),
  ]);
  const sceneTags = uniqueList(profileNotes.bestFor);
  const boundaryTags = uniqueList(profileNotes.boundaries);
  const configActions = [
    {
      id: "profile",
      icon: CircuitBoard,
      label: t.agentConfig.configureProfileAction,
      shortLabel: t.agentConfig.basicTitle,
      metric: `${t.agentConfig.descriptionLabel} / ${t.agentConfig.promptSubtitle}`,
      hint: t.agentConfig.configureProfileHint,
      onClick: () => setProfileOpen(true),
    },
    {
      id: "arms",
      icon: Wrench,
      label: t.agentConfig.configureArmAction,
      shortLabel: "ARM",
      metric: t.agentConfig.armCount(form.arms.length),
      hint: t.agentConfig.configureArmHint,
      onClick: () => openArmsConfig("arms"),
    },
    {
      id: "skills",
      icon: Sparkles,
      label: t.agentConfig.browseSkillWhitelist,
      shortLabel: "Skill",
      metric: t.agentConfig.skillCount(desiredPrivateSkills.length),
      hint: t.agentConfig.configureSkillsHint,
      onClick: () => openArmsConfig("skills"),
    },
    {
      id: "permissions",
      icon: Shield,
      label: t.agentConfig.configurePermissionsAction,
      shortLabel: t.agentConfig.configurePermissionsAction.replace(
        /^调整|^Configure\s+/i,
        "",
      ),
      metric: permissionSummary,
      hint: t.agentConfig.configurePermissionsHint,
      onClick: () => openArmsConfig("permissions"),
    },
  ] as const;
  function openArmsConfig(tab: "arms" | "skills" | "permissions" | "routing") {
    setArmsInitialTab(tab);
    setArmsOpen(true);
  }

  function setField<K extends keyof EditableAgentConfig>(
    key: K,
    value: EditableAgentConfig[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    if (!agent) return;
    try {
      if (agentDirty) {
        await updateAgent.mutateAsync({
          name: agent.name,
          request: {
            description: form.description,
            model: form.model.trim() || null,
            soul: form.soul,
          },
        });
      }
      if (registryDirty) {
        await saveRegistry.mutateAsync({
          arms: form.arms,
          extra_affinity: desiredExtraAffinity,
          private_skills: desiredPrivateSkills,
        });
      }
      toast.success(t.agentConfig.saved);
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 1400);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(t.agentConfig.saveFailed(message));
    }
  }

  async function handleAssembleCapabilityPack() {
    if (!agent || installingPack) return;
    setInstallingPack(true);
    try {
      const result = await installAgent(agent.id);
      const assembledSkillCount =
        result.key_skills?.length ??
        result.registered_skills ??
        desiredPrivateSkills.length;
      toast.success(
        assembledSkillCount > 0
          ? t.agentWorld.toastCapabilityPackInstalled(
              agent.display_name,
              assembledSkillCount,
            )
          : t.agentWorld.toastInstalled(agent.display_name),
      );
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      onInstallChange?.();
      onOpenChange(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(message);
    } finally {
      setInstallingPack(false);
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className={cn(
            "!h-[min(760px,86vh)] !w-[min(1180px,92vw)] !max-w-none overflow-hidden border-white/12 bg-[#2a2a2a] p-0 text-white shadow-2xl sm:rounded-lg",
            "data-[state=open]:duration-base",
          )}
          showCloseButton={false}
        >
          <DialogTitle className="sr-only">{agent.display_name}</DialogTitle>
          <DialogDescription className="sr-only">
            {t.agentConfig.subtitle}
          </DialogDescription>
          <div className="relative h-full overflow-hidden bg-[#2a2a2a]">
            <div className="pointer-events-none absolute inset-0 opacity-[0.18] [background-image:radial-gradient(circle_at_70%_38%,rgba(255,255,255,0.12),transparent_30%),linear-gradient(to_right,rgba(255,255,255,0.045)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,0.03)_1px,transparent_1px)] [background-size:100%_100%,40px_40px,40px_40px]" />
            <div className="relative grid h-full grid-cols-[minmax(300px,0.42fr)_minmax(0,0.58fr)]">
              <section className="relative flex min-h-0 flex-col overflow-hidden px-8 py-6 lg:px-10 lg:py-7">
                <div className="pointer-events-none absolute left-0 top-0 h-5 w-5 border-l border-t border-primary/60" />
                <div className="pointer-events-none absolute bottom-0 right-0 h-5 w-5 border-b border-r border-primary/45" />
                <div className="mb-5 flex shrink-0 items-center justify-between">
                  <Button
                    aria-label={t.agentConfig.back}
                    className="h-8 w-8 shrink-0 rounded-sm"
                    size="icon"
                    variant="ghost"
                    onClick={() => onOpenChange(false)}
                  >
                    <ChevronRight className="size-5 rotate-180" />
                  </Button>
                  <div className="flex items-center gap-2 rounded-sm border border-white/8 bg-white/[0.025] px-2 py-1 font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        isDirty ? "bg-warning" : "bg-success",
                      )}
                    />
                    {isDirty ? t.agentConfig.unsaved : "CHARACTER FILE"}
                  </div>
                  <Button
                    aria-label={t.common.close}
                    className="h-8 w-8 rounded-sm"
                    size="icon"
                    variant="ghost"
                    onClick={() => onOpenChange(false)}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto pr-2 [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.22)_transparent]">
                  <div className="max-w-[360px] pb-6">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.24em] text-primary">
                        <span className="h-px w-6 bg-primary/70" />
                        {t.agentConfig.characterFileLabel}
                      </div>
                      <h1 className="mt-4 truncate text-5xl font-semibold leading-none text-white">
                        {agent.display_name}
                      </h1>
                      <p className="mt-3 text-xl font-medium leading-7 text-[#f4e86f]">
                        {characterProfile.epithet}
                      </p>

                      {/* Evolution Level Display */}
                      {evolutionData && (() => {
                        const level = calculateLevel(evolutionData.learning_events);
                        const stars = calculateStars(level);
                        const { progress } = calculateXP(evolutionData.learning_events);
                        const getStars = (count: number) => "⭐".repeat(count);
                        const getTitle = (lvl: number) => {
                          if (lvl <= 5) return "新手";
                          if (lvl <= 10) return "学徒";
                          if (lvl <= 20) return "熟手";
                          if (lvl <= 35) return "专家";
                          if (lvl <= 50) return "大师";
                          if (lvl <= 75) return "宗师";
                          return "传奇";
                        };

                        return (
                          <div className="mt-3">
                            <div className="flex items-center gap-2 text-sm">
                              <span className="font-semibold text-primary">Lv.{level}</span>
                              <span className="text-xs">{getStars(stars)}</span>
                              <span className="text-white/60">· 🎯 {getTitle(level)}</span>
                            </div>
                            <div className="mt-2">
                              <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                                <div
                                  className="h-full rounded-full bg-[#f4e86f] transition-all duration-500"
                                  style={{ width: `${progress}%` }}
                                />
                              </div>
                              <p className="mt-1 text-xs text-white/50">
                                {progress}% → Lv.{level + 1}
                              </p>
                            </div>
                          </div>
                        );
                      })()}

                      <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-sm border border-white/10 bg-white/10">
                        {identityRows.map(([label, value]) => (
                          <div
                            key={`${label}-${value}`}
                            className="min-w-0 bg-[#2a2a2a]/92 px-2.5 py-2"
                          >
                            <div className="truncate font-mono text-xs uppercase tracking-caps text-white/36">
                              {label}
                            </div>
                            <div className="mt-1 truncate text-xs font-medium leading-4 text-white/88">
                              {value}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="mt-6 border-l-2 border-[#f4e86f] bg-[#f4e86f]/[0.045] px-4 py-3">
                      <p className="text-base font-medium leading-7 text-white/95">
                        &ldquo;{characterProfile.quote}&rdquo;
                      </p>
                    </div>

                    <div className="mt-6">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
                          <span className="h-px w-4 bg-white/18" />
                          PERSONA
                        </div>
                        <span className="font-mono text-xs uppercase tracking-eyebrow text-white/32">
                          {meta.codeName}
                        </span>
                      </div>
                      <p className="text-sm leading-7 text-white/82">
                        {characterProfile.intro}
                      </p>
                    </div>

                    <div className="mt-5">
                      <div className="mb-2 flex items-center gap-2 font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
                        <span className="h-px w-4 bg-white/18" />
                        {t.agentConfig.characterBackgroundLabel}
                      </div>
                      <p className="line-clamp-4 text-xs leading-6 text-white/68">
                        {characterProfile.background}
                      </p>
                    </div>

                    <div className="mt-5">
                      <div className="mb-2 flex items-center gap-2 font-mono text-xs uppercase tracking-eyebrow text-primary">
                        <span className="h-px w-4 bg-primary/60" />
                        {t.agentConfig.characterBestForLabel}
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {sceneTags.map((item) => (
                          <span
                            key={item}
                            className="rounded-full border border-primary/22 bg-primary/[0.075] px-2.5 py-1 text-xs leading-5 text-white/86"
                          >
                            {item}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div className="mt-5 grid grid-cols-[1fr_auto] gap-3">
                      <div className="min-w-0 border-l border-primary/45 pl-3">
                        <div className="font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
                          {t.agentConfig.characterPersonalityLabel}
                        </div>
                        <p className="mt-1 line-clamp-4 text-sm leading-6 text-white/86">
                          {characterProfile.personality}
                        </p>
                      </div>
                      <div className="flex w-[94px] flex-col gap-1.5">
                        {personaTags.slice(0, 4).map((tag) => (
                          <span
                            key={tag}
                            className="truncate rounded-sm border border-white/10 bg-white/[0.035] px-2 py-1 text-xs leading-4 text-white/66"
                            title={tag}
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div className="mt-5 space-y-4">
                      <div>
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
                            {t.agentConfig.characterVisualKeywordsLabel}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {t.agentConfig.characterPromptHint}
                          </span>
                        </div>
                        <div className="flex max-h-20 flex-wrap gap-1.5 overflow-hidden">
                          {characterProfile.visualKeywords.map((keyword) => (
                            <span
                              key={keyword}
                              className="rounded-sm border border-primary/20 bg-primary/10 px-2 py-1 text-xs text-primary"
                            >
                              {keyword}
                            </span>
                          ))}
                        </div>
                      </div>

                      <div className="rounded-sm border border-white/10 bg-black/10 px-3 py-2.5">
                        <div className="mb-2 flex items-center gap-2 font-mono text-xs uppercase tracking-eyebrow text-white/48">
                          <span className="h-px w-4 bg-white/18" />
                          {t.agentConfig.characterBoundaryLabel}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {boundaryTags.map((item) => (
                            <span
                              key={item}
                              className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-xs leading-5 text-white/62"
                            >
                              {item}
                            </span>
                          ))}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-eyebrow text-muted-foreground">
                        <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                        {t.agentConfig.characterProfileReady}
                      </div>

                      {canAssembleCapabilityPack ? (
                        <div className="rounded-sm border border-primary/20 bg-primary/10 p-2.5">
                          <div className="mb-2 font-mono text-xs uppercase tracking-eyebrow text-primary">
                            {t.agentConfig.capabilityPackLabel}
                          </div>
                          <button
                            className="inline-flex items-center gap-1 rounded-sm border border-primary/25 bg-black/15 px-2 py-1 font-mono text-xs uppercase tracking-caps text-primary transition hover:border-primary/45 hover:bg-primary/15"
                            disabled={installingPack}
                            type="button"
                            onClick={() => void handleAssembleCapabilityPack()}
                          >
                            {installingPack ? (
                              <Loader2 className="size-3 animate-spin" />
                            ) : (
                              <Sparkles className="size-3" />
                            )}
                            {t.agentWorld.assembleCapabilityPack}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>

                {/* Bottom Tabs Section */}
                <div className="mt-3 shrink-0 space-y-2">
                  {/* Tab Navigation */}
                  <div className="flex items-center gap-1 border-b border-white/10 pb-0">
                    {[
                      { id: "overview", label: "概览", icon: "📋" },
                      { id: "growth", label: "成长数据", icon: "📈" },
                      { id: "radar", label: "能力雷达", icon: "🎯" },
                      { id: "skills", label: "技能树", icon: "🌳" },
                    ].map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        className={cn(
                          "relative flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition",
                          activeBottomTab === tab.id
                            ? "text-primary"
                            : "text-white/50 hover:text-white/80"
                        )}
                        onClick={() => setActiveBottomTab(tab.id as "overview" | "growth" | "radar" | "skills")}
                      >
                        <span>{tab.icon}</span>
                        <span>{tab.label}</span>
                        {activeBottomTab === tab.id && (
                          <span className="absolute inset-x-0 bottom-0 h-0.5 bg-primary" />
                        )}
                      </button>
                    ))}
                  </div>

                  {/* Tab Content */}
                  <div className="max-h-[180px] overflow-y-auto rounded-sm border border-white/10 bg-black/10 p-3 [scrollbar-width:thin]">
                    {activeBottomTab === "overview" && (
                      <div className="space-y-2">
                        <div className="font-mono text-xs uppercase tracking-eyebrow text-white/48">
                          角色概览
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div className="rounded-sm border border-white/8 bg-white/[0.025] p-2">
                            <div className="text-white/50">总任务数</div>
                            <div className="mt-1 text-lg font-semibold text-white">
                              {evolutionData?.learning_events || 0}
                            </div>
                          </div>
                          <div className="rounded-sm border border-white/8 bg-white/[0.025] p-2">
                            <div className="text-white/50">成功率</div>
                            <div className="mt-1 text-lg font-semibold text-primary">
                              {evolutionData?.skills?.avg_success_rate
                                ? `${Math.round(evolutionData.skills.avg_success_rate * 100)}%`
                                : "N/A"}
                            </div>
                          </div>
                          <div className="rounded-sm border border-white/8 bg-white/[0.025] p-2">
                            <div className="text-white/50">技能数</div>
                            <div className="mt-1 text-lg font-semibold text-white">
                              {skillPerformances?.length || 0}
                            </div>
                          </div>
                          <div className="rounded-sm border border-white/8 bg-white/[0.025] p-2">
                            <div className="text-white/50">规则数</div>
                            <div className="mt-1 text-lg font-semibold text-white">
                              {evolutionData?.memory?.categories?.rules || 0}
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {activeBottomTab === "growth" && (
                      <div className="space-y-2">
                        <div className="font-mono text-xs uppercase tracking-eyebrow text-white/48">
                          成长时间线
                        </div>
                        {tabData?.timelineEvents && tabData.timelineEvents.length > 0 ? (
                          <GrowthTimeline
                            events={tabData.timelineEvents}
                            className="max-h-[120px]"
                          />
                        ) : (
                          <div className="py-4 text-center text-xs text-white/40">
                            暂无成长记录
                          </div>
                        )}
                      </div>
                    )}

                    {activeBottomTab === "radar" && (
                      <div className="flex flex-col items-center gap-2">
                        <div className="font-mono text-xs uppercase tracking-eyebrow text-white/48">
                          六维能力图
                        </div>
                        {tabData?.abilityScores && tabData.abilityScores.length > 0 ? (
                          <AbilityRadarChart
                            data={tabData.abilityScores}
                            size={140}
                            className="scale-90"
                          />
                        ) : (
                          <div className="py-8 text-center text-xs text-white/40">
                            暂无能力数据
                          </div>
                        )}
                      </div>
                    )}

                    {activeBottomTab === "skills" && (
                      <div className="space-y-2">
                        <div className="font-mono text-xs uppercase tracking-eyebrow text-white/48">
                          技能树
                        </div>
                        {tabData?.skills && tabData.skills.length > 0 ? (
                          <SkillTree
                            skills={tabData.skills}
                            className="max-h-[120px]"
                          />
                        ) : (
                          <div className="py-4 text-center text-xs text-white/40">
                            暂无技能数据
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <div className="mt-3 shrink-0 border-t border-white/10 bg-[#2a2a2a]/94 pt-2 shadow-[0_-14px_28px_rgba(0,0,0,0.16)] backdrop-blur">
                  <div className="max-w-[360px]">
                    <div className="flex items-center gap-1.5">
                      <div className="mr-1 flex min-w-0 items-center gap-1.5 font-mono text-xs uppercase tracking-caps text-white/48">
                        <span className="size-1.5 rounded-full bg-primary/80" />
                        <span className="max-w-[72px] truncate">
                          {t.agentConfig.configDockTitle}
                        </span>
                      </div>
                      <div className="grid min-w-0 flex-1 grid-cols-4 gap-1">
                        {configActions.map((action) => {
                          const Icon = action.icon;
                          return (
                            <button
                              key={action.id}
                              type="button"
                              aria-label={action.label}
                              title={`${action.label} · ${action.hint}`}
                              className="group flex h-10 min-w-0 items-center gap-1 rounded-sm border border-white/8 bg-white/[0.025] px-1.5 text-white/78 transition hover:border-primary/28 hover:bg-white/[0.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                              onClick={action.onClick}
                            >
                              <Icon className="size-3.5 shrink-0 text-primary/82" />
                              <span className="min-w-0 flex-1 text-left">
                                <span className="block truncate text-xs leading-4">
                                  {action.shortLabel}
                                </span>
                                <span className="block truncate font-mono text-xs leading-3 text-white/42">
                                  {action.metric}
                                </span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                      <button
                        aria-expanded={configExpanded}
                        aria-label={t.agentConfig.configDockTitle}
                        className="flex size-8 shrink-0 items-center justify-center rounded-sm border border-white/8 bg-white/[0.025] text-white/52 transition hover:border-primary/28 hover:text-primary"
                        type="button"
                        onClick={() => setConfigExpanded((value) => !value)}
                      >
                        <ChevronRight
                          className={cn(
                            "size-3.5 transition-transform",
                            configExpanded ? "-rotate-90" : "",
                          )}
                        />
                      </button>
                    </div>
                    {configExpanded ? (
                      <div className="mt-2 space-y-2 border-t border-white/8 pt-2">
                        <div className="grid grid-cols-2 gap-1.5">
                          {configActions.map((action) => {
                            const Icon = action.icon;
                            return (
                              <button
                                key={action.id}
                                type="button"
                                aria-label={action.label}
                                className="group flex min-h-[40px] min-w-0 items-start gap-2 rounded-sm px-1.5 py-1 text-left transition hover:bg-white/[0.055] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                                onClick={action.onClick}
                              >
                                <span className="flex size-5 shrink-0 items-center justify-center rounded-sm border border-white/10 bg-white/[0.04] text-primary/85 transition group-hover:border-primary/35 group-hover:bg-primary/10">
                                  <Icon className="size-3" />
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-xs font-medium leading-4 text-white/88">
                                    {action.label}
                                  </span>
                                  <span className="line-clamp-1 block text-xs leading-4 text-white/38">
                                    {action.metric}
                                  </span>
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              </section>

              <AgentCoreVisual
                agent={{
                  ...agent,
                  avatar_url: fullAgent?.avatar_url ?? agent.avatar_url,
                  visual_urls: fullAgent?.visual_urls ?? agent.visual_urls,
                }}
                agents={agents}
                codeName={meta.codeName}
                uid={meta.uid}
                onSelectAgent={onSelectAgent}
                onCreateAgent={onCreateAgent}
              />
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={profileOpen} onOpenChange={setProfileOpen}>
        <DialogContent className="max-h-[88vh] overflow-hidden rounded-sm border-white/10 bg-[#191919] p-0 text-white shadow-2xl sm:max-w-3xl">
          <div className="relative overflow-hidden border-b border-white/10 bg-[#202020]/90 px-5 py-4">
            <div className="pointer-events-none absolute inset-0 opacity-30 [background-image:linear-gradient(90deg,rgba(255,255,255,0.06)_1px,transparent_1px),linear-gradient(180deg,rgba(255,255,255,0.045)_1px,transparent_1px)] [background-size:28px_28px]" />
            <DialogTitle className="relative">
              {t.agentConfig.configureProfileAction}
            </DialogTitle>
            <DialogDescription className="relative">
              {t.agentConfig.basicSubtitle} / {t.agentConfig.promptSubtitle}
            </DialogDescription>
          </div>
          <div className="grid gap-4 p-5">
            <div>
              <FieldLabel label={t.agentConfig.descriptionLabel} />
              <Textarea
                className="mt-1 min-h-[96px] border-white/10 bg-black/25 text-sm text-white"
                disabled={isLoading || isSaving}
                value={form.description}
                onChange={(event) =>
                  setField("description", event.target.value)
                }
              />
            </div>
            <div>
              <FieldLabel
                label={t.agentConfig.modelLabel}
                hint={t.agentConfig.modelHint}
              />
              <Input
                className="mt-1 h-9 border-white/10 bg-black/25 text-white"
                disabled={isLoading || isSaving}
                placeholder={t.agentConfig.modelPlaceholder}
                value={form.model}
                onChange={(event) => setField("model", event.target.value)}
              />
            </div>
            <div>
              <FieldLabel label={t.agentConfig.promptTitle} />
              <Textarea
                className="mt-1 min-h-[260px] border-white/10 bg-black/25 font-mono text-xs leading-5 text-white"
                disabled={isLoading || isSaving}
                placeholder={t.agentConfig.soulPlaceholder}
                value={form.soul}
                onChange={(event) => setField("soul", event.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2 border-t border-white/10 pt-4">
              <Button
                className="rounded-sm"
                variant="ghost"
                onClick={() => setProfileOpen(false)}
              >
                {t.common.close}
              </Button>
              <Button
                className="rounded-sm"
                disabled={!isDirty || isLoading || isSaving}
                onClick={() => void handleSave()}
              >
                {isSaving ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <Save className="mr-2 size-4" />
                )}
                {savedFlash
                  ? t.agentConfig.savedButton
                  : t.agentConfig.saveButton}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AgentArmsDialog
        agentId={agent.name}
        agentDisplayName={agent.display_name}
        open={armsOpen}
        onOpenChange={setArmsOpen}
        initialTab={armsInitialTab}
      />
    </>
  );
}
