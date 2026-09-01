import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ParallelApi from "@/core/parallel-agents/api";
import type {
  BatchResult,
  BatchRecoverySnapshot,
  OrchestratorStatus,
  TaskResult,
} from "@/core/parallel-agents/api";
import { renderWithProviders } from "@/test/harness";

import { ParallelAgentsPanel } from "./parallel-agents-panel";

const fetchStatusMock = vi.fn();
const fetchBatchMock = vi.fn();
const fetchRecoverySnapshotMock = vi.fn();

vi.mock("@/core/parallel-agents/api", async () => {
  const actual = await vi.importActual<typeof ParallelApi>(
    "@/core/parallel-agents/api",
  );
  return {
    ...actual,
    fetchOrchestratorStatus: (...args: unknown[]) => fetchStatusMock(...args),
    fetchBatch: (...args: unknown[]) => fetchBatchMock(...args),
    fetchBatchRecoverySnapshot: (...args: unknown[]) =>
      fetchRecoverySnapshotMock(...args),
    cancelAll: vi.fn(),
    cancelTask: vi.fn(),
  };
});

function orchestratorStatus(
  overrides: Partial<OrchestratorStatus> = {},
): OrchestratorStatus {
  return {
    active_count: 0,
    pending_count: 0,
    completed_count: 0,
    failed_count: 0,
    cancelled_count: 0,
    max_concurrency: 8,
    batches: {},
    ...overrides,
  };
}

function task(overrides: Partial<TaskResult> = {}): TaskResult {
  return {
    task_id: "task_1",
    batch_id: "batch_1",
    description: "Run one worker",
    status: "completed",
    result: "ok",
    error: null,
    started_at: null,
    completed_at: null,
    duration_seconds: null,
    subagent_name: "coder",
    work_contract: null,
    ...overrides,
  };
}

function batch(overrides: Partial<BatchResult> = {}): BatchResult {
  return {
    batch_id: "batch_1",
    status: "completed",
    total_tasks: 1,
    completed_tasks: 1,
    failed_tasks: 0,
    cancelled_tasks: 0,
    created_at: null,
    completed_at: null,
    results: [task()],
    aggregated_content: null,
    aggregation_strategy: "concat",
    conflicts: [],
    event_log: [],
    plan: null,
    ...overrides,
  };
}

function recoverySnapshot(
  overrides: Partial<BatchRecoverySnapshot> = {},
): BatchRecoverySnapshot {
  return {
    schema: "echo.parallel_batch_recovery_snapshot.v1",
    batch_id: "batch_1",
    status: "partial",
    terminal: true,
    resume_available: true,
    created_at: null,
    completed_at: null,
    task_count: 2,
    completed_tasks: 1,
    failed_tasks: 1,
    cancelled_tasks: 0,
    running_tasks: 0,
    pending_tasks: 0,
    tasks: [],
    dag: {},
    event_sequence: { last_sequence: 9 },
    artifact_paths: [],
    conflicts: [],
    completion_receipt: { ready: false },
    file_write_observability: {},
    recovery_hints: {
      rerunnable_task_ids: ["task_failed", "task_blocked"],
      failed_task_ids: ["task_failed"],
      blocked_by_dependency: ["task_blocked"],
      checkpoint: { batch_id: "batch_1", after_sequence: 9 },
    },
    safety: {
      raw_subagent_outputs_included: false,
      event_payloads_included: false,
      owner_id_included: false,
    },
    ...overrides,
  };
}

describe("<ParallelAgentsPanel />", () => {
  beforeEach(() => {
    fetchStatusMock.mockReset();
    fetchBatchMock.mockReset();
    fetchRecoverySnapshotMock.mockReset();
    fetchRecoverySnapshotMock.mockResolvedValue(null);
  });

  it("loads a terminal batch instead of hiding it behind the empty state", async () => {
    fetchStatusMock.mockResolvedValue(
      orchestratorStatus({
        failed_count: 1,
        batches: { batch_timeout: "timed_out" },
      }),
    );
    fetchBatchMock.mockResolvedValue(
      batch({
        batch_id: "batch_timeout",
        status: "timed_out",
        completed_tasks: 0,
        failed_tasks: 1,
        results: [
          task({
            batch_id: "batch_timeout",
            status: "timed_out",
            result: null,
            error: "deadline exceeded",
          }),
        ],
      }),
    );

    renderWithProviders(<ParallelAgentsPanel />);

    await waitFor(() =>
      expect(fetchBatchMock).toHaveBeenCalledWith("batch_timeout"),
    );
    expect(await screen.findByText("deadline exceeded")).toBeInTheDocument();
    expect(screen.getByText("Timed Out")).toBeInTheDocument();
    expect(screen.queryByText("No parallel tasks")).not.toBeInTheDocument();
  });

  it("shows recovery snapshot evidence for failed batches", async () => {
    fetchStatusMock.mockResolvedValue(
      orchestratorStatus({
        failed_count: 1,
        batches: { batch_failed: "partial" },
      }),
    );
    fetchBatchMock.mockResolvedValue(
      batch({
        batch_id: "batch_failed",
        status: "partial",
        completed_tasks: 1,
        failed_tasks: 1,
        results: [
          task({ task_id: "task_ok", result: "done" }),
          task({
            task_id: "task_failed",
            status: "failed",
            result: null,
            error: "runner exploded",
          }),
        ],
      }),
    );
    fetchRecoverySnapshotMock.mockResolvedValue(
      recoverySnapshot({ batch_id: "batch_failed" }),
    );

    renderWithProviders(<ParallelAgentsPanel />);

    await waitFor(() =>
      expect(fetchRecoverySnapshotMock).toHaveBeenCalledWith("batch_failed"),
    );
    const recoveryTitle = await screen.findByText("Recovery snapshot");
    const recoveryNotice = recoveryTitle.closest("div")?.parentElement;
    expect(recoveryNotice).not.toBeNull();
    const notice = within(recoveryNotice!);
    expect(notice.getByText("2 rerunnable task(s)")).toBeInTheDocument();
    expect(notice.getByText("1 failed")).toBeInTheDocument();
    expect(notice.getByText("1 dependency-blocked")).toBeInTheDocument();
    expect(notice.getByText("checkpoint #9")).toBeInTheDocument();
    expect(notice.getByText("redacted")).toBeInTheDocument();
  });

  it("still prefers a running batch when terminal history also exists", async () => {
    fetchStatusMock.mockResolvedValue(
      orchestratorStatus({
        active_count: 1,
        failed_count: 1,
        batches: {
          batch_failed: "failed",
          batch_running: "running",
        },
      }),
    );
    fetchBatchMock.mockResolvedValue(
      batch({
        batch_id: "batch_running",
        status: "running",
        completed_tasks: 0,
        results: [
          task({
            batch_id: "batch_running",
            status: "running",
            result: null,
          }),
        ],
      }),
    );

    renderWithProviders(<ParallelAgentsPanel />);

    await waitFor(() =>
      expect(fetchBatchMock).toHaveBeenCalledWith("batch_running"),
    );
    expect(fetchBatchMock).not.toHaveBeenCalledWith("batch_failed");
    expect(fetchRecoverySnapshotMock).not.toHaveBeenCalled();
  });
});
