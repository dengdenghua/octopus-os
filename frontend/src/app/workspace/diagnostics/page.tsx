/**
 * Diagnostics page · operator-facing surface for the management
 * panels we shipped over the last several iterations.
 *
 * Surfaces:
 *   - Runtime self-check (version drift + loopback/backend origin)
 *   - Feature flags (read + reload)
 *   - Ambient suggestions (read + accept/dismiss)
 *   - Remote backends (CRUD + ping)
 *   - Invariants (read + filter + rebuild)
 *
 * Each panel is independent — failures in one don't bring down the
 * page. The tab UI lets ops drill into one subsystem at a time
 * without the page becoming a wall of cards.
 */

import { swallow } from "@/core/utils/log";
import { useEffect, useState } from "react";
import { GaugeIcon } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AmbientSuggestionsPanel } from "@/components/workspace/ambient-suggestions-panel";
import { FeatureFlagsPanel } from "@/components/workspace/feature-flags-panel";
import { InvariantsPanel } from "@/components/workspace/invariants-panel";
import { RemoteBackendsPanel } from "@/components/workspace/remote-backends-panel";
import { RuntimeSelfCheckPanel } from "@/components/workspace/runtime-self-check-panel";
import { StreamTelemetryPanel } from "@/components/workspace/stream-telemetry-panel";
import { useI18n } from "@/core/i18n/hooks";
import { getBackendBaseURL } from "@/core/config";

// The /workspace/diagnostics route renders ObservabilityPage (which embeds
// DiagnosticsContent in its system tab), so this module intentionally has
// no standalone page export.
export function DiagnosticsContent() {
  const { t } = useI18n();
  const [project, setProject] = useState<string>("");

  // Resolve the active workspace root for the ambient-suggestions
  // panel. Reads from ``/api/projects/active`` when the account /
  // scope subsystem has one bound; otherwise leaves empty and the
  // panel renders a "no active project" hint.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await fetch(`${getBackendBaseURL()}/api/projects/active`);
        if (r.ok) {
          const body = (await r.json()) as { project_root?: string };
          if (!cancelled && body.project_root) {
            setProject(body.project_root);
            return;
          }
        }
      } catch (e) {
        swallow(e);
      }
      if (!cancelled) setProject("");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
      <section className="workspace-panel rounded-lg px-6 py-5">
        <div className="flex items-center gap-3">
          <GaugeIcon className="size-5 shrink-0" aria-hidden="true" />
          <div className="flex min-w-0 flex-col">
            <h1 className="text-base font-semibold">
              {t.diagnosticsPage.title}
            </h1>
            <p className="text-muted-foreground text-xs">
              {t.diagnosticsPage.description}
            </p>
          </div>
        </div>
      </section>

      <Tabs defaultValue="runtime" className="flex flex-col gap-3">
        <TabsList className="self-start">
          <TabsTrigger value="runtime">
            {t.diagnosticsPage.tabs.runtime}
          </TabsTrigger>
          <TabsTrigger value="streaming">
            {t.diagnosticsPage.tabs.streaming}
          </TabsTrigger>
          <TabsTrigger value="flags">
            {t.diagnosticsPage.tabs.featureFlags}
          </TabsTrigger>
          <TabsTrigger value="ambient">
            {t.diagnosticsPage.tabs.suggestions}
          </TabsTrigger>
          <TabsTrigger value="remote">
            {t.diagnosticsPage.tabs.remote}
          </TabsTrigger>
          <TabsTrigger value="invariants">
            {t.diagnosticsPage.tabs.invariants}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="runtime">
          <RuntimeSelfCheckPanel />
        </TabsContent>

        <TabsContent value="streaming">
          <StreamTelemetryPanel />
        </TabsContent>

        <TabsContent value="flags">
          <FeatureFlagsPanel />
        </TabsContent>

        <TabsContent value="ambient">
          {project ? (
            <AmbientSuggestionsPanel project={project} />
          ) : (
            <div className="text-muted-foreground rounded-md border p-4 text-sm">
              {t.diagnosticsPage.noActiveProject}
            </div>
          )}
        </TabsContent>

        <TabsContent value="remote">
          <RemoteBackendsPanel />
        </TabsContent>

        <TabsContent value="invariants">
          <InvariantsPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
