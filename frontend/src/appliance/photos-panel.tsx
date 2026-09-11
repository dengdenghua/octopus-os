import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { DesktopAgentContext } from "./desktop-agent-context";
import {
  BrainCircuitIcon,
  ImageIcon,
  ImagesIcon,
  Loader2Icon,
  PauseIcon,
  PlayIcon,
  RefreshCwIcon,
  ScanFaceIcon,
  SearchIcon,
  SparklesIcon,
  UnplugIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { requestHighRiskApproval } from "@/appliance/approval";
import { HighRiskApprovalDialog } from "@/appliance/high-risk-approval-dialog";
import {
  applyPhotoIndex,
  cancelPhotoIndexJob,
  createPhotoIndexPlan,
  PhotoIndexConflictError,
  fetchPhotoLibrary,
  fetchPhotoStatus,
  pausePhotoIndexJob,
  photoOriginalUrl,
  photoThumbnailUrl,
  searchPhotos,
  resumePhotoIndexJob,
  type PhotoFeatureReadiness,
  type PhotoIndexJob,
  type PhotoIndexPlan,
  type PhotoItem,
  type PhotoLibrary,
  type PhotoModelError,
  type PhotoStatus,
} from "@/appliance/photos";
import { cn } from "@/lib/utils";

function countLabel(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function indexJobActive(state?: PhotoIndexJob["state"]) {
  return (
    state === "running" ||
    state === "pausing" ||
    state === "paused" ||
    state === "cancelling"
  );
}

function cleanupImpact(plan: PhotoIndexPlan | null) {
  if (
    !plan?.cleanupOnly ||
    plan.imageCount !== 0 ||
    plan.scanTruncated ||
    (plan.scanErrors ?? 0) > 0 ||
    plan.includeFaces
  )
    return null;
  const count = (field: string) => {
    const change = plan.changes.find((item) => item.field === field);
    return change &&
      Number.isInteger(change.before) &&
      (change.before as number) >= 0 &&
      change.after === 0
      ? (change.before as number)
      : null;
  };
  const indexed = count("indexedPhotos");
  const faces = count("faceRecords");
  const derived = count("derivedRecords");
  return indexed === null || faces === null || derived === null
    ? null
    : { indexed, faces, derived };
}

function indexResultSummary(result: PhotoIndexJob["result"]) {
  const counts: Array<[number | undefined, string, string]> = [
    [result?.reused, "复用", "张"],
    [result?.embedded, "更新", "张"],
    [result?.removed, "移除", "条旧索引"],
  ];
  return counts
    .filter(([count]) => Number.isInteger(count) && (count ?? -1) >= 0)
    .map(([count, label, unit]) => `${label} ${countLabel(count!)} ${unit}`)
    .join(" · ");
}

function indexFailureMessage(
  error: string | null,
  partial = false,
  cleanupOnly = false,
) {
  if (cleanupOnly) {
    return error === "service_restart_interrupted"
      ? "上次索引清理因服务重启而中断，请重新预览清理计划后重试。原图不会被删除。"
      : "上次索引清理未完成，请刷新状态并重新预览清理计划后重试。原图不会被删除。";
  }
  switch (error) {
    case "service_restart_interrupted":
      return "上次索引因服务重启而中断，请重新建立。原照片保留在媒体库中。";
    case "job_state_unreadable":
    case "job_state_unavailable":
      return "索引任务记录无法读取或保存，请检查设备存储空间与写入权限后重试。";
    case "face_model_unavailable":
      return partial
        ? "语义索引已写入，人物分组未完成。请检查设备网络与人脸模型配置后重试。"
        : "人脸模型未能加载。可关闭人物聚类后重新建立，或检查设备网络与模型配置。";
    case "image_inference_busy":
      return "当前设备正在处理其他图片或视频推理，旧索引已保留。稍后重新预览并建立索引即可。";
    default:
      return "上次索引未完成，请检查设备状态后重新建立。原照片保留在媒体库中。";
  }
}

function modelFailureHint(code?: PhotoModelError["code"]) {
  switch (code) {
    case "runtime_import_failed":
      return "推理运行库无法加载，请设备管理员检查智能索引组件及系统运行库是否完整、兼容后重试。";
    case "unsupported_quantization":
      return "当前模型不支持所选量化设置，请设备管理员关闭量化或调整模型配置后重试。";
    case "invalid_model_configuration":
      return "模型配置无效，请设备管理员检查模型名称、运行设备与参数设置后重试。";
    default:
      return "请设备管理员检查设备网络、模型缓存及其读取权限后重试。";
  }
}

function modelLabel(model: PhotoModelError["model"]) {
  switch (model) {
    case "text":
      return "文字检索模型";
    case "vision":
      return "图片模型";
    case "faces":
      return "人物模型";
    default:
      return "本地模型";
  }
}

function ModelReadinessNotice({
  feature,
  label,
}: {
  feature?: PhotoFeatureReadiness;
  label: string;
}) {
  if (feature?.state === "loading")
    return (
      <p role="status" className="mt-3 text-xs leading-5 text-slate-500">
        <Loader2Icon className="mr-1 inline size-3.5 animate-spin" />
        {label}正在本机加载，请稍候。照片仍可浏览。
      </p>
    );
  if (feature?.state !== "load-failed") return null;
  // Render only fixed model labels and guidance, never runtime exceptions or paths.
  const hints = [
    ...new Set(
      (feature.modelErrors ?? []).map(
        ({ model, code }) => `${modelLabel(model)}：${modelFailureHint(code)}`,
      ),
    ),
  ];
  return (
    <div role="alert" className="mt-3 text-xs leading-5 text-amber-700">
      <p>{label}加载失败。</p>
      {(hints.length ? hints : [modelFailureHint()]).map((hint) => (
        <p key={hint}>{hint}</p>
      ))}
    </div>
  );
}

function PhotoTile({
  photo,
  onOpen,
  previewAvailable,
}: {
  photo: PhotoItem;
  onOpen: () => void;
  previewAvailable: boolean;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group relative aspect-square overflow-hidden rounded-[16px] bg-white/52 text-left shadow-[0_8px_24px_rgba(51,65,85,.07)] transition hover:-translate-y-0.5 hover:shadow-[0_16px_32px_rgba(51,65,85,.14)]"
      aria-label={`查看 ${photo.name}`}
    >
      {failed || !previewAvailable ? (
        <span className="grid size-full place-items-center bg-gradient-to-br from-rose-100 to-orange-100 text-rose-300">
          <ImageIcon className="size-9" strokeWidth={1.5} />
        </span>
      ) : (
        <img
          src={photoThumbnailUrl(photo.path)}
          alt={photo.name}
          loading="lazy"
          onError={() => setFailed(true)}
          className="size-full object-cover transition duration-300 group-hover:scale-[1.025]"
        />
      )}
      <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-slate-950/66 via-slate-950/16 to-transparent px-3 pb-2.5 pt-9 opacity-0 transition group-hover:opacity-100">
        <span className="block truncate text-[11px] font-medium text-white">
          {photo.name}
        </span>
      </span>
      {photo.indexed && (
        <span className="absolute right-2 top-2 grid size-5 place-items-center rounded-full bg-slate-950/35 text-white backdrop-blur-md">
          <SparklesIcon className="size-3" />
        </span>
      )}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[104px] rounded-[16px] bg-white/58 px-3.5 py-3 shadow-[0_8px_22px_rgba(51,65,85,.05)]">
      <div className="text-lg font-semibold tracking-tight text-slate-900">
        {value}
      </div>
      <div className="mt-0.5 text-[10px] font-medium text-slate-400">
        {label}
      </div>
    </div>
  );
}

export function PhotosPanel({
  open,
  searchRequest,
  onAskAgent,
  onClose,
}: {
  open: boolean;
  searchRequest?: { query: string; selectedPath?: string } | null;
  onAskAgent?: (context: DesktopAgentContext) => void;
  onClose: () => void;
}) {
  const [library, setLibrary] = useState<PhotoLibrary | null>(null);
  const [status, setStatus] = useState<PhotoStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"semantic" | "filename" | null>(
    null,
  );
  const [searching, setSearching] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [includeFaces, setIncludeFaces] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const [pausePending, setPausePending] = useState(false);
  const [resumePending, setResumePending] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [pendingPlan, setPendingPlan] = useState<PhotoIndexPlan | null>(null);
  const [selected, setSelected] = useState<PhotoItem | null>(null);
  // Closing invalidates all work. View and status requests have separate
  // generations so polling can update job facts without replacing a query.
  const session = useRef(0);
  const viewRequest = useRef(0);
  const statusRequest = useRef(0);
  const activeSearch = useRef<string | null>(null);
  const displayedView = useRef<{
    query: string | null;
    mode: "semantic" | "filename" | null;
  }>({ query: null, mode: null });
  const pagePending = useRef(false);
  const manageAllowed = useRef(true);
  const cancellationAllowed = useRef(false);
  const observedJob = useRef<PhotoIndexJob | null>(null);
  const cancelRequest = useRef<object | null>(null);
  const pauseRequest = useRef<object | null>(null);
  const resumeRequest = useRef<object | null>(null);
  const terminalNotice = useRef<string | null>(null);
  const readiness = status?.index.readiness;
  const canManage = status?.index.canManage !== false;
  const canCancel = status?.index.canManage === true;
  const canCleanup = canManage && status?.index.cleanupAvailable === true;
  const pendingCleanupImpact = cleanupImpact(pendingPlan);
  const job = canManage ? status?.job : undefined;
  const jobActive = indexJobActive(job?.state);
  const semanticAvailable =
    readiness?.semantic.available ?? status?.index.backendAvailable ?? false;
  const facesAvailable = readiness?.faces.available ?? semanticAvailable;
  const previewAvailable = readiness?.previewAvailable ?? true;
  const secureReaderUnavailable =
    readiness?.semantic.missingDependencies.includes("secure-file-access") ??
    false;
  const modelLoadFailed =
    readiness?.semantic.state === "load-failed" ||
    readiness?.faces.state === "load-failed";
  const modelLoading =
    readiness?.semantic.state === "loading" ||
    readiness?.faces.state === "loading";

  const readStatus = useCallback(async () => {
    const currentSession = session.current;
    const request = ++statusRequest.current;
    const current = () =>
      currentSession === session.current && request === statusRequest.current;
    try {
      const next = await fetchPhotoStatus();
      if (!current()) return null;
      manageAllowed.current = next.index.canManage !== false;
      cancellationAllowed.current = next.index.canManage === true;
      observedJob.current = next.job;
      setStatus(next);
      return next;
    } catch (reason) {
      if (current()) throw reason;
      return null;
    }
  }, []);

  const refresh = useCallback(
    async (clearQuery = true, withStatus = true) => {
      const currentSession = session.current;
      const request = ++viewRequest.current;
      const current = () =>
        currentSession === session.current && request === viewRequest.current;
      activeSearch.current = null;
      pagePending.current = false;
      if (clearQuery) setQuery("");
      setLoading(true);
      setSearching(false);
      setLoadingMore(false);
      setSearchMode(null);
      setError(null);
      try {
        const [nextLibrary] = await Promise.all([
          fetchPhotoLibrary(),
          withStatus ? readStatus() : Promise.resolve(null),
        ]);
        if (!current()) return;
        displayedView.current = { query: null, mode: null };
        setLibrary(nextLibrary);
      } catch (reason) {
        if (current())
          setError(
            reason instanceof Error ? reason.message : "照片库暂时不可用",
          );
      } finally {
        if (current()) setLoading(false);
      }
    },
    [readStatus],
  );

  const reportJobResult = useCallback(
    (next: PhotoIndexJob, currentView: number) => {
      if (!manageAllowed.current || indexJobActive(next.state)) return;
      const notice = `${next.jobId}:${next.state}`;
      if (terminalNotice.current === notice) return;
      terminalNotice.current = notice;
      if (next.state === "succeeded") {
        toast.success(
          next.cleanupOnly
            ? "旧照片索引已清理，原图未被删除"
            : "照片智能索引已建立",
        );
        if (
          activeSearch.current === null &&
          currentView === viewRequest.current &&
          !pagePending.current
        )
          void refresh(false, false);
      } else if (next.state === "failed") {
        toast.error(
          indexFailureMessage(
            next.error,
            next.result?.partial,
            next.cleanupOnly,
          ),
        );
      }
    },
    [refresh],
  );

  useEffect(() => {
    if (!open) return;
    session.current += 1;
    displayedView.current = { query: null, mode: null };
    manageAllowed.current = true;
    cancellationAllowed.current = false;
    observedJob.current = null;
    cancelRequest.current = null;
    pauseRequest.current = null;
    resumeRequest.current = null;
    terminalNotice.current = null;
    setLibrary(null);
    setStatus(null);
    setSelected(null);
    setPendingPlan(null);
    setPlanning(false);
    setCancelPending(false);
    setPausePending(false);
    setResumePending(false);
    setCancelError(null);
    void refresh();
    return () => {
      session.current += 1;
    };
  }, [open, refresh]);

  useEffect(() => {
    if (!facesAvailable || !canManage || canCleanup) setIncludeFaces(false);
    if (!canManage) setPendingPlan(null);
  }, [facesAvailable, canManage, canCleanup]);

  useEffect(() => {
    if (!open || !jobActive) return;
    let disposed = false;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      const currentView = viewRequest.current;
      readStatus()
        .then((next) => {
          if (disposed || !next) return;
          reportJobResult(next.job, currentView);
        })
        .catch(() => undefined)
        .finally(() => {
          pending = false;
        });
    }, 2_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [open, jobActive, job?.state, job?.jobId, readStatus, reportJobResult]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (selected) setSelected(null);
      else if (!pendingPlan) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open, pendingPlan, selected]);

  const photos = library?.items ?? [];
  const indexedPercent = useMemo(() => {
    const total = status?.library.imageCount ?? 0;
    if (!total) return 0;
    return Math.min(
      100,
      Math.round(((status?.index.indexed ?? 0) / total) * 100),
    );
  }, [status]);

  const runSearch = useCallback(
    async (value: string) => {
      if (!value) {
        await refresh();
        return;
      }
      const currentSession = session.current;
      const request = ++viewRequest.current;
      const current = () =>
        currentSession === session.current && request === viewRequest.current;
      activeSearch.current = value;
      pagePending.current = false;
      setLoading(false);
      setLoadingMore(false);
      setSearching(true);
      setSearchMode(displayedView.current.mode);
      setError(null);
      try {
        const result = await searchPhotos(value);
        if (!current()) return;
        displayedView.current = { query: value, mode: result.mode };
        setLibrary({
          schema: "echo.photos.library.v1",
          total: result.total,
          offset: 0,
          limit: 50,
          scanTruncated: false,
          unsafeLinksSkipped: status?.library.unsafeLinksSkipped ?? 0,
          items: result.items,
        });
        setSearchMode(result.mode);
      } catch (reason) {
        if (current()) {
          activeSearch.current = displayedView.current.query;
          setSearchMode(displayedView.current.mode);
          toast.error(
            reason instanceof Error ? reason.message : "照片搜索失败",
          );
        }
      } finally {
        if (current()) {
          setSearching(false);
          // Search can be the first model load attempt. Observe its outcome without
          // replacing results, including a valid filename fallback after load failure.
          void readStatus().catch(() => undefined);
        }
      }
    },
    [refresh, readStatus, status?.library.unsafeLinksSkipped],
  );

  const consumedSearch = useRef<object | null>(null);
  const pendingReveal = useRef<string | null>(null);
  useEffect(() => {
    if (!open) {
      consumedSearch.current = null;
      pendingReveal.current = null;
      return;
    }
    if (!searchRequest || consumedSearch.current === searchRequest) return;
    consumedSearch.current = searchRequest;
    setQuery(searchRequest.query);
    setSelected(null);
    setLibrary(null);
    pendingReveal.current = searchRequest.selectedPath ?? null;
    void runSearch(searchRequest.query);
  }, [open, searchRequest, runSearch]);

  useEffect(() => {
    if (!open || !library || !pendingReveal.current) return;
    const photo = library.items.find(
      (item) => item.path === pendingReveal.current,
    );
    pendingReveal.current = null;
    if (photo) setSelected(photo);
    else
      setError(
        "已恢复相册视图，但当前结果中没有这张照片，可能已移动或不在当前页。",
      );
  }, [open, library]);

  const submitSearch = async (event: FormEvent) => {
    event.preventDefault();
    await runSearch(query.trim());
  };

  const beginIndex = async () => {
    if (
      planning ||
      jobActive ||
      cancelPending ||
      (!semanticAvailable && !canCleanup) ||
      !canManage
    )
      return;
    setPlanning(true);
    const currentSession = session.current;
    try {
      const plan = await createPhotoIndexPlan(
        !canCleanup && includeFaces && facesAvailable,
      );
      if (currentSession !== session.current || !manageAllowed.current) return;
      if (!plan.ready) {
        toast.error(plan.blockers[0]?.message || "智能索引暂不可用");
        void readStatus().catch(() => undefined);
        return;
      }
      if (plan.cleanupOnly && !cleanupImpact(plan)) {
        toast.error("无法确认受影响的索引条目，请刷新后重新预览。");
        return;
      }
      setPendingPlan(plan);
    } catch (reason) {
      if (currentSession === session.current)
        toast.error(
          reason instanceof Error ? reason.message : "无法检查索引计划",
        );
    } finally {
      if (currentSession === session.current) setPlanning(false);
    }
  };

  const confirmIndex = async (password: string) => {
    if (!pendingPlan || !canManage) return;
    const currentSession = session.current;
    const plan = pendingPlan;
    try {
      const approval = await requestHighRiskApproval(
        "photos.index.build",
        plan.planId,
        password,
      );
      if (currentSession !== session.current || !manageAllowed.current) return;
      const result = await applyPhotoIndex(
        plan.planId,
        plan.includeFaces,
        approval.approvalToken,
      );
      if (currentSession !== session.current) return;
      // An earlier status request must not replace the newly accepted job.
      statusRequest.current += 1;
      observedJob.current = result.job;
      setCancelError(null);
      setPendingPlan(null);
      setStatus((current) =>
        current ? { ...current, job: result.job } : current,
      );
      toast.success(
        plan.cleanupOnly
          ? "已在设备后台开始清理旧索引，原图不会被删除"
          : "已在设备后台开始建立索引",
      );
    } catch (reason) {
      if (currentSession !== session.current) return;
      if (reason instanceof PhotoIndexConflictError) {
        setPendingPlan(null);
        toast.error(`${reason.message}。请重新预览索引计划后再确认。`);
        void readStatus().catch(() => undefined);
        return;
      }
      throw reason;
    }
  };

  const cancelIndex = async () => {
    const currentJob = observedJob.current;
    if (
      !cancellationAllowed.current ||
      !currentJob?.jobId ||
      !["running", "pausing", "paused"].includes(currentJob.state) ||
      cancelRequest.current ||
      pauseRequest.current ||
      resumeRequest.current
    )
      return;
    const currentSession = session.current;
    const jobId = currentJob.jobId;
    const request = {};
    const current = () =>
      currentSession === session.current && cancelRequest.current === request;
    cancelRequest.current = request;
    // Invalidate a status read begun before this user action. Polling continues.
    statusRequest.current += 1;
    setCancelPending(true);
    setCancelError(null);
    const currentView = viewRequest.current;
    try {
      const result = await cancelPhotoIndexJob(jobId);
      if (!current() || !cancellationAllowed.current) return;
      const latest = observedJob.current;
      if (latest?.jobId !== jobId || result.job.jobId !== jobId) return;
      // A slow cancellation response must not regress an observed completion.
      if (!indexJobActive(latest.state) && indexJobActive(result.job.state))
        return;
      statusRequest.current += 1;
      observedJob.current = result.job;
      setStatus((value) => (value ? { ...value, job: result.job } : value));
      reportJobResult(result.job, currentView);
      if (!indexJobActive(result.job.state))
        void readStatus().catch(() => undefined);
    } catch (reason) {
      if (current() && cancellationAllowed.current) {
        const message =
          reason instanceof Error
            ? reason.message
            : "未能停止索引，请刷新任务状态后重试";
        setCancelError(message);
        toast.error(message);
        void readStatus().catch(() => undefined);
      }
    } finally {
      if (current()) {
        cancelRequest.current = null;
        setCancelPending(false);
      }
    }
  };

  const pauseIndex = async () => {
    const currentJob = observedJob.current;
    if (
      !cancellationAllowed.current ||
      !currentJob?.jobId ||
      currentJob.state !== "running" ||
      pauseRequest.current ||
      cancelRequest.current
    )
      return;
    const currentSession = session.current;
    const jobId = currentJob.jobId;
    const request = {};
    const current = () =>
      currentSession === session.current && pauseRequest.current === request;
    pauseRequest.current = request;
    statusRequest.current += 1;
    setPausePending(true);
    setCancelError(null);
    try {
      const result = await pausePhotoIndexJob(jobId);
      if (!current() || !cancellationAllowed.current) return;
      const latest = observedJob.current;
      if (latest?.jobId !== jobId || result.job.jobId !== jobId) return;
      if (!indexJobActive(latest.state) && indexJobActive(result.job.state))
        return;
      statusRequest.current += 1;
      observedJob.current = result.job;
      setStatus((value) => (value ? { ...value, job: result.job } : value));
    } catch (reason) {
      if (current() && cancellationAllowed.current) {
        const message =
          reason instanceof Error
            ? reason.message
            : "未能暂停索引，请刷新任务状态后重试";
        setCancelError(message);
        toast.error(message);
        void readStatus().catch(() => undefined);
      }
    } finally {
      if (current()) {
        pauseRequest.current = null;
        setPausePending(false);
      }
    }
  };

  const resumeIndex = async () => {
    const currentJob = observedJob.current;
    if (
      !cancellationAllowed.current ||
      !currentJob?.jobId ||
      currentJob.state !== "paused" ||
      resumeRequest.current ||
      cancelRequest.current ||
      pauseRequest.current
    )
      return;
    const currentSession = session.current;
    const jobId = currentJob.jobId;
    const request = {};
    const current = () =>
      currentSession === session.current && resumeRequest.current === request;
    resumeRequest.current = request;
    statusRequest.current += 1;
    setResumePending(true);
    setCancelError(null);
    try {
      const result = await resumePhotoIndexJob(jobId);
      if (!current() || !cancellationAllowed.current) return;
      const latest = observedJob.current;
      if (latest?.jobId !== jobId || result.job.jobId !== jobId) return;
      statusRequest.current += 1;
      observedJob.current = result.job;
      setStatus((value) => (value ? { ...value, job: result.job } : value));
    } catch (reason) {
      if (current() && cancellationAllowed.current) {
        const message =
          reason instanceof Error
            ? reason.message
            : "未能恢复索引，请刷新任务状态后重试";
        setCancelError(message);
        toast.error(message);
        void readStatus().catch(() => undefined);
      }
    } finally {
      if (current()) {
        resumeRequest.current = null;
        setResumePending(false);
      }
    }
  };

  const loadMore = async () => {
    if (
      !library ||
      pagePending.current ||
      loading ||
      searching ||
      activeSearch.current !== null
    )
      return;
    const currentSession = session.current;
    const request = viewRequest.current;
    const current = () =>
      currentSession === session.current && request === viewRequest.current;
    pagePending.current = true;
    setLoadingMore(true);
    try {
      const next = await fetchPhotoLibrary("", library.items.length, 120);
      if (!current()) return;
      setLibrary((previous) => {
        if (!current()) return previous;
        if (!previous) return next;
        const known = new Set(previous.items.map((item) => item.path));
        return {
          ...next,
          offset: 0,
          items: [
            ...previous.items,
            ...next.items.filter((item) => !known.has(item.path)),
          ],
        };
      });
    } catch (reason) {
      if (current())
        toast.error(
          reason instanceof Error ? reason.message : "更多照片读取失败",
        );
    } finally {
      if (current()) {
        pagePending.current = false;
        setLoadingMore(false);
      }
    }
  };

  if (!open) return null;

  return (
    <>
      <div
        data-desktop-interactive
        className="fixed inset-0 z-[88] flex items-center justify-center bg-slate-950/18 p-4 backdrop-blur-[2px]"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <section
          role="dialog"
          aria-modal="true"
          aria-label="照片"
          className="relative flex h-[min(800px,calc(100vh-64px))] w-[min(1160px,calc(100vw-32px))] flex-col overflow-hidden rounded-[24px] border border-white/72 bg-slate-50/91 text-slate-900 shadow-[0_34px_100px_rgba(15,23,42,.34)] backdrop-blur-3xl"
        >
          <header className="relative flex h-14 shrink-0 items-center bg-white/52 px-5 shadow-[0_1px_0_rgba(148,163,184,.18)]">
            <div className="flex gap-2">
              <button
                type="button"
                aria-label="关闭照片"
                onClick={onClose}
                className="grid size-3.5 place-items-center rounded-full bg-[#ff5f57] text-transparent hover:text-red-900/70"
              >
                <XIcon className="size-2.5" />
              </button>
              <span className="size-3.5 rounded-full bg-[#febc2e]" />
              <span className="size-3.5 rounded-full bg-[#28c840]" />
            </div>
            <div className="pointer-events-none absolute left-1/2 flex -translate-x-1/2 items-center gap-2 text-sm font-semibold text-slate-700">
              <ImagesIcon className="size-4 text-rose-500" />
              照片
            </div>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              aria-label="刷新照片库"
              className="ml-auto grid size-8 place-items-center rounded-full text-slate-500 transition hover:bg-slate-200/70 disabled:opacity-50"
            >
              <RefreshCwIcon
                className={cn("size-4", loading && "animate-spin")}
              />
            </button>
          </header>

          <div className="shrink-0 px-6 pb-5 pt-6">
            <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
              <div>
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.16em] text-rose-500">
                  <SparklesIcon className="size-3.5" />
                  本地智能相册
                </div>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
                  你的照片，只在这台设备理解
                </h1>
                <p className="mt-1.5 max-w-xl text-[13px] leading-5 text-slate-500">
                  浏览 NAS
                  原图，缩略图与语义索引都在本地生成。智能索引只读取照片，不移动、不删除原文件。
                </p>
              </div>
              <form
                onSubmit={submitSearch}
                className="flex h-11 w-full items-center gap-2 rounded-full bg-white/82 px-4 shadow-[inset_0_0_0_1px_rgba(148,163,184,.24),0_5px_18px_rgba(51,65,85,.06)] xl:w-[360px]"
              >
                {searching ? (
                  <Loader2Icon className="size-4 animate-spin text-rose-400" />
                ) : (
                  <SearchIcon className="size-4 text-slate-400" />
                )}
                <input
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  placeholder="试试“海边的家人”或文件名"
                  aria-label="搜索照片"
                  className="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
                {query && (
                  <button
                    type="button"
                    aria-label="清除照片搜索"
                    onClick={() => {
                      setQuery("");
                      void refresh();
                    }}
                    className="grid size-6 place-items-center rounded-full text-slate-400 hover:bg-slate-100"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                )}
              </form>
            </div>

            <div className="mt-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex gap-2 overflow-x-auto pb-1">
                <Metric
                  label="媒体库"
                  value={countLabel(
                    status?.library.imageCount ?? library?.total ?? 0,
                  )}
                />
                <Metric
                  label={canCleanup ? "旧照片索引" : "已理解"}
                  value={
                    canCleanup
                      ? countLabel(status?.index.indexed ?? 0)
                      : `${countLabel(status?.index.indexed ?? 0)} · ${indexedPercent}%`
                  }
                />
                <Metric
                  label="人脸记录"
                  value={countLabel(status?.index.faces ?? 0)}
                />
                <Metric
                  label="重复组 / 模糊"
                  value={`${status?.index.duplicateGroups ?? 0} / ${status?.index.blurry ?? 0}`}
                />
              </div>
              <div className="flex shrink-0 items-center gap-2 rounded-full bg-white/58 p-1.5 pl-3">
                <label className="flex cursor-pointer items-center gap-2 text-[11px] font-medium text-slate-500">
                  <ScanFaceIcon className="size-3.5" />
                  人物聚类
                  <input
                    type="checkbox"
                    checked={includeFaces}
                    onChange={(event) =>
                      setIncludeFaces(event.currentTarget.checked)
                    }
                    disabled={
                      !canManage ||
                      canCleanup ||
                      !facesAvailable ||
                      jobActive ||
                      cancelPending
                    }
                    className="size-3.5 accent-rose-500"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void beginIndex()}
                  disabled={
                    planning ||
                    jobActive ||
                    cancelPending ||
                    !canManage ||
                    (!semanticAvailable && !canCleanup)
                  }
                  className="inline-flex h-9 items-center gap-1.5 rounded-full bg-slate-900 px-4 text-[11px] font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {planning || jobActive || cancelPending ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : status?.index.databaseExists ? (
                    <RefreshCwIcon className="size-3.5" />
                  ) : (
                    <BrainCircuitIcon className="size-3.5" />
                  )}
                  {job?.state === "cancelling"
                    ? job.cleanupOnly
                      ? "正在停止索引清理"
                      : "正在停止索引"
                    : job?.state === "pausing"
                      ? job.cleanupOnly
                        ? "正在暂停索引清理"
                        : "正在暂停索引"
                      : job?.state === "paused"
                        ? "索引已暂停"
                        : job?.state === "running"
                          ? job.cleanupOnly
                            ? "后台清理旧索引中"
                            : "后台索引中"
                          : canCleanup
                            ? "清理旧索引"
                            : status?.index.databaseExists
                              ? "更新智能索引"
                              : "建立智能索引"}
                </button>
                {canCancel &&
                  jobActive &&
                  job?.jobId &&
                  job.state === "running" && (
                    <button
                      type="button"
                      onClick={() => void pauseIndex()}
                      disabled={pausePending || cancelPending || resumePending}
                      className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[11px] font-semibold text-slate-600 transition hover:bg-white/80 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {pausePending ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : (
                        <PauseIcon className="size-3.5" />
                      )}
                      {pausePending ? "正在请求暂停" : "暂停索引"}
                    </button>
                  )}
                {canCancel &&
                  jobActive &&
                  job?.jobId &&
                  job.state === "paused" && (
                    <button
                      type="button"
                      onClick={() => void resumeIndex()}
                      disabled={resumePending || cancelPending || pausePending}
                      className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[11px] font-semibold text-slate-600 transition hover:bg-white/80 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {resumePending ? (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      ) : (
                        <PlayIcon className="size-3.5" />
                      )}
                      {resumePending ? "正在恢复" : "继续索引"}
                    </button>
                  )}
                {canCancel && jobActive && job?.jobId && (
                  <button
                    type="button"
                    onClick={() => void cancelIndex()}
                    disabled={
                      cancelPending ||
                      pausePending ||
                      resumePending ||
                      job.state === "cancelling"
                    }
                    className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[11px] font-semibold text-slate-600 transition hover:bg-white/80 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {cancelPending || job.state === "cancelling" ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <XIcon className="size-3.5" />
                    )}
                    {job.state === "cancelling"
                      ? "正在停止"
                      : job.state === "paused"
                        ? "取消索引"
                        : cancelPending
                          ? "正在请求停止"
                          : job.cleanupOnly
                            ? "停止清理"
                            : "停止索引"}
                  </button>
                )}
              </div>
            </div>

            {!canManage && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                当前账户可浏览和搜索照片，建立索引需设备管理员操作。
              </p>
            )}
            {canCleanup && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                当前图库为空，但仍有旧索引或关联记录。可以先预览影响范围，再确认清理；不会删除原图，无需加载模型。
              </p>
            )}
            {(status?.library.scanErrors ?? 0) > 0 && (
              <p role="alert" className="mt-3 text-xs leading-5 text-amber-700">
                部分照片目录未能读取，当前照片数量可能不完整。请检查目录权限和设备连接后刷新，再更新或清理索引。
              </p>
            )}
            {status && !semanticAvailable && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                {secureReaderUnavailable
                  ? "当前系统尚不支持相册所需的安全文件读取，可浏览照片目录和按文件名搜索。完整相册功能需在支持的设备系统上使用。"
                  : readiness?.semantic.state === "disabled"
                    ? "智能索引已关闭，可继续浏览照片和按文件名搜索。"
                    : "智能索引组件尚未就绪，可继续浏览照片和按文件名搜索。请在设备上完成智能索引组件安装后刷新。"}
              </p>
            )}
            {semanticAvailable && !facesAvailable && (
              <p className="mt-3 text-xs leading-5 text-slate-500">
                人脸识别组件尚未就绪；可以先建立语义索引，按画面内容搜索。
              </p>
            )}
            <ModelReadinessNotice
              feature={readiness?.semantic}
              label="语义模型"
            />
            <ModelReadinessNotice feature={readiness?.faces} label="人物模型" />
            {modelLoadFailed && (
              <p className="mt-2 text-xs leading-5 text-slate-500">
                {canManage
                  ? "修复后可重新建立智能索引，也可重新尝试搜索。"
                  : "请设备管理员修复后重新建立智能索引。"}
                可继续浏览照片，搜索结果会标明使用语义还是文件名。
              </p>
            )}
            {semanticAvailable &&
              !canCleanup &&
              readiness?.modelDownloadMayBeRequired &&
              !modelLoadFailed &&
              !modelLoading && (
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  首次建立索引可能需要联网下载模型，照片在本机处理。
                  {!readiness.semantic.modelsLoaded &&
                    "语义模型加载成功后才能进行智能检索。"}
                </p>
              )}
            {canManage && cancelError && (
              <p role="alert" className="mt-3 text-xs leading-5 text-amber-700">
                {cancelError}
              </p>
            )}
            {job?.state === "cancelling" && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                {job.cleanupOnly
                  ? "正在停止索引清理，请稍候。原图不会被删除。"
                  : "正在停止索引，请稍候。照片仍可浏览。"}
              </p>
            )}
            {job?.state === "pausing" && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                {job.cleanupOnly
                  ? "正在暂停索引清理，系统会在安全检查点释放资源。"
                  : "正在暂停索引，系统会在安全检查点保留旧索引并释放资源。"}
              </p>
            )}
            {job?.state === "paused" && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                {job.cleanupOnly
                  ? "索引清理已暂停，旧索引仍可用；继续后会从安全检查点重新开始。"
                  : "智能索引已暂停，旧索引仍可用；继续后会从安全检查点重新开始。"}
              </p>
            )}
            {job?.state === "cancelled" && (
              <p
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                {job.cleanupOnly
                  ? "本次索引清理已取消，可以重新预览后清理。原图未被删除。"
                  : "本次索引已取消，可以重新更新。"}
              </p>
            )}
            {job?.state === "succeeded" && (
              <div
                role="status"
                className="mt-3 text-xs leading-5 text-slate-500"
              >
                <p>
                  {job.cleanupOnly
                    ? "旧照片索引及关联记录已清理，原图未被删除"
                    : "智能索引已更新"}
                  {typeof job.result?.indexed === "number" &&
                  Number.isInteger(job.result.indexed) &&
                  job.result.indexed >= 0
                    ? job.cleanupOnly
                      ? `，当前剩余 ${countLabel(job.result.indexed)} 条照片索引。`
                      : `，当前共 ${countLabel(job.result.indexed)} 张照片。`
                    : "。"}
                </p>
                {indexResultSummary(job.result) && (
                  <p>{indexResultSummary(job.result)}</p>
                )}
              </div>
            )}
            {job?.state === "failed" && (
              <p role="alert" className="mt-3 text-xs leading-5 text-amber-700">
                {indexFailureMessage(
                  job.error,
                  job.result?.partial,
                  job.cleanupOnly,
                )}
              </p>
            )}
            {searchMode && (
              <div className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-500">
                {searchMode === "semantic" ? (
                  <SparklesIcon className="size-3.5 text-rose-500" />
                ) : (
                  <SearchIcon className="size-3.5" />
                )}
                {searchMode === "semantic"
                  ? `本地语义结果 · ${library?.total ?? 0} 张`
                  : `智能索引未就绪，已按文件名查找 · ${library?.total ?? 0} 张`}
              </div>
            )}
            {(status?.library.unsafeLinksSkipped ?? 0) > 0 && (
              <p className="mt-2 text-[10px] text-slate-400">
                已安全跳过 {status?.library.unsafeLinksSkipped}{" "}
                个图片链接，不会读取链接目标。
              </p>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
            {loading && !library ? (
              <div className="grid h-64 place-items-center text-sm text-slate-400">
                <span className="flex items-center gap-2">
                  <Loader2Icon className="size-4 animate-spin" />
                  正在整理照片时间线…
                </span>
              </div>
            ) : error ? (
              <div className="grid h-64 place-items-center text-center">
                <div>
                  <UnplugIcon className="mx-auto size-8 text-slate-300" />
                  <p className="mt-3 text-sm font-medium text-slate-700">
                    {error}
                  </p>
                  <button
                    type="button"
                    onClick={() => void refresh()}
                    className="mt-3 rounded-full bg-slate-900 px-4 py-2 text-xs font-medium text-white"
                  >
                    重新读取
                  </button>
                </div>
              </div>
            ) : photos.length === 0 ? (
              <div className="grid h-64 place-items-center text-center">
                <div>
                  <ImagesIcon
                    className="mx-auto size-10 text-slate-300"
                    strokeWidth={1.4}
                  />
                  <p className="mt-3 text-sm font-medium text-slate-700">
                    {query ? "没有找到匹配的照片" : "NAS 中还没有照片"}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    {query
                      ? "换一种描述试试"
                      : "通过文件管家上传后会自动出现在这里"}
                  </p>
                </div>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                  {photos.map((photo) => (
                    <PhotoTile
                      key={photo.path}
                      photo={photo}
                      previewAvailable={previewAvailable}
                      onOpen={() => setSelected(photo)}
                    />
                  ))}
                </div>
                {!searchMode && photos.length < (library?.total ?? 0) && (
                  <div className="flex justify-center pb-2 pt-5">
                    <button
                      type="button"
                      onClick={() => void loadMore()}
                      disabled={loadingMore || searching}
                      className="inline-flex h-9 items-center gap-2 rounded-full bg-white/72 px-4 text-[11px] font-semibold text-slate-600 shadow-[0_7px_20px_rgba(51,65,85,.07)] transition hover:bg-white disabled:opacity-50"
                    >
                      {loadingMore && (
                        <Loader2Icon className="size-3.5 animate-spin" />
                      )}
                      {loadingMore
                        ? "正在读取"
                        : `加载更多 · ${photos.length} / ${library?.total ?? 0}`}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </section>
      </div>

      {selected && (
        <div
          className="fixed inset-0 z-[120] grid place-items-center bg-slate-950/72 p-6 backdrop-blur-xl"
          data-desktop-interactive
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelected(null);
          }}
        >
          <button
            type="button"
            aria-label="关闭照片预览"
            onClick={() => setSelected(null)}
            className="absolute right-6 top-6 grid size-10 place-items-center rounded-full bg-white/12 text-white backdrop-blur-md hover:bg-white/20"
          >
            <XIcon className="size-5" />
          </button>
          <div className="max-h-full max-w-full text-center">
            {previewAvailable ? (
              <img
                src={photoOriginalUrl(selected.path)}
                alt={selected.name}
                className="mx-auto max-h-[calc(100vh-130px)] max-w-[min(900px,calc(100vw-60px))] rounded-[18px] object-contain shadow-2xl"
              />
            ) : (
              <p
                role="status"
                className="max-w-md text-sm leading-6 text-white/80"
              >
                当前设备无法安全生成照片预览，请根据下方路径在文件管理器中查看原文件。
              </p>
            )}
            <div className="mt-4 text-sm font-medium text-white">
              {selected.name}
            </div>
            <div className="mt-1 text-[11px] text-white/55">
              {selected.width && selected.height
                ? `${selected.width} × ${selected.height} · `
                : ""}
              {selected.path}
            </div>
            {onAskAgent && (
              <button
                type="button"
                className="mt-3 rounded-full bg-white/15 px-4 py-2 text-sm text-white hover:bg-white/25"
                onClick={() => {
                  onAskAgent({
                    app: "photos",
                    kind: "photo",
                    path: selected.path,
                    query: displayedView.current.query ?? "",
                  });
                  setSelected(null);
                  onClose();
                }}
              >
                交给 Agent
              </button>
            )}
          </div>
        </div>
      )}

      <HighRiskApprovalDialog
        open={pendingPlan !== null}
        title={
          pendingPlan?.cleanupOnly
            ? "清理旧照片索引？"
            : "建立本地照片智能索引？"
        }
        description={
          pendingPlan
            ? pendingCleanupImpact
              ? `当前图库已扫描为空。将清理 ${countLabel(pendingCleanupImpact.indexed)} 条旧照片索引和 ${countLabel(pendingCleanupImpact.faces)} 条人脸记录，共 ${countLabel(pendingCleanupImpact.derived)} 条关联索引记录。只清理索引，不删除、修改、移动或上传原图；人物名称和类别设置会保留。再次添加照片后可重新建立索引。${pendingPlan.warnings.map((warning) => warning.message).join(" ")}`
              : `设备将读取最多 ${countLabel(Math.min(pendingPlan.imageCount, pendingPlan.maxFiles))} 张照片，生成语义${pendingPlan.includeFaces ? "与人物" : ""}索引。原图不会被修改、移动或上传。${pendingPlan.warnings.map((warning) => warning.message).join(" ")}`
            : ""
        }
        targetLabel={
          pendingPlan
            ? pendingCleanupImpact
              ? `清理 ${countLabel(pendingCleanupImpact.derived)} 条索引记录 · 仅本机`
              : `照片 ${countLabel(pendingPlan.imageCount)} 张 · 仅本机`
            : undefined
        }
        confirmLabel={pendingPlan?.cleanupOnly ? "确认清理索引" : "开始建立"}
        onCancel={() => setPendingPlan(null)}
        onConfirm={confirmIndex}
      />
    </>
  );
}
