import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { revokePluginPublisherKey, rotatePluginPublisherKey } from "@/core/plugins/api";
import type { PluginPublisherTrustReport } from "@/core/plugins/types";
import { cn } from "@/lib/utils";
import { ShieldAlertIcon } from "lucide-react";
import { useOperatorCopy } from "../use-operator-copy";

export function PublisherTrustCard({
  report,
  onChanged,
}: {
  report: PluginPublisherTrustReport;
  onChanged: (report: PluginPublisherTrustReport) => void;
}) {
  const to = useOperatorCopy();
  const [mode, setMode] = useState<"rotate" | "revoke" | null>(null);
  const [publisherId, setPublisherId] = useState("");
  const [previousKeyId, setPreviousKeyId] = useState("");
  const [keyId, setKeyId] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);

  const openRotate = (publisher = "", previous = "") => {
    setPublisherId(publisher);
    setPreviousKeyId(previous);
    setKeyId("");
    setPublicKey("");
    setReason("scheduled rotation");
    setDialogError(null);
    setMode("rotate");
  };
  const openRevoke = (publisher: string, key: string) => {
    setPublisherId(publisher);
    setKeyId(key);
    setReason("");
    setDialogError(null);
    setMode("revoke");
  };
  const submit = async () => {
    if (!mode) return;
    setBusy(true);
    setDialogError(null);
    try {
      const result =
        mode === "rotate"
          ? await rotatePluginPublisherKey({
              publisher_id: publisherId,
              previous_key_id: previousKeyId || undefined,
              new_key_id: keyId,
              new_public_key: publicKey,
              reason,
            })
          : await revokePluginPublisherKey({
              publisher_id: publisherId,
              key_id: keyId,
              reason,
            });
      onChanged(result.trust);
      setMode(null);
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={cn(
        "mt-3 rounded-lg border px-3 py-2",
        report.ready
          ? "border-border-default bg-muted/15"
          : "border-warning/30 bg-warning/10",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlertIcon className="size-4 text-primary" />
            {to("Publisher trust")}
            <Badge variant="outline" className="text-xs">
              {report.active_key_count} {to("active")}
            </Badge>
            {report.rotation_due_count > 0 && (
              <Badge
                variant="outline"
                className="border-warning/30 text-xs text-warning"
              >
                {report.rotation_due_count} {to("due")}
              </Badge>
            )}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {to(
              "Ed25519 publisher keys · atomic rotation · audited revocation",
            )}
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={() => openRotate()}>
          {to("Rotate key")}
        </Button>
      </div>
      <div className="mt-2 space-y-1.5">
        {report.publishers.flatMap((publisher) =>
          publisher.keys.map((key) => (
            <div
              key={`${publisher.publisher_id}:${key.key_id}`}
              className="flex items-center justify-between gap-2 rounded-md border border-border-default bg-background/40 px-2 py-1.5"
            >
              <div className="min-w-0 text-xs">
                <div className="truncate font-mono">
                  {publisher.publisher_id}/{key.key_id}
                </div>
                <div className="truncate text-muted-foreground">
                  {key.public_key_fingerprint}
                  {key.age_days !== null ? ` · ${key.age_days}d` : ""}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <Badge variant="outline" className="text-xs">
                  {key.status}
                </Badge>
                {key.status === "active" && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        openRotate(publisher.publisher_id, key.key_id)
                      }
                    >
                      {to("Replace")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive"
                      onClick={() =>
                        openRevoke(publisher.publisher_id, key.key_id)
                      }
                    >
                      {to("Revoke")}
                    </Button>
                  </>
                )}
              </div>
            </div>
          )),
        )}
        {report.publishers.length === 0 && (
          <div className="text-xs text-muted-foreground">
            {report.next_actions[0] ?? to("No publisher keys registered.")}
          </div>
        )}
      </div>

      <Dialog
        open={mode !== null}
        onOpenChange={(open) => !open && setMode(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {mode === "rotate"
                ? to("Rotate publisher key")
                : to("Revoke publisher key")}
            </DialogTitle>
            <DialogDescription>
              {mode === "rotate"
                ? to(
                    "Register a new Ed25519 public key and retire the previous key atomically.",
                  )
                : to(
                    "Revocation takes effect immediately and is written to the governance audit chain.",
                  )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              aria-label={to("Publisher ID")}
              placeholder={to("Publisher ID")}
              value={publisherId}
              disabled={mode === "revoke"}
              onChange={(event) => setPublisherId(event.target.value)}
            />
            {mode === "rotate" && (
              <Input
                aria-label={to("Previous key ID")}
                placeholder={to("Previous key ID (optional)")}
                value={previousKeyId}
                onChange={(event) => setPreviousKeyId(event.target.value)}
              />
            )}
            <Input
              aria-label={
                mode === "rotate" ? to("New key ID") : to("Key ID")
              }
              placeholder={
                mode === "rotate" ? to("New key ID") : to("Key ID")
              }
              value={keyId}
              disabled={mode === "revoke"}
              onChange={(event) => setKeyId(event.target.value)}
            />
            {mode === "rotate" && (
              <Textarea
                aria-label={to("Ed25519 public key")}
                placeholder={to("Base64 Ed25519 public key")}
                value={publicKey}
                onChange={(event) => setPublicKey(event.target.value)}
              />
            )}
            <Textarea
              aria-label={to("Reason")}
              placeholder={to("Reason")}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            {dialogError && (
              <div className="text-sm text-destructive">{dialogError}</div>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setMode(null)}
              disabled={busy}
            >
              {to("Cancel")}
            </Button>
            <Button
              variant={mode === "revoke" ? "destructive" : "default"}
              disabled={
                busy ||
                !publisherId.trim() ||
                !keyId.trim() ||
                !reason.trim() ||
                (mode === "rotate" && !publicKey.trim())
              }
              onClick={() => void submit()}
            >
              {busy
                ? to("Applying…")
                : mode === "rotate"
                  ? to("Rotate")
                  : to("Revoke")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
