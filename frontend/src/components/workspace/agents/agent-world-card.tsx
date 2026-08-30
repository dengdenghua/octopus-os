import { useState } from "react";
import { BadgeCheckIcon, Loader2Icon, SparklesIcon } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AuthenticatedImage } from "@/components/ui/authenticated-image";
import { withAgentAvatarVersion } from "@/core/agents/avatar";
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { installAgent, uninstallAgent } from "@/core/agents/agent-world-api";
import type { AgentWorldAgent, AgentWorldCategory } from "@/core/agents/types";

// ---------------------------------------------------------------------------
// Category colours
// ---------------------------------------------------------------------------

export const CATEGORY_STYLES: Record<
  AgentWorldCategory,
  { bg: string; text: string; icon: string }
> = {
  assistant: {
    bg: "bg-info/10",
    text: "text-info dark:text-info",
    icon: "🤖",
  },
  coder: {
    bg: "bg-success/10",
    text: "text-success",
    icon: "💻",
  },
  researcher: {
    bg: "bg-chart-1/10",
    text: "text-chart-1 dark:text-chart-1",
    icon: "🔬",
  },
  creative: {
    bg: "bg-warning/10",
    text: "text-warning",
    icon: "🎨",
  },
  automation: {
    bg: "bg-destructive/10",
    text: "text-destructive",
    icon: "⚡",
  },
  specialist: {
    bg: "bg-info/10",
    text: "text-info dark:text-info",
    icon: "🎯",
  },
  financial: {
    bg: "bg-chart-2/10",
    text: "text-chart-2 dark:text-chart-2",
    icon: "💼",
  },
};

// ---------------------------------------------------------------------------
// AgentWorldCard
// ---------------------------------------------------------------------------

interface AgentWorldCardProps {
  agent: AgentWorldAgent;
  onSelect?: (agent: AgentWorldAgent) => void;
  onInstallChange?: () => void;
  featured?: boolean;
}

export function AgentWorldCard({
  agent,
  onSelect,
  onInstallChange,
  featured: _featured,
}: AgentWorldCardProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [installing, setInstalling] = useState(false);
  const [installed, setInstalled] = useState(agent.is_installed);
  const catStyle = CATEGORY_STYLES[agent.category] ?? CATEGORY_STYLES.assistant;
  const categoryLabel =
    t.agentWorld.categories[agent.category] ?? agent.category;
  const keySkillCount = agent.key_skills?.length ?? 0;
  const hasCapabilityPack = keySkillCount > 0;
  const talentTags = Array.from(
    new Set(
      [categoryLabel, ...agent.tags, ...(agent.key_skills ?? [])]
        .filter((tag) => Boolean(tag?.trim()))
        .map((tag) => tag.trim()),
    ),
  ).slice(0, 3);
  const iconFallback = (
    <span className="bg-gradient-to-br from-primary/12 to-muted/35 text-primary flex h-full w-full items-center justify-center rounded-sm">
      {agent.icon || catStyle.icon}
    </span>
  );

  async function handleInstallToggle(e: React.MouseEvent) {
    e.stopPropagation();
    setInstalling(true);
    try {
      if (installed) {
        await uninstallAgent(agent.id);
        setInstalled(false);
        toast.success(t.agentWorld.toastUninstalled(agent.display_name));
      } else {
        const result = await installAgent(agent.id);
        setInstalled(true);
        const assembledSkillCount =
          result.key_skills?.length ??
          result.registered_skills ??
          keySkillCount;
        toast.success(
          assembledSkillCount > 0
            ? t.agentWorld.toastCapabilityPackInstalled(
                agent.display_name,
                assembledSkillCount,
              )
            : t.agentWorld.toastInstalled(agent.display_name),
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      onInstallChange?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(false);
    }
  }

  return (
    <Card
      className={cn(
        "group relative flex min-h-44 flex-col overflow-hidden rounded-xl border-border-default bg-card py-0 shadow-[var(--shadow-xs)] transition-[border-color,box-shadow,transform] duration-base ease-out",
        "hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[var(--shadow-sm)]",
      )}
    >
      <button
        type="button"
        disabled={!onSelect}
        aria-label={t.agentCard.profileAriaLabel(agent.display_name)}
        className="block w-full cursor-pointer rounded-t-xl text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-default"
        onClick={() => onSelect?.(agent)}
      >
        <CardHeader className="flex flex-1 flex-col px-4 pb-3 pt-4">
          <div className="flex items-start gap-3">
            <div className="relative flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-border-default bg-background text-lg">
              {agent.avatar_url ? (
                <AuthenticatedImage
                  src={withAgentAvatarVersion(agent.avatar_url)}
                  alt={agent.display_name}
                  className="h-full w-full bg-white object-cover [image-rendering:pixelated]"
                  fallback={iconFallback}
                />
              ) : (
                iconFallback
              )}
              {agent.is_official && (
                <div className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-info shadow-[var(--shadow-xs)]">
                  <BadgeCheckIcon className="h-2.5 w-2.5 text-white" />
                </div>
              )}
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-2">
                <CardTitle className="truncate text-[15px] font-semibold leading-5">
                  {agent.display_name}
                </CardTitle>
                {installed ? (
                  <Badge
                    variant="secondary"
                    className="h-5 shrink-0 rounded-full px-2 text-[11px] font-medium"
                  >
                    {t.agentWorld.agentInstalled}
                  </Badge>
                ) : agent.is_official ? (
                  <span
                    className="flex shrink-0 items-center gap-1 text-[11px] text-primary"
                    aria-label={`${t.agentWorld.authorPrefix} ${agent.author}`}
                  >
                    <BadgeCheckIcon className="size-3.5" aria-hidden="true" />
                    {agent.author}
                  </span>
                ) : agent.is_featured ? (
                  <SparklesIcon
                    className="size-3.5 shrink-0 text-warning"
                    aria-hidden="true"
                  />
                ) : null}
              </div>
              <CardDescription
                className="mt-1 truncate text-xs leading-5 text-muted-foreground"
                title={agent.description}
              >
                {agent.description}
              </CardDescription>
              {!agent.is_official && (
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground/75">
                  {t.agentWorld.authorPrefix} {agent.author}
                </p>
              )}
            </div>
          </div>

          {talentTags.length > 0 && (
            <div
              className="mt-3 flex min-h-5 flex-wrap gap-1.5"
              aria-label={talentTags.join(", ")}
            >
              {talentTags.map((tag, index) => (
                <Badge
                  key={`${tag}-${index}`}
                  variant="outline"
                  className={cn(
                    "max-w-32 truncate rounded-full px-2 py-0 text-[11px] font-normal",
                    index === 0
                      ? cn("border-transparent", catStyle.bg, catStyle.text)
                      : "border-border-subtle bg-muted/35 text-muted-foreground",
                  )}
                >
                  {tag}
                </Badge>
              ))}
            </div>
          )}
        </CardHeader>
      </button>

      <CardFooter className="relative mt-auto border-t border-border-subtle bg-muted/10 px-3 py-2.5">
        <Button
          size="sm"
          variant={installed ? "outline" : "default"}
          className={cn(
            "min-h-10 w-full rounded-lg text-xs shadow-none transition-all sm:min-h-9",
            !installed &&
              "shadow-[var(--shadow-xs)] hover:shadow-[var(--shadow-sm)]",
          )}
          disabled={installing}
          onClick={handleInstallToggle}
          aria-label={
            installed
              ? t.agentWorld.uninstallAriaLabel(agent.display_name)
              : t.agentWorld.installAriaLabel(agent.display_name)
          }
        >
          {installing ? (
            <Loader2Icon className="mr-1 h-3 w-3 animate-spin" />
          ) : !installed && hasCapabilityPack ? (
            <SparklesIcon className="mr-1 h-3 w-3" />
          ) : null}
          {installed
            ? t.agentWorld.agentInstalled
            : hasCapabilityPack
              ? t.agentWorld.assembleCapabilityPack
              : t.agentWorld.installThisAgent}
        </Button>
      </CardFooter>
    </Card>
  );
}
