/**
 * Mount Point Dialog · register a remote or local workspace mount.
 *
 * Wraps the ``POST /api/workspaces`` endpoint with per-protocol form
 * fields (smb / nfs / webdav / sftp / s3 / local). Credentials are
 * sent to the backend only — never persisted in the browser. A "Test
 * connection" button triggers ``POST /api/workspaces/{id}/health``
 * after creation if the user wants to validate first; if the backend
 * supports pre-create health checks via a temporary workspace, we
 * fall back to the same `checkHealth` call against an optimistic id.
 */

import {
  FolderOpenIcon,
  HardDriveIcon,
  Loader2Icon,
  PlugZapIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { currentActorId } from "@/core/auth/api";
import { useI18n } from "@/core/i18n/hooks";
import {
  checkHealth,
  createWorkspace,
  deleteWorkspace,
} from "@/core/workspace/api";
import type { MountType, Workspace } from "@/core/workspace/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

interface MountPointDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (workspace: Workspace) => void;
  /** Optional initial mount type (e.g. pre-selected by the caller). */
  defaultMountType?: MountType;
}

interface FieldRowProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: "text" | "password";
  autoComplete?: string;
}

function FieldRow({
  id,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  autoComplete,
}: FieldRowProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      <Input
        id={id}
        type={type}
        value={value}
        autoComplete={autoComplete}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-8 text-sm"
      />
    </div>
  );
}

const PROTOCOL_ITEMS: Array<{
  value: MountType;
  labelKey:
    | "typeLocal"
    | "typeSmb"
    | "typeNfs"
    | "typeWebdav"
    | "typeSftp"
    | "typeS3";
}> = [
  { value: "local", labelKey: "typeLocal" },
  { value: "smb", labelKey: "typeSmb" },
  { value: "nfs", labelKey: "typeNfs" },
  { value: "webdav", labelKey: "typeWebdav" },
  { value: "sftp", labelKey: "typeSftp" },
  { value: "s3", labelKey: "typeS3" },
];

function hasNativeFolderPicker(): boolean {
  return (
    typeof window !== "undefined" && Boolean(window.echo?.dialog?.open)
  );
}

async function openNativeFolderPicker(
  currentDir: string,
): Promise<string | null> {
  const api = window.echo;
  if (!api?.dialog?.open) return null;
  try {
    const result = await api.dialog.open({
      properties: ["openDirectory", "createDirectory"],
      defaultPath: currentDir || undefined,
    });
    if (!result.canceled && result.filePaths.length > 0) {
      return result.filePaths[0] || null;
    }
  } catch (error) {
    console.error("Electron folder picker failed:", error);
  }
  return null;
}

export function MountPointDialog({
  open,
  onOpenChange,
  onCreated,
  defaultMountType = "local",
}: MountPointDialogProps) {
  const { t } = useI18n();
  const tr = t.remoteWorkspace.mountDialog;

  const [name, setName] = useState("");
  const [mountType, setMountType] = useState<MountType>(defaultMountType);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    { ok: boolean; message: string } | null
  >(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setMountType(defaultMountType);
    setFields({});
    setTestResult(null);
  }, [open, defaultMountType]);

  const updateField = (key: string, value: string) => {
    setFields((prev) => ({ ...prev, [key]: value }));
    setTestResult(null);
  };

  const builtMountTarget = useMemo(() => {
    switch (mountType) {
      case "local":
        return fields.path?.trim() ?? "";
      case "smb": {
        const host = fields.host?.trim() ?? "";
        const share = fields.share?.trim() ?? "";
        if (!host) return "";
        return share ? `smb://${host}/${share}` : `smb://${host}`;
      }
      case "nfs": {
        const host = fields.host?.trim() ?? "";
        const exportPath = fields.exportPath?.trim() ?? "";
        if (!host) return "";
        return exportPath ? `nfs://${host}${exportPath}` : `nfs://${host}`;
      }
      case "webdav":
        return fields.url?.trim() ?? "";
      case "sftp": {
        const host = fields.host?.trim() ?? "";
        const port = fields.port?.trim() ?? "";
        if (!host) return "";
        return port ? `sftp://${host}:${port}` : `sftp://${host}`;
      }
      case "s3": {
        const endpoint = fields.endpointUrl?.trim() ?? "";
        const bucket = fields.bucket?.trim() ?? "";
        if (!endpoint) return "";
        return bucket ? `${endpoint}/${bucket}` : endpoint;
      }
      default:
        return "";
    }
  }, [mountType, fields]);

  const mountOptions = useMemo<Record<string, string>>(() => {
    const opts: Record<string, string> = {};
    switch (mountType) {
      case "smb":
        if (fields.username) opts.username = fields.username;
        if (fields.password) opts.password = fields.password;
        if (fields.domain) opts.domain = fields.domain;
        break;
      case "webdav":
        if (fields.username) opts.username = fields.username;
        if (fields.password) opts.password = fields.password;
        break;
      case "sftp":
        if (fields.username) opts.username = fields.username;
        if (fields.password) opts.password = fields.password;
        if (fields.identityFile) opts.identity_file = fields.identityFile;
        if (fields.port) opts.port = fields.port;
        break;
      case "s3":
        if (fields.accessKey) opts.access_key = fields.accessKey;
        if (fields.secretKey) opts.secret_key = fields.secretKey;
        if (fields.region) opts.region = fields.region;
        break;
      default:
        break;
    }
    return opts;
  }, [mountType, fields]);

  const canCreate = useMemo(() => {
    if (!name.trim()) return false;
    if (!builtMountTarget) return false;
    return true;
  }, [name, builtMountTarget]);

  const handlePickLocalFolder = async () => {
    const picked = await openNativeFolderPicker(fields.path ?? "");
    if (picked) updateField("path", picked);
  };

  const handleCreate = async () => {
    if (!canCreate || creating) return;
    setCreating(true);
    setTestResult(null);
    try {
      const created = await createWorkspace({
        name: name.trim(),
        mount_type: mountType,
        mount_target: builtMountTarget,
        mount_options: mountOptions,
        owner_id: currentActorId(),
      });
      toast.success(t.remoteWorkspace.mountDialog.title);
      onCreated?.(created);
      onOpenChange(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      toast.error(tr.createFailed(message));
    } finally {
      setCreating(false);
    }
  };

  const handleTestConnection = async () => {
    if (testing || !canCreate) return;
    setTesting(true);
    setTestResult(null);
    let created: Workspace | null = null;
    try {
      // Provisionally create the workspace so the backend can run a
      // real mount + health check, then tear it down so the user can
      // still adjust fields before committing via "Create".
      created = await createWorkspace({
        name: name.trim(),
        mount_type: mountType,
        mount_target: builtMountTarget,
        mount_options: mountOptions,
        owner_id: currentActorId(),
      });
      const health = await checkHealth(created.id);
      if (health.healthy) {
        setTestResult({ ok: true, message: tr.testOk });
        toast.success(tr.testOk);
      } else {
        setTestResult({
          ok: false,
          message: tr.testFailed(health.detail ?? ""),
        });
        toast.error(tr.testFailed(health.detail ?? ""));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTestResult({ ok: false, message: tr.testFailed(message) });
      toast.error(tr.testFailed(message));
    } finally {
      // Cleanup the throwaway workspace so the registry doesn't fill
      // up with probe entries. A failure here is non-fatal — log it
      // but don't surface to the user.
      if (created) {
        try {
          await deleteWorkspace(created.id);
        } catch (error) {
          console.warn("Failed to clean up probe workspace:", error);
        }
      }
      setTesting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton
        className="w-[min(var(--dialog-xl),calc(100vw-2rem))] gap-4 rounded-lg p-5 sm:max-w-[var(--dialog-xl)]"
      >
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-base">{tr.title}</DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            {tr.credentialsHint}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <FieldRow
              id="workspace-name"
              label={tr.nameLabel}
              value={name}
              onChange={setName}
              placeholder={tr.namePlaceholder}
              autoComplete="off"
            />
            <div className="flex flex-col gap-1.5">
              <Label
                htmlFor="workspace-protocol"
                className="text-xs text-muted-foreground"
              >
                {tr.protocolLabel}
              </Label>
              <Select
                value={mountType}
                onValueChange={(value) => setMountType(value as MountType)}
              >
                <SelectTrigger
                  id="workspace-protocol"
                  size="sm"
                  className="h-8 text-sm"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROTOCOL_ITEMS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {t.remoteWorkspace[item.labelKey]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-3 rounded-lg border border-border-subtle bg-muted/20 p-3">
            {mountType === "local" && (
              <div className="flex flex-col gap-1.5">
                <Label
                  htmlFor="workspace-path"
                  className="text-xs text-muted-foreground"
                >
                  {tr.pathLabel}
                </Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="workspace-path"
                    type="text"
                    value={fields.path ?? ""}
                    onChange={(event) =>
                      updateField("path", event.target.value)
                    }
                    placeholder={tr.pathPlaceholder}
                    className="h-8 flex-1 font-mono text-xs"
                  />
                  {hasNativeFolderPicker() && (
                    <Button
                      type="button"
                      variant="outline"
                      size="icon-sm"
                      onClick={handlePickLocalFolder}
                      title={tr.pathLabel}
                      aria-label={tr.pathLabel}
                    >
                      <FolderOpenIcon className="size-3.5" />
                    </Button>
                  )}
                </div>
              </div>
            )}

            {mountType === "smb" && (
              <div className="grid grid-cols-2 gap-3">
                <FieldRow
                  id="smb-host"
                  label={tr.hostLabel}
                  value={fields.host ?? ""}
                  onChange={(value) => updateField("host", value)}
                  placeholder="192.168.1.10"
                />
                <FieldRow
                  id="smb-share"
                  label={tr.shareLabel}
                  value={fields.share ?? ""}
                  onChange={(value) => updateField("share", value)}
                  placeholder="shared"
                />
                <FieldRow
                  id="smb-username"
                  label={tr.usernameLabel}
                  value={fields.username ?? ""}
                  onChange={(value) => updateField("username", value)}
                  autoComplete="off"
                />
                <FieldRow
                  id="smb-password"
                  label={tr.passwordLabel}
                  value={fields.password ?? ""}
                  onChange={(value) => updateField("password", value)}
                  type="password"
                  autoComplete="new-password"
                />
                <FieldRow
                  id="smb-domain"
                  label={tr.domainLabel}
                  value={fields.domain ?? ""}
                  onChange={(value) => updateField("domain", value)}
                  placeholder="WORKGROUP"
                />
              </div>
            )}

            {mountType === "nfs" && (
              <div className="grid grid-cols-2 gap-3">
                <FieldRow
                  id="nfs-host"
                  label={tr.hostLabel}
                  value={fields.host ?? ""}
                  onChange={(value) => updateField("host", value)}
                  placeholder="192.168.1.11"
                />
                <FieldRow
                  id="nfs-export"
                  label={tr.exportPathLabel}
                  value={fields.exportPath ?? ""}
                  onChange={(value) => updateField("exportPath", value)}
                  placeholder="/exports/project"
                />
              </div>
            )}

            {mountType === "webdav" && (
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <FieldRow
                    id="webdav-url"
                    label={tr.urlLabel}
                    value={fields.url ?? ""}
                    onChange={(value) => updateField("url", value)}
                    placeholder="https://dav.example.com/path"
                  />
                </div>
                <FieldRow
                  id="webdav-username"
                  label={tr.usernameLabel}
                  value={fields.username ?? ""}
                  onChange={(value) => updateField("username", value)}
                  autoComplete="off"
                />
                <FieldRow
                  id="webdav-password"
                  label={tr.passwordLabel}
                  value={fields.password ?? ""}
                  onChange={(value) => updateField("password", value)}
                  type="password"
                  autoComplete="new-password"
                />
              </div>
            )}

            {mountType === "sftp" && (
              <div className="grid grid-cols-2 gap-3">
                <FieldRow
                  id="sftp-host"
                  label={tr.hostLabel}
                  value={fields.host ?? ""}
                  onChange={(value) => updateField("host", value)}
                  placeholder="ssh.example.com"
                />
                <FieldRow
                  id="sftp-port"
                  label={tr.portLabel}
                  value={fields.port ?? ""}
                  onChange={(value) => updateField("port", value)}
                  placeholder="22"
                />
                <FieldRow
                  id="sftp-username"
                  label={tr.usernameLabel}
                  value={fields.username ?? ""}
                  onChange={(value) => updateField("username", value)}
                  autoComplete="off"
                />
                <FieldRow
                  id="sftp-password"
                  label={tr.passwordLabel}
                  value={fields.password ?? ""}
                  onChange={(value) => updateField("password", value)}
                  type="password"
                  autoComplete="new-password"
                />
                <div className="col-span-2">
                  <FieldRow
                    id="sftp-identity"
                    label={tr.identityFileLabel}
                    value={fields.identityFile ?? ""}
                    onChange={(value) =>
                      updateField("identityFile", value)
                    }
                    placeholder="~/.ssh/id_ed25519"
                  />
                </div>
              </div>
            )}

            {mountType === "s3" && (
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <FieldRow
                    id="s3-endpoint"
                    label={tr.endpointUrlLabel}
                    value={fields.endpointUrl ?? ""}
                    onChange={(value) => updateField("endpointUrl", value)}
                    placeholder="https://s3.amazonaws.com"
                  />
                </div>
                <FieldRow
                  id="s3-bucket"
                  label={tr.bucketLabel}
                  value={fields.bucket ?? ""}
                  onChange={(value) => updateField("bucket", value)}
                  placeholder="my-bucket"
                />
                <FieldRow
                  id="s3-region"
                  label={tr.regionLabel}
                  value={fields.region ?? ""}
                  onChange={(value) => updateField("region", value)}
                  placeholder="us-east-1"
                />
                <FieldRow
                  id="s3-access"
                  label={tr.accessKeyLabel}
                  value={fields.accessKey ?? ""}
                  onChange={(value) => updateField("accessKey", value)}
                  autoComplete="off"
                />
                <FieldRow
                  id="s3-secret"
                  label={tr.secretKeyLabel}
                  value={fields.secretKey ?? ""}
                  onChange={(value) => updateField("secretKey", value)}
                  type="password"
                  autoComplete="new-password"
                />
              </div>
            )}

            <div className="flex items-center gap-2 rounded-md bg-background/60 px-2 py-1.5 text-xs text-muted-foreground">
              <HardDriveIcon className="size-3 shrink-0 opacity-60" />
              <span className="text-foreground/80">
                {tr.pathLabel}:
              </span>
              <code
                className={cn(
                  "min-w-0 flex-1 truncate font-mono",
                  !builtMountTarget && "text-muted-foreground/60",
                )}
                title={builtMountTarget}
              >
                {builtMountTarget || "—"}
              </code>
            </div>
          </div>

          {testResult && (
            <div
              role="status"
              className={cn(
                "rounded-md px-2 py-1.5 text-xs",
                testResult.ok
                  ? "bg-success/10 text-success"
                  : "bg-destructive/10 text-destructive",
              )}
            >
              {testResult.message}
            </div>
          )}
        </div>

        <DialogFooter className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleTestConnection}
            disabled={!canCreate || testing || creating}
          >
            {testing ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <PlugZapIcon className="size-3.5" />
            )}
            {testing ? tr.testing : tr.testConnection}
          </Button>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={creating || testing}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleCreate}
              disabled={!canCreate || creating || testing}
            >
              {creating ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : null}
              {creating ? tr.creating : tr.create}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
