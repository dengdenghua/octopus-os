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
  applyOmvBtrfsRaid1,
  applyOmvBtrfsScrub,
  fetchNativeStatus,
  fetchOmvBtrfsMaintenance,
  fetchOmvBtrfsRaid1Candidates,
  planOmvBtrfsRaid1,
  planOmvBtrfsScrub,
  type OmvBtrfsMaintenance,
  type OmvBtrfsRaid1Candidate,
  type OmvBtrfsRaid1DesiredState,
  type OmvBtrfsRaid1Plan,
  type OmvBtrfsScrubPlan,
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

function identity(device: OmvBtrfsRaid1Candidate) {
  return device.wwn || device.serial || "无稳定标识";
}

function scanLabel(item: OmvBtrfsMaintenance) {
  const scan = item.scan;
  if (scan.state === "inProgress")
    return `scrub 进行中${scan.progressPercent == null ? "" : ` · ${scan.progressPercent}%`}`;
  if (scan.state === "completed")
    return scan.errors
      ? `上次 scrub 完成 · ${scan.errors} 个错误`
      : "上次 scrub 已完成";
  if (scan.state === "failed") return "上次 scrub 未完成";
  return "尚未运行 scrub";
}

export function BtrfsRaid1Panel() {
  const [status, setStatus] = useState<OmvStatus | null>(null);
  const [candidates, setCandidates] = useState<OmvBtrfsRaid1Candidate[]>([]);
  const [maintenance, setMaintenance] = useState<OmvBtrfsMaintenance[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [createPlan, setCreatePlan] = useState<OmvBtrfsRaid1Plan | null>(null);
  const [createPassword, setCreatePassword] = useState("");
  const [scrubPlan, setScrubPlan] = useState<OmvBtrfsScrubPlan | null>(null);
  const [scrubPassword, setScrubPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const createAvailable = status?.capabilities.includes(
    "storage.volume.btrfs-raid1.create-mount.v1",
  );
  const scrubAvailable = status?.capabilities.includes(
    "storage.volume.btrfs.scrub.start.v1",
  );
  const validName = /^[a-z][a-z0-9_-]{0,15}$/.test(name);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    setStatus(null);
    setCreatePlan(null);
    setScrubPlan(null);
    setCreatePassword("");
    setScrubPassword("");
    try {
      const nextStatus = await fetchNativeStatus();
      setStatus(nextStatus);
      const [nextCandidates, nextMaintenance] = await Promise.all([
        nextStatus.capabilities.includes(
          "storage.volume.btrfs-raid1.create-mount.v1",
        )
          ? fetchOmvBtrfsRaid1Candidates()
          : Promise.resolve([]),
        nextStatus.capabilities.includes("storage.volume.btrfs.scrub.start.v1")
          ? fetchOmvBtrfsMaintenance()
          : Promise.resolve([]),
      ]);
      setCandidates(nextCandidates);
      setMaintenance(nextMaintenance);
      setSelected((current) =>
        current.filter((devicefile) =>
          nextCandidates.some((item) => item.devicefile === devicefile),
        ),
      );
    } catch (reason) {
      setCandidates([]);
      setMaintenance([]);
      setError(
        reason instanceof Error ? reason.message : "无法读取 Btrfs 存储能力",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const desired = useMemo<OmvBtrfsRaid1DesiredState | null>(() => {
    if (selected.length !== 2 || !confirmed || !validName) return null;
    return {
      schema: "echo.omv.btrfs-raid1-desired.v1",
      name,
      devices: [...selected].sort() as [string, string],
      dataLossConfirmed: true,
    };
  }, [confirmed, name, selected, validName]);

  const changeSelection = (devicefile: string) => {
    setCreatePlan(null);
    setSuccess(null);
    setSelected((current) =>
      current.includes(devicefile)
        ? current.filter((item) => item !== devicefile)
        : current.length < 2
          ? [...current, devicefile]
          : current,
    );
  };

  const previewCreate = async () => {
    if (!desired) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    setScrubPlan(null);
    try {
      setCreatePlan(await planOmvBtrfsRaid1(desired));
    } catch (reason) {
      setCreatePlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 Btrfs 创建预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    if (!createPlan || !createPassword) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "omv.btrfs-raid1.create",
        createPlan.planId,
        createPassword,
      );
      const result = await applyOmvBtrfsRaid1(
        createPlan.desired,
        createPlan.planId,
        approval.approvalToken,
      );
      const mountpoint = result.filesystem?.mountpoint ?? createPlan.mountpoint;
      setSelected([]);
      setName("");
      setConfirmed(false);
      await refresh();
      setSuccess(`Btrfs RAID1 已创建、挂载并验证：${mountpoint}`);
    } catch (reason) {
      setCreatePassword("");
      setError(
        reason instanceof Error ? reason.message : "Btrfs RAID1 创建失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const previewScrub = async (item: OmvBtrfsMaintenance) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    setCreatePlan(null);
    setScrubPassword("");
    try {
      setScrubPlan(
        await planOmvBtrfsScrub({
          schema: "echo.omv.btrfs-scrub-desired.v1",
          filesystemUuid: item.filesystem.uuid,
          operation: "start",
        }),
      );
    } catch (reason) {
      setScrubPlan(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成 scrub 预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const startScrub = async () => {
    if (!scrubPlan || !scrubPassword) return;
    setBusy(true);
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        "omv.btrfs.scrub.start",
        scrubPlan.planId,
        scrubPassword,
      );
      const result = await applyOmvBtrfsScrub(
        scrubPlan.desired,
        scrubPlan.planId,
        approval.approvalToken,
      );
      await refresh();
      setSuccess(
        result.maintenanceState === "completed"
          ? "Btrfs scrub 已完成且未报告错误"
          : result.maintenanceState === "completedWithErrors"
            ? "Btrfs scrub 已完成，但报告了错误，请检查存储健康"
            : "Btrfs scrub 已启动，可刷新查看进度",
      );
    } catch (reason) {
      setScrubPassword("");
      setError(
        reason instanceof Error ? reason.message : "Btrfs scrub 启动失败",
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
            Btrfs RAID1 卷
          </h1>
          <p className="mt-1 text-[12px] text-slate-500">
            两块空盘直接创建、UUID 持久挂载，并管理校验 scrub
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
          刷新 Btrfs
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
            <Loader2Icon className="size-5 animate-spin" /> 正在核验 Btrfs 能力…
          </span>
        </div>
      ) : !createAvailable && !scrubAvailable ? (
        <section className="mt-5 rounded-[22px] bg-white/78 p-5 text-sm text-slate-600 ring-1 ring-white/90">
          当前主机未提供受控 Btrfs RAID1 创建或 scrub 能力。
        </section>
      ) : (
        <>
          {createAvailable && (
            <>
              <section className="mt-5 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
                <div className="flex items-center gap-2">
                  <HardDriveIcon className="size-5 text-cyan-700" />
                  <h2 className="text-sm font-semibold text-slate-900">
                    1. 选择两块空盘
                  </h2>
                </div>
                <p className="mt-2 text-[11px] leading-5 text-slate-500">
                  只显示服务端确认的整盘、空白、未挂载、不可移动且带稳定身份的磁盘；不提供强制覆盖入口。
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
                            ? "bg-cyan-50 ring-cyan-300"
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
                      没有通过安全检查的空盘；这里不会清除已有签名来制造候选盘。
                    </p>
                  )}
                </div>
              </section>

              <section className="mt-4 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
                <h2 className="text-sm font-semibold text-slate-900">
                  2. 命名并确认数据清除
                </h2>
                <label className="mt-4 block text-xs font-medium text-slate-700">
                  Btrfs 卷名称
                  <input
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value);
                      setCreatePlan(null);
                    }}
                    placeholder="例如 family"
                    pattern="[a-z][a-z0-9_-]{0,15}"
                    className="mt-2 h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-cyan-500"
                  />
                  <span className="mt-1 block text-[10px] font-normal text-slate-400">
                    小写字母开头，可含数字、下划线和短横线，最多 16 字符
                  </span>
                </label>
                <label className="mt-4 flex items-start gap-2 rounded-2xl bg-red-50 p-4 text-xs leading-5 text-red-800 ring-1 ring-red-100">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => {
                      setConfirmed(event.target.checked);
                      setCreatePlan(null);
                    }}
                    className="mt-1"
                  />
                  我确认：选中的两块磁盘将创建新 Btrfs
                  RAID1，现有数据会被不可逆清除。
                </label>
                <button
                  type="button"
                  onClick={() => void previewCreate()}
                  disabled={!desired || busy}
                  className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-cyan-700 px-4 text-xs font-semibold text-white disabled:opacity-40"
                >
                  {busy && <Loader2Icon className="size-4 animate-spin" />} 生成
                  Btrfs 创建预览
                </button>
              </section>
            </>
          )}

          {createPlan && (
            <section className="mt-4 rounded-[22px] bg-red-950 p-5 text-white shadow-lg">
              <div className="flex items-center gap-2">
                <ShieldAlertIcon className="size-5 text-red-300" />
                <h2 className="text-sm font-semibold">最终破坏性复核</h2>
              </div>
              <p className="mt-3 text-xs leading-5 text-red-100/80">
                两块磁盘将格式化为 data/metadata 双 RAID1，并按文件系统 UUID
                挂载到 {createPlan.mountpoint}。 不使用
                force；失败时尝试卸载、恢复 fstab 并清除本次新签名。
              </p>
              <label className="mt-4 block text-xs font-medium text-red-50">
                Btrfs 创建管理员密码
                <input
                  type="password"
                  autoComplete="current-password"
                  value={createPassword}
                  onChange={(event) => setCreatePassword(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border border-red-700/60 bg-white/10 px-3 text-sm text-white outline-none focus:border-red-300"
                />
              </label>
              <button
                type="button"
                onClick={() => void create()}
                disabled={!createPassword || busy}
                className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-red-500 px-4 text-xs font-semibold text-white disabled:opacity-40"
              >
                {busy && <Loader2Icon className="size-4 animate-spin" />}{" "}
                确认清空并创建 Btrfs RAID1
              </button>
            </section>
          )}

          {scrubAvailable && (
            <section className="mt-4 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
              <div className="flex items-center gap-2">
                <DatabaseIcon className="size-5 text-violet-700" />
                <h2 className="text-sm font-semibold text-slate-900">
                  Btrfs scrub 校验
                </h2>
              </div>
              <p className="mt-2 text-[11px] leading-5 text-slate-500">
                只允许 Echo 登记、完整、可写且 data/metadata 都为 RAID1
                的卷。scrub
                会读取全部数据，并可能从冗余副本修复校验错误；运行后不可由此页面回滚或取消。
              </p>
              <div className="mt-4 space-y-3">
                {maintenance.map((item) => (
                  <article
                    key={item.filesystem.uuid}
                    className="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <strong className="block text-sm text-slate-900">
                          {item.filesystem.mountpoint}
                        </strong>
                        <span className="mt-1 block font-mono text-[10px] text-slate-400">
                          {item.filesystem.uuid}
                        </span>
                        <span className="mt-1 block text-[11px] text-slate-600">
                          {scanLabel(item)} · {item.filesystem.activeDevices}/
                          {item.filesystem.totalDevices} 成员 · 错误计数{" "}
                          {item.filesystem.deviceErrorCount}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => void previewScrub(item)}
                        disabled={!item.canStartScrub || busy}
                        className="h-9 rounded-xl bg-violet-700 px-3 text-[11px] font-semibold text-white disabled:opacity-40"
                      >
                        预览 scrub
                      </button>
                    </div>
                    {!item.canStartScrub && (
                      <p className="mt-2 text-[10px] text-amber-700">
                        当前拓扑、挂载状态或维护任务不满足安全启动条件。
                      </p>
                    )}
                  </article>
                ))}
                {maintenance.length === 0 && (
                  <p className="rounded-xl bg-slate-50 px-3 py-3 text-xs text-slate-500">
                    当前没有 Echo 登记并挂载的 Btrfs RAID1 卷。
                  </p>
                )}
              </div>
            </section>
          )}

          {scrubPlan && (
            <section className="mt-4 rounded-[22px] bg-violet-950 p-5 text-white shadow-lg">
              <div className="flex items-center gap-2">
                <ShieldAlertIcon className="size-5 text-violet-300" />
                <h2 className="text-sm font-semibold">确认启动 Btrfs scrub</h2>
              </div>
              <p className="mt-3 text-xs leading-5 text-violet-100/80">
                目标 {scrubPlan.filesystem.mountpoint}。任务为高
                I/O、非阻塞启动，可能利用 RAID1
                冗余副本修复数据；接受后没有自动回滚。
              </p>
              <label className="mt-4 block text-xs font-medium text-violet-50">
                scrub 管理员密码
                <input
                  type="password"
                  autoComplete="current-password"
                  value={scrubPassword}
                  onChange={(event) => setScrubPassword(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border border-violet-700/60 bg-white/10 px-3 text-sm text-white outline-none focus:border-violet-300"
                />
              </label>
              <button
                type="button"
                onClick={() => void startScrub()}
                disabled={!scrubPassword || busy}
                className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-violet-500 px-4 text-xs font-semibold text-white disabled:opacity-40"
              >
                {busy && <Loader2Icon className="size-4 animate-spin" />} 启动
                Btrfs scrub
              </button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
