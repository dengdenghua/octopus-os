import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  DatabaseIcon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldAlertIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyOmvExt4Volume,
  fetchNativeStatus,
  fetchOmvExt4VolumeCandidates,
  planOmvExt4Volume,
  type OmvExt4VolumeDesiredState,
  type OmvExt4VolumePlan,
  type OmvManagedMdRaid1,
  type OmvStatus,
} from "@/appliance/omv";
import { cn } from "@/lib/utils";

export function Ext4VolumePanel() {
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [candidates, setCandidates] = useState<OmvManagedMdRaid1[]>([]);
  const [arrayUuid, setArrayUuid] = useState("");
  const [name, setName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [plan, setPlan] = useState<OmvExt4VolumePlan | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const available = status?.capabilities?.includes(
    "storage.volume.ext4.create-mount.v1",
  );
  const validName = /^[a-z][a-z0-9_-]{0,15}$/.test(name);
  const selected = candidates.find((array) => array.uuid === arrayUuid) ?? null;

  const refresh = async () => {
    setLoading(true);
    setError(null);
    setStatus(null);
    setCandidates([]);
    setArrayUuid("");
    setPlan(null);
    setPassword("");
    try {
      const nextStatus = await fetchNativeStatus();
      setStatus(nextStatus);
      if (
        nextStatus.capabilities?.includes("storage.volume.ext4.create-mount.v1")
      ) {
        setCandidates(await fetchOmvExt4VolumeCandidates());
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取 EXT4 卷能力",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const desired = useMemo<OmvExt4VolumeDesiredState | null>(() => {
    if (!arrayUuid || !validName || !confirmed) return null;
    return {
      schema: "echo.omv.ext4-volume-desired.v1",
      arrayUuid,
      name,
      dataLossConfirmed: true,
    };
  }, [arrayUuid, confirmed, name, validName]);

  const preview = async () => {
    if (!desired) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      setPlan(await planOmvExt4Volume(desired));
    } catch (reason) {
      setPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 EXT4 卷预览",
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
        "omv.ext4-volume.create",
        plan.planId,
        password,
      );
      const applied = await applyOmvExt4Volume(
        plan.desired,
        plan.planId,
        approval.approvalToken,
      );
      setSuccess(
        `EXT4 卷已创建并挂载到 ${applied.filesystem?.mountpoint ?? plan.mountpoint}`,
      );
      setName("");
      setConfirmed(false);
      setPlan(null);
      setPassword("");
      await refresh();
    } catch (reason) {
      setPassword("");
      setError(reason instanceof Error ? reason.message : "EXT4 卷创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[980px] px-7 pt-7">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[25px] font-semibold tracking-tight text-slate-900">
            EXT4 数据卷
          </h1>
          <p className="mt-1 text-[12px] text-slate-500">
            把空白 Echo RAID1 格式化，并按 UUID 持久挂载到 /data
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || busy}
          className="inline-flex h-9 items-center gap-1.5 rounded-full bg-white/78 px-3.5 text-[11px] font-semibold text-slate-600 shadow-sm ring-1 ring-slate-200 disabled:opacity-50"
        >
          <RefreshCwIcon
            className={cn("size-3.5", loading && "animate-spin")}
          />
          刷新卷
        </button>
      </header>

      {error && (
        <div
          role="alert"
          className="mt-5 rounded-2xl bg-red-50 px-4 py-3 text-xs text-red-700 ring-1 ring-red-100"
        >
          {error}
        </div>
      )}
      {success && (
        <div
          role="status"
          className="mt-5 flex items-center gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-xs text-emerald-800 ring-1 ring-emerald-100"
        >
          <CheckCircle2Icon className="size-4" /> {success}
        </div>
      )}

      {loading && !status ? (
        <div className="grid min-h-36 place-items-center text-sm text-slate-400">
          <span className="flex items-center gap-2">
            <Loader2Icon className="size-5 animate-spin" /> 正在核验 EXT4 能力…
          </span>
        </div>
      ) : !available ? (
        <section className="mt-5 rounded-[22px] bg-white/78 p-5 text-sm text-slate-600 ring-1 ring-white/90">
          当前主机未提供受控 EXT4 创建与挂载能力。
        </section>
      ) : (
        <>
          <section className="mt-5 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
            <div className="flex items-center gap-2">
              <DatabaseIcon className="size-5 text-emerald-600" />
              <h2 className="text-sm font-semibold text-slate-900">
                1. 选择空白 RAID1
              </h2>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {candidates.map((array) => (
                <button
                  key={array.uuid}
                  type="button"
                  aria-pressed={arrayUuid === array.uuid}
                  onClick={() => {
                    setArrayUuid(array.uuid);
                    setPlan(null);
                    setSuccess(null);
                  }}
                  className={cn(
                    "rounded-2xl p-4 text-left ring-1 transition",
                    arrayUuid === array.uuid
                      ? "bg-emerald-50 ring-emerald-300"
                      : "bg-slate-50/80 ring-slate-200 hover:bg-white",
                  )}
                >
                  <span className="block text-sm font-semibold text-slate-900">
                    {array.devicefile}
                  </span>
                  <span className="mt-1 block font-mono text-[10px] text-slate-400">
                    {array.uuid}
                  </span>
                </button>
              ))}
              {candidates.length === 0 && (
                <p className="col-span-full rounded-2xl bg-amber-50 p-4 text-xs leading-5 text-amber-800">
                  没有健康、未挂载且无文件系统签名的 Echo RAID1。
                </p>
              )}
            </div>
          </section>

          <section className="mt-4 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
            <h2 className="text-sm font-semibold text-slate-900">
              2. 命名并确认格式化
            </h2>
            <label className="mt-4 block text-xs font-medium text-slate-700">
              数据卷名称
              <input
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setPlan(null);
                }}
                placeholder="例如 family"
                pattern="[a-z][a-z0-9_-]{0,15}"
                className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-emerald-400"
              />
              <span className="mt-1 block text-[10px] font-normal text-slate-400">
                同时作为 EXT4 标签与 /data 下目录名，最多 16 字符
              </span>
            </label>
            <label className="mt-4 flex items-start gap-2 rounded-2xl bg-red-50 p-4 text-xs leading-5 text-red-800 ring-1 ring-red-100">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => {
                  setConfirmed(event.target.checked);
                  setPlan(null);
                }}
                className="mt-1"
              />
              我确认：将格式化 {selected?.devicefile ?? "所选阵列"}
              ，现有数据会被不可逆清除。
            </label>
            <button
              type="button"
              onClick={() => void preview()}
              disabled={!desired || busy}
              className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-emerald-600 px-4 text-xs font-semibold text-white disabled:opacity-40"
            >
              {busy && <Loader2Icon className="size-4 animate-spin" />}{" "}
              生成格式化预览
            </button>
          </section>

          {plan && (
            <section className="mt-4 rounded-[22px] bg-red-950 p-5 text-white shadow-lg">
              <div className="flex items-center gap-2">
                <ShieldAlertIcon className="size-5 text-red-300" />
                <h2 className="text-sm font-semibold">最终破坏性复核</h2>
              </div>
              <p className="mt-3 text-xs leading-5 text-red-100/80">
                将把 {plan.array.devicefile} 格式化为 EXT4，并按新文件系统 UUID
                挂载到 {plan.mountpoint}。
                不使用强制格式化参数；执行前会重新核验阵列与配置状态。
              </p>
              <label className="mt-4 block text-xs font-medium text-red-50">
                设备管理员密码
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border border-red-700/60 bg-white/10 px-3 text-sm text-white outline-none focus:border-red-300"
                />
              </label>
              <button
                type="button"
                onClick={() => void apply()}
                disabled={!password || busy}
                className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-red-500 px-4 text-xs font-semibold text-white disabled:opacity-40"
              >
                {busy && <Loader2Icon className="size-4 animate-spin" />}{" "}
                确认格式化并挂载
              </button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
