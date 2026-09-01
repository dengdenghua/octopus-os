import {
  AlertCircleIcon,
  BarChart3Icon,
  CheckIcon,
  ChevronLeftIcon,
  ClipboardIcon,
  CodeIcon,
  EyeOffIcon,
  GlobeIcon,
  KeyIcon,
  Loader2Icon,
  PlusIcon,
  PowerIcon,
  RefreshCwIcon,
  ScrollTextIcon,
  SendIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { swallow } from "@/core/utils/log";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { copyTextToClipboard } from "@/core/clipboard";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PublishedAPI {
  api_id: string;
  name: string;
  agent_name: string;
  endpoint_path: string;
  description: string;
  rate_limit_rpm: number;
  rate_limit_daily: number;
  enabled: boolean;
  config_overrides: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  key_count: number;
}

interface APIKey {
  key_id: string;
  api_id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
}

interface APIKeyCreated extends APIKey {
  raw_key: string;
}

interface CallLog {
  call_id: string;
  api_id: string;
  key_id: string;
  input_text: string;
  output_text: string;
  status: string;
  latency_ms: number;
  tokens_used: number;
  error_message: string;
  timestamp: string;
}

interface UsageStats {
  api_id: string;
  total_calls: number;
  successful_calls: number;
  error_calls: number;
  rate_limited_calls: number;
  avg_latency_ms: number;
  total_tokens: number;
  calls_today: number;
  calls_this_week: number;
  daily_counts: Array<{
    date: string;
    count: number;
    avg_latency_ms: number;
  }>;
}

interface Agent {
  name: string;
  display_name?: string;
  description: string;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const apiClient = {
  async listPublished(): Promise<PublishedAPI[]> {
    const res = await fetch(`${getBackendBaseURL()}/api/publish`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
    return res.json();
  },

  async getPublished(apiId: string): Promise<PublishedAPI> {
    const res = await fetch(`${getBackendBaseURL()}/api/publish/${apiId}`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
    return res.json();
  },

  async publish(data: {
    name: string;
    agent_name: string;
    endpoint_path: string;
    description: string;
    rate_limit_rpm: number;
    rate_limit_daily: number;
  }): Promise<PublishedAPI> {
    const res = await fetch(`${getBackendBaseURL()}/api/publish`, {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Publish failed: ${res.statusText}`);
    }
    return res.json();
  },

  async update(
    apiId: string,
    data: Partial<PublishedAPI>,
  ): Promise<PublishedAPI> {
    const res = await fetch(`${getBackendBaseURL()}/api/publish/${apiId}`, {
      method: "PUT",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Update failed: ${res.statusText}`);
    }
    return res.json();
  },

  async unpublish(apiId: string): Promise<void> {
    const res = await fetch(`${getBackendBaseURL()}/api/publish/${apiId}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Delete failed: ${res.statusText}`);
  },

  async createKey(apiId: string, name: string): Promise<APIKeyCreated> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/publish/${apiId}/keys`,
      {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ name }),
      },
    );
    if (!res.ok) throw new Error(`Key creation failed: ${res.statusText}`);
    return res.json();
  },

  async listKeys(apiId: string): Promise<APIKey[]> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/publish/${apiId}/keys`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
    return res.json();
  },

  async revokeKey(apiId: string, keyId: string): Promise<void> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/publish/${apiId}/keys/${keyId}`,
      { method: "DELETE", headers: authHeaders() },
    );
    if (!res.ok) throw new Error(`Revoke failed: ${res.statusText}`);
  },

  async getLogs(apiId: string, limit = 50): Promise<CallLog[]> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/publish/${apiId}/logs?limit=${limit}`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
    return res.json();
  },

  async getStats(apiId: string): Promise<UsageStats> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/publish/${apiId}/stats`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
    return res.json();
  },

  async listAgents(): Promise<{ agents: Agent[] }> {
    const res = await fetch(`${getBackendBaseURL()}/api/agents`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
    return res.json();
  },

  async testRun(
    endpointPath: string,
    apiKey: string,
    input: string,
  ): Promise<Record<string, unknown>> {
    const res = await fetch(
      `${getBackendBaseURL()}/api/published/${endpointPath}/run`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({ input }),
      },
    );
    return res.json();
  },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await copyTextToClipboard(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t.clipboard.failedToCopyToClipboard);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors"
    >
      {copied ? (
        <CheckIcon className="size-3" />
      ) : (
        <ClipboardIcon className="size-3" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Publish Form
// ---------------------------------------------------------------------------

function PublishForm({
  agents,
  onPublished,
  onCancel,
}: {
  agents: Agent[];
  onPublished: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [agentName, setAgentName] = useState(agents[0]?.name ?? "");
  const [endpointPath, setEndpointPath] = useState("");
  const [description, setDescription] = useState("");
  const [rateRpm, setRateRpm] = useState(60);
  const [rateDaily, setRateDaily] = useState(1000);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await apiClient.publish({
        name,
        agent_name: agentName,
        endpoint_path: endpointPath,
        description,
        rate_limit_rpm: rateRpm,
        rate_limit_daily: rateDaily,
      });
      onPublished();
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3 border-b p-4">
      <div className="text-sm font-medium">
        {t.apiPublish.publishAgentAsApi}
      </div>

      {error && <div className="text-xs text-destructive">{error}</div>}

      <div className="space-y-2">
        <label className="block text-xs font-medium">
          {t.apiPublish.apiName}
        </label>
        <input
          className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
          placeholder="My Agent API"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <div className="space-y-2">
        <label className="block text-xs font-medium">
          {t.apiPublish.agent}
        </label>
        <select
          className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
          value={agentName}
          onChange={(e) => setAgentName(e.target.value)}
        >
          {agents.map((a) => (
            <option key={a.name} value={a.name}>
              {a.display_name || a.name}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-2">
        <label className="block text-xs font-medium">
          {t.apiPublish.endpointPath}
          <span className="text-muted-foreground ml-1 font-normal">
            {t.apiPublish.endpointPathHint}
          </span>
        </label>
        <div className="flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">/api/published/</span>
          <input
            className="bg-muted/50 border-border flex-1 rounded border px-2 py-1.5 text-xs"
            placeholder="my-agent"
            value={endpointPath}
            onChange={(e) =>
              setEndpointPath(
                e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""),
              )
            }
          />
          <span className="text-muted-foreground">/run</span>
        </div>
      </div>

      <div className="space-y-2">
        <label className="block text-xs font-medium">
          {t.skillsMarket.description}
        </label>
        <textarea
          className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
          rows={2}
          placeholder="Optional description..."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <label className="block text-xs font-medium">
            {t.apiPublish.rpmLimit}
          </label>
          <input
            type="number"
            className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
            value={rateRpm}
            min={1}
            max={10000}
            onChange={(e) => setRateRpm(Number(e.target.value))}
          />
        </div>
        <div className="space-y-1">
          <label className="block text-xs font-medium">
            {t.apiPublish.dailyLimit}
          </label>
          <input
            type="number"
            className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
            value={rateDaily}
            min={1}
            max={1000000}
            onChange={(e) => setRateDaily(Number(e.target.value))}
          />
        </div>
      </div>

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          className="bg-primary text-primary-foreground hover:bg-primary/90 flex-1 rounded px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          disabled={!name || !agentName || !endpointPath || submitting}
          onClick={handleSubmit}
        >
          {submitting ? (
            <Loader2Icon className="mx-auto size-3.5 animate-spin" />
          ) : (
            t.apiPublish.publish
          )}
        </button>
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground rounded px-3 py-1.5 text-xs"
          onClick={onCancel}
        >
          {t.apiPublish.cancel}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// API Detail View
// ---------------------------------------------------------------------------

function APIDetailView({
  api,
  onBack,
  onRefresh,
}: {
  api: PublishedAPI;
  onBack: () => void;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  type Tab = "keys" | "snippets" | "logs" | "stats" | "test";
  const [tab, setTab] = useState<Tab>("snippets");
  const [keys, setKeys] = useState<APIKey[]>([]);
  const [logs, setLogs] = useState<CallLog[]>([]);
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(false);

  // New key management
  const [newKeyName, setNewKeyName] = useState("default");
  const [createdKey, setCreatedKey] = useState<APIKeyCreated | null>(null);
  const [showRawKey, setShowRawKey] = useState(false);

  // Test panel
  const [testInput, setTestInput] = useState("");
  const [testApiKey, setTestApiKey] = useState("");
  const [testResponse, setTestResponse] = useState<string | null>(null);
  const [testRunning, setTestRunning] = useState(false);

  const endpointUrl = `${getBackendBaseURL()}/api/published/${api.endpoint_path}`;

  const fetchKeys = useCallback(async () => {
    try {
      const data = await apiClient.listKeys(api.api_id);
      setKeys(data);
    } catch (e) {
      swallow(e);
    }
  }, [api.api_id]);

  const fetchLogs = useCallback(async () => {
    try {
      const data = await apiClient.getLogs(api.api_id);
      setLogs(data);
    } catch (e) {
      swallow(e);
    }
  }, [api.api_id]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiClient.getStats(api.api_id);
      setStats(data);
    } catch (e) {
      swallow(e);
    }
  }, [api.api_id]);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys]);

  useEffect(() => {
    if (tab === "logs") fetchLogs();
    if (tab === "stats") fetchStats();
  }, [tab, fetchLogs, fetchStats]);

  const handleCreateKey = async () => {
    setLoading(true);
    try {
      const created = await apiClient.createKey(api.api_id, newKeyName);
      setCreatedKey(created);
      setShowRawKey(true);
      fetchKeys();
    } catch (e) {
      swallow(e);
    } finally {
      setLoading(false);
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (
      !(await confirm({
        title: t.apiPublish.revokeKeyConfirmTitle,
        description: t.apiPublish.revokeKeyConfirmDescription,
        confirmLabel: t.apiPublish.revoke,
      }))
    )
      return;
    try {
      await apiClient.revokeKey(api.api_id, keyId);
      fetchKeys();
    } catch (e) {
      swallow(e);
    }
  };

  const handleToggle = async () => {
    try {
      await apiClient.update(api.api_id, { enabled: !api.enabled });
      onRefresh();
    } catch (e) {
      swallow(e);
    }
  };

  const handleTest = async () => {
    setTestRunning(true);
    setTestResponse(null);
    try {
      const result = await apiClient.testRun(
        api.endpoint_path,
        testApiKey,
        testInput,
      );
      setTestResponse(JSON.stringify(result, null, 2));
    } catch (err) {
      swallow(err);
      setTestResponse(
        `Error: ${err instanceof Error ? err.message : "Request failed"}`,
      );
    } finally {
      setTestRunning(false);
    }
  };

  // Code snippets
  const curlSnippet = `curl -X POST "${endpointUrl}/run" \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -d '{"input": "Hello, how are you?"}'`;

  const pythonSnippet = `import requests

response = requests.post(
    "${endpointUrl}/run",
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer YOUR_API_KEY",
    },
    json={"input": "Hello, how are you?"}
)

print(response.json())`;

  const jsSnippet = `const response = await fetch("${endpointUrl}/run", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_API_KEY",
  },
  body: JSON.stringify({ input: "Hello, how are you?" }),
});

const data = await response.json();
console.log(data);`;

  const streamSnippet = `// SSE Streaming
const response = await fetch("${endpointUrl}/stream", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_API_KEY",
  },
  body: JSON.stringify({ input: "Hello, how are you?" }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  console.log(decoder.decode(value));
}`;

  const tabs: { key: Tab; label: string; icon: React.ElementType }[] = [
    { key: "snippets", label: t.apiPublish.tabs.code, icon: CodeIcon },
    { key: "keys", label: t.apiPublish.tabs.keys, icon: KeyIcon },
    { key: "logs", label: t.apiPublish.tabs.logs, icon: ScrollTextIcon },
    { key: "stats", label: t.apiPublish.tabs.stats, icon: BarChart3Icon },
    { key: "test", label: t.apiPublish.tabs.test, icon: SendIcon },
  ];

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {confirmDialog}
      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground rounded p-0.5"
          onClick={onBack}
        >
          <ChevronLeftIcon className="size-4" />
        </button>
        <div className="flex-1 truncate">
          <div className="truncate text-sm font-semibold">{api.name}</div>
          <div className="text-muted-foreground truncate text-xs">
            {api.agent_name}
          </div>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className={cn(
                "rounded p-1 transition-colors",
                api.enabled
                  ? "text-success hover:text-success"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={handleToggle}
            >
              <PowerIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {api.enabled ? t.apiPublish.disable : t.apiPublish.enable}
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Endpoint URL */}
      <div className="border-b px-3 py-2">
        <div className="text-muted-foreground mb-1 text-xs font-medium uppercase">
          {t.apiPublish.endpoint}
        </div>
        <div className="bg-muted/50 flex items-center gap-1.5 rounded px-2 py-1.5">
          <code className="flex-1 truncate text-xs">{endpointUrl}/run</code>
          <CopyButton text={`${endpointUrl}/run`} />
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex border-b">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            className={cn(
              "flex flex-1 items-center justify-center gap-1 py-2 text-xs font-medium transition-colors",
              tab === key
                ? "border-primary text-primary border-b-2"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setTab(key)}
          >
            <Icon className="size-3" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-y-auto">
        {/* Code Snippets */}
        {tab === "snippets" && (
          <div className="space-y-3 p-3">
            <SnippetBlock title="cURL" code={curlSnippet} language="bash" />
            <SnippetBlock
              title="Python"
              code={pythonSnippet}
              language="python"
            />
            <SnippetBlock
              title="JavaScript"
              code={jsSnippet}
              language="javascript"
            />
            <SnippetBlock
              title="Streaming (JS)"
              code={streamSnippet}
              language="javascript"
            />
          </div>
        )}

        {/* API Keys */}
        {tab === "keys" && (
          <div className="space-y-3 p-3">
            {/* Create key */}
            <div className="flex items-center gap-2">
              <input
                className="bg-muted/50 border-border flex-1 rounded border px-2 py-1.5 text-xs"
                placeholder={t.apiPublish.keyName}
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
              />
              <button
                type="button"
                className="bg-primary text-primary-foreground hover:bg-primary/90 rounded px-2.5 py-1.5 text-xs disabled:opacity-50"
                disabled={loading || !newKeyName}
                onClick={handleCreateKey}
              >
                {loading ? (
                  <Loader2Icon className="size-3 animate-spin" />
                ) : (
                  t.apiPublish.generateKey
                )}
              </button>
            </div>

            {/* Show created key (once) */}
            {createdKey && showRawKey && (
              <div className="rounded border border-warning/30 bg-warning/5 p-2">
                <div className="mb-1 text-xs font-medium text-warning">
                  {t.apiPublish.copyKeyWarning}
                </div>
                <div className="flex items-center gap-1">
                  <code className="flex-1 break-all text-xs">
                    {createdKey.raw_key}
                  </code>
                  <CopyButton text={createdKey.raw_key} />
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground rounded p-0.5"
                    onClick={() => setShowRawKey(false)}
                  >
                    <EyeOffIcon className="size-3" />
                  </button>
                </div>
              </div>
            )}

            {/* Key list */}
            {keys.length === 0 ? (
              <div className="text-muted-foreground py-4 text-center text-xs">
                {t.apiPublish.noApiKeys}
              </div>
            ) : (
              <div className="space-y-1.5">
                {keys.map((k) => (
                  <div
                    key={k.key_id}
                    className="flex items-center gap-2 rounded border px-2.5 py-2"
                  >
                    <KeyIcon className="text-muted-foreground size-3 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="truncate text-xs font-medium">
                        {k.name}
                      </div>
                      <div className="text-muted-foreground text-xs">
                        {k.key_prefix} | Created{" "}
                        {new Date(k.created_at).toLocaleDateString()}
                        {k.last_used_at &&
                          ` | Last used ${new Date(k.last_used_at).toLocaleDateString()}`}
                      </div>
                    </div>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="text-muted-foreground hover:text-destructive rounded p-0.5"
                          onClick={() => handleRevokeKey(k.key_id)}
                        >
                          <Trash2Icon className="size-3" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="left">
                        {t.apiPublish.revoke}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Call Logs */}
        {tab === "logs" && (
          <div className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-muted-foreground text-xs">
                {t.apiPublish.recentCalls(logs.length)}
              </span>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground rounded p-0.5"
                onClick={fetchLogs}
              >
                <RefreshCwIcon className="size-3" />
              </button>
            </div>
            {logs.length === 0 ? (
              <div className="text-muted-foreground py-4 text-center text-xs">
                {t.apiPublish.noCallsYet}
              </div>
            ) : (
              <div className="space-y-1.5">
                {logs.map((l) => (
                  <LogEntry key={l.call_id} log={l} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Usage Stats */}
        {tab === "stats" && (
          <div className="p-3">
            {stats ? (
              <StatsView stats={stats} />
            ) : (
              <div className="text-muted-foreground py-4 text-center text-xs">
                {t.apiPublish.loadingStats}
              </div>
            )}
          </div>
        )}

        {/* Test Panel */}
        {tab === "test" && (
          <div className="space-y-3 p-3">
            <div className="space-y-2">
              <label className="block text-xs font-medium">
                {t.apiPublish.apiKey}
              </label>
              <input
                type="password"
                className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
                placeholder="oct_..."
                value={testApiKey}
                onChange={(e) => setTestApiKey(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <label className="block text-xs font-medium">
                {t.apiPublish.inputLabel}
              </label>
              <textarea
                className="bg-muted/50 border-border w-full rounded border px-2 py-1.5 text-xs"
                rows={3}
                placeholder={t.apiPublish.inputPlaceholder}
                value={testInput}
                onChange={(e) => setTestInput(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="bg-primary text-primary-foreground hover:bg-primary/90 flex w-full items-center justify-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium disabled:opacity-50"
              disabled={!testApiKey || !testInput || testRunning}
              onClick={handleTest}
            >
              {testRunning ? (
                <Loader2Icon className="size-3 animate-spin" />
              ) : (
                <>
                  <SendIcon className="size-3" />
                  {t.apiPublish.sendRequest}
                </>
              )}
            </button>
            {testResponse && (
              <div className="space-y-1">
                <div className="text-muted-foreground text-xs font-medium">
                  {t.apiPublish.response}
                </div>
                <pre className="bg-muted/50 max-h-48 overflow-auto rounded p-2 text-xs">
                  {testResponse}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Snippet block
// ---------------------------------------------------------------------------

function SnippetBlock({
  title,
  code,
}: {
  title: string;
  code: string;
  language: string;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-muted-foreground text-xs font-medium">
          {title}
        </span>
        <CopyButton text={code} />
      </div>
      <pre className="bg-muted/50 overflow-x-auto rounded p-2 text-xs leading-relaxed">
        {code}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Log entry
// ---------------------------------------------------------------------------

function LogEntry({ log }: { log: CallLog }) {
  const [expanded, setExpanded] = useState(false);
  const statusColor =
    log.status === "success"
      ? "text-success"
      : log.status === "rate_limited"
        ? "text-warning"
        : "text-destructive";

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      className="cursor-pointer rounded border px-2.5 py-1.5 transition-colors hover:bg-accent/30"
      onClick={() => setExpanded(!expanded)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        setExpanded(!expanded);
      }}
    >
      <div className="flex items-center gap-2">
        <span className={cn("text-xs font-medium", statusColor)}>
          {log.status}
        </span>
        <span className="text-muted-foreground flex-1 truncate text-xs">
          {log.input_text.slice(0, 60)}
          {log.input_text.length > 60 ? "..." : ""}
        </span>
        <span className="text-muted-foreground text-xs">
          {log.latency_ms.toFixed(0)}ms
        </span>
      </div>
      <div className="text-muted-foreground mt-0.5 text-xs">
        {new Date(log.timestamp).toLocaleString()}
        {log.tokens_used > 0 && ` | ${log.tokens_used} tokens`}
      </div>
      {expanded && (
        <div className="mt-2 space-y-1.5">
          <div>
            <div className="text-muted-foreground text-xs font-medium">
              Input
            </div>
            <pre className="bg-muted/50 mt-0.5 max-w-full overflow-x-auto rounded p-1.5 text-xs">
              {log.input_text}
            </pre>
          </div>
          <div>
            <div className="text-muted-foreground text-xs font-medium">
              Output
            </div>
            <pre className="bg-muted/50 mt-0.5 max-h-32 overflow-auto rounded p-1.5 text-xs">
              {log.output_text || log.error_message || "(empty)"}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stats view
// ---------------------------------------------------------------------------

function StatsView({ stats }: { stats: UsageStats }) {
  // All 6 stat labels + the chart heading were hardcoded English ·
  // even though the zh-CN locale already had matching keys
  // (totalCalls / today / avgLatency / …). Wire them through so
  // Chinese users see Chinese labels matching the rest of the panel.
  const { t } = useI18n();
  const maxCount = Math.max(...stats.daily_counts.map((d) => d.count), 1);

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-2">
        <StatCard
          label={t.apiPublish.totalCalls}
          value={stats.total_calls.toLocaleString()}
        />
        <StatCard
          label={t.apiPublish.today}
          value={stats.calls_today.toLocaleString()}
        />
        <StatCard
          label={t.apiPublish.avgLatency}
          value={`${stats.avg_latency_ms.toFixed(0)}ms`}
        />
        <StatCard
          label={t.apiPublish.totalTokens}
          value={stats.total_tokens.toLocaleString()}
        />
        <StatCard
          label={t.apiPublish.success}
          value={stats.successful_calls.toLocaleString()}
          color="text-success"
        />
        <StatCard
          label={t.apiPublish.errors}
          value={stats.error_calls.toLocaleString()}
          color="text-destructive"
        />
      </div>

      {/* Simple bar chart */}
      {stats.daily_counts.length > 0 && (
        <div>
          <div className="text-muted-foreground mb-2 text-xs font-medium">
            {t.apiPublish.dailyCalls}
          </div>
          <div className="flex items-end gap-px" style={{ height: 80 }}>
            {stats.daily_counts.map((d) => (
              <Tooltip key={d.date}>
                <TooltipTrigger asChild>
                  <div
                    className="bg-primary/60 hover:bg-primary flex-1 rounded-t transition-colors"
                    style={{
                      height: `${Math.max((d.count / maxCount) * 100, 2)}%`,
                    }}
                  />
                </TooltipTrigger>
                <TooltipContent side="top">
                  <div className="text-xs">
                    <div>{d.date}</div>
                    <div>
                      {d.count} calls | {d.avg_latency_ms.toFixed(0)}ms avg
                    </div>
                  </div>
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="rounded border p-2">
      <div className="text-muted-foreground text-xs uppercase">{label}</div>
      <div className={cn("text-sm font-semibold", color)}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// API List Item
// ---------------------------------------------------------------------------

function APIListItem({
  api,
  onClick,
  onDelete,
}: {
  api: PublishedAPI;
  onClick: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      className="hover:bg-accent/30 flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 transition-colors"
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onClick();
      }}
    >
      <div
        className={cn(
          "size-2 shrink-0 rounded-lg",
          api.enabled ? "bg-success" : "bg-muted-foreground/40",
        )}
      />
      <div className="flex-1 min-w-0">
        <div className="truncate text-xs font-medium">{api.name}</div>
        <div className="text-muted-foreground truncate text-xs">
          /{api.endpoint_path} | {api.agent_name}
          {api.key_count > 0 && ` | ${api.key_count} keys`}
        </div>
      </div>
      <button
        type="button"
        className="text-muted-foreground hover:text-destructive rounded p-0.5 transition-colors"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        <Trash2Icon className="size-3" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function APIPublishPanel({ className }: { className?: string }) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [apis, setApis] = useState<PublishedAPI[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPublish, setShowPublish] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const fetchApis = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiClient.listPublished();
      setApis(data);
    } catch (err) {
      swallow(err);
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAgents = useCallback(async () => {
    try {
      const data = await apiClient.listAgents();
      setAgents(data.agents);
    } catch (e) {
      swallow(e);
    }
  }, []);

  useEffect(() => {
    fetchApis();
    fetchAgents();
  }, [fetchApis, fetchAgents]);

  const selected = useMemo(
    () => apis.find((a) => a.api_id === selectedId) ?? null,
    [apis, selectedId],
  );

  const handleDelete = async (apiId: string) => {
    if (
      !(await confirm({
        title: t.apiPublish.deleteApiConfirmTitle,
        description: t.apiPublish.deleteApiConfirmDescription,
        confirmLabel: t.common.delete,
      }))
    )
      return;
    try {
      await apiClient.unpublish(apiId);
      setApis((prev) => prev.filter((a) => a.api_id !== apiId));
      if (selectedId === apiId) setSelectedId(null);
    } catch (e) {
      swallow(e);
    }
  };

  // Detail view
  if (selected) {
    return (
      <div className={cn("flex h-full flex-col", className)}>
        <APIDetailView
          api={selected}
          onBack={() => setSelectedId(null)}
          onRefresh={fetchApis}
        />
      </div>
    );
  }

  return (
    <div className={cn("flex h-full flex-col", className)}>
      {confirmDialog}
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <GlobeIcon className="size-4" />
          {t.apiPublish.title}
        </div>
        <div className="flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
                onClick={fetchApis}
                disabled={loading}
              >
                <RefreshCwIcon
                  className={cn("size-3.5", loading && "animate-spin")}
                />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {t.apiPublish.refreshTooltip}
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
                onClick={() => setShowPublish(!showPublish)}
              >
                {showPublish ? (
                  <XIcon className="size-3.5" />
                ) : (
                  <PlusIcon className="size-3.5" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {showPublish
                ? t.apiPublish.cancel
                : t.apiPublish.publishAgentAsApi}
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

      {/* Publish form */}
      {showPublish && (
        <PublishForm
          agents={agents}
          onPublished={() => {
            setShowPublish(false);
            fetchApis();
          }}
          onCancel={() => setShowPublish(false)}
        />
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto p-3">
        {apis.length === 0 && !loading ? (
          <div className="text-muted-foreground flex flex-col items-center gap-2 py-8 text-center">
            <GlobeIcon className="size-8 opacity-30" />
            <div className="text-xs">
              {t.apiPublish.noPublishedApis}
              <br />
              {t.apiPublish.noPublishedApisHint}
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            {apis.map((a) => (
              <APIListItem
                key={a.api_id}
                api={a}
                onClick={() => setSelectedId(a.api_id)}
                onDelete={() => handleDelete(a.api_id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sheet-wrapped version (for sidebar slide-over)
// ---------------------------------------------------------------------------

export function APIPublishSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[var(--dialog-lg)] p-0 sm:w-[var(--dialog-lg)]">
        <SheetHeader className="sr-only">
          <SheetTitle>API Publish</SheetTitle>
        </SheetHeader>
        <APIPublishPanel className="h-full" />
      </SheetContent>
    </Sheet>
  );
}
