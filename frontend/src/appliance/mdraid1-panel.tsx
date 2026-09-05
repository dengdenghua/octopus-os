import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  HardDriveIcon,
  Layers3Icon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldAlertIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyOmvMdRaid1,
  fetchNativeStatus,
  fetchOmvMdRaid1Candidates,
  planOmvMdRaid1,
  type OmvMdRaid1Candidate,
  type OmvMdRaid1DesiredState,
  type OmvMdRaid1Plan,
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

function identity(device: OmvMdRaid1Candidate) {
  return device.wwn || device.serial || "无稳定标识";
}

export function MdRaid1Panel() {
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [candidates, setCandidates] = useState<OmvMdRaid1Candidate[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [plan, setPlan] = useState<OmvMdRaid1Plan | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const available = status?.capabilities?.includes(
    "storage.array.mdraid1.create.v1",
  );
  const validName = /^[a-z][a-z0-9_-]{0,26}$/.test(name);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    setStatus(null);
    setCandidates([]);
    setSelected([]);
    setPlan(null);
    setPassword("");
    try {
      const nextStatus = await fetchNativeStatus();
      setStatus(nextStatus);
      if (
        nextStatus.capabilities?.includes("storage.array.mdraid1.create.v1")
      ) {
        setCandidates(await fetchOmvMdRaid1Candidates());
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取 Linux RAID1 能力",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const desired = useMemo<OmvMdRaid1DesiredState | null>(() => {
    if (selected.length !== 2 || !confirmed || !validName) return null;
    return {
      schema: "echo.omv.mdraid1-desired.v1",
      name,
      devices: [...selected].sort() as [string, string],
      dataLossConfirmed: true,
    };
  }, [confirmed, name, selected, validName]);

  const changeSelection = (devicefile: string) => {
    setPlan(null);
    setSuccess(null);
    setSelected((current) =>
      current.includes(devicefile)
        ? current.filter((item) => item !== devicefile)
        : current.length < 2
          ? [...current, devicefile]
          : current,
    );
  };

  const preview = async () => {
    if (!desired) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      setPlan(await planOmvMdRaid1(desired));
    } catch (reason) {
      setPlan(null);
      setError(reason instanceof Error ? reason.message : "无法生成阵列预览");
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
        "omv.mdraid1.create",
        plan.planId,
        password,
      );
      const applied = await applyOmvMdRaid1(
        plan.desired,
        plan.planId,
        approval.approvalToken,
      );
      setPassword("");
      setPlan(null);
      setSelected([]);
      setConfirmed(false);
      setName("");
      setSuccess(
        `RAID1 ${applied.array?.devicefile ?? plan.target} 已创建并验证；尚未格式化或挂载`,
      );
      await refresh();
    } catch (reason) {
      setPassword("");
      setError(
        reason instanceof Error ? reason.message : "Linux RAID1 创建失败",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[980px] px-7 pt-7">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[25px] font-semibold tracking-tight text-slate-900">
            Linux RAID1 阵列
          </h1>
          <p className="mt-1 text-[12px] text-slate-500">
            两块空盘镜像；本步骤只创建块阵列，不格式化、不挂载
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
          刷新 RAID1
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
            <Loader2Icon className="size-5 animate-spin" /> 正在核验 RAID1 能力…
          </span>
        </div>
      ) : !available ? (
        <section className="mt-5 rounded-[22px] bg-white/78 p-5 text-sm text-slate-600 ring-1 ring-white/90">
          当前主机未提供受控 Linux RAID1 创建能力。需要 mdadm、lsblk、wipefs 和
          update-initramfs。
        </section>
      ) : (
        <>
          <section className="mt-5 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
            <div className="flex items-center gap-2">
              <Layers3Icon className="size-5 text-indigo-600" />
              <h2 className="text-sm font-semibold text-slate-900">
                1. 选择两块空盘
              </h2>
            </div>
            <p className="mt-2 text-[11px] leading-5 text-slate-500">
              只显示整盘、无分区/签名、未挂载、不可移动且带稳定序列号或 WWN
              的磁盘。两条设备路径必须对应不同物理盘。
            </p>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {candidates.map((device) => {
                const active = selected.includes(device.devicefile);
                return (
                  <button
                    key={device.devicefile}
                    type="button"
                    aria-pressed={active}
                    onClick={() => changeSelection(device.devicefile)}
                    className={cn(
                      "rounded-2xl p-4 text-left ring-1 transition",
                      active
                        ? "bg-indigo-50 ring-indigo-300"
                        : "bg-slate-50/80 ring-slate-200 hover:bg-white",
                    )}
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <HardDriveIcon className="size-4" /> {device.devicefile}
                    </span>
                    <span className="mt-2 block text-xs text-slate-600">
                      {device.model || "未知型号"} ·{" "}
                      {formatBytes(device.sizeBytes)}
                    </span>
                    <span className="mt-1 block break-all font-mono text-[10px] text-slate-400">
                      {identity(device)}
                    </span>
                  </button>
                );
              })}
              {candidates.length === 0 && (
                <p className="col-span-full rounded-2xl bg-amber-50 p-4 text-xs leading-5 text-amber-800">
                  没有通过服务端安全检查的空盘。这里不会提供强制清盘入口。
                </p>
              )}
            </div>
          </section>

          <section className="mt-4 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
            <h2 className="text-sm font-semibold text-slate-900">
              2. 命名并确认数据清除
            </h2>
            <label className="mt-4 block text-xs font-medium text-slate-700">
              阵列名称
              <input
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setPlan(null);
                }}
                placeholder="例如 family"
                pattern="[a-z][a-z0-9_-]{0,26}"
                className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-indigo-400"
              />
              <span className="mt-1 block text-[10px] font-normal text-slate-400">
                小写字母开头，可含数字、下划线和短横线，最多 27 字符
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
              我确认：选中的两块磁盘将用于新 Linux
              RAID1，现有数据会被不可逆清除。
            </label>
            <button
              type="button"
              onClick={() => void preview()}
              disabled={!desired || busy}
              className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-indigo-600 px-4 text-xs font-semibold text-white disabled:opacity-40"
            >
              {busy && <Loader2Icon className="size-4 animate-spin" />}{" "}
              生成阵列预览
            </button>
          </section>

          {plan && (
            <section className="mt-4 rounded-[22px] bg-red-950 p-5 text-white shadow-lg">
              <div className="flex items-center gap-2">
                <ShieldAlertIcon className="size-5 text-red-300" />
                <h2 className="text-sm font-semibold">最终破坏性复核</h2>
              </div>
              <p className="mt-3 text-xs leading-5 text-red-100/80">
                将创建 {plan.target}（RAID1），可用容量约{" "}
                {formatBytes(plan.usableBytes)}。 不使用
                force/assume-clean，也不降级启动；服务端会重新核验磁盘身份和空盘状态。
              </p>
              <p className="mt-2 rounded-xl bg-white/8 px-3 py-2 text-[11px] leading-5 text-amber-100">
                阵列创建后仍不能存文件：需要后续创建文件系统并挂载。
              </p>
              <ul className="mt-3 space-y-1 font-mono text-[11px] text-red-100/70">
                {plan.devices.map((device) => (
                  <li key={device.devicefile}>
                    {device.devicefile} · {formatBytes(device.sizeBytes)} ·{" "}
                    {identity(device)}
                  </li>
                ))}
              </ul>
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
                确认清空并创建 RAID1
              </button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
