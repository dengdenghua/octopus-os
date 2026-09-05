import { useEffect, useState } from "react";
import {
  ArchiveRestoreIcon,
  DatabaseIcon,
  Loader2Icon,
  LogOutIcon,
  ShieldCheckIcon,
} from "lucide-react";

import { requestHighRiskApproval } from "@/appliance/approval";
import {
  applyOmvZfsPoolExport,
  applyOmvZfsPoolImport,
  fetchOmvZfsImportCandidates,
  fetchOmvZfsPools,
  planOmvZfsPoolExport,
  planOmvZfsPoolImport,
  type OmvStatus,
  type OmvZfsImportCandidate,
  type OmvZfsPool,
  type OmvZfsPoolExportPlan,
  type OmvZfsPoolImportPlan,
} from "@/appliance/omv";

type PendingPlan =
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
  const exportAvailable = Boolean(
    status?.capabilities?.includes("storage.pool.zfs.export.safe.v1"),
  );
  const importAvailable = Boolean(
    status?.capabilities?.includes("storage.pool.zfs.import.echo-root.v1"),
  );
  const [pools, setPools] = useState<OmvZfsPool[]>([]);
  const [candidates, setCandidates] = useState<OmvZfsImportCandidate[]>([]);
  const [pending, setPending] = useState<PendingPlan | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const refresh = async () => {
    if (!exportAvailable && !importAvailable) return;
    setLoading(true);
    setError(null);
    try {
      const [nextPools, nextCandidates] = await Promise.all([
        exportAvailable ? fetchOmvZfsPools() : Promise.resolve([]),
        importAvailable ? fetchOmvZfsImportCandidates() : Promise.resolve([]),
      ]);
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
  }, [exportAvailable, importAvailable]);

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
      const action =
        pending.kind === "export"
          ? "omv.zfs-pool.export"
          : "omv.zfs-pool.import";
      const approval = await requestHighRiskApproval(
        action,
        pending.plan.planId,
        password,
      );
      if (pending.kind === "export") {
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

  if (!exportAvailable && !importAvailable) return null;

  return (
    <section className="mt-5 rounded-[22px] bg-white/78 p-5 shadow-sm ring-1 ring-white/90">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <DatabaseIcon className="size-5 text-emerald-600" />
          <h2 className="text-sm font-semibold text-slate-900">
            数据保留导出 / 导入
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
        只接受 ONLINE、无维护任务、无受管共享且挂载在 /data/&lt;池名&gt;
        的未加密池。操作按数字 GUID 绑定，不使用强制、回退或恢复参数。
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
            {pending.kind === "export" ? "数据保留导出复核" : "GUID 导入复核"}
          </h3>
          <p className="mt-2 text-[11px] leading-5 text-emerald-100/80">
            {pending.kind === "export"
              ? `将同步并非强制导出 ${pending.plan.desired.name}；导出完成后数据仍保留在磁盘上。`
              : `将先以只读且不挂载方式检查 ${pending.plan.desired.name}，恢复导出状态后再按 GUID 正常导入，仅挂载 /data/${pending.plan.desired.name} 树。`}
          </p>
          <p className="mt-1 break-all font-mono text-[10px] text-emerald-100/60">
            GUID {pending.plan.desired.poolGuid}
          </p>
          <label className="mt-3 block text-xs font-medium text-emerald-50">
            设备管理员密码（{pending.kind === "export" ? "导出" : "导入"}）
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
            确认{pending.kind === "export" ? "安全导出" : "按 GUID 导入"}
          </button>
        </div>
      )}
    </section>
  );
}
