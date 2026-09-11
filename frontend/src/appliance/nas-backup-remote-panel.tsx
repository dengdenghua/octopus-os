import {
  useCallback,
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyNasBackupRemote,
  fetchNasBackupRemotes,
  planNasBackupRemote,
  type NasBackupRemote,
  type NasBackupRemoteDesired,
  type NasBackupRemotePlan,
  type NasBackupS3RemoteCreateDesired,
} from "@/appliance/nas-backup";

export function NasBackupRemotePanel({
  disabled = false,
  onChanged,
}: {
  disabled?: boolean;
  onChanged?: (mountpoint?: string) => void | Promise<void>;
}) {
  const [remotes, setRemotes] = useState<NasBackupRemote[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remoteId, setRemoteId] = useState("");
  const [label, setLabel] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [region, setRegion] = useState("us-east-1");
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("echo-nas");
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [pendingDesired, setPendingDesired] =
    useState<NasBackupRemoteDesired | null>(null);
  const [pendingPlan, setPendingPlan] = useState<NasBackupRemotePlan | null>(
    null,
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchNasBackupRemotes();
      setRemotes(result.remotes);
      setError(null);
    } catch (reason) {
      setRemotes([]);
      setError(
        reason instanceof Error ? reason.message : "无法读取 S3 兼容备份远端",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const previewCreate = async () => {
    const desired: NasBackupS3RemoteCreateDesired = {
      schema: "echo.nas-backup-remote-desired.v1",
      operation: "create",
      remoteId: remoteId.trim(),
      label: label.trim(),
      endpoint: endpoint.trim(),
      region: region.trim(),
      bucket: bucket.trim(),
      prefix: prefix.trim(),
      accessKeyId,
      secretAccessKey,
    };
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(desired.remoteId)) {
      setError("远端 ID 需以小写字母开头，只能包含小写字母、数字和连字符");
      return;
    }
    if (!desired.label || !desired.endpoint.startsWith("https://")) {
      setError("请填写显示名称和 HTTPS S3 兼容端点");
      return;
    }
    if (
      !desired.bucket ||
      !desired.accessKeyId ||
      desired.secretAccessKey.length < 8
    ) {
      setError("请填写存储桶、Access Key 和至少 8 字符的 Secret Key");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setPendingDesired(desired);
      setPendingPlan(await planNasBackupRemote(desired));
    } catch (reason) {
      setPendingDesired(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成远端挂载预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const previewRemove = async (remote: NasBackupRemote) => {
    const desired: NasBackupRemoteDesired = {
      schema: "echo.nas-backup-remote-desired.v1",
      operation: "remove",
      remoteId: remote.id,
    };
    setBusy(true);
    setError(null);
    try {
      setPendingDesired(desired);
      setPendingPlan(await planNasBackupRemote(desired));
    } catch (reason) {
      setPendingDesired(null);
      setError(reason instanceof Error ? reason.message : "无法生成移除预览");
    } finally {
      setBusy(false);
    }
  };

  const cancelPending = () => {
    setPendingDesired(null);
    setPendingPlan(null);
  };

  const apply = async (administratorPassword: string) => {
    if (!pendingDesired || !pendingPlan) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "storage.nas-backup.remote.configure",
        pendingPlan.planId,
        administratorPassword,
      );
      const result = await applyNasBackupRemote(
        pendingDesired,
        pendingPlan.planId,
        approval.approvalToken,
      );
      const created = pendingDesired.operation === "create";
      cancelPending();
      setAccessKeyId("");
      setSecretAccessKey("");
      if (created) {
        setRemoteId("");
        setLabel("");
        setBucket("");
      }
      await refresh();
      await onChanged?.(created ? result.mountpoint : undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法配置远端挂载");
      throw reason;
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-semibold text-slate-800">
            S3 兼容异地备份挂载
          </h4>
          <p className="mt-1 text-[10px] leading-4 text-slate-500">
            支持 MinIO、Cloudflare R2、Backblaze B2 S3 等 HTTPS
            端点。连接信息仅以 systemd
            加密凭据保存，成功挂载后会出现在上方安全候选中。
          </p>
        </div>
        <button
          type="button"
          disabled={loading || busy}
          onClick={() => void refresh()}
          className="shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[10px] font-semibold text-slate-600 disabled:opacity-40"
        >
          刷新
        </button>
      </div>

      {remotes.length > 0 && (
        <div className="mt-3 grid gap-2">
          {remotes.map((remote) => (
            <div
              key={remote.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-[11px] font-semibold text-slate-700">
                  {remote.label}
                </p>
                <p className="text-[10px] text-slate-500">
                  {remote.id} · {remote.mounted ? "已安全挂载" : "挂载异常"}
                </p>
              </div>
              <button
                type="button"
                disabled={disabled || busy}
                onClick={() => void previewRemove(remote)}
                className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[10px] font-semibold text-red-700 disabled:opacity-40"
              >
                移除
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {[
          ["远端 ID", remoteId, setRemoteId, "offsite"],
          ["显示名称", label, setLabel, "异地对象存储"],
          ["HTTPS S3 端点", endpoint, setEndpoint, "https://s3.example.com"],
          ["区域", region, setRegion, "us-east-1"],
          ["存储桶", bucket, setBucket, "echo-backups"],
          ["桶内前缀", prefix, setPrefix, "echo-nas"],
        ].map(([fieldLabel, value, setter, placeholder]) => (
          <label
            key={fieldLabel as string}
            className="text-[10px] font-medium text-slate-600"
          >
            {fieldLabel as string}
            <input
              aria-label={fieldLabel as string}
              value={value as string}
              disabled={disabled || busy}
              onChange={(event) =>
                (setter as Dispatch<SetStateAction<string>>)(event.target.value)
              }
              placeholder={placeholder as string}
              className="mt-1 h-8 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] outline-none focus:border-blue-400 disabled:bg-slate-100"
            />
          </label>
        ))}
        <label className="text-[10px] font-medium text-slate-600">
          Access Key
          <input
            type="password"
            autoComplete="off"
            aria-label="S3 Access Key"
            value={accessKeyId}
            disabled={disabled || busy}
            onChange={(event) => setAccessKeyId(event.target.value)}
            className="mt-1 h-8 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] outline-none focus:border-blue-400 disabled:bg-slate-100"
          />
        </label>
        <label className="text-[10px] font-medium text-slate-600">
          Secret Key
          <input
            type="password"
            autoComplete="new-password"
            aria-label="S3 Secret Key"
            value={secretAccessKey}
            disabled={disabled || busy}
            onChange={(event) => setSecretAccessKey(event.target.value)}
            className="mt-1 h-8 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] outline-none focus:border-blue-400 disabled:bg-slate-100"
          />
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          disabled={disabled || loading || busy}
          onClick={() => void previewCreate()}
          className="rounded-lg bg-blue-600 px-3 py-2 text-[10px] font-semibold text-white disabled:opacity-40"
        >
          预览并建立加密挂载
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-[10px] text-red-700">
          {error}
        </p>
      )}

      <HighRiskApprovalDialog
        open={Boolean(pendingPlan)}
        title={
          pendingDesired?.operation === "remove"
            ? "确认移除异地备份挂载"
            : "确认建立异地备份挂载"
        }
        description="系统会更新 root 管理的加密凭据、远端注册表和 systemd 挂载服务，并验证真实 fuse.rclone 挂载。连接失败会恢复原状态。"
        targetLabel={
          pendingPlan?.desired.label ?? pendingPlan?.desired.remoteId
        }
        confirmLabel={
          pendingDesired?.operation === "remove" ? "确认移除" : "确认建立"
        }
        destructive={pendingDesired?.operation === "remove"}
        onCancel={cancelPending}
        onConfirm={apply}
      />
    </section>
  );
}
