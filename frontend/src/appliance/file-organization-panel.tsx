import { useEffect, useRef, useState } from "react";
import { DownloadIcon, Loader2Icon, RefreshCwIcon, XIcon } from "lucide-react";

import { requestHighRiskApproval } from "./approval";
import { HighRiskApprovalDialog } from "./high-risk-approval-dialog";
import { downloadFile } from "./files";
import {
  applyOrganizationPlan,
  cancelOrganizationPlan,
  canApplyOrganizationPlan,
  createOrganizationPlan,
  createOrganizationUndoPlan,
  fetchOrganizationPlan,
  fetchOrganizationPlans,
  fetchOrganizationResult,
  isOrganizationPlanId,
  OrganizationApiError,
  type OrganizationEntry,
  type OrganizationPlan,
  type OrganizationPlanList,
  type OrganizationResult,
} from "./file-organization";

const storageKey = (path: string) =>
  `echo:files:organization:last:${encodeURIComponent(path)}`;
const buttonClass =
  "rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium disabled:opacity-50";
const primaryClass =
  "rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50";

const reasons: Record<string, string> = {
  extraction_timed_out: "文字解析超过时间限制，请检查文件或拆分后重试",
  extraction_resource_limited: "文字解析达到资源限制，保留原件待确认",
  extraction_worker_failed: "文字解析进程未正常完成，保留原件待确认",
  extraction_cancelled: "服务已停止本次解析，请重新预览",
  scan_time_limit: "本次预览已达到时间限制，请缩小目录范围后重新预览",
  scan_cancelled: "服务已停止本次预览，不能执行未完成的计划",
  unsupported_format: "暂不支持此格式",
  extraction_unavailable: "文字提取暂不可用",
  no_text: "未取得可用文字，请提供含文字的文件或先完成文字识别后重试",
  text_truncated: "内容未读取完整，暂不判断日期",
  encoding_uncertain: "文字编码无法确定",
  not_invoice: "未确认这是发票",
  no_invoice_date: "未找到明确的开票日期",
  invalid_invoice_date: "开票日期无效",
  ambiguous_invoice_date: "开票日期含义不明确",
  conflicting_invoice_dates: "发现多个不同的开票日期",
  destination_exists: "目标已有同名文件，未覆盖",
  source_changed: "原文件已变化，请重新预览",
  source_missing: "原文件已不存在，请检查结果中的实际位置",
  already_organized: "已在对应年月目录中，无需移动",
  scan_incomplete: "目录未扫描完整，不能执行此计划",
  permission_denied: "没有所需的文件访问权限",
  recovery_required: "请先检查保留的恢复副本，勿重复移动",
};
const reasonText = (reason: string | null, fallback: string) =>
  reason ? (reasons[reason] ?? fallback) : fallback;

function evidenceCandidates(raw: unknown) {
  return Array.isArray(raw)
    ? raw
        .slice(0, 8)
        .filter(
          (value): value is Record<string, unknown> =>
            !!value && typeof value === "object",
        )
    : [];
}

function Evidence({ entry }: { entry: OrganizationEntry }) {
  const groups = [
    {
      label: "查看日期依据",
      rows: evidenceCandidates(entry.evidence?.dateCandidates),
    },
    {
      label: "查看金额依据",
      rows: evidenceCandidates(entry.evidence?.amountCandidates),
    },
  ];
  return (
    <>
      {entry.date && <p>开票日期：{entry.date}</p>}
      {entry.amount && (
        <p>
          金额：{entry.amount} {entry.currency}
        </p>
      )}
      {groups
        .filter((group) => group.rows.length > 0)
        .map((group) => (
          <details key={group.label} className="mt-1">
            <summary className="cursor-pointer">{group.label}</summary>
            {group.rows.map((candidate, index) => (
              <p key={index} className="mt-1 break-words text-slate-500">
                {typeof candidate.label === "string" &&
                  candidate.label.slice(0, 40)}
                ：
                {typeof candidate.snippet === "string"
                  ? candidate.snippet.slice(0, 100)
                  : typeof candidate.value === "string"
                    ? candidate.value
                    : "待确认"}
              </p>
            ))}
          </details>
        ))}
    </>
  );
}

const searchText = (value: string) => value.normalize("NFKC").toLowerCase();
function matchesSearch(
  entry: Pick<OrganizationEntry, "source" | "target"> &
    Partial<Pick<OrganizationEntry, "date" | "amount" | "currency">>,
  terms: string[],
) {
  const fields = [
    entry.source.split(/[\\/]/).at(-1),
    entry.target?.split(/[\\/]/).at(-1),
    entry.date,
    entry.amount,
    entry.currency,
  ]
    .filter((value): value is string => !!value)
    .map(searchText);
  return terms.every((term) => {
    const numeric = /^\d[\d,.]*$/.test(term);
    return fields.some((value) =>
      (numeric ? value.replaceAll(",", "") : value).includes(
        numeric ? term.replaceAll(",", "") : term,
      ),
    );
  });
}

function resultTitle(result: OrganizationResult) {
  const counts = result.counts;
  if (result.state === "running") return "正在处理文件";
  if (result.state === "cancelled") return "已停止后续处理";
  if (
    result.finalizationPending &&
    !counts.uncertain &&
    !result.results.some((row) => row.status === "uncertain")
  )
    return "文件已处理，记录尚未完成";
  if (
    result.state === "uncertain" ||
    counts.uncertain ||
    result.results.some((row) => row.status === "uncertain")
  )
    return "部分结果待核实";
  if (
    result.state === "completed" &&
    result.executionComplete &&
    result.auditRecorded !== false &&
    result.taskRecorded !== false &&
    result.receiptRecorded !== false &&
    !counts.conflicts &&
    !counts.failed &&
    !counts.pending &&
    !result.results.some((row) =>
      ["conflict", "failed", "pending"].includes(row.status),
    )
  ) {
    return result.reviewCount > 0
      ? "可确认的文件已处理，其余文件待确认"
      : result.direction === "undo"
        ? "本次撤销已完成"
        : "本次整理已完成";
  }
  return counts.moved || result.results.some((row) => row.committed === true)
    ? "部分文件已处理"
    : "本次操作尚未完成";
}

import { desktopActionHref, revealFileRequest } from "./desktop-actions";

export function FileOrganizationPanel({
  path,
  onClose,
  onChanged,
  onOpenTask,
  onReveal,
}: {
  path: string;
  onClose: () => void;
  onChanged?: () => void;
  onOpenTask?: (taskId: string) => void;
  onReveal?: (path: string) => void;
}) {
  const [plan, setPlan] = useState<OrganizationPlan | null>(null);
  const [result, setResult] = useState<OrganizationResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvalPlan, setApprovalPlan] = useState<OrganizationPlan | null>(
    null,
  );
  const [reconciling, setReconciling] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [filter, setFilter] = useState<"all" | "review">("all");
  const [query, setQuery] = useState("");
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<OrganizationPlanList | null>(null);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [lookupId, setLookupId] = useState("");
  const discoverySequence = useRef(0);
  const generation = useRef(0);
  const busyRef = useRef<string | null>(null);
  const cancelRef = useRef(false);
  const currentPlan = useRef<OrganizationPlan | null>(null);
  const resultSequence = useRef(0);
  const latestResult = useRef(result);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  const discover = async () => {
    const ticket = generation.current;
    const sequence = ++discoverySequence.current;
    setDiscoveryBusy(true);
    setDiscoveryError(null);
    // Do not retain old summaries after a denied or failed refresh.
    setDiscovery(null);
    try {
      const next = await fetchOrganizationPlans(path);
      if (
        generation.current === ticket &&
        discoverySequence.current === sequence
      )
        setDiscovery(next);
    } catch (reason) {
      if (
        generation.current === ticket &&
        discoverySequence.current === sequence
      )
        setDiscoveryError(
          reason instanceof Error ? reason.message : "无法读取最近整理计划",
        );
    } finally {
      if (
        generation.current === ticket &&
        discoverySequence.current === sequence
      )
        setDiscoveryBusy(false);
    }
  };
  const discoverRef = useRef(discover);
  discoverRef.current = discover;

  const remember = (next: OrganizationPlan) => {
    if (next.path !== path)
      throw new OrganizationApiError("保存的计划属于其他目录，请重新预览");
    currentPlan.current = next;
    setPlan(next);
    latestResult.current = next.result ?? null;
    setResult(next.result ?? null);
    setFilter("all");
    setQuery("");
    setPreviewExpanded(false);
    setDownloadBusy(null);
    setLookupId("");
    try {
      localStorage.setItem(storageKey(path), next.planId);
    } catch {
      setError("浏览器无法保存恢复入口，请保持页面打开直到操作结束。");
    }
  };

  useEffect(() => {
    const ticket = ++generation.current;
    busyRef.current = null;
    cancelRef.current = false;
    currentPlan.current = null;
    latestResult.current = null;
    setPlan(null);
    setResult(null);
    setError(null);
    setApprovalPlan(null);
    setReconciling(false);
    setCancelling(false);
    setDownloadBusy(null);
    setFilter("all");
    setQuery("");
    setPreviewExpanded(false);
    setLookupId("");
    void discoverRef.current();
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(storageKey(path));
    } catch {
      /* Service authorization remains authoritative. */
    }
    if (isOrganizationPlanId(saved)) {
      busyRef.current = "loading";
      setBusy("loading");
      void fetchOrganizationPlan(saved)
        .then(async (next) => {
          if (generation.current !== ticket) return;
          if (next.path !== path)
            throw new OrganizationApiError(
              "保存的计划不属于此目录，请重新预览",
            );
          currentPlan.current = next;
          latestResult.current = next.result ?? null;
          setPlan(next);
          setResult(next.result ?? null);
          if (!next.result) {
            try {
              const recovered = await fetchOrganizationResult(next.planId);
              if (generation.current === ticket) {
                if (recovered.direction !== next.direction)
                  throw new OrganizationApiError(
                    "执行记录与计划不一致，请重新读取",
                  );
                latestResult.current = recovered;
                setResult(recovered);
              }
            } catch (reason) {
              if (
                !(
                  reason instanceof OrganizationApiError &&
                  reason.status === 404
                )
              )
                throw reason;
            }
          }
        })
        .catch((reason: unknown) => {
          if (generation.current !== ticket) return;
          currentPlan.current = null;
          latestResult.current = null;
          setPlan(null);
          setResult(null);
          setError(
            reason instanceof Error ? reason.message : "无法恢复上次整理记录",
          );
        })
        .finally(() => {
          if (generation.current === ticket) {
            busyRef.current = null;
            setBusy(null);
          }
        });
    } else setBusy(null);
    return () => {
      generation.current += 1;
      resultSequence.current += 1;
    };
  }, [path]);

  const acceptResult = (next: OrganizationResult, ticket: number) => {
    if (
      generation.current !== ticket ||
      currentPlan.current?.planId !== next.planId
    )
      return;
    if (currentPlan.current.direction !== next.direction)
      throw new OrganizationApiError("执行记录与当前计划不一致，请刷新结果");
    // A delayed apply response must not hide a terminal receipt already read by polling.
    if (next.state === "running" && latestResult.current?.executionComplete)
      return;
    resultSequence.current += 1;
    latestResult.current = next;
    setResult(next);
    setReconciling(next.state === "running");
    setError(null);
    onChangedRef.current?.();
  };

  const readResult = async (missingIsExpected = false) => {
    const selected = currentPlan.current;
    if (!selected) return;
    const ticket = generation.current;
    const sequence = ++resultSequence.current;
    try {
      const next = await fetchOrganizationResult(selected.planId);
      if (
        ticket !== generation.current ||
        sequence !== resultSequence.current ||
        selected.planId !== currentPlan.current?.planId
      )
        return;
      if (selected.direction !== next.direction)
        throw new OrganizationApiError("执行记录与当前计划不一致，请刷新结果");
      if (next.state === "running" && latestResult.current?.executionComplete)
        return;
      latestResult.current = next;
      setResult(next);
      setReconciling(next.state === "running");
      if (next.state !== "running") setError(null);
      if (next.state !== "running") onChangedRef.current?.();
    } catch (reason) {
      if (ticket !== generation.current || sequence !== resultSequence.current)
        return;
      if (
        missingIsExpected &&
        reason instanceof OrganizationApiError &&
        reason.status === 404
      )
        return;
      if (
        reason instanceof OrganizationApiError &&
        [401, 403].includes(reason.status)
      ) {
        currentPlan.current = null;
        latestResult.current = null;
        setPlan(null);
        setResult(null);
        setApprovalPlan(null);
        setReconciling(false);
      }
      setError(reason instanceof Error ? reason.message : "无法读取整理结果");
    }
  };
  const readResultRef = useRef(readResult);
  readResultRef.current = readResult;
  const active =
    busy === "applying" || result?.state === "running" || reconciling;
  const planId = plan?.planId;
  useEffect(() => {
    if (!active || !planId) return;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void readResultRef.current().finally(() => {
        pending = false;
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [active, planId]);

  const makePlan = async (undo = false) => {
    if (busyRef.current || active) return;
    const selected = currentPlan.current;
    if (undo && !selected) return;
    const ticket = generation.current;
    busyRef.current = "planning";
    setBusy("planning");
    setError(null);
    try {
      const next = undo
        ? await createOrganizationUndoPlan(selected!.planId)
        : await createOrganizationPlan(path);
      if (generation.current !== ticket) return;
      remember(next);
      setReconciling(false);
    } catch (reason) {
      if (generation.current === ticket)
        setError(reason instanceof Error ? reason.message : "无法生成整理预览");
    } finally {
      if (generation.current === ticket) {
        busyRef.current = null;
        setBusy(null);
      }
    }
  };

  const choosePlan = async (id: string) => {
    if (busyRef.current || active || approvalPlan) return;
    if (!isOrganizationPlanId(id)) {
      setError("请输入完整的 64 位计划编号");
      return;
    }
    const ticket = generation.current;
    busyRef.current = "loading";
    setBusy("loading");
    setError(null);
    setReconciling(false);
    currentPlan.current = null;
    latestResult.current = null;
    resultSequence.current += 1;
    setPlan(null);
    setResult(null);
    setQuery("");
    setDownloadBusy(null);
    try {
      let next = await fetchOrganizationPlan(id);
      if (generation.current !== ticket) return;
      if (next.path !== path)
        throw new OrganizationApiError(
          "此计划不属于当前目录，请打开对应目录后查看",
        );
      if (!next.result) {
        try {
          const recovered = await fetchOrganizationResult(id);
          if (recovered.direction !== next.direction)
            throw new OrganizationApiError("执行记录与计划不一致，请重新读取");
          next = { ...next, result: recovered };
        } catch (reason) {
          if (
            !(reason instanceof OrganizationApiError && reason.status === 404)
          )
            throw reason;
        }
      }
      if (generation.current !== ticket) return;
      remember(next);
      setLookupId("");
    } catch (reason) {
      if (generation.current === ticket)
        setError(reason instanceof Error ? reason.message : "无法读取所选计划");
    } finally {
      if (generation.current === ticket) {
        busyRef.current = null;
        setBusy(null);
      }
    }
  };

  const confirm = async (password: string) => {
    const selected = approvalPlan;
    if (
      busyRef.current ||
      !selected ||
      !canApplyOrganizationPlan(selected) ||
      selected.planId !== currentPlan.current?.planId
    )
      return;
    const ticket = generation.current;
    let applying = false;
    busyRef.current = "approval";
    setBusy("approval");
    setError(null);
    try {
      const approval = await requestHighRiskApproval(
        selected.approval.action,
        selected.planId,
        password,
      );
      if (
        generation.current !== ticket ||
        currentPlan.current?.planId !== selected.planId
      )
        return;
      setApprovalPlan(null);
      applying = true;
      busyRef.current = "applying";
      setBusy("applying");
      latestResult.current = null;
      setReconciling(true);
      resultSequence.current += 1;
      const next = await applyOrganizationPlan(
        selected.planId,
        approval.approvalToken,
      );
      acceptResult(next, ticket);
    } catch (reason) {
      if (generation.current !== ticket) return;
      if (!applying) throw reason;
      setError(
        reason instanceof Error ? reason.message : "执行结果待核实，请刷新结果",
      );
      // The server may already have committed a subset; retain the plan ID.
      const rejected =
        reason instanceof OrganizationApiError &&
        reason.status >= 400 &&
        reason.status < 500;
      setReconciling(!rejected);
      void readResultRef.current(rejected);
    } finally {
      if (generation.current === ticket) {
        busyRef.current = null;
        setBusy(null);
      }
    }
  };

  const cancel = async () => {
    const selected = currentPlan.current;
    if (!selected || cancelRef.current) return;
    const ticket = generation.current;
    cancelRef.current = true;
    setCancelling(true);
    try {
      acceptResult(await cancelOrganizationPlan(selected.planId), ticket);
    } catch (reason) {
      if (generation.current === ticket)
        setError(
          reason instanceof Error
            ? reason.message
            : "未能停止后续操作，请刷新结果",
        );
    } finally {
      if (generation.current === ticket) {
        cancelRef.current = false;
        setCancelling(false);
      }
    }
  };

  const download = async (actualPath: string, id: string, planId: string) => {
    if (currentPlan.current?.planId !== planId) return;
    const ticket = generation.current;
    setDownloadBusy(id);
    try {
      await downloadFile(
        actualPath,
        actualPath.split("/").at(-1) || "document",
        undefined,
        { organizationOriginal: { planId, entryId: id } },
      );
    } catch (reason) {
      if (
        generation.current === ticket &&
        currentPlan.current?.planId === planId
      )
        setError(reason instanceof Error ? reason.message : "原件下载失败");
    } finally {
      if (
        generation.current === ticket &&
        currentPlan.current?.planId === planId
      )
        setDownloadBusy(null);
    }
  };
  const exportResult = () => {
    if (!result) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `document-organization-${result.planId.slice(0, 12)}.json`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const canRetry =
    !!result &&
    !active &&
    result.state !== "completed" &&
    result.counts.uncertain === 0 &&
    !result.results.some((row) => row.status === "uncertain") &&
    (result.finalizationPending === true ||
      result.counts.pending + result.counts.conflicts + result.counts.failed >
        0);
  const showApply = !result || canRetry;
  const hasCommitted =
    !!result &&
    (result.counts.moved > 0 ||
      result.results.some((row) => row.committed === true));
  const searchTerms = searchText(query).trim().split(/\s+/).filter(Boolean);
  const searching = searchTerms.length > 0;
  const entries =
    plan?.entries.filter(
      (row) =>
        (filter === "all" || row.status === "needs_review") &&
        matchesSearch(row, searchTerms),
    ) ?? [];
  const planEntries = new Map(
    plan?.planId === result?.planId && plan?.direction === result?.direction
      ? plan?.entries.map((entry) => [entry.entryId, entry])
      : [],
  );
  const outcomes =
    result?.results
      .map((row) => ({ row, entry: planEntries.get(row.entryId) }))
      .filter(({ row, entry }) => matchesSearch(entry ?? row, searchTerms)) ??
    [];

  return (
    <div
      className="fixed inset-0 z-[130] grid place-items-center bg-slate-950/30 p-5 backdrop-blur-sm"
      data-desktop-interactive
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label="按年月整理文档"
        className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-2xl border border-white bg-white text-slate-900 shadow-2xl"
      >
        <header className="flex items-start justify-between border-b p-5">
          <div>
            <h2 className="text-lg font-semibold">
              {plan?.direction === "undo"
                ? "预览撤销文件整理"
                : "按年月整理文档"}
            </h2>
            <p className="mt-1 break-all text-sm text-slate-500">
              目录：{path || "NAS 根目录"}
            </p>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              根据明确的开票日期整理文件。不确定的文件保留原位；同名文件不会被覆盖。
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭整理面板"
            className="rounded-lg p-2 hover:bg-slate-100"
            onClick={onClose}
          >
            <XIcon className="size-4" />
          </button>
        </header>
        <div className="min-h-0 space-y-4 overflow-y-auto p-5">
          {busy && (
            <p
              role="status"
              className="flex items-center gap-2 text-sm text-blue-700"
            >
              <Loader2Icon className="size-4 animate-spin" />
              {busy === "planning"
                ? "正在只读扫描并生成预览…"
                : busy === "applying"
                  ? "正在处理文件，可以停止后续项目或稍后回来查看结果。"
                  : busy === "approval"
                    ? "正在复核操作权限…"
                    : "正在读取整理记录…"}
            </p>
          )}
          {error && (
            <p
              role="alert"
              className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900"
            >
              {error}
            </p>
          )}
          <section
            aria-label="最近整理计划"
            className="space-y-2 rounded-xl border border-slate-200 p-3 text-xs"
          >
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-semibold">此目录的最近计划</h3>
              <button
                type="button"
                className={buttonClass}
                disabled={discoveryBusy}
                onClick={() => void discover()}
              >
                刷新计划列表
              </button>
            </div>
            <p className="text-slate-500">
              可查看 Agent
              或此前生成的计划。选择计划只读取清单，执行仍需单独确认。
            </p>
            {discoveryBusy && <p role="status">正在读取最近计划…</p>}
            {discoveryError && (
              <p role="alert" className="text-amber-800">
                {discoveryError}
              </p>
            )}
            {discovery && !discovery.complete && (
              <p className="text-amber-800">
                仅显示本次可检索范围内的最近记录；未列出的计划可通过完整编号查找。
              </p>
            )}
            {discovery?.plans.length === 0 && (
              <p className="text-slate-500">本次未找到可显示的计划。</p>
            )}
            {discovery?.plans.map((item) => (
              <div
                key={item.planId}
                className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 py-2"
              >
                <div className="min-w-0">
                  <p>
                    {item.direction === "undo" ? "撤销计划" : "整理计划"} ·{" "}
                    {item.state === null
                      ? "尚未执行"
                      : {
                          running: "正在处理",
                          completed: "已有执行回执",
                          partial: "部分文件已处理",
                          cancelled: "已停止后续处理",
                          failed: "执行失败",
                          uncertain: "结果待核实",
                        }[item.state]}
                  </p>
                  <p className="break-all text-slate-500">{item.planId}</p>
                  <p className="text-slate-500">
                    可处理 {item.summary.ready} · 待确认{" "}
                    {item.summary.needsReview} · 冲突 {item.summary.conflicts}
                  </p>
                </div>
                <button
                  type="button"
                  className={buttonClass}
                  disabled={!!busy || active || !!approvalPlan}
                  onClick={() => void choosePlan(item.planId)}
                  aria-label={`查看${item.direction === "undo" ? "撤销" : "整理"}计划 ${item.planId}`}
                >
                  查看计划
                </button>
              </div>
            ))}
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void choosePlan(lookupId.trim());
              }}
            >
              <label className="min-w-0 flex-1">
                完整计划编号
                <input
                  className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-2"
                  value={lookupId}
                  onChange={(event) => setLookupId(event.target.value)}
                  maxLength={64}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
              <button
                type="submit"
                className={buttonClass}
                disabled={
                  !!busy || active || !!approvalPlan || !lookupId.trim()
                }
              >
                按编号查看
              </button>
            </form>
          </section>
          {reconciling && !busy && result?.state !== "running" && (
            <p role="status" className="text-sm text-amber-800">
              尚未取得完整执行回执，正在核实结果。请勿重复提交。
            </p>
          )}
          {!plan && !busy && (
            <p className="text-sm text-slate-500">
              先查看变更清单。生成预览不会移动文件。
            </p>
          )}
          {plan && (
            <>
              {plan.direction === "undo" && (
                <p className="text-sm text-slate-600">
                  撤销仅恢复文件原位置，整理时创建的空年月目录会保留。
                </p>
              )}
              <section
                aria-label="本计划文件查找"
                className="space-y-2 rounded-xl border border-slate-200 p-3 text-xs"
              >
                <div className="flex items-end gap-2">
                  <label className="min-w-0 flex-1">
                    在本计划中查找
                    <input
                      type="search"
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      placeholder="文件名、开票日期或金额"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </label>
                  <button
                    type="button"
                    className={buttonClass}
                    disabled={!query}
                    onClick={() => setQuery("")}
                  >
                    清空查找
                  </button>
                </div>
                <p className="text-slate-500">
                  只查本计划已加载的文件名、开票日期和金额，不搜索其他计划或全盘文件。
                  日期与金额依据来自生成此计划时的识别记录。
                </p>
                <p className="text-slate-600">
                  审批针对整份计划中的 {plan.summary.ready}{" "}
                  个可执行文件，查找和筛选不改变执行范围。
                </p>
              </section>
              <div className="flex flex-wrap gap-2 text-xs">
                <span>共扫描 {plan.summary.scanned} 个文件</span>
                <span>· 可处理 {plan.summary.ready}</span>
                <button
                  type="button"
                  onClick={() => setFilter(filter === "all" ? "review" : "all")}
                  className="font-medium text-amber-700 underline"
                >
                  待确认 {plan.summary.needsReview} 个
                </button>
                <span>· 已归档 {plan.summary.alreadyOrganized}</span>
                <span>· 冲突 {plan.summary.conflicts}</span>
                <span>· 不支持 {plan.summary.unsupported}</span>
                {filter === "review" && (
                  <button
                    type="button"
                    onClick={() => {
                      setFilter("all");
                      setPreviewExpanded(true);
                    }}
                    className="text-blue-700 underline"
                  >
                    显示全部文件
                  </button>
                )}
                {result && filter === "all" && (
                  <button
                    type="button"
                    className="text-blue-700 underline"
                    onClick={() => setPreviewExpanded(!previewExpanded)}
                  >
                    {previewExpanded ? "收起预览清单" : "查看全部预览清单"}
                  </button>
                )}
              </div>
              {(!plan.scanComplete || plan.blockers.length > 0) && (
                <p
                  role="alert"
                  className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900"
                >
                  目录检查未通过，不能执行。请检查目录权限、文件状态后重新预览。
                  {plan.blockers
                    .map((reason) => reasons[reason])
                    .filter(Boolean)
                    .join("；")}
                </p>
              )}
              {(!result || filter === "review" || previewExpanded) && (
                <section aria-label="文件整理预览" className="space-y-2">
                  <p className="text-xs text-slate-500" aria-live="polite">
                    预览清单：显示 {entries.length} / {plan.entries.length} 项
                    {filter === "review" && "（仅待确认）"}
                  </p>
                  <div className="divide-y rounded-xl border border-slate-200">
                    {entries.map((entry) => (
                      <article
                        key={entry.entryId}
                        className="space-y-1 p-3 text-xs"
                      >
                        <p className="break-all font-medium">{entry.source}</p>
                        <p className="break-all">
                          {entry.status === "ready" && entry.target
                            ? `拟移动至：${entry.target}`
                            : reasonText(
                                entry.reason,
                                entry.status === "already_organized"
                                  ? "已归档，保持原位"
                                  : entry.status === "unsupported"
                                    ? "暂不支持此文件，保持原位"
                                    : entry.status === "conflict"
                                      ? "存在冲突，保持原位"
                                      : "日期待确认，保持原位",
                              )}
                        </p>
                        <Evidence entry={entry} />
                      </article>
                    ))}
                    {entries.length === 0 && (
                      <p className="p-3 text-xs text-slate-500">
                        {searching
                          ? filter === "review"
                            ? "本计划的待确认文件中没有匹配项"
                            : "本计划的预览清单中没有匹配文件"
                          : filter === "review"
                            ? "没有待确认文件"
                            : "此目录没有可整理文件"}
                      </p>
                    )}
                  </div>
                </section>
              )}
            </>
          )}
          {result && (
            <section aria-label="文件整理结果" className="space-y-3">
              <h3 className="font-semibold">{resultTitle(result)}</h3>
              {result.finalizationPending &&
                !result.counts.uncertain &&
                !result.results.some((row) => row.status === "uncertain") && (
                  <p className="text-sm text-amber-800">
                    文件结果已核实，任务或审计记录尚未完成；重新审批补齐记录，已提交文件不会重复移动。
                  </p>
                )}
              <p className="text-sm">
                {result.direction === "undo" ? "已恢复" : "已移动"}{" "}
                {result.counts.moved} · 冲突 {result.counts.conflicts} · 失败{" "}
                {result.counts.failed} · 待处理 {result.counts.pending} · 待核实{" "}
                {result.counts.uncertain} · 跳过 {result.counts.skipped}
              </p>
              {result.reviewCount > 0 && (
                <p className="text-sm text-amber-800">
                  仍有 {result.reviewCount} 个文件需要确认，未按猜测移动。
                </p>
              )}
              {result.state === "cancelled" && (
                <p className="text-sm text-slate-600">
                  已完成的移动保留；如需恢复原位置，请另行预览撤销。
                </p>
              )}
              <p className="text-xs text-slate-500" aria-live="polite">
                执行结果：显示 {outcomes.length} / {result.results.length} 项
              </p>
              <div className="divide-y rounded-xl border border-slate-200">
                {outcomes.map(({ row, entry }) => (
                  <article key={row.entryId} className="space-y-1 p-3 text-xs">
                    <p className="break-all font-medium">{row.source}</p>
                    <p>
                      {reasonText(
                        row.reason,
                        {
                          moved:
                            result.direction === "undo"
                              ? "已恢复原位置"
                              : "已移动",
                          conflict: "存在冲突，未完成",
                          failed: "处理失败",
                          pending: "尚未处理",
                          skipped: "已跳过",
                          uncertain: "当前位置待核实，请勿重复移动",
                        }[row.status],
                      )}
                    </p>
                    {row.target && (
                      <p className="break-all text-slate-500">
                        计划位置：{row.target}
                      </p>
                    )}
                    {entry && <Evidence entry={entry} />}
                    {row.actualPath && revealFileRequest(row.actualPath) && (
                      <a
                        className="mr-3 inline-flex text-blue-700 underline"
                        href={desktopActionHref({
                          type: "files.reveal",
                          path: row.actualPath,
                        })}
                        onClick={(event) => {
                          if (
                            !onReveal ||
                            event.ctrlKey ||
                            event.metaKey ||
                            event.shiftKey ||
                            event.altKey
                          )
                            return;
                          event.preventDefault();
                          onReveal(row.actualPath!);
                        }}
                      >
                        在文件夹中显示
                      </a>
                    )}
                    {row.actualPath ? (
                      <button
                        type="button"
                        disabled={downloadBusy !== null}
                        className="inline-flex items-center gap-1 text-blue-700 underline"
                        onClick={() =>
                          void download(
                            row.actualPath!,
                            row.entryId,
                            result.planId,
                          )
                        }
                      >
                        <DownloadIcon className="size-3" />
                        下载原件
                      </button>
                    ) : (
                      <p className="text-amber-700">尚未核实可读取的位置</p>
                    )}
                    {row.recoveryPaths.map((recovery) => (
                      <p key={recovery} className="break-all text-amber-800">
                        保留的恢复副本：{recovery}
                      </p>
                    ))}
                  </article>
                ))}
                {outcomes.length === 0 && (
                  <p className="p-3 text-xs text-slate-500">
                    {searching
                      ? "本计划的执行结果中没有匹配文件，可查看预览清单中的待确认项目"
                      : "尚无逐项执行结果"}
                  </p>
                )}
              </div>
              {result.taskId && (
                <details className="text-xs text-slate-500">
                  <summary>任务记录</summary>
                  <p className="break-all">{result.taskId}</p>
                  {onOpenTask && (
                    <button
                      type="button"
                      onClick={() => onOpenTask(result.taskId!)}
                      className="text-blue-700 underline"
                    >
                      在任务空间查看
                    </button>
                  )}
                </details>
              )}
            </section>
          )}
        </div>
        <footer className="flex flex-wrap gap-2 border-t p-4">
          <button
            type="button"
            className={buttonClass}
            disabled={!!busy || active}
            onClick={() => void makePlan()}
          >
            {plan ? "重新预览此目录" : "生成整理预览"}
          </button>
          {plan && showApply && (
            <button
              type="button"
              className={primaryClass}
              disabled={!!busy || active || !canApplyOrganizationPlan(plan)}
              onClick={() => setApprovalPlan(plan)}
            >
              {result?.finalizationPending && canRetry
                ? "重新审批补齐记录"
                : plan.direction === "undo"
                  ? "确认撤销计划"
                  : canRetry
                    ? "重试未完成项"
                    : "确认整理计划"}
            </button>
          )}
          {plan && (
            <button
              type="button"
              className={buttonClass}
              disabled={busy === "loading" || busy === "planning"}
              onClick={() => void readResult()}
            >
              <RefreshCwIcon className="mr-1 inline size-3" />
              刷新结果
            </button>
          )}
          {active && (
            <button
              type="button"
              className={buttonClass}
              disabled={cancelling}
              onClick={() => void cancel()}
            >
              {cancelling ? "正在请求停止…" : "停止后续处理"}
            </button>
          )}
          {plan?.direction === "apply" && hasCommitted && (
            <button
              type="button"
              className={buttonClass}
              disabled={!!busy || active}
              onClick={() => void makePlan(true)}
            >
              预览撤销本次操作
            </button>
          )}
          {result && (
            <button
              type="button"
              className={buttonClass}
              onClick={exportResult}
            >
              下载结果清单
            </button>
          )}
        </footer>
      </section>
      <HighRiskApprovalDialog
        open={approvalPlan !== null}
        title={
          approvalPlan?.direction === "undo"
            ? "确认恢复这些文件的位置？"
            : "确认按预览整理文件？"
        }
        description={
          result?.finalizationPending && canRetry
            ? "重新批准整份计划以补齐任务或审计记录；列表查找和筛选不改变审批范围，已提交的文件不会再次移动。"
            : `批准整份计划中可执行的 ${approvalPlan?.summary.ready ?? 0} 个文件，不限于当前查找或筛选显示的项目；待确认和冲突项不会被强行移动。执行前会再次检查文件与目录权限。`
        }
        targetLabel={path || "NAS 根目录"}
        confirmLabel={
          approvalPlan?.direction === "undo" ? "执行撤销" : "开始整理"
        }
        onCancel={() => setApprovalPlan(null)}
        onConfirm={confirm}
      />
    </div>
  );
}
