"""Workflow seam: model-authored orchestration scripts over subagents.

Port of dsh ``packages/workflow`` (dynamic-workflow capability family).
``engine.WorkflowEngine.start()`` validates synchronously and returns a
holder-owned ``WorkflowRun`` whose ``result`` never rejects; scripts run
in a subprocess worker against a restricted hook vocabulary
(``agent`` / ``phase`` / ``log`` / ``parallel`` / ``pipeline``).
"""

from __future__ import annotations

from .engine import (
    ChildDispatcher,
    WorkflowEngine,
    WorkflowObserver,
    WorkflowRun,
    _default_child_dispatcher,
)
from .meta import validate_meta
from .realm import (
    WORKFLOW_BUILTINS,
    build_globals,
    check_meta_statement,
    materialize_json,
    validate_script,
    wrap_body,
)
from .types import (
    WorkflowAgentEndInfo,
    WorkflowAgentInfo,
    WorkflowAgentOutcome,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowMeta,
    WorkflowPhase,
    WorkflowResult,
    WorkflowResultInfo,
    WorkflowRunId,
    WorkflowRunInfo,
    WorkflowStopReason,
    is_fatal_workflow_error,
)

__all__ = [
    "ChildDispatcher",
    "WORKFLOW_BUILTINS",
    "WorkflowAgentEndInfo",
    "WorkflowAgentInfo",
    "WorkflowAgentOutcome",
    "WorkflowEngine",
    "WorkflowError",
    "WorkflowErrorCode",
    "WorkflowMeta",
    "WorkflowObserver",
    "WorkflowPhase",
    "WorkflowResult",
    "WorkflowResultInfo",
    "WorkflowRun",
    "WorkflowRunId",
    "WorkflowRunInfo",
    "WorkflowStopReason",
    "_default_child_dispatcher",
    "build_globals",
    "check_meta_statement",
    "is_fatal_workflow_error",
    "materialize_json",
    "validate_meta",
    "validate_script",
    "wrap_body",
]
