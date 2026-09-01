from __future__ import annotations

import json
from typing import Any

from ._journal_models import (
    CURRENT_SCHEMA_VERSION,
    AssistantChunkEvent,
    BrowserArtifactEvent,
    BudgetBreakerResetEvent,
    BudgetEvent,
    CurriculumGoalDecisionEvent,
    FileOpEvent,
    FileRollbackEvent,
    GoalChangeEvent,
    HookInvokedEvent,
    HookResultEvent,
    ImmuneEvent,
    JobChangeEvent,
    JournalEvent,
    McpProposalDecisionEvent,
    NodeStartedEvent,
    PreviewRefreshEvent,
    ProtocolDriftDecisionEvent,
    ReactCheckpointEvent,
    ReflexHitEvent,
    SkillProposalDecisionEvent,
    StepEvent,
    SubSessionSummaryEvent,
    SubTextDeltaEvent,
    SubToolEndEvent,
    SubToolStartEvent,
    TaskCheckpointEvent,
    TaskPausedEvent,
    TaskResumedEvent,
    TaskStartedEvent,
    TokenUsageEvent,
    ToolEffectIntentEvent,
    ToolEffectReconciliationEvent,
    TrajectoryEvent,
    UserMessageEvent,
    WorkflowEndEvent,
    WorkflowProgressEvent,
    WorkflowStartEvent,
)

_EVENT_CLASSES: dict[str, type[JournalEvent]] = {
    "step": StepEvent,
    "trajectory": TrajectoryEvent,
    "immune": ImmuneEvent,
    "budget_squirt": BudgetEvent,
    "budget_commit": BudgetEvent,
    "budget_breaker_reset": BudgetBreakerResetEvent,
    "task_started": TaskStartedEvent,
    "node_started": NodeStartedEvent,
    "task_checkpoint": TaskCheckpointEvent,
    "react_checkpoint": ReactCheckpointEvent,
    "tool_effect_intent": ToolEffectIntentEvent,
    "tool_effect_reconciliation": ToolEffectReconciliationEvent,
    "task_paused": TaskPausedEvent,
    "task_resumed": TaskResumedEvent,
    "token_usage": TokenUsageEvent,
    "reflex_hit": ReflexHitEvent,
    "file_op": FileOpEvent,
    "preview_refresh": PreviewRefreshEvent,
    "skill_proposal_decision": SkillProposalDecisionEvent,
    "curriculum_goal_decision": CurriculumGoalDecisionEvent,
    "mcp_proposal_decision": McpProposalDecisionEvent,
    "protocol_drift_decision": ProtocolDriftDecisionEvent,
    "file_rollback": FileRollbackEvent,
    "goal_change": GoalChangeEvent,
    "user/message": UserMessageEvent,
    "assistant/chunk": AssistantChunkEvent,
    "hook/invoked": HookInvokedEvent,
    "hook/result": HookResultEvent,
    "sub_tool_start": SubToolStartEvent,
    "sub_tool_end": SubToolEndEvent,
    "sub_text_delta": SubTextDeltaEvent,
    "sub_session_summary": SubSessionSummaryEvent,
    "browser_artifact": BrowserArtifactEvent,
    "workflow/start": WorkflowStartEvent,
    "workflow/progress": WorkflowProgressEvent,
    "workflow/end": WorkflowEndEvent,
    "job/change": JobChangeEvent,
}


# Migrations · key = schema_version the event was written at;
# value = function that mutates the dict in place, moving it ONE version
# forward. For an event at v1, chain: v1 → v2 → v3 → ... → CURRENT.
# Today there's no v2 yet — so this dict is empty. When we add v2,
# register `_EVENT_MIGRATIONS[1] = migrate_v1_to_v2`.
_EVENT_MIGRATIONS: dict[int, Any] = {}


def _migrate_event(data: dict) -> dict:
    """Walk ``data`` forward through ``_EVENT_MIGRATIONS`` until it's at
    ``CURRENT_SCHEMA_VERSION``. Events without a version field are
    assumed to be v1 (the version this mechanism was introduced at).
    """
    version = int(data.get("schema_version") or 1)
    while version < CURRENT_SCHEMA_VERSION:
        migrator = _EVENT_MIGRATIONS.get(version)
        if migrator is None:
            # No migration path — write through the current version
            # and let pydantic decide if it still validates.
            break
        data = migrator(data)
        version += 1
    data["schema_version"] = version
    return data


def _parse_event_data(data: dict) -> JournalEvent:
    """Validate one decoded event dict (migration + class resolution).

    Shared by ``_parse_event`` (JSONL lines) and the chunk-row
    expansion path, so packed storage rows decode through the exact
    same validation as verbatim lines.
    """
    data = _migrate_event(dict(data))
    event_type = data.get("event_type")
    cls = (
        _EVENT_CLASSES.get(event_type, JournalEvent)
        if isinstance(event_type, str)
        else JournalEvent
    )
    return cls.model_validate(data)


def _parse_event(line: str) -> JournalEvent:
    return _parse_event_data(json.loads(line))
