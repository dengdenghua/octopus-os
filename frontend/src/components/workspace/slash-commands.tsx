import { swallow } from "@/core/utils/log";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { usePlugins } from "@/core/plugins/hooks";
import { useSkills } from "@/core/skills/hooks";
import { cn } from "@/lib/utils";
import {
  ActivityIcon,
  BookOpenIcon,
  BrainIcon,
  CircleDotIcon,
  CoinsIcon,
  EraserIcon,
  FlaskConicalIcon,
  FolderPlusIcon,
  GitCommitHorizontalIcon,
  LayersIcon,
  Minimize2Icon,
  MonitorIcon,
  PackageIcon,
  PlayIcon,
  PuzzleIcon,
  RocketIcon,
  SearchIcon,
  SettingsIcon,
  SwordsIcon,
  TerminalIcon,
  UploadCloudIcon,
  UserIcon,
  WrenchIcon,
  ZapIcon,
} from "lucide-react";
import {
  type RefObject,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export interface SlashCommand {
  id: string;
  label: string;
  description: string;
  icon: React.ElementType;
  action: () => void;
}

// Hardcoded personality preset list (matches backend BUILTIN_TEMPLATES).
// Kept in sync manually — these are static built-in presets.
const PERSONALITY_PRESETS: { name: string; display_name: string }[] = [
  { name: "researcher", display_name: "Research Analyst" },
  { name: "code-reviewer", display_name: "Code Reviewer" },
  { name: "copywriter", display_name: "Copywriter" },
  { name: "data-analyst", display_name: "Data Analyst" },
  { name: "devops-engineer", display_name: "DevOps Engineer" },
  { name: "concise", display_name: "Concise" },
  { name: "teacher", display_name: "Teacher" },
  { name: "creative", display_name: "Creative Writer" },
  { name: "pirate", display_name: "Pirate" },
  { name: "shakespeare", display_name: "Shakespeare" },
  { name: "philosopher", display_name: "Philosopher" },
  { name: "technical", display_name: "Technical Expert" },
  { name: "helpful", display_name: "Helpful Assistant" },
  { name: "noir", display_name: "Film Noir Detective" },
];

interface SlashCommandOverlayProps {
  inputRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (value: string) => void;
  onClear?: () => void;
  onCompact?: () => void;
  onModeChange?: (mode: string) => void;
  onModelChange?: (modelName: string) => void;
  onSendMessage?: (prompt: string) => void;
  onSwitchPanel?: (panel: string) => void;
  onPersonalityApply?: (name: string) => void;
  models?: { name: string; display_name: string }[];
  currentMode?: string;
}

export function useSlashCommands({
  inputRef: _inputRef,
  value,
  onChange,
  onClear,
  onCompact,
  onModeChange,
  onModelChange,
  onSendMessage,
  onSwitchPanel,
  onPersonalityApply,
  models,
  currentMode: _currentMode,
}: SlashCommandOverlayProps) {
  const { t } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [filter, setFilter] = useState("");
  const triggerRef = useRef(false);
  const { plugins } = usePlugins();
  const { skills } = useSkills();

  const commands: SlashCommand[] = useMemo(() => {
    const cmds: SlashCommand[] = [
      {
        id: "clear",
        label: "/clear",
        description: t.slashCommands.clear,
        icon: EraserIcon,
        action: () => {
          onChange("");
          onClear?.();
          setIsOpen(false);
        },
      },
      {
        id: "compact",
        label: "/compact",
        description: t.slashCommands.compact,
        icon: Minimize2Icon,
        action: () => {
          onCompact?.();
          setIsOpen(false);
        },
      },
    ];

    if (onModeChange) {
      cmds.push({
        id: "mode",
        label: "/mode",
        description: t.slashCommands.mode,
        icon: TerminalIcon,
        action: () => {
          setFilter("mode:");
          setIsOpen(true);
        },
      });
    }

    if (onModelChange && models && models.length > 0) {
      cmds.push({
        id: "model",
        label: "/model",
        description: t.slashCommands.model,
        icon: MonitorIcon,
        action: () => {
          setFilter("model:");
          setIsOpen(true);
        },
      });
    }

    cmds.push({
      id: "personality",
      label: "/personality",
      description: t.slashCommands.personality,
      icon: UserIcon,
      action: () => {
        setFilter("personality:");
        setIsOpen(true);
      },
    });

    // Plugin and Skill commands
    cmds.push(
      {
        id: "plugin",
        label: "/plugin",
        description: "Select a plugin to invoke",
        icon: PuzzleIcon,
        action: () => {
          setFilter("plugin:");
          setIsOpen(true);
        },
      },
      {
        id: "skill",
        label: "/skill",
        description: "Select a skill to execute",
        icon: ZapIcon,
        action: () => {
          setFilter("skill:");
          setIsOpen(true);
        },
      },
    );

    // Code-focused commands
    if (onSendMessage) {
      cmds.push(
        {
          id: "commit",
          label: "/commit",
          description: t.slashCommands.commit,
          icon: GitCommitHorizontalIcon,
          action: () => {
            onSendMessage(
              "Review all current changes with `git diff`, then create a well-structured commit with a descriptive message.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "review",
          label: "/review",
          description: t.slashCommands.review,
          icon: SearchIcon,
          action: () => {
            onSendMessage(
              "Run `git diff` and review all changes. Check for bugs, security issues, and style problems. Provide a summary.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "test",
          label: "/test",
          description: t.slashCommands.test,
          icon: FlaskConicalIcon,
          action: () => {
            onSendMessage(
              "Find and run the project's test suite. If any tests fail, analyze the failures and suggest fixes.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "fix",
          label: "/fix",
          description: t.slashCommands.fix,
          icon: WrenchIcon,
          action: () => {
            onSendMessage(
              "Run the project's linter and type checker. Fix all reported errors.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "search",
          label: "/search",
          description: t.slashCommands.search,
          icon: SearchIcon,
          action: () => {
            onChange("/search ");
            setIsOpen(false);
          },
        },
        {
          id: "memory",
          label: "/memory",
          description: t.slashCommands.memory,
          icon: BrainIcon,
          action: () => {
            onSendMessage(
              "Read the CLAUDE.md file in the project root and show its contents. If it doesn't exist, explain how auto-memory works.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "cost",
          label: "/cost",
          description: t.slashCommands.cost,
          icon: CoinsIcon,
          action: () => {
            onSendMessage(
              "Show the current session's token usage and estimated cost breakdown by model.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "context",
          label: "/context",
          description: t.slashCommands.context,
          icon: LayersIcon,
          action: () => {
            onSendMessage(
              "Analyze the current conversation context: how many tokens are used, what's taking up space, and suggest ways to free up context.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "init",
          label: "/init",
          description: t.slashCommands.init,
          icon: FolderPlusIcon,
          action: () => {
            onSendMessage(
              "Analyze this project's structure, find build/run/test commands, and create a CLAUDE.md with the findings.",
            );
            onChange("");
            setIsOpen(false);
          },
        },
      );
    }

    // Panel-switching commands
    if (onSwitchPanel) {
      cmds.push(
        {
          id: "wiki",
          label: "/wiki",
          description: t.slashCommands.wiki,
          icon: BookOpenIcon,
          action: () => {
            onSwitchPanel("wiki");
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "monitor",
          label: "/monitor",
          description: t.slashCommands.monitor,
          icon: ActivityIcon,
          action: () => {
            onSwitchPanel("monitor");
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "arena",
          label: "/arena",
          description: t.slashCommands.arena,
          icon: SwordsIcon,
          action: () => {
            onSwitchPanel("arena");
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "quest",
          label: "/quest",
          description: t.slashCommands.quest,
          icon: RocketIcon,
          action: () => {
            onSwitchPanel("quest");
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "skills",
          label: "/skills",
          description: t.slashCommands.skills,
          icon: PackageIcon,
          action: () => {
            onSwitchPanel("skills");
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "deploy",
          label: "/deploy",
          description: t.slashCommands.deploy,
          icon: UploadCloudIcon,
          action: () => {
            onSwitchPanel("deploy");
            onChange("");
            setIsOpen(false);
          },
        },
      );
    }

    // Teach & Repeat commands
    if (onSwitchPanel) {
      cmds.push(
        {
          id: "record",
          label: "/record",
          description: t.slashCommands.record,
          icon: CircleDotIcon,
          action: () => {
            onSwitchPanel("teach-repeat");
            onChange("");
            setIsOpen(false);
          },
        },
        {
          id: "replay",
          label: "/replay",
          description: t.slashCommands.replay,
          icon: PlayIcon,
          action: () => {
            onSwitchPanel("teach-repeat");
            onChange("");
            setIsOpen(false);
          },
        },
      );
    }

    cmds.push({
      id: "settings",
      label: "/settings",
      description: t.slashCommands.settings,
      icon: SettingsIcon,
      action: () => {
        window.dispatchEvent(new Event("echo:open-settings"));
        setIsOpen(false);
      },
    });

    return cmds;
  }, [
    t,
    onChange,
    onClear,
    onCompact,
    onModeChange,
    onModelChange,
    onSendMessage,
    onSwitchPanel,
    models,
  ]);

  const modeCommands: SlashCommand[] = useMemo(() => {
    if (!onModeChange) return [];
    return [
      {
        id: "mode:ask",
        label: "/mode ask",
        description: t.codeMode.ask,
        icon: TerminalIcon,
        action: () => {
          onModeChange("ask");
          onChange("");
          setIsOpen(false);
        },
      },
      {
        id: "mode:plan",
        label: "/mode plan",
        description: t.codeMode.plan,
        icon: TerminalIcon,
        action: () => {
          onModeChange("plan");
          onChange("");
          setIsOpen(false);
        },
      },
      {
        id: "mode:agent",
        label: "/mode agent",
        description: t.codeMode.agent,
        icon: TerminalIcon,
        action: () => {
          onModeChange("agent");
          onChange("");
          setIsOpen(false);
        },
      },
      {
        id: "mode:solo",
        label: "/mode solo",
        description: t.codeMode.solo,
        icon: TerminalIcon,
        action: () => {
          onModeChange("solo");
          onChange("");
          setIsOpen(false);
        },
      },
    ];
  }, [t, onModeChange, onChange]);

  const modelCommands: SlashCommand[] = useMemo(() => {
    if (!onModelChange || !models) return [];
    return models.map((m) => ({
      id: `model:${m.name}`,
      label: `/model ${m.display_name}`,
      description: m.name,
      icon: MonitorIcon,
      action: () => {
        onModelChange(m.name);
        onChange("");
        setIsOpen(false);
      },
    }));
  }, [models, onModelChange, onChange]);

  const personalityCommands: SlashCommand[] = useMemo(() => {
    return PERSONALITY_PRESETS.map((p) => ({
      id: `personality:${p.name}`,
      label: `/personality ${p.display_name}`,
      description: p.name,
      icon: UserIcon,
      action: () => {
        if (onPersonalityApply) {
          onPersonalityApply(p.name);
        } else {
          // Fallback: call the overlay API directly
          fetch(
            `${getBackendBaseURL()}/api/personality/templates/${p.name}/overlay`,
            {
              method: "POST",
              headers: authHeaders(),
            },
          )
            .then((res) => res.json())
            .catch((e) => {
              swallow(e);
            });
        }
        onChange("");
        setIsOpen(false);
      },
    }));
  }, [onPersonalityApply, onChange]);

  const pluginCommands: SlashCommand[] = useMemo(() => {
    return plugins.map((plugin) => ({
      id: `plugin:${plugin.id}`,
      label: `/plugin ${plugin.name}`,
      description: plugin.description || plugin.name,
      icon: PuzzleIcon,
      action: () => {
        // Insert @plugin:id token and keep cursor ready for user input
        onChange(`@plugin:${plugin.id} `);
        setIsOpen(false);
      },
    }));
  }, [plugins, onChange]);

  const skillCommands: SlashCommand[] = useMemo(() => {
    return skills.map((skill) => ({
      id: `skill:${skill.name}`,
      label: `/skill ${skill.name}`,
      description: skill.description || skill.name,
      icon: ZapIcon,
      action: () => {
        // Insert @skill:name token and keep cursor ready for user input
        onChange(`@skill:${skill.name} `);
        setIsOpen(false);
      },
    }));
  }, [skills, onChange]);

  const activeCommands = useMemo(() => {
    if (filter.startsWith("mode:")) {
      const sub = filter.slice(5);
      return modeCommands.filter((c) =>
        c.label.toLowerCase().includes(sub.toLowerCase()),
      );
    }
    if (filter.startsWith("model:")) {
      const sub = filter.slice(6);
      return modelCommands.filter(
        (c) =>
          c.label.toLowerCase().includes(sub.toLowerCase()) ||
          c.description.toLowerCase().includes(sub.toLowerCase()),
      );
    }
    if (filter.startsWith("personality:")) {
      const sub = filter.slice(12);
      return personalityCommands.filter(
        (c) =>
          c.label.toLowerCase().includes(sub.toLowerCase()) ||
          c.description.toLowerCase().includes(sub.toLowerCase()),
      );
    }
    if (filter.startsWith("plugin:")) {
      const sub = filter.slice(7);
      return pluginCommands.filter(
        (c) =>
          c.label.toLowerCase().includes(sub.toLowerCase()) ||
          c.description.toLowerCase().includes(sub.toLowerCase()),
      );
    }
    if (filter.startsWith("skill:")) {
      const sub = filter.slice(6);
      return skillCommands.filter(
        (c) =>
          c.label.toLowerCase().includes(sub.toLowerCase()) ||
          c.description.toLowerCase().includes(sub.toLowerCase()),
      );
    }
    const query = filter.toLowerCase();
    return commands.filter(
      (c) =>
        c.label.toLowerCase().includes(query) ||
        c.description.toLowerCase().includes(query),
    );
  }, [
    filter,
    commands,
    modeCommands,
    modelCommands,
    personalityCommands,
    pluginCommands,
    skillCommands,
  ]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [activeCommands]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!isOpen || activeCommands.length === 0) {
        if (e.key === "/" && value === "") {
          setIsOpen(true);
          setFilter("");
          triggerRef.current = true;
        }
        return;
      }

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => (i + 1) % activeCommands.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex(
          (i) => (i - 1 + activeCommands.length) % activeCommands.length,
        );
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        activeCommands[selectedIndex]?.action();
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setIsOpen(false);
        setFilter("");
        return;
      }
      if (e.key === "Backspace" && value === "/") {
        setIsOpen(false);
        setFilter("");
        return;
      }
    },
    [isOpen, activeCommands, selectedIndex, value],
  );

  useEffect(() => {
    if (value.startsWith("/")) {
      setFilter(value.slice(1));
    } else {
      if (isOpen) {
        setIsOpen(false);
        setFilter("");
      }
    }
  }, [value, isOpen]);

  return {
    isOpen,
    activeCommands,
    selectedIndex,
    handleKeyDown,
  };
}

export function SlashCommandPopup({
  commands,
  selectedIndex,
  className,
}: {
  commands: SlashCommand[];
  selectedIndex: number;
  className?: string;
}) {
  if (commands.length === 0) return null;

  return (
    <div
      className={cn(
        "bg-popover text-popover-foreground absolute bottom-full left-0 z-50 mb-1 w-72 overflow-hidden rounded-lg border shadow-[var(--shadow-md)]",
        className,
      )}
    >
      <div className="max-h-64 overflow-y-auto p-1">
        {commands.map((cmd, index) => {
          const Icon = cmd.icon;
          return (
            <button
              key={cmd.id}
              className={cn(
                "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                index === selectedIndex
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/50",
              )}
              onClick={cmd.action}
            >
              <Icon className="text-muted-foreground size-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="font-medium">{cmd.label}</div>
                <div className="text-muted-foreground truncate text-xs">
                  {cmd.description}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
