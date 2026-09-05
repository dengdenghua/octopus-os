import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2Icon,
  DatabaseIcon,
  HardDriveIcon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldAlertIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyOmvZfsMirror,
  fetchNativeStatus,
  fetchOmvZfsMirrorCandidates,
  planOmvZfsMirror,
  type OmvStatus,
  type OmvZfsMirrorCandidate,
  type OmvZfsMirrorDesiredState,
  type OmvZfsMirrorPlan,
} from "@/appliance/omv";
import { cn } from "@/lib/utils";
import { ZfsLifecyclePanel } from "./zfs-lifecycle-panel";

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

function identity(device: OmvZfsMirrorCandidate) {
  return device.wwn || device.serial || "无稳定标识";
}

export function ZfsMirrorPanel() {
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [candidates, setCandidates] = useState<OmvZfsMirrorCandidate[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [plan, setPlan] = useState<OmvZfsMirrorPlan | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const available = status?.capabilities?.includes(
    "storage.pool.zfs-mirror.create.v1",
  );
  const lifecycleAvailable = Boolean(
    status?.capabilities?.some((capability) =>
      [
        "storage.pool.zfs-mirror.replace.blank.v1",
        "storage.pool.zfs.export.safe.v1",
        "storage.pool.zfs.import.echo-root.v1",
      ].includes(capability),
    ),
  );
  const anyAvailable = Boolean(available || lifecycleAvailable);
  const validName =
    /^[a-z][a-z0-9_-]{0,31}$/.test(name) &&
    !/^(?:mirror|raidz|draid|spare|log|c\d)/.test(name);

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
        nextStatus.capabilities?.includes("storage.pool.zfs-mirror.create.v1")
      ) {
        const nextCandidates = await fetchOmvZfsMirrorCandidates();
        setCandidates(nextCandidates);
        setSelected((current) =>
          current.filter((devicefile) =>
            nextCandidates.some((item) => item.devicefile === devicefile),
          ),
        );
      } else {
        setCandidates([]);
        setSelected([]);
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取 ZFS 建池能力",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const desired = useMemo<OmvZfsMirrorDesiredState | null>(() => {
    if (selected.length !== 2 || !confirmed || !validName) return null;
    const devices = [...selected].sort() as [string, string];
    return {
      schema: "echo.omv.zfs-mirror-desired.v1",
      name,
      devices,
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
      setPlan(await planOmvZfsMirror(desired));
    } catch (reason) {
      setPlan(null);
      setError(reason instanceof Error ? reason.message : "无法生成建池预览");
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
        "omv.zfs-mirror.create",
        plan.planId,
        password,
      );
      const applied = await applyOmvZfsMirror(
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
        `存储池 ${applied.pool?.name ?? plan.desired.name} 已创建并验证为 ONLINE`,
      );
      await refresh();
    } catch (reason) {
      setPassword("");
      setError(reason instanceof Error ? reason.message : "ZFS 镜像创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-[980px] px-7 py-7">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[25px] font-semibold tracking-tight text-slate-900">
            存储池
          </h1>
          <p className="mt-1 text-[12px] text-slate-500">
            创建镜像池，并安全导出或重新导入现有数据池
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
          刷新存储池
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
        <div className="grid min-h-72 place-items-center text-sm text-slate-400">
          <span className="flex items-center gap-2">
            <Loader2Icon className="size-5 animate-spin" /> 正在核验建池能力…
          </span>
        </div>
      ) : !anyAvailable ? (
        <section className="mt-5 rounded-[22px] bg-white/78 p-5 text-sm text-slate-600 ring-1 ring-white/90">
          当前主机未提供受控 ZFS 存储池能力。创建镜像需要 zpool、zfs、lsblk 和
          wipefs；数据保留导出/导入需要 zpool 与 zfs。
        </section>
      ) : (
        <>
          {available && (
            <>
              <section className="mt-5 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
                <div className="flex items-center gap-2">
                  <DatabaseIcon className="size-5 text-blue-600" />
                  <h2 className="text-sm font-semibold text-slate-900">
                    1. 选择两块空盘
                  </h2>
                </div>
                <p className="mt-2 text-[11px] leading-5 text-slate-500">
                  这里只显示整盘、无分区/签名、未挂载、不可移动且带稳定序列号或
                  WWN 的磁盘。系统盘不会出现在候选列表。
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
                            ? "bg-blue-50 ring-blue-300"
                            : "bg-slate-50/80 ring-slate-200 hover:bg-white",
                        )}
                      >
                        <span className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                          <HardDriveIcon className="size-4" />{" "}
                          {device.devicefile}
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
                      没有通过服务端安全检查的空盘。不会提供强制清盘或绕过入口。
                    </p>
                  )}
                </div>
              </section>

              <section className="mt-4 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
                <h2 className="text-sm font-semibold text-slate-900">
                  2. 命名并确认数据清除
                </h2>
                <label className="mt-4 block text-xs font-medium text-slate-700">
                  存储池名称
                  <input
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value);
                      setPlan(null);
                    }}
                    placeholder="例如 family"
                    pattern="[a-z][a-z0-9_-]{0,31}"
                    className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-blue-400"
                  />
                  <span className="mt-1 block text-[10px] font-normal text-slate-400">
                    小写字母开头，可含数字、下划线和短横线，最多 32 字符
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
                  我确认：选中的两块磁盘将用于新 ZFS
                  mirror，现有数据会被不可逆清除。
                </label>
                <button
                  type="button"
                  onClick={() => void preview()}
                  disabled={!desired || busy}
                  className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-blue-600 px-4 text-xs font-semibold text-white disabled:opacity-40"
                >
                  {busy && <Loader2Icon className="size-4 animate-spin" />}{" "}
                  生成建池预览
                </button>
              </section>

              {plan && (
                <section className="mt-4 rounded-[22px] bg-red-950 p-5 text-white shadow-lg">
                  <div className="flex items-center gap-2">
                    <ShieldAlertIcon className="size-5 text-red-300" />
                    <h2 className="text-sm font-semibold">最终破坏性复核</h2>
                  </div>
                  <p className="mt-3 text-xs leading-5 text-red-100/80">
                    将创建 {plan.desired.name}（mirror），挂载到{" "}
                    {plan.mountpoint}
                    。不会使用强制参数；审批后服务端仍会重新核验磁盘身份和空盘状态。
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
                    确认清空并创建镜像
                  </button>
                </section>
              )}
            </>
          )}
          <ZfsLifecyclePanel status={status} />
        </>
      )}
    </div>
  );
}
