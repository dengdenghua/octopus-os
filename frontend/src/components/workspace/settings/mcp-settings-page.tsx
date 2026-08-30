import {
  LoaderCircleIcon,
  ServerIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  Trash2Icon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import {
  approveMCPTrust,
  forgetMCPOAuth,
  listMCPTrust,
  loadMCPConfig,
  revokeMCPTrust,
  updateMCPConfig,
  type MCPTrustEntry,
} from "@/core/mcp/api";
import type { MCPConfig } from "@/core/mcp/types";
import { SettingsSection } from "./settings-section";
import { isSupportedMcpUrl } from "./settings-resilience";
import { getSettingsUxCopy } from "./settings-ux-copy";

interface McpServer {
  name: string;
  type: string;
  enabled: boolean;
  command?: string;
  url?: string;
  description?: string;
  error?: string;
}

type LoadState = "loading" | "ready" | "error";

export function McpSettingsPage() {
  const { t, locale } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const copy = getSettingsUxCopy(locale).mcp;
  const [servers, setServers] = useState<McpServer[]>([]);
  const [serversLoadState, setServersLoadState] =
    useState<LoadState>("loading");
  const [rawConfig, setRawConfig] = useState<MCPConfig | null>(null);
  const [trustEntries, setTrustEntries] = useState<MCPTrustEntry[]>([]);
  const [trustLoadState, setTrustLoadState] = useState<LoadState>("loading");
  const [addName, setAddName] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [addAuth, setAddAuth] = useState("");
  const [adding, setAdding] = useState(false);
  const [pendingServer, setPendingServer] = useState<string | null>(null);
  const [serverToRemove, setServerToRemove] = useState<string | null>(null);

  const readServers = useCallback((data: MCPConfig): McpServer[] => {
    return Object.entries(data.mcp_servers || {}).map(([name, cfg]) => ({
      name,
      type:
        typeof cfg.transport === "string"
          ? cfg.transport
          : typeof cfg.command === "string" && cfg.command
            ? "stdio"
            : typeof cfg.url === "string" && cfg.url
              ? "http"
              : "stdio",
      enabled: cfg.enabled !== false,
      command: typeof cfg.command === "string" ? cfg.command : undefined,
      url: typeof cfg.url === "string" ? cfg.url : undefined,
      description: typeof cfg.description === "string" ? cfg.description : "",
      error: typeof cfg.error === "string" ? cfg.error : undefined,
    }));
  }, []);

  const fetchServers = useCallback(
    async (showLoading = true) => {
      if (showLoading) setServersLoadState("loading");
      try {
        const data = await loadMCPConfig();
        setRawConfig(data);
        setServers(readServers(data));
        setServersLoadState("ready");
      } catch (error) {
        console.error(error);
        setServersLoadState("error");
      }
    },
    [readServers],
  );

  const fetchTrust = useCallback(async () => {
    setTrustLoadState("loading");
    try {
      const { entries } = await listMCPTrust();
      setTrustEntries(entries || []);
      setTrustLoadState("ready");
    } catch (error) {
      console.error("[mcp-settings] load trust entries failed:", error);
      setTrustEntries([]);
      setTrustLoadState("error");
    }
  }, []);

  useEffect(() => {
    void fetchServers();
    void fetchTrust();
  }, [fetchServers, fetchTrust]);

  const trustOf = (name: string) =>
    trustEntries.find((e) => e.server_name === name);

  const approve = async (name: string) => {
    if (pendingServer || trustLoadState !== "ready") return;
    setPendingServer(name);
    try {
      await approveMCPTrust(name, [], "approved via UI");
      toast.success(t.mcpSettings.toastTrustSuccess(name));
      await fetchTrust();
    } catch {
      toast.error(t.mcpSettings.toastTrustFailed);
    } finally {
      setPendingServer(null);
    }
  };

  const revoke = async (name: string) => {
    if (pendingServer || trustLoadState !== "ready") return;
    if (
      !(await confirm({
        title: t.mcpSettings.revokeConfirmTitle,
        description: t.mcpSettings.revokeConfirmDescription(name),
        confirmLabel: t.mcpSettings.revokeButton,
      }))
    )
      return;
    setPendingServer(name);
    try {
      await revokeMCPTrust(name);
      toast.success(t.mcpSettings.toastRevokeSuccess(name));
      await fetchTrust();
    } catch {
      toast.error(t.mcpSettings.toastRevokeFailed);
    } finally {
      setPendingServer(null);
    }
  };

  const toggleServer = async (name: string, enabled: boolean) => {
    if (pendingServer || serversLoadState !== "ready") return;
    const previousServers = servers;
    setPendingServer(name);
    setServers((prev) =>
      prev.map((s) => (s.name === name ? { ...s, enabled } : s)),
    );
    try {
      const data = rawConfig ?? (await loadMCPConfig());
      const mcpServers = { ...data.mcp_servers };
      if (mcpServers[name]) {
        mcpServers[name] = { ...mcpServers[name], enabled };
      }
      const nextConfig = { ...data, mcp_servers: mcpServers };
      const result = await updateMCPConfig(nextConfig);
      const appliedConfig = { mcp_servers: result.mcp_servers };
      setRawConfig(appliedConfig);
      setServers(readServers(appliedConfig));
      const runtimeStatus = result._status?.[name];
      if (runtimeStatus?.ok === false) {
        toast.error(copy.activationFailed(name, runtimeStatus.error));
        return;
      }
      toast.success(t.mcpSettings.toastToggleSuccess(name, enabled));
    } catch {
      setServers(previousServers);
      toast.error(t.mcpSettings.toastUpdateFailed);
    } finally {
      setPendingServer(null);
    }
  };

  const addServer = async () => {
    const name = addName.trim();
    const url = addUrl.trim();
    const duplicate = servers.some(
      (server) => server.name.toLowerCase() === name.toLowerCase(),
    );
    if (!name || !isSupportedMcpUrl(url)) {
      toast.error(t.mcpSettings.toastAddInvalid);
      return;
    }
    if (duplicate) {
      toast.error(copy.duplicateName(name));
      return;
    }
    if (serversLoadState !== "ready") return;
    setAdding(true);
    try {
      const data = rawConfig ?? (await loadMCPConfig());
      const token = addAuth.trim();
      const mcpServers = {
        ...data.mcp_servers,
        [name]: {
          enabled: false,
          description: "",
          transport: "http" as const,
          url,
          ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
        },
      };
      const nextConfig = { ...data, mcp_servers: mcpServers };
      const result = await updateMCPConfig(nextConfig);
      const appliedConfig = { mcp_servers: result.mcp_servers };
      setRawConfig(appliedConfig);
      setServers(readServers(appliedConfig));
      toast.success(t.mcpSettings.toastAddSuccess(name));
      setAddName("");
      setAddUrl("");
      setAddAuth("");
    } catch {
      toast.error(t.mcpSettings.toastAddFailed);
    } finally {
      setAdding(false);
    }
  };

  const removeServer = async (name: string) => {
    if (pendingServer || serversLoadState !== "ready") return;
    const server = servers.find((item) => item.name === name);
    if (!server) return;
    setPendingServer(name);
    try {
      const data = rawConfig ?? (await loadMCPConfig());
      let workingConfig = data;

      // Revoking trust first also stops any registered tools immediately.
      if (trustOf(name)) {
        await revokeMCPTrust(name);
      }
      // Persist the disabled state before dropping the entry. If the final
      // removal request fails, the UI can recover to a truthful safe state
      // instead of showing an enabled service whose runtime was stopped.
      if (server.enabled) {
        const disabledConfig = {
          ...data,
          mcp_servers: {
            ...data.mcp_servers,
            [name]: {
              ...data.mcp_servers[name],
              enabled: false,
              description: data.mcp_servers[name]?.description ?? "",
            },
          },
        };
        const disabled = await updateMCPConfig(disabledConfig);
        workingConfig = { mcp_servers: disabled.mcp_servers };
      }

      const remainingServers = { ...workingConfig.mcp_servers };
      delete remainingServers[name];
      const result = await updateMCPConfig({
        ...workingConfig,
        mcp_servers: remainingServers,
      });
      const appliedConfig = { mcp_servers: result.mcp_servers };
      setRawConfig(appliedConfig);
      setServers(readServers(appliedConfig));
      setTrustEntries((current) =>
        current.filter((entry) => entry.server_name !== name),
      );
      await forgetMCPOAuth(name).catch(() => undefined);
      toast.success(copy.removeSuccess(name));
      setServerToRemove(null);
    } catch {
      toast.error(copy.removeFailed);
      await Promise.all([fetchServers(false), fetchTrust()]);
    } finally {
      setPendingServer(null);
    }
  };

  const normalizedAddName = addName.trim();
  const normalizedAddUrl = addUrl.trim();
  const duplicateAddName = servers.some(
    (server) => server.name.toLowerCase() === normalizedAddName.toLowerCase(),
  );
  const invalidAddUrl =
    normalizedAddUrl.length > 0 && !isSupportedMcpUrl(normalizedAddUrl);
  const canAdd =
    serversLoadState === "ready" &&
    normalizedAddName.length > 0 &&
    normalizedAddUrl.length > 0 &&
    !invalidAddUrl &&
    !duplicateAddName &&
    !adding;

  return (
    <div className="space-y-6">
      {confirmDialog}
      <SettingsSection title={copy.title} description={copy.description}>
        {serversLoadState === "loading" ? (
          <McpStateNotice state="loading" copy={copy} />
        ) : serversLoadState === "error" ? (
          <McpStateNotice
            state="error"
            copy={copy}
            onRetry={() => void fetchServers()}
          />
        ) : (
          <div className="space-y-2">
            {trustLoadState === "error" && (
              <div
                role="alert"
                className="rounded-lg border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning"
              >
                {copy.trustLoadFailed}
              </div>
            )}
            {servers.map((server) => {
              const trust = trustOf(server.name);
              const trustKnown = trustLoadState === "ready";
              const trusted = trustKnown && !!trust?.approved;
              const pending = pendingServer === server.name;
              return (
                <div
                  key={server.name}
                  className="flex min-w-0 flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <ServerIcon className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <div className="font-medium flex flex-wrap items-center gap-2">
                        {server.name}
                        {!trustKnown ? (
                          <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                            <ShieldAlertIcon className="size-3" />{" "}
                            {copy.trustUnknown}
                          </span>
                        ) : trusted ? (
                          <span className="inline-flex items-center gap-1 rounded bg-success/10 px-1.5 py-0.5 text-xs text-success dark:bg-success/40 dark:text-success">
                            <ShieldCheckIcon className="size-3" />{" "}
                            {t.mcpSettings.trustedTag}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning dark:bg-warning/40 dark:text-warning">
                            <ShieldAlertIcon className="size-3" />{" "}
                            {t.mcpSettings.untrustedTag}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground break-words">
                        {server.type}{" "}
                        {server.command
                          ? `· ${server.command}`
                          : server.url
                            ? `· ${server.url}`
                            : ""}
                      </div>
                      {server.description && (
                        <div className="text-xs text-muted-foreground break-words">
                          {server.description}
                        </div>
                      )}
                      {server.error && (
                        <div
                          role="alert"
                          className="mt-1 text-xs text-destructive"
                        >
                          {copy.runtimeError(server.error)}
                        </div>
                      )}
                      {trustKnown && !trusted && server.enabled && (
                        <div className="text-xs text-warning mt-1">
                          {t.mcpSettings.unapprovedHint}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {trusted ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => revoke(server.name)}
                        disabled={pendingServer !== null || !trustKnown}
                        aria-label={copy.revokeLabel(server.name)}
                      >
                        {t.mcpSettings.revokeButton}
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => approve(server.name)}
                        disabled={pendingServer !== null || !trustKnown}
                        aria-label={copy.trustLabel(server.name)}
                      >
                        {pending ? t.common.loading : t.mcpSettings.trustButton}
                      </Button>
                    )}
                    <Switch
                      aria-label={copy.toggleLabel(server.name)}
                      checked={server.enabled}
                      disabled={pendingServer !== null}
                      onCheckedChange={(v) => toggleServer(server.name, v)}
                    />
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-9 text-muted-foreground hover:text-destructive"
                      disabled={pendingServer !== null}
                      onClick={() => setServerToRemove(server.name)}
                      aria-label={copy.removeLabel(server.name)}
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
                  </div>
                </div>
              );
            })}
            {servers.length === 0 && (
              <div className="flex flex-col items-center rounded-lg border border-dashed bg-muted/15 px-4 py-7 text-center">
                <span className="mb-2 grid size-9 place-items-center rounded-full bg-muted text-muted-foreground">
                  <ServerIcon aria-hidden="true" className="size-4" />
                </span>
                <p className="max-w-md text-sm leading-6 text-muted-foreground">
                  {copy.noServers}
                </p>
              </div>
            )}
          </div>
        )}
      </SettingsSection>
      <SettingsSection title={t.mcpSettings.addRemoteTitle}>
        <form
          className="grid gap-3 sm:grid-cols-[minmax(8rem,0.7fr)_minmax(12rem,1.4fr)_minmax(10rem,1fr)_auto] sm:items-start"
          onSubmit={(event) => {
            event.preventDefault();
            void addServer();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="mcp-server-name" className="text-xs">
              {copy.nameLabel}
            </Label>
            <Input
              id="mcp-server-name"
              name="mcp-server-name"
              autoComplete="off"
              placeholder={t.mcpSettings.addNamePlaceholder}
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              disabled={adding || serversLoadState !== "ready"}
              aria-invalid={duplicateAddName || undefined}
              aria-describedby={
                duplicateAddName ? "mcp-server-name-error" : undefined
              }
            />
            {duplicateAddName && normalizedAddName ? (
              <p
                id="mcp-server-name-error"
                role="alert"
                className="text-xs leading-snug text-destructive"
              >
                {copy.duplicateName(normalizedAddName)}
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="mcp-server-url" className="text-xs">
              {copy.urlLabel}
            </Label>
            <Input
              id="mcp-server-url"
              name="mcp-server-url"
              type="url"
              inputMode="url"
              autoComplete="url"
              autoCapitalize="none"
              spellCheck={false}
              placeholder={t.mcpSettings.addUrlPlaceholder}
              value={addUrl}
              onChange={(e) => setAddUrl(e.target.value)}
              disabled={adding || serversLoadState !== "ready"}
              aria-invalid={invalidAddUrl || undefined}
              aria-describedby={
                invalidAddUrl ? "mcp-server-url-error" : undefined
              }
            />
            {invalidAddUrl ? (
              <p
                id="mcp-server-url-error"
                role="alert"
                className="text-xs leading-snug text-destructive"
              >
                {copy.invalidUrl}
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="mcp-server-token" className="text-xs">
              {copy.tokenLabel}
            </Label>
            <Input
              id="mcp-server-token"
              name="mcp-server-token"
              type="password"
              autoComplete="new-password"
              autoCapitalize="none"
              spellCheck={false}
              placeholder={t.mcpSettings.addAuthPlaceholder}
              value={addAuth}
              onChange={(e) => setAddAuth(e.target.value)}
              disabled={adding || serversLoadState !== "ready"}
              aria-describedby="mcp-server-token-hint"
            />
            <p
              id="mcp-server-token-hint"
              className="text-xs leading-snug text-muted-foreground"
            >
              {copy.tokenHint}
            </p>
          </div>
          <Button type="submit" className="sm:mt-[1.375rem]" disabled={!canAdd}>
            {adding ? copy.adding : copy.add}
          </Button>
        </form>
      </SettingsSection>
      <Dialog
        open={serverToRemove !== null}
        onOpenChange={(open) => {
          if (!open && pendingServer === null) setServerToRemove(null);
        }}
      >
        <DialogContent
          showCloseButton={false}
          className="w-[min(380px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[380px]"
        >
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-base">
              {copy.removeTitle}
            </DialogTitle>
            <DialogDescription className="text-caption leading-5">
              {serverToRemove ? copy.removeDescription(serverToRemove) : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pendingServer !== null}
              onClick={() => setServerToRemove(null)}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={pendingServer !== null || !serverToRemove}
              onClick={() => {
                if (serverToRemove) void removeServer(serverToRemove);
              }}
            >
              {pendingServer === serverToRemove ? (
                <LoaderCircleIcon className="size-3.5 animate-spin" />
              ) : (
                <Trash2Icon className="size-3.5" />
              )}
              {copy.removeConfirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function McpStateNotice({
  state,
  copy,
  onRetry,
}: {
  state: Exclude<LoadState, "ready">;
  copy: Pick<
    ReturnType<typeof getSettingsUxCopy>["mcp"],
    "loading" | "loadFailed" | "retry"
  >;
  onRetry?: () => void;
}) {
  const failed = state === "error";
  return (
    <div
      role={failed ? "alert" : "status"}
      aria-live="polite"
      className={
        failed
          ? "flex items-center justify-between gap-3 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-3 text-xs text-destructive"
          : "flex items-center gap-2 rounded-lg border border-border-subtle bg-muted/25 px-3 py-3 text-xs text-muted-foreground"
      }
    >
      <span className="flex min-w-0 items-center gap-2">
        {failed ? null : (
          <LoaderCircleIcon className="size-3.5 shrink-0 animate-spin" />
        )}
        <span>{failed ? copy.loadFailed : copy.loading}</span>
      </span>
      {failed && onRetry ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 shrink-0 px-2 text-xs"
          onClick={onRetry}
        >
          {copy.retry}
        </Button>
      ) : null}
    </div>
  );
}
