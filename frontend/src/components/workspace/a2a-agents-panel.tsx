import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  GlobeIcon,
  Loader2Icon,
  NetworkIcon,
  PlusIcon,
  RefreshCwIcon,
  SendIcon,
  Trash2Icon,
  WifiIcon,
  WifiOffIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { swallow } from "@/core/utils/log";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentSkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
  examples?: string[];
}

interface AgentCapabilities {
  streaming: boolean;
  pushNotifications: boolean;
  multiTurn: boolean;
}

interface RemoteAgent {
  agent_id: string;
  base_url: string;
  name: string;
  description: string;
  version: string;
  status: "active" | "unreachable" | "unknown";
  registered_at: string;
  updated_at: string;
  last_health_check: string | null;
  capabilities: AgentCapabilities;
  skills: AgentSkill[];
}

interface TaskResult {
  id: string;
  status: {
    state: string;
    message?: string;
  };
  messages: Array<{
    role: string;
    parts: Array<{ type: string; text?: string }>;
  }>;
  artifacts: Array<{
    name?: string;
    parts: Array<{ type: string; text?: string }>;
  }>;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const api = {
  async listAgents(): Promise<{ agents: RemoteAgent[]; count: number }> {
    const res = await fetch(`${getBackendBaseURL()}/api/a2a/agents`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to list agents: ${res.statusText}`);
    return res.json();
  },

  async registerAgent(url: string): Promise<RemoteAgent> {
    const res = await fetch(`${getBackendBaseURL()}/api/a2a/agents/register`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ url }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Registration failed: ${res.statusText}`);
    }
    return res.json();
  },

  async unregisterAgent(agentId: string): Promise<void> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/a2a/agents/${agentId}`,
      { method: "DELETE" },
    );
    if (!res.ok) throw new Error(`Unregister failed: ${res.statusText}`);
  },

  async healthCheck(
    agentId: string,
  ): Promise<{ healthy: boolean; status: string; error?: string }> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/a2a/agents/${agentId}/health`,
      { method: "POST" },
    );
    if (!res.ok) throw new Error(`Health check failed: ${res.statusText}`);
    return res.json();
  },

  async sendTask(agentId: string, text: string): Promise<TaskResult> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/a2a/agents/${agentId}/send`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, stream: false }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Send failed: ${res.statusText}`);
    }
    return res.json();
  },
};

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function A2AAgentsPanel({ className }: { className?: string }) {
  const { t } = useI18n();
  const [agents, setAgents] = useState<RemoteAgent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRegister, setShowRegister] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.listAgents();
      setAgents(data.agents);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAgents();
    const timer = setInterval(fetchAgents, 30000);
    return () => clearInterval(timer);
  }, [fetchAgents]);

  const selected = useMemo(
    () => agents.find((a) => a.agent_id === selectedAgent) ?? null,
    [agents, selectedAgent],
  );

  return (
    <div className={cn("flex flex-col", className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <NetworkIcon className="size-4" />
          {t.a2a.title}
        </div>
        <div className="flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
                onClick={fetchAgents}
                disabled={loading}
              >
                <RefreshCwIcon
                  className={cn("size-3.5", loading && "animate-spin")}
                />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t.a2a.refresh}</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
                onClick={() => setShowRegister(!showRegister)}
              >
                {showRegister ? (
                  <XIcon className="size-3.5" />
                ) : (
                  <PlusIcon className="size-3.5" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {showRegister ? t.common.cancel : t.a2a.registerAgent}
            </TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 border-b bg-destructive/5 px-4 py-2 text-xs text-destructive">
          <AlertCircleIcon className="size-3.5 shrink-0" />
          <span className="truncate">{error}</span>
        </div>
      )}

      {/* Register form */}
      {showRegister && (
        <RegisterForm
          onRegistered={() => {
            setShowRegister(false);
            fetchAgents();
          }}
          onCancel={() => setShowRegister(false)}
        />
      )}

      {/* Content */}
      {selected ? (
        <AgentDetailView
          agent={selected}
          onBack={() => setSelectedAgent(null)}
          onRefresh={fetchAgents}
        />
      ) : agents.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
          {agents.map((agent) => (
            <AgentCard
              key={agent.agent_id}
              agent={agent}
              onClick={() => setSelectedAgent(agent.agent_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Register form
// ---------------------------------------------------------------------------

function RegisterForm({
  onRegistered,
  onCancel: _onCancel,
}: {
  onRegistered: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [url, setUrl] = useState("");
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setRegistering(true);
    setError(null);
    try {
      await api.registerAgent(url.trim());
      onRegistered();
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setRegistering(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="border-b px-4 py-3">
      <label className="text-muted-foreground mb-1.5 block text-xs font-medium">
        {t.a2a.remoteAgentUrl}
      </label>
      <div className="flex gap-2">
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://agent.example.com"
          className="border-input bg-background flex-1 rounded-lg border px-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-info"
          disabled={registering}
          autoFocus
        />
        <button
          type="submit"
          disabled={!url.trim() || registering}
          className="inline-flex items-center gap-1.5 rounded-lg bg-info px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-info disabled:opacity-50"
        >
          {registering ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <GlobeIcon className="size-3" />
          )}
          {t.a2a.connect}
        </button>
      </div>
      {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  const { t } = useI18n();
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-12">
      <div className="rounded-lg bg-muted/40 p-4">
        <NetworkIcon className="text-muted-foreground/40 size-8" />
      </div>
      <p className="text-muted-foreground text-sm font-medium">
        {t.a2a.noAgents}
      </p>
      <p className="text-muted-foreground/60 text-center text-xs leading-relaxed whitespace-pre-line">
        {t.a2a.noAgentsDesc}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent card
// ---------------------------------------------------------------------------

function AgentCard({
  agent,
  onClick,
}: {
  agent: RemoteAgent;
  onClick: () => void;
}) {
  const { t } = useI18n();
  const isActive = agent.status === "active";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onClick}
          className="bg-card flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:shadow-[var(--shadow-xs)]"
        >
          {/* Status indicator */}
          <div
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-lg",
              isActive ? "bg-success/10" : "bg-destructive/10",
            )}
          >
            {isActive ? (
              <WifiIcon className="size-4 text-success" />
            ) : (
              <WifiOffIcon className="size-4 text-destructive" />
            )}
          </div>

          {/* Info */}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{agent.name}</span>
              <span className="text-muted-foreground text-xs">
                v{agent.version}
              </span>
            </div>
            <p className="text-muted-foreground mt-0.5 truncate text-xs">
              {agent.description || agent.base_url}
            </p>
            {agent.skills.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {agent.skills.slice(0, 3).map((skill) => (
                  <span
                    key={skill.id}
                    className="bg-muted rounded px-1.5 py-0.5 text-xs"
                  >
                    {skill.name}
                  </span>
                ))}
                {agent.skills.length > 3 && (
                  <span className="text-muted-foreground text-xs">
                    +{agent.skills.length - 3}
                  </span>
                )}
              </div>
            )}
          </div>

          <ChevronRightIcon className="text-muted-foreground size-4 shrink-0" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="left" className="max-w-64">
        <p className="text-xs">{agent.base_url}</p>
        <p className="text-muted-foreground mt-1 text-xs">
          {t.a2a.status}: {agent.status} | {t.a2a.skills}: {agent.skills.length}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

// ---------------------------------------------------------------------------
// Agent detail view
// ---------------------------------------------------------------------------

function AgentDetailView({
  agent,
  onBack,
  onRefresh,
}: {
  agent: RemoteAgent;
  onBack: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const [checking, setChecking] = useState(false);
  const [healthResult, setHealthResult] = useState<{
    healthy: boolean;
    error?: string;
  } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [taskText, setTaskText] = useState("");
  const [sending, setSending] = useState(false);
  const [taskResult, setTaskResult] = useState<TaskResult | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);

  const handleHealthCheck = async () => {
    setChecking(true);
    setHealthResult(null);
    try {
      const result = await api.healthCheck(agent.agent_id);
      setHealthResult(result);
    } catch (err) {
      swallow(err);
      setHealthResult({
        healthy: false,
        error: err instanceof Error ? err.message : "Check failed",
      });
    } finally {
      setChecking(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await api.unregisterAgent(agent.agent_id);
      onRefresh();
      onBack();
    } catch (e) {
      swallow(e);
      setDeleting(false);
    }
  };

  const handleSendTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskText.trim()) return;

    setSending(true);
    setTaskResult(null);
    setTaskError(null);
    try {
      const result = await api.sendTask(agent.agent_id, taskText.trim());
      setTaskResult(result);
      setTaskText("");
    } catch (err) {
      swallow(err);
      setTaskError(err instanceof Error ? err.message : "Send failed");
    } finally {
      setSending(false);
    }
  };

  const isActive = agent.status === "active";

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <button
          type="button"
          onClick={onBack}
          aria-label={t.common.back}
          className="text-muted-foreground hover:text-foreground"
        >
          <ChevronRightIcon className="size-4 rotate-180" />
        </button>
        <div
          className={cn(
            "flex size-7 items-center justify-center rounded-lg",
            isActive ? "bg-success/10" : "bg-destructive/10",
          )}
        >
          {isActive ? (
            <WifiIcon className="size-3.5 text-success" />
          ) : (
            <WifiOffIcon className="size-3.5 text-destructive" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-sm font-semibold">{agent.name}</span>
          <span className="text-muted-foreground ml-1.5 text-xs">
            v{agent.version}
          </span>
        </div>
      </div>

      {/* Info section */}
      <div className="space-y-3 border-b px-4 py-3">
        <div>
          <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
            {t.a2a.endpoint}
          </span>
          <p className="mt-0.5 truncate text-xs">{agent.base_url}</p>
        </div>
        {agent.description && (
          <div>
            <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
              {t.a2a.description}
            </span>
            <p className="text-foreground/80 mt-0.5 text-xs leading-relaxed">
              {agent.description}
            </p>
          </div>
        )}
        <div>
          <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
            {t.a2a.capabilities}
          </span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <CapabilityBadge
              label={t.a2a.streaming}
              enabled={agent.capabilities?.streaming}
            />
            <CapabilityBadge
              label={t.a2a.multiTurn}
              enabled={agent.capabilities?.multiTurn}
            />
            <CapabilityBadge
              label={t.a2a.push}
              enabled={agent.capabilities?.pushNotifications}
            />
          </div>
        </div>
      </div>

      {/* Skills */}
      {agent.skills.length > 0 && (
        <div className="border-b px-4 py-3">
          <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
            {t.a2a.skills} ({agent.skills.length})
          </span>
          <div className="mt-2 space-y-2">
            {agent.skills.map((skill) => (
              <div key={skill.id} className="rounded-lg bg-muted/30 px-3 py-2">
                <p className="text-xs font-medium">{skill.name}</p>
                {skill.description && (
                  <p className="text-muted-foreground mt-0.5 text-xs">
                    {skill.description}
                  </p>
                )}
                {skill.tags.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {skill.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded bg-muted px-1 py-0.5 text-xs"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="border-b px-4 py-3">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleHealthCheck}
            disabled={checking}
            className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-50"
          >
            {checking ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3" />
            )}
            {t.a2a.testConnection}
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleting}
            className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/30 px-3 py-1.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/5 disabled:opacity-50 dark:hover:bg-destructive/20"
          >
            {deleting ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <Trash2Icon className="size-3" />
            )}
            {t.a2a.remove}
          </button>
        </div>

        {/* Health check result */}
        {healthResult && (
          <div
            className={cn(
              "mt-2 flex items-center gap-2 rounded-lg px-3 py-2 text-xs",
              healthResult.healthy
                ? "bg-success/10 text-success"
                : "bg-destructive/10 text-destructive",
            )}
          >
            {healthResult.healthy ? (
              <CheckCircle2Icon className="size-3.5" />
            ) : (
              <AlertCircleIcon className="size-3.5" />
            )}
            {healthResult.healthy
              ? t.a2a.connectionSuccessful
              : healthResult.error || t.a2a.connectionFailed}
          </div>
        )}
      </div>

      {/* Send task */}
      <div className="px-4 py-3">
        <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
          {t.a2a.sendTask}
        </span>
        <form onSubmit={handleSendTask} className="mt-2 flex gap-2">
          <input
            type="text"
            value={taskText}
            onChange={(e) => setTaskText(e.target.value)}
            placeholder={t.a2a.sendTaskPlaceholder}
            className="border-input bg-background flex-1 rounded-lg border px-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-info"
            disabled={sending}
          />
          <button
            type="submit"
            disabled={!taskText.trim() || sending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-info px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-info disabled:opacity-50"
          >
            {sending ? (
              <Loader2Icon className="size-3 animate-spin" />
            ) : (
              <SendIcon className="size-3" />
            )}
            {t.a2a.send}
          </button>
        </form>

        {/* Task error */}
        {taskError && (
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <AlertCircleIcon className="size-3.5 shrink-0" />
            {taskError}
          </div>
        )}

        {/* Task result */}
        {taskResult && (
          <div className="mt-3">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-lg px-2 py-0.5 text-xs font-medium",
                  taskResult.status.state === "completed"
                    ? "bg-success/10 text-success"
                    : taskResult.status.state === "failed"
                      ? "bg-destructive/10 text-destructive"
                      : "bg-info/10 text-info",
                )}
              >
                {taskResult.status.state}
              </span>
              <span className="text-muted-foreground text-xs">
                Task {taskResult.id.slice(0, 8)}
              </span>
            </div>

            {/* Show agent response */}
            {taskResult.messages
              .filter((m) => m.role === "agent")
              .map((msg, i) => (
                <div key={i} className="mt-2 rounded-lg bg-muted/30 px-3 py-2">
                  {msg.parts.map((part, j) => (
                    <p key={j} className="text-xs leading-relaxed">
                      {part.text || t.a2a.nonTextContent}
                    </p>
                  ))}
                </div>
              ))}

            {/* Show artifacts */}
            {taskResult.artifacts.length > 0 && (
              <div className="mt-2">
                <span className="text-muted-foreground text-xs">
                  {t.a2a.artifacts}: {taskResult.artifacts.length}
                </span>
                {taskResult.artifacts.map((artifact, i) => (
                  <div
                    key={i}
                    className="mt-1 rounded-lg border px-3 py-2 text-xs"
                  >
                    <span className="font-medium">
                      {artifact.name || `Artifact ${i + 1}`}
                    </span>
                    {artifact.parts.map((part, j) => (
                      <p
                        key={j}
                        className="text-muted-foreground mt-1 leading-relaxed"
                      >
                        {part.text || t.a2a.binaryContent}
                      </p>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Capability badge
// ---------------------------------------------------------------------------

function CapabilityBadge({
  label,
  enabled,
}: {
  label: string;
  enabled?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-lg px-2 py-0.5 text-xs font-medium",
        enabled
          ? "bg-success/10 text-success"
          : "bg-muted text-muted-foreground",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-lg",
          enabled ? "bg-success" : "bg-muted-foreground/30",
        )}
      />
      {label}
    </span>
  );
}
