import {
  BookOpenIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleDotIcon,
  GitBranchIcon,
  LibraryIcon,
  LoaderCircleIcon,
  PlusIcon,
  PlugZapIcon,
  RefreshCwIcon,
  SaveIcon,
  ScrollTextIcon,
  SparklesIcon,
  ShieldCheckIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { dispatchNarrativeStage } from "@/core/narrative";
import type { NarrativeStageName } from "@/core/narrative";
import { cn } from "@/lib/utils";

import {
  createBranch,
  createChapter,
  createContextPack,
  createNarrativeProject,
  createPipelineRun,
  createReviewRequest,
  createScene,
  commitCanonReview,
  getNarrativeStatus,
  importEchoUniverse,
  listNarrativeProjects,
  loadNarrativeExtensions,
  loadNarrativeWorkspace,
  submitPipelineStage,
  updateChapter,
  updateScene,
  voteReviewRequest,
  type NarrativeBranch,
  type NarrativeProject,
  type NarrativeContextPack,
  type NarrativeExtensions,
  type NarrativePipelineRun,
  type NarrativeReviewRequest,
  type NarrativeStudioStatus,
  type NarrativeWorkspace,
} from "./api";
import {
  buildStoryVolumes,
  nextChapterOrdinal,
  nextSceneOrdinal,
} from "./story-model";
import {
  CanonCommitDialog,
  ContextPackPreview,
  GovernanceActionDialog,
  StudioInspector,
  type NarrativeAgentCandidate,
  type NarrativeAgentMessage,
  type InspectorTab,
} from "./studio-panels";

type CreateMode = "project" | "branch" | "chapter" | "scene" | null;
type GovernanceAction =
  | { kind: "review" }
  | {
      kind: "vote";
      review: NarrativeReviewRequest;
      decision: "approve" | "reject" | "abstain";
    };

const fieldClass =
  "border-border/70 bg-background/55 shadow-none focus-visible:ring-violet-400/40";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function agentErrorMessage(error: unknown): string {
  const message = errorMessage(error);
  if (
    /no LLM model configured|ANTHROPIC_API_KEY|custom_models\.json/i.test(
      message,
    )
  ) {
    return "尚未配置可用模型，请先在设置中添加模型后再运行。";
  }
  return message;
}

const NARRATIVE_AGENT_STAGES = new Set<string>([
  "outline",
  "draft",
  "continuity",
  "style",
  "revision",
  "editorial",
]);

function isNarrativeAgentStage(value: string): value is NarrativeStageName {
  return NARRATIVE_AGENT_STAGES.has(value);
}

function pipelineOutputText(output: unknown): string {
  if (typeof output === "string") return output;
  if (output == null) return "";
  try {
    return JSON.stringify(output, null, 2);
  } catch {
    return String(output);
  }
}

function isCancelledAgentRun(error: unknown, signal: AbortSignal): boolean {
  if (signal.aborted) return true;
  if (!error || typeof error !== "object") return false;
  const candidate = error as { code?: unknown; name?: unknown };
  return candidate.code === "aborted" || candidate.name === "AbortError";
}

function CandidateBadge({ compact = false }: { compact?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-amber-400/20 bg-amber-400/8 font-medium text-amber-700 dark:text-amber-300",
        compact ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-1 text-xs",
      )}
    >
      <CircleDotIcon className={compact ? "size-2.5" : "size-3"} />
      候选态
    </span>
  );
}

function SurfaceCard({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "min-w-0 overflow-hidden rounded-2xl border border-border/60 bg-card/70 shadow-sm backdrop-blur-xl",
        className,
      )}
    >
      {children}
    </section>
  );
}

function EmptyPane({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: typeof BookOpenIcon;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center px-8 py-14 text-center">
      <div className="mb-4 grid size-12 place-items-center rounded-2xl border border-border/70 bg-muted/45">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
        {description}
      </p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

function Modal({
  title,
  description,
  children,
  onClose,
}: {
  title: string;
  description: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
    >
      <div
        className="w-full max-w-lg rounded-2xl border border-border/70 bg-background p-5 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="narrative-modal-title"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="narrative-modal-title" className="text-lg font-semibold">
              {title}
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {description}
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="关闭"
          >
            <XIcon className="size-4" />
          </Button>
        </div>
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}

export default function NarrativePage() {
  const [status, setStatus] = useState<NarrativeStudioStatus | null>(null);
  const [projects, setProjects] = useState<NarrativeProject[]>([]);
  const [activeProjectId, setActiveProjectId] = useState("");
  const [workspace, setWorkspace] = useState<NarrativeWorkspace | null>(null);
  const [extensions, setExtensions] = useState<NarrativeExtensions | null>(
    null,
  );
  const [selectedBranchId, setSelectedBranchId] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [selectedSceneId, setSelectedSceneId] = useState("");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("world");
  const [createMode, setCreateMode] = useState<CreateMode>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [loadingExtensions, setLoadingExtensions] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionKey, setActionKey] = useState("");
  const [agentActionKey, setAgentActionKey] = useState("");
  const [agentCandidate, setAgentCandidate] =
    useState<NarrativeAgentCandidate | null>(null);
  const [agentMessage, setAgentMessage] =
    useState<NarrativeAgentMessage | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [activeContextPack, setActiveContextPack] =
    useState<NarrativeContextPack | null>(null);
  const [commitReview, setCommitReview] =
    useState<NarrativeReviewRequest | null>(null);
  const [governanceAction, setGovernanceAction] =
    useState<GovernanceAction | null>(null);
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const agentAbortRef = useRef<AbortController | null>(null);
  const agentExecutionRef = useRef(0);

  const [projectTitle, setProjectTitle] = useState("");
  const [projectPremise, setProjectPremise] = useState("");
  const [projectLanguage, setProjectLanguage] = useState("zh");
  const [branchName, setBranchName] = useState("");
  const [branchPurpose, setBranchPurpose] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newSummary, setNewSummary] = useState("");

  const [draftTitle, setDraftTitle] = useState("");
  const [draftSummary, setDraftSummary] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [draftGoal, setDraftGoal] = useState("");
  const [draftConflict, setDraftConflict] = useState("");
  const [draftOutcome, setDraftOutcome] = useState("");

  const refreshProjects = useCallback(async (preferredId?: string) => {
    setLoadingProjects(true);
    setError("");
    try {
      const rows = await listNarrativeProjects();
      setProjects(rows);
      setActiveProjectId((current) => {
        const candidate = preferredId || current;
        return rows.some((project) => project.id === candidate)
          ? candidate
          : (rows[0]?.id ?? "");
      });
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoadingProjects(false);
    }
  }, []);

  const refreshWorkspace = useCallback(async (projectId: string) => {
    if (!projectId) {
      setWorkspace(null);
      return;
    }
    setLoadingWorkspace(true);
    setError("");
    try {
      const next = await loadNarrativeWorkspace(projectId);
      setWorkspace(next);
      setSelectedBranchId((current) => {
        if (next.branches.some((branch) => branch.id === current))
          return current;
        return (
          next.project.default_branch_id ||
          next.branches.find((branch) => branch.id === "main")?.id ||
          next.branches[0]?.id ||
          ""
        );
      });
    } catch (nextError) {
      setWorkspace(null);
      setError(errorMessage(nextError));
    } finally {
      setLoadingWorkspace(false);
    }
  }, []);

  const refreshExtensions = useCallback(async (projectId: string) => {
    if (!projectId) {
      setExtensions(null);
      return;
    }
    setLoadingExtensions(true);
    try {
      const next = await loadNarrativeExtensions(projectId);
      setExtensions(next);
    } catch (nextError) {
      setExtensions({
        arcs: [],
        entities: [],
        relationships: [],
        foreshadows: [],
        contextPacks: [],
        pipelineRuns: [],
        reviewRequests: [],
        canonCommits: [],
        warnings: [errorMessage(nextError)],
      });
    } finally {
      setLoadingExtensions(false);
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
    void getNarrativeStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [refreshProjects]);

  useEffect(() => {
    void refreshWorkspace(activeProjectId);
    setExtensions(null);
    setActiveContextPack(null);
    void refreshExtensions(activeProjectId);
  }, [activeProjectId, refreshExtensions, refreshWorkspace]);

  useEffect(() => {
    agentExecutionRef.current += 1;
    agentAbortRef.current?.abort();
    agentAbortRef.current = null;
    setAgentActionKey("");
    setAgentCandidate(null);
    setAgentMessage(null);
  }, [activeProjectId, selectedChapterId, selectedSceneId]);

  useEffect(
    () => () => {
      agentAbortRef.current?.abort();
    },
    [],
  );

  const branchChapters = useMemo(
    () =>
      workspace?.chapters
        .filter((chapter) => chapter.branch_id === selectedBranchId)
        .sort((left, right) => left.ordinal - right.ordinal) ?? [],
    [selectedBranchId, workspace?.chapters],
  );

  useEffect(() => {
    if (!branchChapters.some((chapter) => chapter.id === selectedChapterId)) {
      setSelectedChapterId(branchChapters[0]?.id ?? "");
      setSelectedSceneId("");
    }
  }, [branchChapters, selectedChapterId]);

  const selectedChapter = useMemo(
    () =>
      workspace?.chapters.find((chapter) => chapter.id === selectedChapterId) ??
      null,
    [selectedChapterId, workspace?.chapters],
  );
  const selectedScene = useMemo(
    () =>
      workspace?.scenes.find((scene) => scene.id === selectedSceneId) ?? null,
    [selectedSceneId, workspace?.scenes],
  );

  useEffect(() => {
    const packs = extensions?.contextPacks ?? [];
    const matching = packs.filter(
      (pack) =>
        pack.chapter_id === selectedChapterId &&
        (!selectedSceneId ||
          !pack.scene_id ||
          pack.scene_id === selectedSceneId),
    );
    setActiveContextPack(
      (current) =>
        matching.at(-1) ??
        (current?.chapter_id === selectedChapterId &&
        (!selectedSceneId ||
          !current.scene_id ||
          current.scene_id === selectedSceneId)
          ? current
          : null),
    );
  }, [extensions?.contextPacks, selectedChapterId, selectedSceneId]);

  useEffect(() => {
    const source = selectedScene || selectedChapter;
    setDraftTitle(source?.title ?? "");
    setDraftBody(source?.body ?? "");
    setDraftSummary(
      selectedChapter && !selectedScene ? selectedChapter.summary : "",
    );
    setDraftGoal(selectedScene?.goal ?? "");
    setDraftConflict(selectedScene?.conflict ?? "");
    setDraftOutcome(selectedScene?.outcome ?? "");
  }, [selectedChapter, selectedScene]);

  const volumes = useMemo(
    () =>
      buildStoryVolumes(
        workspace?.chapters ?? [],
        workspace?.scenes ?? [],
        selectedBranchId,
      ),
    [selectedBranchId, workspace?.chapters, workspace?.scenes],
  );

  const editorDirty = selectedScene
    ? draftTitle !== selectedScene.title ||
      draftBody !== selectedScene.body ||
      draftGoal !== selectedScene.goal ||
      draftConflict !== selectedScene.conflict ||
      draftOutcome !== selectedScene.outcome
    : selectedChapter
      ? draftTitle !== selectedChapter.title ||
        draftBody !== selectedChapter.body ||
        draftSummary !== selectedChapter.summary
      : false;

  async function saveEditor() {
    if (!workspace || !selectedChapter) return;
    setSaving(true);
    setError("");
    try {
      if (selectedScene) {
        await updateScene(
          workspace.project.id,
          selectedChapter.id,
          selectedScene.id,
          {
            title: draftTitle,
            goal: draftGoal,
            conflict: draftConflict,
            outcome: draftOutcome,
            body: draftBody,
          },
        );
      } else {
        await updateChapter(workspace.project.id, selectedChapter.id, {
          title: draftTitle,
          summary: draftSummary,
          body: draftBody,
        });
      }
      await refreshWorkspace(workspace.project.id);
      setNotice("候选稿已保存");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!createMode) return;
    setBusy(true);
    setError("");
    try {
      if (createMode === "project") {
        const created = await createNarrativeProject({
          title: projectTitle.trim(),
          premise: projectPremise.trim(),
          language: projectLanguage,
        });
        await refreshProjects(created.id);
        setNotice("项目已创建，默认 main 候选分支已就绪");
      } else if (workspace && createMode === "branch") {
        const created = await createBranch(workspace.project.id, {
          name: branchName.trim(),
          purpose: branchPurpose.trim(),
          base_branch_id: selectedBranchId || undefined,
        });
        await refreshWorkspace(workspace.project.id);
        setSelectedBranchId(created.id);
        setNotice("候选分支已创建");
      } else if (workspace && createMode === "chapter" && selectedBranchId) {
        const created = await createChapter(workspace.project.id, {
          branch_id: selectedBranchId,
          ordinal: nextChapterOrdinal(workspace.chapters, selectedBranchId),
          title: newTitle.trim(),
          summary: newSummary.trim(),
          body: "",
        });
        await refreshWorkspace(workspace.project.id);
        setSelectedChapterId(created.id);
        setSelectedSceneId("");
        setNotice("候选章节已创建");
      } else if (workspace && createMode === "scene" && selectedChapter) {
        const created = await createScene(
          workspace.project.id,
          selectedChapter.id,
          {
            branch_id: selectedChapter.branch_id,
            ordinal: nextSceneOrdinal(workspace.scenes, selectedChapter.id),
            title: newTitle.trim(),
            goal: newSummary.trim(),
            body: "",
          },
        );
        await refreshWorkspace(workspace.project.id);
        setSelectedSceneId(created.id);
        setNotice("候选场景已创建");
      }
      setCreateMode(null);
      setProjectTitle("");
      setProjectPremise("");
      setBranchName("");
      setBranchPurpose("");
      setNewTitle("");
      setNewSummary("");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  }

  async function importEcho() {
    if (!workspace) return;
    setBusy(true);
    setError("");
    try {
      const result = await importEchoUniverse(workspace.project.id, {
        pack_name: "ECHO Universe",
        include_content: true,
      });
      if (result.imported) {
        await refreshWorkspace(workspace.project.id);
        await refreshExtensions(workspace.project.id);
        setInspectorTab("world");
        setNotice(`已导入 ${result.inventory.total_files} 份 ECHO 资料`);
      } else {
        setError(result.reason || "未找到可导入的 ECHO 世界资料");
      }
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  }

  async function buildContextPack(tokenBudget: number) {
    if (!workspace || !selectedChapter) return;
    setActionKey("context:build");
    setError("");
    try {
      const created = await createContextPack(workspace.project.id, {
        branch_id: selectedChapter.branch_id,
        chapter_id: selectedChapter.id,
        scene_id: selectedScene?.id,
        token_budget: tokenBudget,
        max_chars: tokenBudget * 4,
        max_items: 48,
        query: selectedScene?.goal || selectedChapter.summary || draftTitle,
      });
      setActiveContextPack(created);
      await refreshExtensions(workspace.project.id);
      setInspectorTab("continuity");
      setNotice(
        `上下文包已构建：${created.sources.length} 个来源，约 ${created.token_count.toLocaleString()} tokens`,
      );
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  async function startPipeline() {
    if (!workspace || !selectedChapter) return;
    setActionKey("pipeline:create");
    setError("");
    try {
      await createPipelineRun(workspace.project.id, {
        branch_id: selectedChapter.branch_id,
        chapter_id: selectedChapter.id,
        scene_id: selectedScene?.id,
        context_pack_id: activeContextPack?.id,
        objective: selectedScene?.goal || selectedChapter.summary || draftTitle,
      });
      await refreshExtensions(workspace.project.id);
      setInspectorTab("pipeline");
      setNotice("创作流水线已创建，等待第一阶段提交");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  async function submitStage(run: NarrativePipelineRun, stageId: string) {
    if (!workspace || !selectedChapter) return;
    const key = `stage:${run.id}:${stageId}`;
    setActionKey(key);
    setError("");
    try {
      await submitPipelineStage(workspace.project.id, run.id, stageId, {
        actor: "human-editor",
        output: {
          target_type: selectedScene ? "scene" : "chapter",
          target_id: selectedScene?.id || selectedChapter.id,
          title: draftTitle,
          body: draftBody,
          summary: selectedScene ? undefined : draftSummary,
          context_pack_id: activeContextPack?.id,
        },
        notes: "由叙事工坊人工提交当前候选稿快照",
      });
      await refreshExtensions(workspace.project.id);
      setNotice("阶段产物已真实提交");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  async function runAgentStage(run: NarrativePipelineRun, stageId: string) {
    if (
      !workspace ||
      !selectedChapter ||
      !activeContextPack ||
      !isNarrativeAgentStage(stageId)
    ) {
      setAgentMessage({
        runId: run.id,
        stageId,
        kind: "error",
        message: activeContextPack
          ? "当前阶段不是受支持的叙事 Agent 阶段。"
          : "请先构建章节上下文包，再运行 AI 阶段。",
      });
      return;
    }

    agentAbortRef.current?.abort();
    const controller = new AbortController();
    agentAbortRef.current = controller;
    const executionId = agentExecutionRef.current + 1;
    agentExecutionRef.current = executionId;
    const key = `agent:${run.id}:${stageId}`;
    setAgentActionKey(key);
    setAgentCandidate(null);
    setAgentMessage(null);
    setError("");

    const goal =
      selectedScene?.goal.trim() ||
      selectedChapter.summary.trim() ||
      draftTitle.trim() ||
      `完成${selectedScene ? "场景" : "章节"}《${selectedScene?.title || selectedChapter.title}》的${stageId}阶段`;
    const completedUpstreamStages = run.stages
      .filter(
        (stage) =>
          ["completed", "submitted"].includes(stage.status) &&
          isNarrativeAgentStage(stage.id) &&
          pipelineOutputText(stage.output).trim(),
      )
      .map((stage) => ({
        stage: stage.id as NarrativeStageName,
        status: stage.status,
        output: pipelineOutputText(stage.output),
      }));

    try {
      const result = await dispatchNarrativeStage({
        project: {
          id: workspace.project.id,
          title: workspace.project.title,
          premise: workspace.project.premise,
          language: workspace.project.language,
        },
        run: { id: run.id },
        stage: stageId,
        goal,
        contextPack: {
          id: activeContextPack.id,
          label: `${selectedChapter.title} · 章节上下文`,
          sources: activeContextPack.sources
            .filter((source) => source.included)
            .map((source) => ({
              reference: source.reference,
              kind: source.kind,
              title: source.title,
              excerpt: source.excerpt,
              truncated: source.truncated,
              origin: "project",
            })),
        },
        completedUpstreamStages,
        signal: controller.signal,
      });
      if (agentExecutionRef.current !== executionId) return;
      setAgentCandidate({
        runId: run.id,
        stageId,
        output: result.output,
        model: result.metadata.model,
        inputTokens: result.metadata.usage.inputTokens,
        outputTokens: result.metadata.usage.outputTokens,
        totalTokens: result.metadata.usage.totalTokens,
        promptChars: result.promptAudit.promptChars,
        maxPromptChars: result.promptAudit.maxPromptChars,
        promptTruncated: result.promptAudit.truncated,
        omittedContextSources: result.promptAudit.omittedContextSources,
        omittedUpstreamStages: result.promptAudit.omittedUpstreamStages,
        generatedAt: new Date().toISOString(),
      });
      setNotice("AI 候选产物已生成，确认提交前不会推进流水线");
    } catch (nextError) {
      if (agentExecutionRef.current !== executionId) return;
      const cancelled = isCancelledAgentRun(nextError, controller.signal);
      setAgentMessage({
        runId: run.id,
        stageId,
        kind: cancelled ? "cancelled" : "error",
        message: cancelled
          ? "AI 运行已取消，未产生或提交任何内容。"
          : `AI 阶段运行失败：${agentErrorMessage(nextError)}`,
      });
    } finally {
      if (agentExecutionRef.current === executionId) {
        agentAbortRef.current = null;
        setAgentActionKey("");
      }
    }
  }

  function cancelAgentStage() {
    agentAbortRef.current?.abort();
  }

  async function submitAgentCandidate(candidate: NarrativeAgentCandidate) {
    if (!workspace || !selectedChapter) return;
    const key = `stage:${candidate.runId}:${candidate.stageId}`;
    setActionKey(key);
    setError("");
    try {
      await submitPipelineStage(
        workspace.project.id,
        candidate.runId,
        candidate.stageId,
        {
          actor: "human-editor",
          output: candidate.output,
          notes:
            "人工审阅后显式提交的 AI 候选产物；生成阶段本身未自动推进流水线",
        },
      );
      setAgentCandidate(null);
      setAgentMessage(null);
      await refreshExtensions(workspace.project.id);
      setNotice("AI 候选产物已由人工显式提交，流水线已推进");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  async function submitReview() {
    if (!workspace || !selectedChapter) return;
    const target = selectedScene || selectedChapter;
    setActionKey("review:create");
    setError("");
    try {
      await createReviewRequest(workspace.project.id, {
        target_type: selectedScene ? "scene" : "chapter",
        target_id: target.id,
        branch_id: selectedChapter.branch_id,
        chapter_id: selectedChapter.id,
        revision: 1,
        title: `${target.title} · 正典审核`,
        summary:
          (selectedScene
            ? [
                selectedScene.goal,
                selectedScene.conflict,
                selectedScene.outcome,
              ]
                .filter(Boolean)
                .join("；")
            : selectedChapter.summary) || "请审核当前候选修订与连续性状态。",
        blocking: false,
        requested_by: "human-editor",
      });
      await refreshExtensions(workspace.project.id);
      setInspectorTab("canon");
      setGovernanceAction(null);
      setNotice("正典审核请求已提交，尚未进入正典");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  async function castReviewVote(
    review: NarrativeReviewRequest,
    decision: "approve" | "reject" | "abstain",
  ) {
    if (!workspace) return;
    const key = `vote:${review.id}:${decision}`;
    setActionKey(key);
    setError("");
    try {
      await voteReviewRequest(workspace.project.id, review.id, {
        actor: "human-editor",
        decision,
        rationale:
          decision === "approve"
            ? "人工编辑确认当前候选修订可继续治理流程"
            : "人工编辑要求继续修订",
      });
      await refreshExtensions(workspace.project.id);
      setGovernanceAction(null);
      setNotice(decision === "approve" ? "赞成票已记录" : "反对票已记录");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  async function confirmCanonCommit(actor: string, rationale: string) {
    if (!workspace || !commitReview) return;
    setActionKey(`commit:${commitReview.id}`);
    setError("");
    try {
      await commitCanonReview(workspace.project.id, commitReview.id, {
        actor,
        rationale,
        confirm: true,
      });
      await refreshExtensions(workspace.project.id);
      setCommitReview(null);
      setNotice("正典提交已由服务端确认并记录");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionKey("");
    }
  }

  const activeBranch: NarrativeBranch | undefined = workspace?.branches.find(
    (branch) => branch.id === selectedBranchId,
  );

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-auto bg-[radial-gradient(circle_at_50%_-20%,hsl(var(--primary)/0.12),transparent_42%)]">
      <header className="sticky top-0 z-20 border-b border-border/55 bg-background/85 px-4 py-3 backdrop-blur-xl md:px-6">
        <div className="mx-auto flex w-full max-w-[1720px] flex-wrap items-center gap-3">
          <div className="mr-auto flex min-w-0 items-center gap-3">
            <div className="grid size-10 shrink-0 place-items-center rounded-2xl bg-violet-500/12 text-violet-600 dark:text-violet-300">
              <BookOpenIcon className="size-5" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-base font-semibold">叙事工坊</h1>
                <CandidateBadge compact />
              </div>
              <p className="truncate text-xs text-muted-foreground">
                通用叙事引擎 · ECHO 是首个可导入世界包
              </p>
            </div>
          </div>

          {projects.length ? (
            <select
              className="h-9 max-w-56 rounded-lg border border-border/70 bg-background px-3 text-sm outline-none focus:border-violet-400/60"
              value={activeProjectId}
              onChange={(event) => {
                setActiveProjectId(event.target.value);
                setSelectedChapterId("");
                setSelectedSceneId("");
                setNotice("");
              }}
              aria-label="选择叙事项目"
            >
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIntegrationsOpen(true)}
          >
            <PlugZapIcon className="size-4" />
            MCP · {status?.packaged_skills?.length ?? 3} Skills
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCreateMode("project")}
          >
            <PlusIcon className="size-4" />
            新建项目
          </Button>
          <Button
            size="sm"
            onClick={() => void importEcho()}
            disabled={!workspace || busy}
            className="bg-violet-600 text-white hover:bg-violet-500"
          >
            {busy ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <SparklesIcon className="size-4" />
            )}
            导入 ECHO
          </Button>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-[1720px] flex-1 flex-col p-3 md:p-5">
        {error ? (
          <div className="mb-3 flex items-start justify-between gap-4 rounded-xl border border-red-400/25 bg-red-500/8 px-4 py-3 text-sm text-red-700 dark:text-red-300">
            <span>{error}</span>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() => setError("")}
            >
              <XIcon className="size-3.5" />
            </Button>
          </div>
        ) : null}
        {notice ? (
          <div className="mb-3 flex items-center justify-between gap-4 rounded-xl border border-emerald-400/20 bg-emerald-500/8 px-4 py-2.5 text-sm text-emerald-700 dark:text-emerald-300">
            <span className="flex items-center gap-2">
              <CheckIcon className="size-4" />
              {notice}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() => setNotice("")}
            >
              <XIcon className="size-3.5" />
            </Button>
          </div>
        ) : null}

        {loadingProjects ? (
          <div className="grid flex-1 place-items-center">
            <LoaderCircleIcon className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : !projects.length ? (
          <SurfaceCard className="my-auto">
            <EmptyPane
              icon={LibraryIcon}
              title="建立第一个叙事项目"
              description="项目是题材无关的容器。创建后会自动获得 main 候选分支，你可以从零开始，也可以导入 ECHO 世界资料。"
              action={
                <Button onClick={() => setCreateMode("project")}>
                  <PlusIcon className="size-4" />
                  新建项目
                </Button>
              }
            />
          </SurfaceCard>
        ) : loadingWorkspace || !workspace ? (
          <div className="grid flex-1 place-items-center">
            <LoaderCircleIcon className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="grid min-h-[720px] flex-1 gap-3 xl:grid-cols-[240px_minmax(360px,1fr)_320px] 2xl:grid-cols-[280px_minmax(440px,1fr)_360px]">
            <SurfaceCard className="flex min-h-0 flex-col">
              <div className="border-b border-border/60 px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
                      Story map
                    </p>
                    <h2 className="mt-1 truncate text-sm font-semibold">
                      {activeBranch?.name || "故事结构"}
                    </h2>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    onClick={() => void refreshWorkspace(workspace.project.id)}
                    aria-label="刷新"
                  >
                    <RefreshCwIcon className="size-3.5" />
                  </Button>
                </div>
                <select
                  className="mt-3 h-9 w-full rounded-lg border border-border/70 bg-background px-2.5 text-sm"
                  value={selectedBranchId}
                  onChange={(event) => {
                    setSelectedBranchId(event.target.value);
                    setSelectedChapterId("");
                    setSelectedSceneId("");
                  }}
                >
                  {workspace.branches.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </select>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCreateMode("branch")}
                  >
                    <GitBranchIcon className="size-3.5" />
                    分支
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCreateMode("chapter")}
                    disabled={!selectedBranchId}
                  >
                    <PlusIcon className="size-3.5" />
                    章节
                  </Button>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-auto px-2 py-3">
                {!volumes.length ? (
                  <EmptyPane
                    icon={ScrollTextIcon}
                    title="这个分支还没有章节"
                    description="创建第一章，开始组织卷、章节和场景。"
                    action={
                      <Button
                        size="sm"
                        onClick={() => setCreateMode("chapter")}
                      >
                        <PlusIcon className="size-4" />
                        创建第一章
                      </Button>
                    }
                  />
                ) : (
                  volumes.map((volume) => (
                    <div key={volume.id} className="mb-3">
                      <div className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                        {volume.title}
                      </div>
                      <div className="space-y-1">
                        {volume.chapters.map((chapter) => (
                          <div key={chapter.id}>
                            <button
                              type="button"
                              className={cn(
                                "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/60",
                                selectedChapterId === chapter.id &&
                                  !selectedSceneId &&
                                  "bg-violet-500/10 text-violet-700 dark:text-violet-200",
                              )}
                              onClick={() => {
                                setSelectedChapterId(chapter.id);
                                setSelectedSceneId("");
                              }}
                            >
                              <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
                              <span className="truncate">
                                <span className="mr-2 text-xs text-muted-foreground">
                                  {String(chapter.ordinal).padStart(2, "0")}
                                </span>
                                {chapter.title}
                              </span>
                            </button>
                            {chapter.scenes.length ? (
                              <div className="ml-5 border-l border-border/60 pl-2">
                                {chapter.scenes.map((scene) => (
                                  <button
                                    key={scene.id}
                                    type="button"
                                    className={cn(
                                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted/55 hover:text-foreground",
                                      selectedSceneId === scene.id &&
                                        "bg-violet-500/10 text-violet-700 dark:text-violet-200",
                                    )}
                                    onClick={() => {
                                      setSelectedChapterId(chapter.id);
                                      setSelectedSceneId(scene.id);
                                    }}
                                  >
                                    <CircleDotIcon className="size-2.5 shrink-0" />
                                    <span className="truncate">
                                      场景 {scene.ordinal} · {scene.title}
                                    </span>
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </SurfaceCard>

            <SurfaceCard className="flex min-h-0 flex-col">
              {selectedChapter ? (
                <>
                  <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-3">
                    <div className="mr-auto min-w-0">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>
                          {selectedScene
                            ? `场景 ${selectedScene.ordinal}`
                            : `第 ${selectedChapter.ordinal} 章`}
                        </span>
                        <span>·</span>
                        <CandidateBadge compact />
                      </div>
                      <p className="mt-1 truncate text-sm font-medium">
                        {selectedScene ? "场景编辑器" : "章节编辑器"}
                      </p>
                    </div>
                    {!selectedScene ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setCreateMode("scene")}
                      >
                        <PlusIcon className="size-3.5" />
                        添加场景
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      onClick={() => void saveEditor()}
                      disabled={!editorDirty || saving || !draftTitle.trim()}
                    >
                      {saving ? (
                        <LoaderCircleIcon className="size-4 animate-spin" />
                      ) : (
                        <SaveIcon className="size-4" />
                      )}
                      {editorDirty ? "保存候选稿" : "已保存"}
                    </Button>
                  </div>
                  <ContextPackPreview
                    pack={activeContextPack}
                    loading={actionKey === "context:build"}
                    onBuild={(tokenBudget) =>
                      void buildContextPack(tokenBudget)
                    }
                  />
                  <div className="min-h-0 flex-1 overflow-auto p-4 md:p-6">
                    <Input
                      value={draftTitle}
                      onChange={(event) => setDraftTitle(event.target.value)}
                      className={cn(
                        fieldClass,
                        "h-auto border-0 bg-transparent px-0 text-xl font-semibold shadow-none focus-visible:ring-0 md:text-2xl",
                      )}
                      placeholder="标题"
                    />
                    {selectedScene ? (
                      <div className="mt-4 grid gap-3 md:grid-cols-3">
                        <label className="space-y-1.5 text-xs text-muted-foreground">
                          目标
                          <Input
                            className={fieldClass}
                            value={draftGoal}
                            onChange={(event) =>
                              setDraftGoal(event.target.value)
                            }
                            placeholder="场景要达成什么"
                          />
                        </label>
                        <label className="space-y-1.5 text-xs text-muted-foreground">
                          冲突
                          <Input
                            className={fieldClass}
                            value={draftConflict}
                            onChange={(event) =>
                              setDraftConflict(event.target.value)
                            }
                            placeholder="阻力是什么"
                          />
                        </label>
                        <label className="space-y-1.5 text-xs text-muted-foreground">
                          结果
                          <Input
                            className={fieldClass}
                            value={draftOutcome}
                            onChange={(event) =>
                              setDraftOutcome(event.target.value)
                            }
                            placeholder="状态如何改变"
                          />
                        </label>
                      </div>
                    ) : (
                      <Textarea
                        value={draftSummary}
                        onChange={(event) =>
                          setDraftSummary(event.target.value)
                        }
                        className={cn(
                          fieldClass,
                          "mt-3 min-h-16 resize-none text-sm",
                        )}
                        placeholder="章节摘要：这一章推动了什么？"
                      />
                    )}
                    <Textarea
                      value={draftBody}
                      onChange={(event) => setDraftBody(event.target.value)}
                      className={cn(
                        fieldClass,
                        "mt-4 min-h-[440px] resize-y border-border/50 bg-background/35 font-serif text-[15px] leading-8 md:min-h-[560px]",
                      )}
                      placeholder={
                        selectedScene
                          ? "写下这个场景的候选正文……"
                          : "写下这一章的候选正文……"
                      }
                    />
                  </div>
                </>
              ) : (
                <EmptyPane
                  icon={BookOpenIcon}
                  title="选择或创建章节"
                  description="左侧故事树负责结构，中间编辑器负责正文；所有保存都只进入候选分支。"
                  action={
                    <Button onClick={() => setCreateMode("chapter")}>
                      <PlusIcon className="size-4" />
                      创建章节
                    </Button>
                  }
                />
              )}
            </SurfaceCard>

            <SurfaceCard className="flex min-h-[620px] flex-col xl:min-h-0">
              <StudioInspector
                tab={inspectorTab}
                onTabChange={setInspectorTab}
                project={workspace.project}
                worldPacks={workspace.worldPacks}
                stateChanges={workspace.stateChanges}
                selectedChapter={selectedChapter}
                selectedScene={selectedScene}
                extensions={extensions}
                activeContextPack={activeContextPack}
                loading={loadingExtensions}
                actionKey={actionKey}
                agentActionKey={agentActionKey}
                agentCandidate={agentCandidate}
                agentMessage={agentMessage}
                onRetry={() => void refreshExtensions(workspace.project.id)}
                onImportWorldPack={() => void importEcho()}
                onCreatePipeline={() => void startPipeline()}
                onSubmitStage={(run, stageId) => void submitStage(run, stageId)}
                onRunAgentStage={(run, stageId) =>
                  void runAgentStage(run, stageId)
                }
                onCancelAgentStage={cancelAgentStage}
                onSubmitAgentCandidate={(candidate) =>
                  void submitAgentCandidate(candidate)
                }
                onCreateReview={() => setGovernanceAction({ kind: "review" })}
                onVote={(review, decision) =>
                  setGovernanceAction({ kind: "vote", review, decision })
                }
                onRequestCommit={setCommitReview}
              />
            </SurfaceCard>
          </div>
        )}
      </div>

      {createMode ? (
        <Modal
          title={
            createMode === "project"
              ? "新建叙事项目"
              : createMode === "branch"
                ? "新建候选分支"
                : createMode === "chapter"
                  ? "新建章节"
                  : "新建场景"
          }
          description={
            createMode === "project"
              ? "题材无关的项目容器，可从零创作或导入任意世界包。"
              : "新内容始终写入候选态，不会直接改变正典。"
          }
          onClose={() => setCreateMode(null)}
        >
          <form
            className="space-y-4"
            onSubmit={(event) => void submitCreate(event)}
          >
            {createMode === "project" ? (
              <>
                <label className="block space-y-1.5 text-sm">
                  项目名称
                  <Input
                    autoFocus
                    className={fieldClass}
                    value={projectTitle}
                    onChange={(event) => setProjectTitle(event.target.value)}
                    required
                    maxLength={160}
                    placeholder="例如：陌生人的记忆"
                  />
                </label>
                <label className="block space-y-1.5 text-sm">
                  故事前提
                  <Textarea
                    className={cn(fieldClass, "min-h-24")}
                    value={projectPremise}
                    onChange={(event) => setProjectPremise(event.target.value)}
                    placeholder="一句话说明冲突、主角与代价"
                  />
                </label>
                <label className="block space-y-1.5 text-sm">
                  创作语言
                  <select
                    className="h-10 w-full rounded-lg border border-border/70 bg-background px-3 text-sm"
                    value={projectLanguage}
                    onChange={(event) => setProjectLanguage(event.target.value)}
                  >
                    <option value="zh">中文</option>
                    <option value="en">English</option>
                    <option value="bilingual">中英双语</option>
                  </select>
                </label>
              </>
            ) : createMode === "branch" ? (
              <>
                <label className="block space-y-1.5 text-sm">
                  分支名称
                  <Input
                    autoFocus
                    className={fieldClass}
                    value={branchName}
                    onChange={(event) => setBranchName(event.target.value)}
                    required
                    maxLength={160}
                    placeholder="例如：零没有打开那扇门"
                  />
                </label>
                <label className="block space-y-1.5 text-sm">
                  分支目的
                  <Textarea
                    className={cn(fieldClass, "min-h-20")}
                    value={branchPurpose}
                    onChange={(event) => setBranchPurpose(event.target.value)}
                    placeholder="这个分支要验证什么叙事可能？"
                  />
                </label>
              </>
            ) : (
              <>
                <label className="block space-y-1.5 text-sm">
                  {createMode === "chapter" ? "章节标题" : "场景标题"}
                  <Input
                    autoFocus
                    className={fieldClass}
                    value={newTitle}
                    onChange={(event) => setNewTitle(event.target.value)}
                    required
                    maxLength={240}
                    placeholder={createMode === "chapter" ? "新章节" : "新场景"}
                  />
                </label>
                <label className="block space-y-1.5 text-sm">
                  {createMode === "chapter" ? "章节摘要" : "场景目标"}
                  <Textarea
                    className={cn(fieldClass, "min-h-20")}
                    value={newSummary}
                    onChange={(event) => setNewSummary(event.target.value)}
                    placeholder={
                      createMode === "chapter"
                        ? "本章推进的剧情与状态变化"
                        : "这个场景需要达成什么"
                    }
                  />
                </label>
              </>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateMode(null)}
              >
                取消
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : (
                  <PlusIcon className="size-4" />
                )}
                创建
              </Button>
            </div>
          </form>
        </Modal>
      ) : null}

      {governanceAction ? (
        <GovernanceActionDialog
          title={
            governanceAction.kind === "review"
              ? "确认提交正典审核"
              : governanceAction.decision === "approve"
                ? "确认投出赞成票"
                : "确认投出反对票"
          }
          description={
            governanceAction.kind === "review"
              ? "将为当前候选修订创建真实审核记录；它不会直接进入正典。"
              : `这张票将记录到审核“${governanceAction.review.title}”，提交后由服务端重新计算 quorum 与通过率。`
          }
          confirmLabel={
            governanceAction.kind === "review" ? "确认提交审核" : "确认投票"
          }
          busy={Boolean(actionKey)}
          onClose={() => {
            if (!actionKey) setGovernanceAction(null);
          }}
          onConfirm={() => {
            if (governanceAction.kind === "review") {
              void submitReview();
            } else {
              void castReviewVote(
                governanceAction.review,
                governanceAction.decision,
              );
            }
          }}
        />
      ) : null}

      {integrationsOpen ? (
        <Modal
          title="插件能力"
          description="Narrative Studio 随插件安装、停用和卸载同步管理。"
          onClose={() => setIntegrationsOpen(false)}
        >
          <div className="space-y-4">
            <div className="rounded-xl border border-border/70 bg-muted/20 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    <PlugZapIcon className="size-4 text-violet-500" />
                    Narrative MCP
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    可供其他 Agent 和 MCP
                    客户端读取项目、构建上下文并创建候选章节；直接继承 Echo
                    当前身份与权限，无需再次登录或授权。
                  </p>
                </div>
                <span
                  className={cn(
                    "rounded-full px-2 py-1 text-micro font-medium",
                    status?.mcp?.enabled
                      ? "bg-emerald-500/12 text-emerald-600 dark:text-emerald-300"
                      : "bg-amber-500/12 text-amber-700 dark:text-amber-300",
                  )}
                >
                  {status?.mcp?.enabled ? "已启用" : "等待插件加载"}
                </span>
              </div>
              <code className="mt-3 block overflow-x-auto rounded-lg bg-background/70 px-3 py-2 text-xs text-muted-foreground">
                {status?.mcp?.endpoint || "/api/plugins/narrative-studio/mcp"}
              </code>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(status?.mcp?.tools ?? []).map((tool) => (
                  <span
                    key={tool}
                    className="rounded-md border border-border/60 px-2 py-1 text-micro text-muted-foreground"
                  >
                    {tool}
                  </span>
                ))}
              </div>
            </div>

            <div className="rounded-xl border border-border/70 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <ShieldCheckIcon className="size-4 text-emerald-500" />
                插件自带 Skills
              </div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                安装时注册，停用或卸载时一并撤销；所有写入都只生成候选稿。
              </p>
              <div className="mt-3 grid gap-2">
                {(status?.packaged_skills ?? []).map((skill) => (
                  <div
                    key={skill.name}
                    className="rounded-lg bg-muted/25 px-3 py-2"
                  >
                    <div className="text-xs font-semibold">{skill.name}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {skill.description}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-xl border border-violet-400/20 bg-violet-500/6 px-4 py-3 text-xs leading-5 text-muted-foreground">
              MCP 与 Skills 均不提供直接写入正典、跳过投票或解除审核阻断的能力。
            </div>
          </div>
        </Modal>
      ) : null}

      {commitReview ? (
        <CanonCommitDialog
          review={commitReview}
          busy={actionKey === `commit:${commitReview.id}`}
          onClose={() => {
            if (!actionKey) setCommitReview(null);
          }}
          onConfirm={(actor, rationale) =>
            void confirmCanonCommit(actor, rationale)
          }
        />
      ) : null}

      <div className="pointer-events-none fixed bottom-3 left-1/2 z-30 hidden -translate-x-1/2 items-center gap-2 rounded-full border border-border/70 bg-background/85 px-3 py-1.5 text-[11px] text-muted-foreground shadow-lg backdrop-blur md:flex">
        <span
          className={cn(
            "size-1.5 rounded-full",
            status?.ready ? "bg-emerald-500" : "bg-amber-500",
          )}
        />
        Narrative Engine {status?.version || "0.1"} · candidate only
      </div>
    </div>
  );
}
