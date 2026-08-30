import { useCallback, useEffect, useState } from "react";
import {
  MessageSquareIcon,
  CodeIcon,
  UsersIcon,
  GlobeIcon,
  GitBranchIcon,
  ShoppingBagIcon,
  ClipboardListIcon,
  SearchIcon,
  PanelLeftIcon,
  TerminalIcon,
  AtSignIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  RocketIcon,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { OnboardingStep } from "./onboarding-step";

const STORAGE_KEY = "echo:onboarding_completed";
const TOTAL_STEPS = 4;

export function OnboardingGuide() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    const completed = localStorage.getItem(STORAGE_KEY);
    if (!completed) {
      setOpen(true);
    }
  }, []);

  const handleDismiss = useCallback(() => {
    localStorage.setItem(STORAGE_KEY, "true");
    setOpen(false);
  }, []);

  const handleNext = useCallback(() => {
    if (step < TOTAL_STEPS - 1) {
      setStep((s) => s + 1);
    } else {
      handleDismiss();
    }
  }, [step, handleDismiss]);

  const handlePrev = useCallback(() => {
    setStep((s) => Math.max(0, s - 1));
  }, []);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleDismiss()}>
      <DialogContent
        className="sm:max-w-lg md:max-w-xl overflow-hidden"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">{t.onboarding.title}</DialogTitle>
        <DialogDescription className="sr-only">
          {t.onboarding.welcomeDesc}
        </DialogDescription>

        {/* Step content with transition */}
        <div className="min-h-[320px] flex items-center justify-center">
          <div
            key={step}
            className="w-full animate-in fade-in slide-in-from-right-4 duration-slow"
          >
            {step === 0 && <WelcomeStep />}
            {step === 1 && <ChatModesStep />}
            {step === 2 && <KeyFeaturesStep />}
            {step === 3 && <QuickTipsStep />}
          </div>
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between pt-2 border-t">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDismiss}
            className="text-muted-foreground"
          >
            {t.onboarding.skip}
          </Button>

          {/* Step dots */}
          <div className="flex items-center gap-1.5">
            {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
              <button
                key={i}
                onClick={() => setStep(i)}
                className={cn(
                  "size-2 rounded-lg transition-all duration-base",
                  i === step
                    ? "bg-primary w-5"
                    : "bg-muted-foreground/30 hover:bg-muted-foreground/50",
                )}
                aria-label={t.onboarding.goToStep(i + 1)}
              />
            ))}
          </div>

          <div className="flex items-center gap-2">
            {step > 0 && (
              <Button variant="outline" size="sm" onClick={handlePrev}>
                <ChevronLeftIcon className="size-4" />
                {t.onboarding.previous}
              </Button>
            )}
            <Button size="sm" onClick={handleNext}>
              {step < TOTAL_STEPS - 1 ? (
                <>
                  {t.onboarding.next}
                  <ChevronRightIcon className="size-4" />
                </>
              ) : (
                <>
                  {t.onboarding.getStarted}
                  <RocketIcon className="size-4" />
                </>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ---------- Step 1: Welcome ---------- */
function WelcomeStep() {
  const { t } = useI18n();
  return (
    <OnboardingStep
      title={t.onboarding.welcomeToEcho}
      description={t.onboarding.yourAIPlatform}
    >
      <div className="flex flex-col items-center gap-4">
        <div className="text-6xl">&#128025;</div>
        <p className="text-muted-foreground text-sm max-w-sm">
          {t.onboarding.welcomeDesc}
        </p>
      </div>
    </OnboardingStep>
  );
}

/* ---------- Step 2: Task Capabilities ---------- */
function ChatModesStep() {
  const { t } = useI18n();
  const modes = [
    {
      icon: MessageSquareIcon,
      title: t.onboarding.modeChat,
      desc: t.onboarding.modeChatDesc,
      color: "text-info bg-info/10",
    },
    {
      icon: CodeIcon,
      title: t.onboarding.modeCode,
      desc: t.onboarding.modeCodeDesc,
      color: "text-success bg-success/10",
    },
    {
      icon: UsersIcon,
      title: t.onboarding.modeTeam,
      desc: t.onboarding.modeTeamDesc,
      color: "text-chart-1 bg-chart-1/10",
    },
  ];

  return (
    <OnboardingStep
      title={t.onboarding.chatModes}
      description={t.onboarding.chatModesDesc}
    >
      <div className="grid grid-cols-3 gap-3">
        {modes.map((mode) => (
          <div
            key={mode.title}
            className="flex flex-col items-center gap-2 rounded-lg border p-4 hover:bg-accent/50 transition-colors"
          >
            <div className={cn("rounded-lg p-2.5", mode.color)}>
              <mode.icon className="size-5" />
            </div>
            <span className="font-medium text-sm">{mode.title}</span>
            <span className="text-muted-foreground text-xs text-center">
              {mode.desc}
            </span>
          </div>
        ))}
      </div>
    </OnboardingStep>
  );
}

/* ---------- Step 3: Key Features ---------- */
function KeyFeaturesStep() {
  const { t } = useI18n();
  const features = [
    {
      icon: GlobeIcon,
      title: t.onboarding.featureAgentWorld,
      desc: t.onboarding.featureAgentWorldDesc,
      color: "text-info bg-info/10",
    },
    {
      icon: GitBranchIcon,
      title: t.onboarding.featureWorkflows,
      desc: t.onboarding.featureWorkflowsDesc,
      color: "text-chart-7 bg-chart-7/10",
    },
    {
      icon: ShoppingBagIcon,
      title: t.onboarding.featureSkillsMarket,
      desc: t.onboarding.featureSkillsMarketDesc,
      color: "text-chart-3 bg-chart-3/10",
    },
    {
      icon: ClipboardListIcon,
      title: t.onboarding.featureTaskBoard,
      desc: t.onboarding.featureTaskBoardDesc,
      color: "text-success bg-success/10",
    },
  ];

  return (
    <OnboardingStep
      title={t.onboarding.keyFeatures}
      description={t.onboarding.keyFeaturesDesc}
    >
      <div className="grid grid-cols-2 gap-3">
        {features.map((feature) => (
          <div
            key={feature.title}
            className="flex items-start gap-3 rounded-lg border p-3 hover:bg-accent/50 transition-colors"
          >
            <div className={cn("rounded-lg p-2 shrink-0", feature.color)}>
              <feature.icon className="size-4" />
            </div>
            <div className="text-left">
              <div className="font-medium text-sm">{feature.title}</div>
              <div className="text-muted-foreground text-xs">
                {feature.desc}
              </div>
            </div>
          </div>
        ))}
      </div>
    </OnboardingStep>
  );
}

/* ---------- Step 4: Quick Tips ---------- */
function QuickTipsStep() {
  const { t } = useI18n();
  const shortcuts = [
    {
      icon: SearchIcon,
      keys: "Ctrl + K",
      label: t.onboarding.tipSearch,
    },
    {
      icon: PanelLeftIcon,
      keys: "Ctrl + B",
      label: t.onboarding.tipToggleSidebar,
    },
    {
      icon: TerminalIcon,
      keys: "/",
      label: t.onboarding.tipSlashCommands,
    },
    {
      icon: AtSignIcon,
      keys: "@",
      label: t.onboarding.tipMentionAgents,
    },
  ];

  return (
    <OnboardingStep
      title={t.onboarding.quickTips}
      description={t.onboarding.quickTipsDesc}
    >
      <div className="grid grid-cols-2 gap-3">
        {shortcuts.map((shortcut) => (
          <div
            key={shortcut.label}
            className="flex items-center gap-3 rounded-lg border p-3 hover:bg-accent/50 transition-colors"
          >
            <shortcut.icon className="size-4 text-muted-foreground shrink-0" />
            <div className="text-left">
              <kbd className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono font-medium">
                {shortcut.keys}
              </kbd>
              <div className="text-muted-foreground text-xs mt-1">
                {shortcut.label}
              </div>
            </div>
          </div>
        ))}
      </div>
    </OnboardingStep>
  );
}
