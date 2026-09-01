import { useMemo, useRef } from "react";

import type {
  EvidenceReference,
  WorkbenchSnapshotV2,
} from "@/core/realtime/items";
import {
  businessAgentPhaseKey,
  deriveAgentPhases,
  normalizeAgentPhaseTitle,
  normalizeBusinessPhaseKey,
  type AgentPhase,
} from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import {
  normalizeEventsForSettledDisplay,
  pickCurrentWorkBlock,
  type WorkBlock,
} from "./work-blocks";
import {
  type AgentTile,
  type AgentWorkbenchTabId,
  type DiffEntry,
  agentEventGroupId,
  diffEntriesFromBlocks,
  inferWorkbenchCwd,
  workspaceFocusTabFromEvents,
} from "./agent-workbench-utils";
import { IncrementalSnapshotCalculator } from "./incremental-snapshot-calculator";

export type AgentWorkbenchSnapshotOptions = {
  deriveAgentTiles: (events: LiveToolEvent[]) => AgentTile[];
  hasAnswer?: boolean;
  isLoading?: boolean;
  paused?: boolean;
  runFailed?: boolean;
  runSettled?: boolean;
  workDir?: string;
};

export type AgentWorkbenchSnapshot = {
  agentTiles: AgentTile[];
  blocks: WorkBlock[];
  currentBlock: WorkBlock | null;
  currentPhase: AgentPhase | null;
  deferOutputSurfaces: boolean;
  diffEntries: DiffEntry[];
  displayEvents: LiveToolEvent[];
  evidence: EvidenceReference[];
  fingerprint: string;
  focusedTab: AgentWorkbenchTabId | null;
  hasContent: boolean;
  inferredWorkDir?: string;
  phases: AgentPhase[];
  version: number;
  visibleDiffEntries: DiffEntry[];
};

export type ScreenFrameSnapshot = {
  block: WorkBlock | null;
  index: number;
  total: number;
};

export function useAgentWorkbenchSnapshot(
  events: LiveToolEvent[],
  options: AgentWorkbenchSnapshotOptions,
): AgentWorkbenchSnapshot {
  const previousRef = useRef<AgentWorkbenchSnapshot | null>(null);
  const calculatorRef = useRef<IncrementalSnapshotCalculator | null>(null);

  // 特性开关：启用增量计算
  const useIncremental = typeof window !== "undefined" &&
    localStorage.getItem("echo:incremental-snapshot") === "1";

  const {
    deriveAgentTiles,
    hasAnswer,
    isLoading,
    paused,
    runFailed,
    runSettled,
    workDir,
  } = options;

  return useMemo(() => {
    let candidate: AgentWorkbenchSnapshot;

    if (useIncremental) {
      // 增量计算路径
      if (!calculatorRef.current) {
        calculatorRef.current = new IncrementalSnapshotCalculator();
      }
      candidate = calculatorRef.current.compute(events, {
        deriveAgentTiles,
        hasAnswer,
        isLoading,
        paused,
        runFailed,
        runSettled,
        workDir,
      });
    } else {
      // 全量计算路径（原始逻辑）
      candidate = buildAgentWorkbenchSnapshot(events, {
        deriveAgentTiles,
        hasAnswer,
        isLoading,
        paused,
        runFailed,
        runSettled,
        workDir,
      });
    }

    const previous = previousRef.current;
    if (previous && previous.fingerprint === candidate.fingerprint) {
      return previous;
    }
    const next = {
      ...candidate,
      version: previous ? previous.version + 1 : 1,
    };
    previousRef.current = next;
    return next;
  }, [
    events,
    deriveAgentTiles,
    hasAnswer,
    isLoading,
    paused,
    runFailed,
    runSettled,
    workDir,
    useIncremental,
  ]);
}

export function buildAgentWorkbenchSnapshot(
  events: LiveToolEvent[],
  options: AgentWorkbenchSnapshotOptions,
): AgentWorkbenchSnapshot {
  const displayEvents = normalizeEventsForSettledDisplay(events, options);
  const derived = deriveAgentPhases(displayEvents, options);
  const blocks = derived.blocks;
  const serverSnapshot = latestServerWorkbenchSnapshot(displayEvents);
  const observedPhaseBlockIds = observedPhaseBlockIdsFromSnapshots(
    displayEvents,
    blocks,
  );
  const phases = reconcileTodoPhases(
    serverSnapshotToAgentPhases(
      serverSnapshot,
      blocks,
      options,
      observedPhaseBlockIds,
    ),
    derived.phases,
  );
  const optimisticPhase =
    phases.length === 0 && isLiveRunInProgress(options)
      ? optimisticPlanningPhase()
      : null;
  const visiblePhases = optimisticPhase ? [optimisticPhase] : phases;
  const currentPhase =
    currentPhaseFromServerSnapshot(serverSnapshot, visiblePhases, options) ??
    derived.currentPhase ??
    optimisticPhase;
  const currentBlocks = currentPhase
    ? blocks.filter((block) => currentPhase.blockIds.includes(block.id))
    : blocks;
  const currentBlock =
    currentBlockFromServerSnapshot(serverSnapshot, blocks) ??
    pickCurrentWorkBlock(currentBlocks) ??
    currentBlocks[0] ??
    null;
  const diffEntries = diffEntriesFromBlocks(blocks);
  const deferOutputSurfaces =
    !options.hasAnswer &&
    !options.runSettled &&
    !options.runFailed &&
    !options.paused;
  const visibleDiffEntries = deferOutputSurfaces ? [] : diffEntries;
  const snapshot: AgentWorkbenchSnapshot = {
    agentTiles: options.deriveAgentTiles(displayEvents),
    blocks,
    currentBlock,
    currentPhase,
    deferOutputSurfaces,
    diffEntries,
    displayEvents,
    evidence: serverSnapshot?.evidence ?? [],
    fingerprint: "",
    focusedTab:
      tabFromServerWorkbenchSnapshot(serverSnapshot) ??
      workspaceFocusTabFromEvents(displayEvents),
    hasContent: Boolean(
      currentPhase && visiblePhases.length > 0 && blocks.length > 0,
    ),
    inferredWorkDir: inferWorkbenchCwd(blocks, options.workDir),
    phases: visiblePhases,
    version: 0,
    visibleDiffEntries,
  };
  return {
    ...snapshot,
    fingerprint: fingerprintWorkbenchSnapshot(snapshot, options),
  };
}

/**
 * The todo_write payload is the state shown by the inline checklist and is
 * updated more frequently than the optional workbench snapshot embedded in
 * the same event stream. Keep the snapshot's block associations, but let the
 * newest todo payload own item order and status so the two surfaces cannot
 * disagree (for example inline 4/7 complete while the workbench still says
 * 0/7).
 */
function reconcileTodoPhases(
  serverPhases: AgentPhase[] | null,
  derivedPhases: AgentPhase[],
): AgentPhase[] {
  if (!serverPhases) return derivedPhases;
  if (
    derivedPhases.length === 0 ||
    !derivedPhases.every((phase) => phase.id.startsWith("todo-phase:"))
  ) {
    return serverPhases;
  }

  const unusedServerPhases = new Set(serverPhases);
  return derivedPhases.map((phase, index) => {
    const normalizedTitle = normalizeAgentPhaseTitle(
      phase.title,
    ).toLocaleLowerCase();
    const matchingServerPhase =
      serverPhases.find((candidate) => candidate.id === phase.id) ??
      serverPhases.find(
        (candidate) =>
          unusedServerPhases.has(candidate) &&
          normalizeAgentPhaseTitle(candidate.title).toLocaleLowerCase() ===
            normalizedTitle,
      ) ??
      serverPhases[index];
    if (matchingServerPhase) unusedServerPhases.delete(matchingServerPhase);
    return {
      ...matchingServerPhase,
      ...phase,
      detail: phase.detail ?? matchingServerPhase?.detail,
      blockIds: uniqueBlockIds([
        ...(matchingServerPhase?.blockIds ?? []),
        ...phase.blockIds,
      ]),
    };
  });
}

function isLiveRunInProgress(options: AgentWorkbenchSnapshotOptions): boolean {
  return Boolean(
    options.isLoading &&
    !options.runSettled &&
    !options.runFailed &&
    !options.paused &&
    !options.hasAnswer,
  );
}

function optimisticPlanningPhase(): AgentPhase {
  return {
    id: "optimistic:planning",
    title: "规划中",
    businessKey: "planning",
    status: "running",
    blockIds: [],
  };
}

function latestServerWorkbenchSnapshot(
  events: LiveToolEvent[],
): WorkbenchSnapshotV2 | null {
  for (const event of [...events].reverse()) {
    const snapshot = event.input?.workbenchSnapshot;
    if (isWorkbenchSnapshotV2(snapshot)) {
      return snapshot;
    }
  }
  return null;
}

function serverSnapshotToAgentPhases(
  snapshot: WorkbenchSnapshotV2 | null,
  blocks: WorkBlock[],
  options: AgentWorkbenchSnapshotOptions,
  observedPhaseBlockIds: Map<string, string[]> = new Map(),
): AgentPhase[] | null {
  if (!snapshot || snapshot.phases.length === 0) return null;
  const blockIds = new Set(blocks.map((block) => block.id));
  const acceptsCurrentItem = snapshotHasActionableCurrentItem(snapshot);
  return snapshot.phases.map((phase, index) => {
    const activeItemId =
      phase.activeItemId ??
      (acceptsCurrentItem && phase.id === snapshot.currentPhaseId
        ? snapshot.currentItemId
        : null);
    const activeBlockIds =
      activeItemId && blockIds.has(activeItemId)
        ? [activeItemId]
        : phase.status === "running" || phase.status === "waiting_approval"
          ? blocks
              .filter(
                (block) =>
                  block.status === "running" ||
                  block.status === "waiting_approval",
              )
              .map((block) => block.id)
          : [];
    const observedBlockIds = observedPhaseBlockIds
      .get(phase.id)
      ?.filter((blockId) => blockIds.has(blockId));
    return {
      id: phase.id || `server-phase:${index}`,
      title: normalizeAgentPhaseTitle(phase.title),
      businessKey:
        normalizeBusinessPhaseKey(phase.phaseKind) ??
        businessAgentPhaseKey(phase.title) ??
        undefined,
      detail: phase.detail ?? undefined,
      status: serverPhaseStatus(phase.status, options),
      blockIds: uniqueBlockIds([
        ...(observedBlockIds ?? []),
        ...activeBlockIds,
      ]),
    };
  });
}

function observedPhaseBlockIdsFromSnapshots(
  events: LiveToolEvent[],
  blocks: WorkBlock[],
): Map<string, string[]> {
  const blockIds = new Set(blocks.map((block) => block.id));
  const byPhase = new Map<string, string[]>();
  for (const event of events) {
    const snapshot = event.input?.workbenchSnapshot;
    if (!isWorkbenchSnapshotV2(snapshot)) continue;
    for (const phase of snapshot.phases) {
      if (phase.activeItemId && blockIds.has(phase.activeItemId)) {
        appendPhaseBlockId(byPhase, phase.id, phase.activeItemId);
      }
    }
    if (
      snapshot.currentPhaseId &&
      snapshot.currentItemId &&
      snapshotHasActionableCurrentItem(snapshot) &&
      blockIds.has(snapshot.currentItemId)
    ) {
      appendPhaseBlockId(
        byPhase,
        snapshot.currentPhaseId,
        snapshot.currentItemId,
      );
    }
  }
  return new Map(
    Array.from(byPhase.entries()).map(([phaseId, phaseBlockIds]) => [
      phaseId,
      uniqueBlockIds(phaseBlockIds),
    ]),
  );
}

function appendPhaseBlockId(
  byPhase: Map<string, string[]>,
  phaseId: string,
  blockId: string,
) {
  const existing = byPhase.get(phaseId);
  if (existing) existing.push(blockId);
  else byPhase.set(phaseId, [blockId]);
}

function uniqueBlockIds(blockIds: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const blockId of blockIds) {
    if (seen.has(blockId)) continue;
    seen.add(blockId);
    result.push(blockId);
  }
  return result;
}

function currentPhaseFromServerSnapshot(
  snapshot: WorkbenchSnapshotV2 | null,
  phases: AgentPhase[],
  options: AgentWorkbenchSnapshotOptions,
): AgentPhase | null {
  if (!snapshot || phases.length === 0) return null;
  if (
    options.runSettled &&
    options.hasAnswer &&
    !options.runFailed &&
    phases.every((phase) => phase.status === "done")
  ) {
    return phases[phases.length - 1] ?? null;
  }
  if (snapshot.currentPhaseId) {
    const explicit = phases.find(
      (phase) => phase.id === snapshot.currentPhaseId,
    );
    if (explicit) return explicit;
  }
  return (
    phases.find((phase) => phase.status === "waiting_approval") ??
    phases.find((phase) => phase.status === "running") ??
    phases.find((phase) => phase.status === "error") ??
    phases.find((phase) => phase.status === "pending") ??
    phases[phases.length - 1] ??
    null
  );
}

function currentBlockFromServerSnapshot(
  snapshot: WorkbenchSnapshotV2 | null,
  blocks: WorkBlock[],
): WorkBlock | null {
  if (!snapshot?.currentItemId || !snapshotHasActionableCurrentItem(snapshot)) {
    return null;
  }
  return blocks.find((block) => block.id === snapshot.currentItemId) ?? null;
}

function snapshotHasActionableCurrentItem(
  snapshot: WorkbenchSnapshotV2,
): boolean {
  return ["running", "waiting_approval", "error"].includes(snapshot.status);
}

function tabFromServerWorkbenchSnapshot(
  snapshot: WorkbenchSnapshotV2 | null,
): AgentWorkbenchTabId | null {
  const view = snapshot?.workspaceFocus?.view;
  if (!view) return null;
  if (view === "browser") return null;
  if (view === "terminal") return "terminal";
  if (view === "diff") return "diff";
  if (view === "file") return "agent";
  if (view === "artifact" || view === "image") return "agent";
  if (view === "subagent") return "subagents";
  return "agent";
}

function serverPhaseStatus(
  status: WorkbenchSnapshotV2["phases"][number]["status"],
  _options: AgentWorkbenchSnapshotOptions,
): AgentPhase["status"] {
  if (status === "done") return "done";
  if (status === "error") return "error";
  // Workbench snapshots already carry the authoritative task state. A final
  // answer must not manufacture completion for pending/running plan rows.
  if (status === "waiting_approval") return "waiting_approval";
  if (status === "running") return "running";
  return "pending";
}

function isWorkbenchSnapshotV2(value: unknown): value is WorkbenchSnapshotV2 {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const record = value as Partial<WorkbenchSnapshotV2>;
  return (
    record.schemaVersion === 2 &&
    typeof record.version === "number" &&
    Array.isArray(record.phases)
  );
}

export function screenBlocksForAgent(
  blocks: WorkBlock[],
  agentId: string | null | undefined,
): WorkBlock[] {
  if (agentId) {
    return blocks.filter((block) => agentEventGroupId(block.event) === agentId);
  }
  return blocks.filter((block) => {
    const groupId = agentEventGroupId(block.event);
    return !groupId || groupId === "__main__";
  });
}

export function currentScreenFrame(
  blocks: WorkBlock[],
  selectedBlockId?: string | null,
): ScreenFrameSnapshot {
  const selected =
    selectedBlockId &&
    blocks.find(
      (block) => block.id === selectedBlockId && !isAgentLifecycleBlock(block),
    );
  const block =
    selected ||
    [...blocks]
      .reverse()
      .find(
        (candidate) =>
          !isAgentLifecycleBlock(candidate) &&
          (candidate.status === "running" ||
            candidate.status === "waiting_approval"),
      ) ||
    [...blocks]
      .reverse()
      .find((candidate) => !isAgentLifecycleBlock(candidate)) ||
    pickCurrentWorkBlock(blocks) ||
    blocks[blocks.length - 1] ||
    null;
  return {
    block,
    index: block
      ? Math.max(
          0,
          blocks.findIndex((item) => item.id === block.id),
        )
      : -1,
    total: blocks.length,
  };
}

function isAgentLifecycleBlock(block: WorkBlock): boolean {
  return Boolean(
    block.event.lifecycle ||
    /subagent_(spawned|finished)/i.test(block.event.name),
  );
}

function fingerprintWorkbenchSnapshot(
  snapshot: AgentWorkbenchSnapshot,
  options: AgentWorkbenchSnapshotOptions,
): string {
  return stableStringify({
    agentTiles: snapshot.agentTiles.map((agent) => [
      agent.id,
      agent.status,
      agent.currentTool,
      agent.lastThought,
      agent.eventCount,
      agent.finishedAt,
    ]),
    blocks: snapshot.blocks.map((block) => [
      block.id,
      block.kind,
      block.status,
      block.actionKey,
      JSON.stringify(block.title),
      block.subtitle,
      block.startedAt,
      block.event.finishedAt,
      block.event.durationMs,
      block.event.lifecycle,
      compactUnknown(block.event.input),
      compactUnknown(block.event.output),
    ]),
    currentBlockId: snapshot.currentBlock?.id ?? null,
    currentPhaseId: snapshot.currentPhase?.id ?? null,
    deferOutputSurfaces: snapshot.deferOutputSurfaces,
    diffEntries: snapshot.visibleDiffEntries.map((entry) => [
      entry.id,
      entry.path,
      entry.created,
      entry.status,
    ]),
    flags: {
      hasAnswer: Boolean(options.hasAnswer),
      paused: Boolean(options.paused),
      runFailed: Boolean(options.runFailed),
      runSettled: Boolean(options.runSettled),
      workDir: options.workDir ?? "",
    },
    focusedTab: snapshot.focusedTab,
    evidence: snapshot.evidence.map((item) => [
      item.id,
      item.kind,
      item.title,
      item.uri ?? "",
      item.status,
      item.sourceItemId ?? "",
      item.phaseId ?? "",
    ]),
    phases: snapshot.phases.map((phase) => [
      phase.id,
      phase.title,
      phase.businessKey ?? "",
      phase.status,
      phase.blockIds,
    ]),
  });
}

// Object payload identities are stable across streaming frames (the realtime
// adapter memoizes LiveToolEvent input/output), so cache their compact
// fingerprint per identity instead of re-running the full stableStringify on
// every delta frame. Strings keep the cheap slice fast path below.
const compactObjectCache = new WeakMap<object, string>();

function compactUnknown(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") {
    return value.length <= 500 ? value : value.slice(0, 500);
  }
  const cacheKey = typeof value === "object" ? (value as object) : null;
  if (cacheKey) {
    const cached = compactObjectCache.get(cacheKey);
    if (cached !== undefined) return cached;
  }
  try {
    const text = stableStringify(value);
    const compact = text.length <= 500 ? text : text.slice(0, 500);
    if (cacheKey) compactObjectCache.set(cacheKey, compact);
    return compact;
  } catch {
    return String(value).slice(0, 500);
  }
}

function stableStringify(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (!value || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(sortValue);
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, entry]) => [key, sortValue(entry)]),
  );
}
