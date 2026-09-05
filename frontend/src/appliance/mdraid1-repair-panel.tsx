import { useEffect, useState } from "react";
import {
  CheckCircle2Icon,
  HardDriveIcon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldAlertIcon,
  WrenchIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import { MdRaidMaintenancePanel } from "@/appliance/mdraid-maintenance-panel";
import {
  applyOmvMdRaid1Replace,
  fetchNativeStatus,
  fetchOmvMdRaid1ReplacementCandidates,
  planOmvMdRaid1Replace,
  type OmvMdRaid1ReplacePlan,
  type OmvMdRaid1ReplacementCandidate,
  type OmvStatus,
} from "@/appliance/omv";
import { cn } from "@/lib/utils";

function formatBytes(bytes: number) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

export function MdRaid1RepairPanel() {
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [candidates, setCandidates] = useState<
    OmvMdRaid1ReplacementCandidate[]
  >([]);
  const [plan, setPlan] = useState<OmvMdRaid1ReplacePlan | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const available = Boolean(
    status?.capabilities?.includes(
      "storage.array.mdraid1.replace-failed.blank.v1",
    ),
  );

  const refresh = async () => {
    setLoading(true);
    setError(null);
    setPlan(null);
    setPassword("");
    try {
      const nextStatus = await fetchNativeStatus();
      setStatus(nextStatus);
      setCandidates(
        nextStatus.capabilities?.includes(
          "storage.array.mdraid1.replace-failed.blank.v1",
        )
          ? await fetchOmvMdRaid1ReplacementCandidates()
          : [],
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取 RAID1 修复状态",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const preview = async (
    candidate: OmvMdRaid1ReplacementCandidate,
    replacementDevice: string,
  ) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    setPassword("");
    try {
      setPlan(
        await planOmvMdRaid1Replace({
          schema: "echo.omv.mdraid1-replace-desired.v1",
          name: candidate.array.name,
          arrayUuid: candidate.array.uuid,
          replacementDevice,
          dataPreserved: true,
        }),
      );
    } catch (reason) {
      setPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 RAID1 换盘预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan || !password) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "omv.mdraid1.replace",
        plan.planId,
        password,
      );
      const applied = await applyOmvMdRaid1Replace(
        plan.desired,
        plan.planId,
        approval.approvalToken,
      );
      setSuccess(
        applied.maintenanceState === "recovering"
          ? `${plan.array.devicefile} 已接纳新盘，正在后台重建`
          : `${plan.array.devicefile} 已接纳新盘并完成服务端核验`,
      );
      await refresh();
    } catch (reason) {
      setPassword("");
      setError(reason instanceof Error ? reason.message : "RAID1 换盘失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[980px] px-7 pt-5">
      <section className="rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <WrenchIcon className="size-5 text-amber-600" />
              <h2 className="text-sm font-semibold text-slate-900">
                降级 RAID1 换盘修复
              </h2>
            </div>
            <p className="mt-2 text-[11px] leading-5 text-slate-500">
              仅处理 Echo 自管双盘 RAID1：一块内核确认正常、一块已故障或缺失。
              不会主动标坏正常盘，也不提供强制模式。
            </p>
          </div>
          <button
            type="button"
            aria-label="刷新 RAID1 修复候选"
            onClick={() => void refresh()}
            disabled={loading || busy}
            className="grid size-8 shrink-0 place-items-center rounded-full bg-slate-50 text-slate-500 ring-1 ring-slate-200 disabled:opacity-40"
          >
            <RefreshCwIcon
              className={cn("size-3.5", loading && "animate-spin")}
            />
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="mt-4 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700"
          >
            {error}
          </div>
        )}
        {success && (
          <div
            role="status"
            className="mt-4 flex items-center gap-2 rounded-xl bg-emerald-50 px-3 py-2 text-xs text-emerald-800"
          >
            <CheckCircle2Icon className="size-4" /> {success}
          </div>
        )}

        {loading && !status ? (
          <p className="mt-4 flex items-center gap-2 text-xs text-slate-400">
            <Loader2Icon className="size-4 animate-spin" /> 正在核验降级阵列…
          </p>
        ) : !available ? (
          <p className="mt-4 rounded-xl bg-slate-50 p-3 text-xs text-slate-500">
            当前主机未提供受控 RAID1 故障盘替换能力。
          </p>
        ) : candidates.length === 0 ? (
          <p className="mt-4 rounded-xl bg-emerald-50 p-3 text-xs text-emerald-800">
            当前没有符合安全边界的单盘故障 RAID1。
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {candidates.map((candidate) => (
              <article
                key={candidate.array.uuid}
                className="rounded-2xl bg-amber-50/70 p-4 ring-1 ring-amber-100"
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ShieldAlertIcon className="size-4 text-amber-600" />
                  {candidate.array.devicefile}
                </div>
                <p className="mt-2 text-[11px] text-slate-600">
                  存活盘 {candidate.survivingMember.devicefile} · 缺失角色{" "}
                  {candidate.missingSlot}
                  {candidate.failedMember
                    ? ` · 故障盘 ${candidate.failedMember.devicefile}`
                    : " · 原盘已离线"}
                </p>
                <p className="mt-1 text-[10px] text-slate-400">
                  新盘至少 {formatBytes(candidate.minimumReplacementBytes)}
                  ；只显示通过空盘与稳定身份检查的磁盘
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {candidate.replacementDevices.map((device) => (
                    <button
                      key={device.devicefile}
                      type="button"
                      onClick={() => void preview(candidate, device.devicefile)}
                      disabled={busy}
                      className="inline-flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-xs font-semibold text-slate-700 ring-1 ring-amber-200 disabled:opacity-40"
                    >
                      <HardDriveIcon className="size-4" />
                      {device.devicefile} · {formatBytes(device.sizeBytes)}
                    </button>
                  ))}
                  {candidate.replacementDevices.length === 0 && (
                    <span className="text-xs text-amber-800">
                      没有容量足够的安全空盘
                    </span>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {plan && (
        <section className="mt-4 rounded-[22px] bg-slate-950 p-5 text-white shadow-lg">
          <h2 className="text-sm font-semibold">最终换盘复核</h2>
          <p className="mt-3 text-xs leading-5 text-slate-300">
            将从 {plan.array.devicefile} 移除已确认故障的成员（若仍存在），加入{" "}
            {plan.replacement.devicefile}
            ，随后由内核后台重建。服务端执行前会重新核验全部身份和状态。
          </p>
          <label className="mt-4 block text-xs font-medium text-slate-100">
            设备管理员密码
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-2 h-10 w-full rounded-xl border border-slate-700 bg-white/10 px-3 text-sm text-white outline-none focus:border-amber-300"
            />
          </label>
          <button
            type="button"
            onClick={() => void apply()}
            disabled={!password || busy}
            className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-amber-500 px-4 text-xs font-semibold text-slate-950 disabled:opacity-40"
          >
            {busy && <Loader2Icon className="size-4 animate-spin" />}{" "}
            确认换盘并开始重建
          </button>
        </section>
      )}
      <MdRaidMaintenancePanel />
    </div>
  );
}
