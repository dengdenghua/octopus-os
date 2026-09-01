/**
 * Remote Backends Panel · register + health-check remote
 * echo-agent runtimes.
 *
 * Surfaces the ``/api/remote-backends`` CRUD. Operator adds an
 * endpoint, pings to confirm reachability, and can remove entries.
 * Actual proxying / streaming is driven elsewhere (the chat layer
 * flips to the selected backend); this panel is purely management.
 */

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { useI18n } from "@/core/i18n/hooks";
import {
  useRemoteBackends,
  type RemoteBackend,
} from "@/hooks/use-remote-backends";

export interface RemoteBackendsPanelProps {
  baseUrl?: string;
}

function healthVariant(
  status: RemoteBackend["last_health"],
): "default" | "destructive" | "outline" {
  switch (status) {
    case "ok":
      return "default";
    case "error":
      return "destructive";
    default:
      return "outline";
  }
}

export function RemoteBackendsPanel({ baseUrl }: RemoteBackendsPanelProps) {
  const { t } = useI18n();
  const { backends, enabled, loading, error, add, remove, ping } =
    useRemoteBackends({ baseUrl });

  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAdding(true);
    setAddError(null);
    const result = await add({ name: name.trim(), url: url.trim() });
    setAdding(false);
    if (result.ok) {
      setName("");
      setUrl("");
    } else {
      setAddError(result.error || t.remoteBackendsPanel.addFailed);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">
          {t.remoteBackendsPanel.title}
        </CardTitle>
        {!enabled && (
          <Badge
            variant="outline"
            aria-label={t.remoteBackendsPanel.disabledAria}
          >
            {t.remoteBackendsPanel.disabled}
          </Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && (
          <div role="alert" className="text-destructive text-sm">
            {t.remoteBackendsPanel.loadFailed(error)}
          </div>
        )}

        {enabled && (
          <form
            className="border-border-default flex flex-col gap-2 rounded-md border p-3"
            onSubmit={onSubmit}
            aria-label={t.remoteBackendsPanel.addBackendAria}
          >
            <div className="flex flex-col gap-2 md:flex-row">
              <input
                type="text"
                placeholder={t.remoteBackendsPanel.namePlaceholder}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                className="border-border bg-background flex-1 rounded border px-2 py-1 text-sm"
                aria-label={t.remoteBackendsPanel.nameAria}
              />
              <input
                type="text"
                placeholder={t.remoteBackendsPanel.urlPlaceholder}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
                className="border-border bg-background flex-1 rounded border px-2 py-1 text-sm"
                aria-label={t.remoteBackendsPanel.urlAria}
              />
              <Button
                type="submit"
                size="sm"
                disabled={adding || !name.trim() || !url.trim()}
              >
                {adding
                  ? t.remoteBackendsPanel.adding
                  : t.remoteBackendsPanel.add}
              </Button>
            </div>
            {addError && (
              <div role="alert" className="text-destructive text-xs">
                {addError}
              </div>
            )}
          </form>
        )}

        {loading && backends.length === 0 && (
          <div className="text-muted-foreground text-sm">
            {t.remoteBackendsPanel.loading}
          </div>
        )}

        {!loading && backends.length === 0 && (
          <div className="text-muted-foreground text-sm">
            {t.remoteBackendsPanel.empty}
          </div>
        )}

        {backends.length > 0 && (
          <ul className="divide-border divide-y">
            {backends.map((b) => (
              <BackendRow
                key={b.id}
                backend={b}
                disabled={!enabled}
                onPing={() => ping(b.id)}
                onRemove={() => remove(b.id)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

interface BackendRowProps {
  backend: RemoteBackend;
  disabled: boolean;
  onPing: () => Promise<unknown>;
  onRemove: () => Promise<unknown>;
}

function BackendRow({ backend, disabled, onPing, onRemove }: BackendRowProps) {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [pinging, setPinging] = useState(false);
  const [removing, setRemoving] = useState(false);

  const handlePing = async () => {
    setPinging(true);
    try {
      await onPing();
    } finally {
      setPinging(false);
    }
  };

  const handleRemove = async () => {
    if (
      !(await confirm({
        title: t.remoteBackendsPanel.removeConfirmTitle(backend.name),
        description: t.remoteBackendsPanel.removeConfirmDescription,
        confirmLabel: t.remoteBackendsPanel.remove,
      }))
    )
      return;
    setRemoving(true);
    try {
      await onRemove();
    } finally {
      setRemoving(false);
    }
  };

  return (
    <li className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-semibold">{backend.name}</span>
          <Badge variant={healthVariant(backend.last_health)}>
            {backend.last_health === null
              ? t.remoteBackendsPanel.untested
              : backend.last_health === "ok"
                ? t.remoteBackendsPanel.reachable
                : backend.health_detail || t.remoteBackendsPanel.error}
          </Badge>
          {backend.ssh && (
            <Badge variant="outline">ssh {backend.ssh.host}</Badge>
          )}
        </div>
        <code className="text-muted-foreground text-xs">{backend.url}</code>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handlePing}
          disabled={disabled || pinging}
          aria-label={t.remoteBackendsPanel.pingAria(backend.name)}
        >
          {pinging ? t.remoteBackendsPanel.pinging : t.remoteBackendsPanel.ping}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRemove}
          disabled={disabled || removing}
          aria-label={t.remoteBackendsPanel.removeAria(backend.name)}
        >
          {removing
            ? t.remoteBackendsPanel.removing
            : t.remoteBackendsPanel.remove}
        </Button>
      </div>
      {confirmDialog}
    </li>
  );
}
