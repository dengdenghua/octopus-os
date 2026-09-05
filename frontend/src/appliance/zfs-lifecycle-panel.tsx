import { useEffect, useState } from "react";
import {
  ArchiveRestoreIcon,
  DatabaseIcon,
  Loader2Icon,
  LogOutIcon,
  ShieldCheckIcon,
  WrenchIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyOmvZfsMirrorReplace,
  applyOmvZfsPoolExport,
  applyOmvZfsPoolImport,
  fetchOmvZfsImportCandidates,
  fetchOmvZfsMirrorReplacementCandidates,
  fetchOmvZfsPools,
  planOmvZfsMirrorReplace,
  planOmvZfsPoolExport,
  planOmvZfsPoolImport,
  type OmvStatus,
  type OmvZfsImportCandidate,
  type OmvZfsMirrorReplacementCandidate,
  type OmvZfsMirrorReplacePlan,
  type OmvZfsPool,
  type OmvZfsPoolExportPlan,
  type OmvZfsPoolImportPlan,
} from "@/appliance/omv";

type PendingPlan =
  | { kind: "replace"; plan: OmvZfsMirrorReplacePlan }
  | { kind: "export"; plan: OmvZfsPoolExportPlan }
  | { kind: "import"; plan: OmvZfsPoolImportPlan };

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

function shortGuid(guid: string) {
  return guid.length > 12 ? `${guid.slice(0, 6)}…${guid.slice(-6)}` : guid;
}

export function ZfsLifecyclePanel({ status }: { status: OmvStatus | null }) {
  const replaceAvailable = Boolean(
    status?.capabilities?.includes("storage.pool.zfs-mirror.replace.blank.v1"),
  );
  const exportAvailable = Boolean(
    status?.capabilities?.includes("storage.pool.zfs.export.safe.v1"),
  );
  const importAvailable = Boolean(
    status?.capabilities?.includes("storage.pool.zfs.import.echo-root.v1"),
  );
  const [pools, setPools] = useState<OmvZfsPool[]>([]);
  const [candidates, setCandidates] = useState<OmvZfsImportCandidate[]>([]);
  const [replacements, setReplacements] = useState<
    OmvZfsMirrorReplacementCandidate[]
  >([]);
  const [pending, setPending] = useState<PendingPlan | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const refresh = async () => {
    if (!replaceAvailable && !exportAvailable && !importAvailable) return;
    setLoading(true);
    setError(null);
    try {
      const [nextReplacements, nextPools, nextCandidates] = await Promise.all([
        replaceAvailable
          ? fetchOmvZfsMirrorReplacementCandidates()
          : Promise.resolve([]),
        exportAvailable ? fetchOmvZfsPools() : Promise.resolve([]),
        importAvailable ? fetchOmvZfsImportCandidates() : Promise.resolve([]),
      ]);
      setReplacements(nextReplacements);
      setPools(nextPools);
      setCandidates(nextCandidates);
      setPending(null);
      setPassword("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法读取 ZFS 存储池状态",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // The capability booleans are the lifecycle boundary; status identity is irrelevant.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replaceAvailable, exportAvailable, importAvailable]);

  const previewReplace = async (
    replacement: OmvZfsMirrorReplacementCandidate,
    devicefile: string,
  ) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    setPassword("");
    try {
      const plan = await planOmvZfsMirrorReplace({
        schema: "echo.omv.zfs-mirror-replace-desired.v1",
        name: replacement.pool.name,
        poolGuid: replacement.pool.poolGuid,
        oldVdevGuid: replacement.replaceableMember.vdevGuid,
        replacementDevice: devicefile,
        dataPreserved: true,
      });
      setPending({ kind: "replace", plan });
    } catch (reason) {
      setPending(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成镜像换盘预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const previewExport = async (pool: OmvZfsPool) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    setPassword("");
    try {
      const plan = await planOmvZfsPoolExport({
        schema: "echo.omv.zfs-pool-export-desired.v1",
        name: pool.name,
        poolGuid: pool.poolGuid,
        dataPreserved: true,
      });
      setPending({ kind: "export", plan });
    } catch (reason) {
      setPending(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成安全导出预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const previewImport = async (candidate: OmvZfsImportCandidate) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    setPassword("");
    try {
      const plan = await planOmvZfsPoolImport({
        schema: "echo.omv.zfs-pool-import-desired.v1",
        name: candidate.name,
        poolGuid: candidate.poolGuid,
        mountPolicy: "echoDataRootOnly",
      });
      setPending({ kind: "import", plan });
    } catch (reason) {
      setPending(null);
      setError(
        reason instanceof Error ? reason.message : "无法生成安全导入预览",
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!pending || !password) return;
    setBusy(true);
    setError(null);
    try {
      const action = {
        replace: "omv.zfs-mirror.replace",
        export: "omv.zfs-pool.export",
        import: "omv.zfs-pool.import",
      } as const;
      const approval = await requestHighRiskApproval(
        action[pending.kind],
        pending.plan.planId,
        password,
      );
      if (pending.kind === "replace") {
        await applyOmvZfsMirrorReplace(
          pending.plan.desired,
          pending.plan.planId,
          approval.approvalToken,
        );
        setSuccess(
          `存储池 ${pending.plan.desired.name} 已接受新盘，正在或已经完成 resilver`,
        );
      } else if (pending.kind === "export") {
        await applyOmvZfsPoolExport(
          pending.plan.desired,
          pending.plan.planId,
          approval.approvalToken,
        );
        setSuccess(`存储池 ${pending.plan.desired.name} 已安全导出，数据保留`);
      } else {
        await applyOmvZfsPoolImport(
          pending.plan.desired,
          pending.plan.planId,
          approval.approvalToken,
        );
        setSuccess(
          `存储池 ${pending.plan.desired.name} 已按 GUID 导入并完成挂载验证`,
        );
      }
      await refresh();
    } catch (reason) {
      setPassword("");
      setError(reason instanceof Error ? reason.message : "ZFS 存储池操作失败");
    } finally {
      setBusy(false);
    }
  };

  if (!replaceAvailable && !exportAvailable && !importAvailable) return null;

  const pendingTitle =
    pending?.kind === "replace"
      ? "故障镜像盘换盘复核"
      : pending?.kind === "export"
        ? "数据保留导出复核"
        : "GUID 导入复核";
  const pendingOperation =
    pending?.kind === "replace"
      ? "换盘"
      : pending?.kind === "export"
        ? "导出"
        : "导入";
  const pendingButton =
    pending?.kind === "replace"
      ? "启动校验重建"
      : pending?.kind === "export"
        ? "安全导出"
        : "按 GUID 导入";

  return (
    <section className="mt-5 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <DatabaseIcon className="size-5 text-emerald-600" />
          <h2 className="text-sm font-semibold text-slate-900">
            故障换盘 / 数据保留导出与导入
          </h2>
        </div>
        {loading && (
          <Loader2Icon
            aria-label="刷新池状态"
            className="size-4 animate-spin"
          />
        )}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-500">
        换盘只接受单一双盘 mirror
        的唯一故障成员与足够大的空白整盘；生命周期操作只接受 Echo
        数据根布局。所有目标按数字 GUID 与磁盘稳定身份绑定，不使用强制参数。
      </p>

      {error && (
        <div
          role="alert"
          className="mt-4 rounded-2xl bg-red-50 p-3 text-xs text-red-700"
        >
          {error}
        </div>
      )}
      {success && (
        <div
          role="status"
          className="mt-4 rounded-2xl bg-emerald-50 p-3 text-xs text-emerald-800"
        >
          {success}
        </div>
      )}

      {replaceAvailable && (
        <div className="mt-4 rounded-2xl bg-amber-50/80 p-4 ring-1 ring-amber-200">
          <h3 className="flex items-center gap-2 text-xs font-semibold text-amber-900">
            <WrenchIcon className="size-4" /> 故障镜像修复
          </h3>
          <p className="mt-1 text-[10px] leading-5 text-amber-800/80">
            仅列出恰有一个故障成员、另一个成员 ONLINE，且当前没有
            scrub、resilver 或既有 replacing 树的双盘镜像。
          </p>
          <div className="mt-3 space-y-2">
            {replacements.map((replacement) => (
              <div
                key={`${replacement.pool.poolGuid}:${replacement.replaceableMember.vdevGuid}`}
                className="rounded-xl bg-white p-3 ring-1 ring-amber-200"
              >
                <p className="text-xs font-semibold text-slate-900">
                  {replacement.pool.name} · {replacement.pool.health}
                </p>
                <p className="mt-1 text-[10px] text-slate-500">
                  故障槽位 {replacement.replaceableMember.slot}（
                  {replacement.replaceableMember.state}）· 最小盘容量{" "}
                  {formatBytes(replacement.minimumReplacementBytes)}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {replacement.replacementDevices.map((device) => (
                    <button
                      key={device.devicefile}
                      type="button"
                      onClick={() =>
                        void previewReplace(replacement, device.devicefile)
                      }
                      disabled={busy}
                      className="rounded-lg bg-amber-600 px-3 py-1.5 text-[10px] font-semibold text-white disabled:opacity-40"
                    >
                      预览用 {device.devicefile} 修复 {replacement.pool.name}
                    </button>
                  ))}
                  {replacement.replacementDevices.length === 0 && (
                    <span className="text-[10px] text-amber-800">
                      没有通过空白、稳定身份和容量检查的新盘
                    </span>
                  )}
                </div>
              </div>
            ))}
            {!loading && replacements.length === 0 && (
              <p className="text-[11px] leading-5 text-amber-800/70">
                当前没有符合窄修复策略的故障双盘镜像。
              </p>
            )}
          </div>
        </div>
      )}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {exportAvailable && (
          <div className="rounded-2xl bg-slate-50/80 p-4 ring-1 ring-slate-200">
            <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-800">
              <LogOutIcon className="size-4" /> 可安全导出
            </h3>
            <div className="mt-3 space-y-2">
              {pools.map((pool) => (
                <div
                  key={pool.poolGuid}
                  className="rounded-xl bg-white p-3 ring-1 ring-slate-200"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold text-slate-900">
                        {pool.name}
                      </p>
                      <p className="mt-1 text-[10px] text-slate-500">
                        {formatBytes(pool.sizeBytes)} · {pool.datasetCount}{" "}
                        个数据集 · GUID {shortGuid(pool.poolGuid)}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void previewExport(pool)}
                      disabled={busy}
                      className="rounded-lg bg-slate-800 px-3 py-1.5 text-[10px] font-semibold text-white disabled:opacity-40"
                    >
                      预览导出 {pool.name}
                    </button>
                  </div>
                </div>
              ))}
              {!loading && pools.length === 0 && (
                <p className="text-[11px] leading-5 text-slate-500">
                  当前没有同时满足安全策略的已导入池。
                </p>
              )}
            </div>
          </div>
        )}

        {importAvailable && (
          <div className="rounded-2xl bg-blue-50/70 p-4 ring-1 ring-blue-100">
            <h3 className="flex items-center gap-2 text-xs font-semibold text-blue-900">
              <ArchiveRestoreIcon className="size-4" /> 可安全导入
            </h3>
            <div className="mt-3 space-y-2">
              {candidates.map((candidate) => (
                <div
                  key={candidate.poolGuid}
                  className="rounded-xl bg-white p-3 ring-1 ring-blue-100"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold text-slate-900">
                        {candidate.name}
                      </p>
                      <p className="mt-1 text-[10px] text-slate-500">
                        {candidate.layout} · GUID{" "}
                        {shortGuid(candidate.poolGuid)}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void previewImport(candidate)}
                      disabled={busy}
                      className="rounded-lg bg-blue-600 px-3 py-1.5 text-[10px] font-semibold text-white disabled:opacity-40"
                    >
                      预览导入 {candidate.name}
                    </button>
                  </div>
                </div>
              ))}
              {!loading && candidates.length === 0 && (
                <p className="text-[11px] leading-5 text-blue-700/70">
                  未发现无需强制参数即可导入的 ONLINE 池。
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {pending && (
        <div className="mt-4 rounded-2xl bg-emerald-950 p-4 text-white">
          <h3 className="flex items-center gap-2 text-xs font-semibold">
            <ShieldCheckIcon className="size-4 text-emerald-300" />
            {pendingTitle}
          </h3>
          <p className="mt-2 text-[11px] leading-5 text-emerald-100/80">
            {pending.kind === "replace"
              ? `将以空白整盘 ${pending.plan.desired.replacementDevice} 替换故障 vdev GUID ${pending.plan.desired.oldVdevGuid}，启动带校验的 resilver；接受后不会自动拆除新盘。`
              : pending.kind === "export"
                ? `将同步并非强制导出 ${pending.plan.desired.name}；导出完成后数据仍保留在磁盘上。`
                : `将先以只读且不挂载方式检查 ${pending.plan.desired.name}，恢复导出状态后再按 GUID 正常导入，仅挂载 /data/${pending.plan.desired.name} 树。`}
          </p>
          <p className="mt-1 break-all font-mono text-[10px] text-emerald-100/60">
            GUID {pending.plan.desired.poolGuid}
          </p>
          {pending.kind === "replace" && (
            <p className="mt-1 break-all font-mono text-[10px] text-emerald-100/70">
              新盘 {pending.plan.replacement.devicefile} ·{" "}
              {formatBytes(pending.plan.replacement.sizeBytes)} ·{" "}
              {pending.plan.replacement.model || "未知型号"} ·{" "}
              {pending.plan.replacement.wwn ||
                pending.plan.replacement.serial ||
                "无稳定标识"}
            </p>
          )}
          <label className="mt-3 block text-xs font-medium text-emerald-50">
            设备管理员密码（{pendingOperation}）
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-2 h-10 w-full rounded-xl border border-emerald-700/60 bg-white/10 px-3 text-sm text-white outline-none focus:border-emerald-300"
            />
          </label>
          <button
            type="button"
            onClick={() => void apply()}
            disabled={!password || busy}
            className="mt-3 inline-flex h-9 items-center gap-2 rounded-xl bg-emerald-500 px-4 text-[11px] font-semibold text-emerald-950 disabled:opacity-40"
          >
            {busy && <Loader2Icon className="size-4 animate-spin" />}
            确认{pendingButton}
          </button>
        </div>
      )}
    </section>
  );
}
