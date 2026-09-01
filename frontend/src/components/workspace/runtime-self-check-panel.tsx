import { RefreshCwIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

interface RuntimeCheckRow {
  id: string;
  passed: boolean;
  detail: string;
  severity?: "error" | "warn" | string;
}

interface RuntimeSurfaceSelfCheck {
  schema?: string;
  ready?: boolean;
  route_count?: number;
  missing_required_routes?: string[];
  missing_route_methods?: Array<{
    path?: string;
    missing_methods?: string[];
  }>;
  missing_methods?: Array<{
    method?: string;
    reason?: string;
  }>;
  capabilities?: Record<string, boolean>;
}

interface RuntimeSelfCheckPayload {
  ready: boolean;
  status: string;
  generated_at?: string;
  version?: string;
  version_drift?: {
    runtime_matches_pyproject?: boolean;
    frontend_matches_runtime?: boolean;
    version_sources?: {
      runtime?: string;
      pyproject?: string;
      frontend_package?: string;
    };
  };
  process?: {
    pid?: number;
    python?: string;
    executable?: string;
    cwd?: string;
    argv?: string[];
  };
  backend?: {
    canonical_base_url?: string;
    request_origin_base_url?: string;
    request_url?: string;
    host?: string;
    canonical_host?: string;
    port?: number;
    env_port?: number | null;
    server_host?: string;
    server_port?: number | null;
  };
  frontend?: {
    observed_origin?: string;
    canonical_origin?: string;
    canonical_host?: string;
    port?: number;
    env_port?: number | null;
    dev_proxy_mode?: boolean;
    proxy_target?: string;
    proxy_targets_backend?: boolean;
    origin_normalized?: boolean;
    loopback_aliases?: string[];
  };
  webui?: {
    available?: boolean;
    selected_dist?: string;
    env_dist?: string;
    env_dist_invalid?: boolean;
    assets_count?: number;
    dev_fallback_expected?: boolean;
    detail?: string;
  };
  model_compat?: {
    available?: boolean;
    profile_count?: number;
    domestic_profile_count?: number;
    profile_ids?: string[];
    required_profile_ids?: string[];
    missing_required_profile_ids?: string[];
    required_profiles_present?: boolean;
  };
  orchestration?: RuntimeSurfaceSelfCheck;
  run_evidence?: RuntimeSurfaceSelfCheck;
  automation?: RuntimeSurfaceSelfCheck;
  api_surface?: {
    route_count?: number;
    required_routes_present?: boolean;
    missing_required_routes?: string[];
  };
  loopback_aliases?: {
    requested_host?: string;
    canonical_host?: string;
    same_loopback_family?: boolean;
    aliases?: string[];
  };
  paths?: {
    project_root?: string;
    journal_source?: string;
  };
  checks?: RuntimeCheckRow[];
  next_actions?: string[];
  warnings?: string[];
}

export interface RuntimeSelfCheckPanelProps {
  baseUrl?: string;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function normalizeBaseUrl(baseUrl?: string) {
  return typeof baseUrl === "string" ? baseUrl.replace(/\/+$/, "") : "";
}

export function RuntimeSelfCheckPanel({ baseUrl }: RuntimeSelfCheckPanelProps) {
  const { t } = useI18n();
  const [data, setData] = useState<RuntimeSelfCheckPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const endpoint = useMemo(() => {
    const base = normalizeBaseUrl(baseUrl ?? getBackendBaseURL());
    return `${base}/api/runtime/self-check`;
  }, [baseUrl]);
  const clientBackendBaseUrl = useMemo(
    () => normalizeBaseUrl(baseUrl ?? getBackendBaseURL()),
    [baseUrl],
  );

  const load = useCallback(
    async ({ refresh = false }: { refresh?: boolean } = {}) => {
      if (refresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);
      try {
        const response = await fetch(endpoint);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = (await response.json()) as RuntimeSelfCheckPayload;
        setData(payload);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (refresh) {
          setRefreshing(false);
        } else {
          setLoading(false);
        }
      }
    },
    [endpoint],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const versions = data?.version_drift?.version_sources ?? {};
  const process = data?.process ?? {};
  const backend = data?.backend ?? {};
  const frontend = data?.frontend ?? {};
  const webui = data?.webui ?? {};
  const modelCompat = data?.model_compat ?? {};
  const runtimeSurfaces = useMemo(
    () =>
      [
        {
          id: "orchestration",
          title: t.runtimeSelfCheckPanel.orchestration,
          surface: data?.orchestration,
        },
        {
          id: "run_evidence",
          title: t.runtimeSelfCheckPanel.runEvidence,
          surface: data?.run_evidence,
        },
        {
          id: "automation",
          title: t.runtimeSelfCheckPanel.automation,
          surface: data?.automation,
        },
      ] as const,
    [
      data?.automation,
      data?.orchestration,
      data?.run_evidence,
      t.runtimeSelfCheckPanel.automation,
      t.runtimeSelfCheckPanel.orchestration,
      t.runtimeSelfCheckPanel.runEvidence,
    ],
  );
  const apiSurface = data?.api_surface ?? {};
  const loopbackAliases = data?.loopback_aliases?.aliases ?? [];
  const checks = data?.checks ?? [];
  const nextActions = data?.next_actions ?? [];
  const warnings = data?.warnings ?? [];
  const degraded = Boolean(data && (!data.ready || data.status === "degraded"));

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <CardTitle className="text-base">
            {t.runtimeSelfCheckPanel.title}
          </CardTitle>
          {data && (
            <Badge variant={degraded ? "destructive" : "default"}>
              {degraded
                ? t.runtimeSelfCheckPanel.degraded
                : t.runtimeSelfCheckPanel.ready}
            </Badge>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void load({ refresh: true })}
          disabled={loading || refreshing}
          aria-label={t.runtimeSelfCheckPanel.refreshAria}
        >
          <RefreshCwIcon className="size-3.5" aria-hidden="true" />
          {refreshing
            ? t.runtimeSelfCheckPanel.refreshing
            : t.runtimeSelfCheckPanel.refresh}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {loading && (
          <div className="text-muted-foreground text-sm">
            {t.runtimeSelfCheckPanel.loading}
          </div>
        )}
        {error && (
          <div role="alert" className="text-destructive text-sm">
            {t.runtimeSelfCheckPanel.loadFailed(error)}
          </div>
        )}
        {!loading && !error && data && (
          <>
            <section className="grid gap-3 md:grid-cols-3">
              <Metric
                label={t.runtimeSelfCheckPanel.status}
                value={data.status}
              />
              <Metric
                label={t.runtimeSelfCheckPanel.runtimeVersion}
                value={versions.runtime ?? data.version}
              />
              <Metric
                label={t.runtimeSelfCheckPanel.generatedAt}
                value={data.generated_at}
              />
            </section>

            <section className="grid gap-4 lg:grid-cols-2">
              <InfoBlock title={t.runtimeSelfCheckPanel.versions}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.runtime}
                  value={versions.runtime}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.pyproject}
                  value={versions.pyproject}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.frontendPackage}
                  value={versions.frontend_package}
                />
              </InfoBlock>

              <InfoBlock title={t.runtimeSelfCheckPanel.process}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.pid}
                  value={process.pid}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.python}
                  value={process.python}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.cwd}
                  value={process.cwd}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.argv}
                  value={process.argv?.join(" ")}
                  mono
                />
              </InfoBlock>

              <InfoBlock title={t.runtimeSelfCheckPanel.backend}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.canonicalBaseUrl}
                  value={backend.canonical_base_url}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.requestOrigin}
                  value={backend.request_origin_base_url}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.host}
                  value={`${formatValue(backend.host)} -> ${formatValue(
                    backend.canonical_host,
                  )}`}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.port}
                  value={backend.port}
                />
              </InfoBlock>

              <InfoBlock title={t.runtimeSelfCheckPanel.frontend}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.clientBackendBaseUrl}
                  value={
                    clientBackendBaseUrl ||
                    t.runtimeSelfCheckPanel.sameOriginBackend
                  }
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.selfCheckEndpoint}
                  value={endpoint}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.observedOrigin}
                  value={frontend.observed_origin}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.canonicalOrigin}
                  value={frontend.canonical_origin}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.proxyTarget}
                  value={frontend.proxy_target}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.proxyMatchesBackend}
                  value={frontend.proxy_targets_backend}
                />
              </InfoBlock>

              <InfoBlock title={t.runtimeSelfCheckPanel.webui}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.webuiAvailable}
                  value={webui.available}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.webuiDist}
                  value={webui.selected_dist}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.webuiEnvDist}
                  value={webui.env_dist}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.webuiAssets}
                  value={webui.assets_count}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.webuiEnvInvalid}
                  value={webui.env_dist_invalid}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.webuiDevFallback}
                  value={webui.dev_fallback_expected}
                />
              </InfoBlock>

              <InfoBlock title={t.runtimeSelfCheckPanel.modelCompat}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.compatProfiles}
                  value={modelCompat.profile_count}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.domesticProfiles}
                  value={modelCompat.domestic_profile_count}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.requiredProfilesPresent}
                  value={modelCompat.required_profiles_present}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.missingProfiles}
                  value={modelCompat.missing_required_profile_ids?.join(", ")}
                  mono
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.profileIds}
                  value={modelCompat.profile_ids?.join(", ")}
                  mono
                />
              </InfoBlock>

              <InfoBlock title={t.runtimeSelfCheckPanel.apiSurface}>
                <InfoRow
                  label={t.runtimeSelfCheckPanel.routeCount}
                  value={apiSurface.route_count}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.requiredRoutesPresent}
                  value={apiSurface.required_routes_present}
                />
                <InfoRow
                  label={t.runtimeSelfCheckPanel.missingRoutes}
                  value={apiSurface.missing_required_routes?.join(", ")}
                  mono
                />
              </InfoBlock>
            </section>

            <InfoBlock title={t.runtimeSelfCheckPanel.capabilitySurfaces}>
              <div className="overflow-x-auto max-w-full">
                <table className="w-full min-w-[480px] text-sm md:min-w-0">
                  <thead className="text-muted-foreground border-b text-left text-xs">
                    <tr>
                      <th className="py-2 pr-3 font-medium">
                        {t.runtimeSelfCheckPanel.surface}
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        {t.runtimeSelfCheckPanel.status}
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        {t.runtimeSelfCheckPanel.routeCount}
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        {t.runtimeSelfCheckPanel.capabilities}
                      </th>
                      <th className="py-2 font-medium">
                        {t.runtimeSelfCheckPanel.missing}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-border divide-y">
                    {runtimeSurfaces.map(({ id, title, surface }) => (
                      <SurfaceRow
                        key={id}
                        title={title}
                        surface={surface}
                        labels={{
                          ready: t.runtimeSelfCheckPanel.ready,
                          blocked: t.runtimeSelfCheckPanel.blocked,
                          notReported: t.runtimeSelfCheckPanel.notReported,
                        }}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </InfoBlock>

            <InfoBlock title={t.runtimeSelfCheckPanel.loopbackAliases}>
              {loopbackAliases.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {loopbackAliases.map((alias) => (
                    <code
                      key={alias}
                      className="border-border bg-muted rounded border px-2 py-1 text-xs"
                    >
                      {alias}
                    </code>
                  ))}
                </div>
              ) : (
                <div className="text-muted-foreground text-sm">
                  {t.runtimeSelfCheckPanel.empty}
                </div>
              )}
            </InfoBlock>

            <InfoBlock title={t.runtimeSelfCheckPanel.checks}>
              {checks.length > 0 ? (
                <ul className="divide-border divide-y">
                  {checks.map((check) => (
                    <li
                      key={check.id}
                      className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge
                          variant={check.passed ? "outline" : "destructive"}
                        >
                          {check.passed
                            ? t.runtimeSelfCheckPanel.passed
                            : t.runtimeSelfCheckPanel.failed}
                        </Badge>
                        {check.severity && (
                          <Badge variant="outline">{check.severity}</Badge>
                        )}
                        <code className="text-sm font-semibold">
                          {check.id}
                        </code>
                      </div>
                      <p className="text-muted-foreground break-words text-xs">
                        {check.detail}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-muted-foreground text-sm">
                  {t.runtimeSelfCheckPanel.empty}
                </div>
              )}
            </InfoBlock>

            {warnings.length > 0 && (
              <InfoBlock title={t.runtimeSelfCheckPanel.warnings}>
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {warnings.map((warning) => (
                    <li key={warning} className="break-words">
                      {warning}
                    </li>
                  ))}
                </ul>
              </InfoBlock>
            )}

            {nextActions.length > 0 && (
              <InfoBlock title={t.runtimeSelfCheckPanel.nextActions}>
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {nextActions.map((action) => (
                    <li key={action} className="break-words">
                      {action}
                    </li>
                  ))}
                </ul>
              </InfoBlock>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="border-border-default rounded-md border px-3 py-2">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 truncate text-sm font-medium">
        {formatValue(value)}
      </div>
    </div>
  );
}

function InfoBlock({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-border-default rounded-md border p-3">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function SurfaceRow({
  title,
  surface,
  labels,
}: {
  title: string;
  surface?: RuntimeSurfaceSelfCheck;
  labels: {
    ready: string;
    blocked: string;
    notReported: string;
  };
}) {
  const reported = surface !== undefined;
  const ready = surface?.ready === true;
  const capabilities = Object.entries(surface?.capabilities ?? {});
  const enabledCapabilities = capabilities.filter(([, enabled]) => enabled);
  const missing = [
    ...(surface?.missing_required_routes ?? []),
    ...(surface?.missing_route_methods ?? []).map((row) => {
      const methods = row.missing_methods?.join("|") || "?";
      return `${row.path ?? "-"}:${methods}`;
    }),
    ...(surface?.missing_methods ?? []).map((row) => {
      const reason = row.reason ? `:${row.reason}` : "";
      return `${row.method ?? "-"}${reason}`;
    }),
  ];
  return (
    <tr>
      <td className="py-2 pr-3 align-top">
        <div className="font-medium">{title}</div>
        {surface?.schema && (
          <code className="text-muted-foreground text-xs">
            {surface.schema}
          </code>
        )}
      </td>
      <td className="py-2 pr-3 align-top">
        <Badge variant={!reported || ready ? "outline" : "destructive"}>
          {!reported
            ? labels.notReported
            : ready
              ? labels.ready
              : labels.blocked}
        </Badge>
      </td>
      <td className="py-2 pr-3 align-top">
        {formatValue(surface?.route_count)}
      </td>
      <td className="py-2 pr-3 align-top">
        <div className="flex max-w-[22rem] flex-wrap gap-1">
          {enabledCapabilities.length > 0 ? (
            enabledCapabilities.map(([name]) => (
              <Badge
                key={name}
                variant="outline"
                className="font-mono text-xs"
              >
                {name}
              </Badge>
            ))
          ) : (
            <span className="text-muted-foreground text-xs">-</span>
          )}
        </div>
      </td>
      <td className="py-2 align-top">
        {missing.length > 0 ? (
          <div className="flex max-w-[24rem] flex-col gap-1">
            {missing.slice(0, 5).map((item) => (
              <code key={item} className="break-words text-xs">
                {item}
              </code>
            ))}
            {missing.length > 5 && (
              <span className="text-muted-foreground text-xs">
                +{missing.length - 5}
              </span>
            )}
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">-</span>
        )}
      </td>
    </tr>
  );
}

function InfoRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: unknown;
  mono?: boolean;
}) {
  return (
    <div className="grid gap-1 py-1.5 text-sm sm:grid-cols-[9rem_minmax(0,1fr)]">
      <div className="text-muted-foreground">{label}</div>
      <div
        className={
          mono ? "min-w-0 break-words font-mono text-xs" : "min-w-0 break-words"
        }
      >
        {formatValue(value)}
      </div>
    </div>
  );
}
