"""Method names — the verbs of the realtime protocol.

Centralizing these as a closed set avoids string typos and makes the
contract grep-friendly. Adding a method means adding a constant here
*and* a handler/emitter elsewhere; one without the other is a bug.
"""

from __future__ import annotations

from enum import StrEnum


class ClientMethod(StrEnum):
    """Methods clients invoke on the server.

    Each value is the JSON-RPC ``method`` string sent over the wire.
    """

    # Thread lifecycle
    THREAD_START = "thread/start"
    THREAD_RESUME = "thread/resume"
    THREAD_EVENTS = "thread/events"
    THREAD_READ = "thread/read"
    THREAD_LIST = "thread/list"
    THREAD_ARCHIVE = "thread/archive"

    # Turn control
    TURN_START = "turn/start"
    TURN_STEER = "turn/steer"
    TURN_INTERRUPT = "turn/interrupt"

    # Apply-patch hunk-level decisions (client-initiated accept/reject
    # on a single hunk after the FileChange item has completed).
    FILE_CHANGE_HUNK_DECIDE = "item/fileChange/hunkDecide"

    # Skills / models / config
    SKILLS_LIST = "skills/list"
    MODEL_LIST = "model/list"
    CONFIG_READ = "config/read"

    # MCP integration
    MCP_SERVER_LIST = "mcpServer/list"
    MCP_TOOL_CALL = "mcpServer/tool/call"


class ServerMethod(StrEnum):
    """Methods the server pushes to the client.

    Notifications are stateless event broadcasts. Requests demand a
    client-side reply (used for human-in-the-loop checks).
    """

    # ── Notifications (no reply expected) ────────────────────

    # Thread lifecycle
    THREAD_STARTED = "thread/started"
    THREAD_STATUS_CHANGED = "thread/status/changed"
    THREAD_TOKEN_USAGE_UPDATED = "thread/tokenUsage/updated"
    # Compaction rewrote the visible turn set: ``supersededTurnIds`` were
    # summarised into ``summaryTurn``. Emitted alongside the legacy
    # ``thread/status/changed`` compaction marker so reducer-capable
    # clients can apply the rewrite live instead of waiting for the next
    # resume. Mirrors the persisted ``turn_compacted`` log event.
    TURN_COMPACTED = "turn/compacted"

    # Turn lifecycle
    TURN_STARTED = "turn/started"
    TURN_COMPLETED = "turn/completed"
    # Cancellation: the active turn was interrupted (user-initiated or
    # safety-triggered). Reducer handles this to stop the current
    # streaming spinner and mark the turn as cancelled.
    TURN_INTERRUPTED = "turn/interrupted"
    TURN_DIFF_UPDATED = "turn/diff/updated"
    # Emitted by ``_ReactBridgeState._emit_turn_update`` on every tool
    # lifecycle tick, carrying ``phases`` + ``workbenchSnapshot``. The
    # phases themselves are still *derived* — ``_phases_from_todo_preview``
    # reads the agent's ``todo_write`` input server-side — so a turn that
    # never calls ``todo_write`` has no plan to send, and the right-hand
    # workbench stays empty. Making the plan unconditional (rather than a
    # by-product of one tool) is what a plan-first workbench would need.
    TURN_PLAN_UPDATED = "turn/plan/updated"
    # Versioned current-frame payload for the right-side workbench. This
    # keeps realtime and replay rendering on one source of truth instead
    # of rebuilding "current state" from the full historical item stack.
    WORKBENCH_SNAPSHOT = "workbench/snapshot"
    # Lightweight keepalive for long-running swarm/cluster roles.
    # Emitted every 15s by TeamRunner's heartbeat thread so the
    # frontend's pong-timeout (70s) never kills the WS during
    # roles that don't produce text deltas.
    TURN_HEARTBEAT = "turn/heartbeat"
    # Soft hand-off hint: when the user's prompt strongly matches
    # a 能力包 / Meta-Skill (via ``match_meta_skill``), the runtime
    # emits this BEFORE ReAct kicks in. The frontend renders a
    # dismissible chip that links to /workspace/meta-skills?q=…;
    # the ReAct loop continues normally so the user gets an answer
    # even if they don't follow the link. No execution rewire —
    # this is informational only until the graph runtime is wired
    # through realtime gateway.
    TURN_META_SKILL_HINT = "turn/metaSkill/hint"
    # Codebase grounding: when a code/project turn retrieves relevant wiki
    # pages + source chunks and folds them into the prompt, the runtime emits
    # this with the consulted sources. The frontend bridges it onto the AI
    # message's ``additional_kwargs.grounding`` and renders a plain-language
    # "consulted N project docs" chip. Informational — grounding already
    # happened; this just makes it visible.
    TURN_GROUNDING = "turn/grounding"

    # Workflow lifecycle
    # Emitted when a workflow (multi-agent orchestration) completes, whether
    # successfully or with an error. Payload includes workflow metadata, final
    # status, agent count, and run ID. Frontend can show a notification and/or
    # update the workbench state.
    WORKFLOW_COMPLETED = "workflow/completed"

    # Generic item lifecycle
    ITEM_STARTED = "item/started"
    ITEM_COMPLETED = "item/completed"

    # Item content streams
    ITEM_AGENT_MESSAGE_DELTA = "item/agentMessage/delta"
    ITEM_REASONING_TEXT_DELTA = "item/reasoning/textDelta"
    # Genuinely reserved: nothing emits this today (unlike
    # ``TURN_PLAN_UPDATED`` above, which is live). It would carry the plan
    # as a first-class streaming item instead of a per-tick snapshot. The
    # reducer already has a handler, so wiring it up is a backend-only
    # change.
    ITEM_PLAN_DELTA = "item/plan/delta"
    ITEM_COMMAND_OUTPUT_DELTA = "item/commandExecution/outputDelta"
    # ``ITEM_FILE_CHANGE_OUTPUT_DELTA`` is reserved but not currently
    # emitted. ``ITEM_FILE_CHANGE_HUNK_DELTA`` IS emitted (see
    # realtime_event_bridge.py) — file-edit streaming surfaces incremental
    # hunks today; only the whole-file output-delta variant is unwired.
    ITEM_FILE_CHANGE_OUTPUT_DELTA = "item/fileChange/outputDelta"
    ITEM_FILE_CHANGE_HUNK_DELTA = "item/fileChange/hunkDelta"
    ITEM_FILE_CHANGE_HUNK_DECISION = "item/fileChange/hunkDecision"
    # ``ITEM_MCP_TOOL_CALL_PROGRESS`` is reserved but not currently
    # emitted — MCP long-running tools surface a static spinner.
    ITEM_MCP_TOOL_CALL_PROGRESS = "item/mcpToolCall/progress"

    # Errors / model events
    ERROR = "error"
    # ``MODEL_REROUTED`` is reserved but not currently emitted/consumed —
    # intended for when smart routing falls back from the requested model to an
    # available alternative, so the UI can surface an inline notice. Wiring
    # needs backend emission + a reducer handler + UI rendering.
    MODEL_REROUTED = "model/rerouted"

    # ── Server-initiated requests (client must reply) ────────

    REQ_COMMAND_APPROVAL = "item/commandExecution/requestApproval"
    REQ_FILE_APPROVAL = "item/fileChange/requestApproval"
    REQ_PERMISSIONS_APPROVAL = "item/permissions/requestApproval"
    REQ_USER_INPUT = "item/tool/requestUserInput"
    REQ_MCP_ELICITATION = "mcpServer/elicitation/request"
    PLAN_MODE_EXIT_REQUEST = "item/planMode/exitRequest"
