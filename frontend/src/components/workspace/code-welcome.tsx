import {
  BugIcon,
  PlusIcon,
  RefreshCwIcon,
  RocketIcon,
  SearchIcon,
  TestTubeIcon,
  WrenchIcon,
} from "lucide-react";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface CodeWelcomeProps {
  workDir: string;
  onSubmit?: (prompt: string) => void;
  className?: string;
}

export function CodeWelcome({
  workDir,
  onSubmit,
  className,
}: CodeWelcomeProps) {
  const { t } = useI18n();
  const shortDir = workDir.split(/[\\/]/).pop() || workDir;

  const actions = [
    {
      icon: BugIcon,
      label: t.codeWelcome.fixBug,
      prompt: t.codeWelcome.fixBugPrompt,
      color: "text-destructive",
    },
    {
      icon: PlusIcon,
      label: t.codeWelcome.addFeature,
      prompt: t.codeWelcome.addFeaturePrompt,
      color: "text-success",
    },
    {
      icon: RefreshCwIcon,
      label: t.codeWelcome.refactor,
      prompt: t.codeWelcome.refactorPrompt,
      color: "text-info",
    },
    {
      icon: TestTubeIcon,
      label: t.codeWelcome.writeTests,
      prompt: t.codeWelcome.writeTestsPrompt,
      color: "text-warning",
    },
    {
      icon: SearchIcon,
      label: t.codeWelcome.explainCode,
      prompt: t.codeWelcome.explainCodePrompt,
      color: "text-chart-1",
    },
    {
      icon: WrenchIcon,
      label: t.codeWelcome.optimize,
      prompt: t.codeWelcome.optimizePrompt,
      color: "text-chart-7",
    },
  ];

  return (
    <div
      className={cn(
        "mx-auto flex w-full max-w-lg flex-col items-center gap-6 px-4 py-6",
        className,
      )}
    >
      <div className="flex flex-col items-center gap-2">
        <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10">
          <RocketIcon className="size-6 text-primary" />
        </div>
        <div className="text-center">
          <h2 className="text-lg font-semibold">{t.codeWelcome.title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {shortDir}
            </code>
          </p>
        </div>
      </div>

      <div className="grid w-full grid-cols-2 gap-2 sm:grid-cols-3">
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <button
              key={action.label}
              type="button"
              onClick={() => onSubmit?.(action.prompt)}
              className={cn(
                "flex flex-col items-center gap-2 rounded-lg border border-border-default bg-card/50 px-3 py-4",
                "text-center transition-all duration-fast",
                "hover:border-border hover:bg-muted/50 hover:shadow-[var(--shadow-xs)]",
                "active:scale-[0.98]",
              )}
            >
              <Icon className={cn("size-5", action.color)} />
              <span className="text-xs font-medium">{action.label}</span>
            </button>
          );
        })}
      </div>

      <p className="text-center text-xs text-muted-foreground/60">
        {t.codeWelcome.hint}
      </p>
    </div>
  );
}
