"""Programmatic subagent bridge.

This module intentionally lives outside ``runtime.execution.suckers`` because
subagents are not skills. A caller invokes a subagent runner, which creates an
isolated agent turn and returns a compact result to the caller.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from runtime.protocol.text_limits import MAX_SUBAGENT_MISSION_CHARS

from ._bridge_identity import (
    _CODENAME_POOL,  # noqa: F401 — re-exported for test access via bridge._CODENAME_POOL
    _avatar_for_role,
    _codename_for_role,
    _resolve_cheap_subagent_model,
)
from ._bridge_trace import (
    _attach_trace_fields,
    _ensure_context_trace_fields,
    _safe_emit,
    _safe_journal_emit,
    _subagent_trace_context,
)
from .registry import SubagentRegistry
from .schema_output import (
    coerce_schema_output,
    schema_correction,
    schema_instruction,
)

_log = logging.getLogger("runtime.execution.subagents")

SubAgentRunner = Callable[..., str]

_RUNNER: SubAgentRunner | None = None
_REGISTRY: SubagentRegistry | None = None

_INHERITED_WORK_CONTEXT_KEYS: tuple[str, ...] = (
    "mode",
    "capability_mode",
    "code_mode",
    "agent_mode",
    "personal_mode",
    "personal_instructions",
    "workflow_mode",
    "workflow_preset",
    "completion_policy",
    "mode_preset",
    "mode_contract",
    "workspace_path",
    "workspace_scope",
    "personal_workspace_path",
    "personal_workspace_enabled",
    "project_signals",
    "skill_pack_profile",
    "verification_policy",
    "default_skill_packs",
    "default_plugins",
)


def _inherit_parent_work_context(
    context: dict[str, Any] | None,
    session: Any,
) -> dict[str, Any]:
    """Carry trusted per-turn work policy into every delegated child."""

    merged: dict[str, Any] = dict(context or {})
    metadata = getattr(session, "metadata", None) if session is not None else None
    if isinstance(metadata, dict):
        for key in _INHERITED_WORK_CONTEXT_KEYS:
            if key in metadata:
                # Parent Session metadata is the trusted per-turn contract.
                # A model-authored child ``context`` must not be able to turn
                # audit into develop (or otherwise change the selected task
                # strategy) while spawning a worker.
                merged[key] = metadata[key]

    from runtime.execution.misc.skill_policy import is_audit_read_only_context

    if is_audit_read_only_context(merged):
        # Trusted parent policy may narrow a child but the child/model must not
        # be able to widen it.  The mini-loop intersects its tool catalog with
        # the established read-only verifier surface when this flag is set.
        merged["tool_allowlist_read_only"] = True
    return merged


# Each child holds one global slot for its lifetime, so the same cap bounds
# both width and recursive depth. The deployment can override the generous
# fail-closed default through ECHO_MAX_ACTIVE_SUBAGENTS.
def _default_max_active_subagents() -> int:
    raw = os.environ.get("ECHO_MAX_ACTIVE_SUBAGENTS", "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:  # expected · malformed env value falls through to the default
            pass
    return 64


MAX_ACTIVE_SUBAGENTS: int = _default_max_active_subagents()
_ACTIVE_SUBAGENTS: int = 0
_ACTIVE_SUBAGENTS_LOCK = threading.Lock()


def _acquire_subagent_slot() -> bool:
    """Reserve a concurrency slot. Returns False when the global cap is
    already reached (caller must refuse to spawn)."""
    global _ACTIVE_SUBAGENTS
    with _ACTIVE_SUBAGENTS_LOCK:
        if _ACTIVE_SUBAGENTS >= MAX_ACTIVE_SUBAGENTS:
            return False
        _ACTIVE_SUBAGENTS += 1
        return True


def _release_subagent_slot() -> None:
    global _ACTIVE_SUBAGENTS
    with _ACTIVE_SUBAGENTS_LOCK:
        if _ACTIVE_SUBAGENTS > 0:
            _ACTIVE_SUBAGENTS -= 1


def active_subagent_count() -> int:
    """Current number of concurrently-executing subagents (test/introspection)."""
    with _ACTIVE_SUBAGENTS_LOCK:
        return _ACTIVE_SUBAGENTS


def set_sub_agent_runner(runner: SubAgentRunner | None) -> None:
    """Inject the runner used for persistent subagent dispatch."""
    global _RUNNER
    _RUNNER = runner


def get_sub_agent_runner() -> SubAgentRunner | None:
    return _RUNNER


def set_subagent_registry(registry: SubagentRegistry | None) -> None:
    """Install the Claude-style subagent definition registry."""
    global _REGISTRY
    _REGISTRY = registry


def get_subagent_registry() -> SubagentRegistry | None:
    return _REGISTRY


def _publish_bus_lifecycle(kind: str, event: dict, session: Any) -> None:
    """Mirror sub-agent spawn/finish lifecycle onto the typed event bus.

    The bus is what the Workbench subscribes to for an independent,
    full-fidelity stream. Publishing here (in addition to the journal +
    ``event_emitter``) means EVERY dispatch — including custom runners that
    never touch the ephemeral runner's emit helpers — surfaces lifecycle on
    the bus, keyed to the caller's thread lineage root.
    """
    import contextlib

    with contextlib.suppress(Exception):
        from runtime.execution.subagents.event_bus import publish_subagent_event

        meta = getattr(session, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        thread_id = meta.get("thread_id") or getattr(session, "thread_id", None) or ""
        root = meta.get("root_thread_id") or thread_id or ""
        role = event.get("role") or event.get("agent_id") or ""
        if kind == "subagent_spawned":
            publish_subagent_event(
                "sub_started",
                {
                    "role": role,
                    "agent_id": event.get("requested_agent_id") or event.get("agent_id") or "",
                    "resolved_agent_id": event.get("agent_id") or "",
                    "codename": event.get("codename") or "",
                    "avatar": event.get("avatar") or "",
                    "prompt_preview": (event.get("prompt_preview") or "")[:200],
                    "started_at": event.get("started_at"),
                    "parent_tool_use_id": event.get("parent_tool_use_id") or "",
                },
                thread_id=thread_id,
                root_thread_id=root,
            )
        else:
            ok = bool(event.get("ok"))
            bus_type = "sub_concluded" if ok and not event.get("error") else "sub_failed"
            publish_subagent_event(
                bus_type,
                {
                    "role": role,
                    "agent_id": event.get("requested_agent_id") or event.get("agent_id") or "",
                    "resolved_agent_id": event.get("agent_id") or "",
                    "codename": event.get("codename") or "",
                    "avatar": event.get("avatar") or "",
                    "ok": ok,
                    "error": event.get("error") or "",
                    "duration_s": event.get("duration_s"),
                    "iteration_count": event.get("iteration_count"),
                    "files_touched": event.get("files_touched") or 0,
                    "status": event.get("status") or "",
                    "output": event.get("output") or "",
                    "parent_tool_use_id": event.get("parent_tool_use_id") or "",
                },
                thread_id=thread_id,
                root_thread_id=root,
            )


def call_subagent(
    agent_id: str = "",
    prompt: str = "",
    *,
    role: str = "",
    task: str = "",
    name: str = "",
    message: str = "",
    query: str = "",
    context: dict[str, Any] | None = None,
    timeout_s: int = 300,
    timeout_seconds: float | None = None,
    event_emitter: Callable[[dict], None] | None = None,
    session: Any = None,
    use_cheap_model: bool = False,
    extra_denied_paths: list[str] | None = None,
    workspace_path: str = "",
    output_schema: dict[str, Any] | None = None,
    schema_max_retries: int = 1,
    requires_capabilities: Iterable[str] | None = None,
    continue_session_id: str | None = None,
    runner: SubAgentRunner | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Invoke a subagent and return a structured result.

    Alias parameters are accepted for backward compatibility with older model
    outputs and router code. They are not intended to make this a skill.

    Parameters
    ----------
    timeout_seconds :
        Wall-clock deadline in seconds. When set and exceeded, the subagent
        run is cancelled (best-effort) and a structured timeout result is
        returned instead of hanging. ``None`` (default) means no limit.
    event_emitter :
        Optional callable that receives plain JSON-serializable dicts for
        live progress events. Called with ``sub_tool_start`` before each
        tool invocation and ``sub_tool_end`` after. Exceptions from the
        emitter are swallowed so they never crash the runner.
    use_cheap_model :
        When true and the caller didn't pin an explicit ``model_name`` in
        ``context``, the cheap default resolved by
        :func:`_resolve_cheap_subagent_model` is injected so the subagent
        runs on a lower-cost tier. Used by the swarm dispatcher to route
        research-style roles cheaply.
    output_schema :
        Optional JSON Schema. When set, the subagent is asked to return a
        single JSON value matching the schema; the reply is extracted and
        validated, and on a successful match the parsed object is attached to
        the result as ``parsed`` with ``schema_ok=True``. On a validation
        failure the subagent is re-asked up to ``schema_max_retries`` times
        with the validation error; if it still fails, ``schema_ok`` is False
        and ``schema_error`` carries the reason (the raw ``output`` is always
        preserved). Default ``None`` leaves the free-text contract unchanged.
    schema_max_retries :
        How many extra attempts to grant when ``output_schema`` is set and the
        first reply doesn't validate. Ignored when ``output_schema`` is None.
    requires_capabilities :
        Capabilities the chosen subagent must declare. Checked BEFORE any
        runner work starts; a missing capability fails loud with the declared
        set instead of being accepted-then-ignored (dsh fail-loud rule).
        Registry-backed definitions only — ephemeral roles are not checked.
    continue_session_id :
        Durable subagent session to continue (dsh ``continuable``). When set,
        the prior transcript is injected into the prompt and the new turn is
        appended to the same session; an unknown session fails loud BEFORE any
        runner work. When omitted, a fresh session is created (best-effort)
        and its id is attached to the result as ``session_id`` so the caller
        can continue this subagent later.
    runner :
        Explicit caller-owned persistent runner. When omitted, the historical
        process-global runner installed by :func:`set_sub_agent_runner` remains
        the compatibility fallback. Application routers should pass their own
        runner so multiple app instances cannot overwrite one another.
    """
    agent_id = agent_id or role or name
    prompt = prompt or task or message or query
    if not agent_id:
        return {
            "agent_id": agent_id,
            "output": "",
            "success": False,
            "error": "agent_id is required",
        }
    if not prompt:
        return {
            "agent_id": agent_id,
            "output": "",
            "success": False,
            "error": "prompt is required",
        }

    # Public lane identity is the id requested by the delegating call. It is
    # intentionally distinct from ``agent_id`` below, which can be a generic
    # builtin selected by fallback routing. Without this distinction two
    # custom lanes that both resolve to ``explorer`` overwrite each other in
    # realtime and replay observability.
    _requested_agent_id = (
        str((context or {}).get("requested_agent_id") or agent_id).strip() or agent_id
    )
    _session_meta = getattr(session, "metadata", None) if session is not None else None
    _parent_tool_use_id = str(
        (context or {}).get("parent_tool_use_id")
        or (
            (_session_meta or {}).get("_active_parent_tool_use_id")
            if isinstance(_session_meta, dict)
            else ""
        )
        or ""
    ).strip()

    required = tuple(requires_capabilities or ())
    if required and _REGISTRY is not None and _REGISTRY.has(agent_id):
        definition = _REGISTRY.get(agent_id)
        missing = [cap for cap in required if cap not in definition.capabilities]
        if missing:
            declared = ", ".join(definition.capabilities) or "none"
            return {
                "agent_id": agent_id,
                "output": "",
                "success": False,
                "error": (
                    f"subagent {agent_id!r} lacks required capability(ies): "
                    f"{', '.join(missing)} · declared: {declared}"
                ),
                "capability_error": "missing_required_capability",
            }

    # Capture the ambient parent Session before entering the timeout worker.
    # ContextVars do not propagate into ThreadPoolExecutor threads, but the
    # child needs the same turn_id for blackboard exchange, trace attribution,
    # workspace scope, and approval context.
    if session is None:
        try:
            from runtime.platform.process.session import current_session

            session = current_session()
        except (ImportError, AttributeError):
            session = None
    if not _parent_tool_use_id and session is not None:
        _resolved_session_meta = getattr(session, "metadata", None)
        if isinstance(_resolved_session_meta, dict):
            _parent_tool_use_id = str(
                _resolved_session_meta.get("_active_parent_tool_use_id") or ""
            ).strip()

    context = _inherit_parent_work_context(context, session)

    # Capture the parent turn's react stack (ambient ContextVar set around
    # the main conversation's ``stream_react_loop``) so the runner can drive
    # this sub-agent through the SAME react loop instead of the bespoke
    # mini-loop. Ambient only — never persisted into session metadata.
    if (context or {}).get("react_stack") is None:
        try:
            from runtime.execution.subagents._ambient import current_react_stack

            _ambient_stack = current_react_stack()
        except (ImportError, AttributeError):
            _ambient_stack = None
        if _ambient_stack is not None:
            context = {**(context or {}), "react_stack": _ambient_stack}
            # Real-time sub-agents dispatched inside the parent react loop are
            # driven through the MAIN react loop by default (the react-drive
            # path is now validated; sequential + parallel children reuse the
            # same machinery and the runner falls back to the mini-loop if the
            # react path yields no result). Explicit callers may opt out by
            # passing ``react_loop_subagent=False``.
            if (context or {}).get("react_loop_subagent") is None:
                context = {**context, "react_loop_subagent": True}
            # Real-time sub-agents also get their OWN thread identity by
            # default (independent thread_id), so each child is a real,
            # addressable thread — journal/trace attribute to the child and
            # parallel children no longer collide on the parent's busy flag.
            # Blackboard continuity is preserved via ``blackboard_root_turn_id``
            # (the parent's turn), independent of the child thread id. Callers
            # may opt out with ``flip_subagent_thread=False``.
            if (context or {}).get("flip_subagent_thread") is None:
                context = {**context, "flip_subagent_thread": True}

    # When a schema is requested, steer the model toward schema-valid JSON up
    # front. Enforcement still happens post-hoc (see ``_do_call_with_schema``)
    # so this works on any model, not just ones with native structured output.
    if output_schema:
        prompt = prompt + schema_instruction(output_schema)

    _trace_context = _subagent_trace_context(context, session)
    context = _ensure_context_trace_fields(context, _trace_context)

    # Trusted callers (e.g. the worktree loop) confine this sub-agent's file
    # writes to ONE directory by passing ``workspace_path`` (or
    # context["workspace_path"]). It is carried as ``_locked_write_root`` on the
    # sub-agent's Session; the ephemeral chokepoint injects it as the
    # sandbox_dir for write skills (ephemeral runs bypass the executor's own
    # sandbox-arg injector). NOT a model-facing argument.
    _locked_root = str(
        workspace_path
        or (context.get("workspace_path") if isinstance(context, dict) else "")
        or "",
    ).strip()

    # Track the highest round number seen via the emitter so we can
    # report rounds_completed on timeout.
    _rounds_state: dict[str, int] = {"max_round": 0}
    # Track files the subagent wrote/edited (echo optimisation
    # lane H). The emitter sees ``sub_tool_end`` events with
    # ``skill`` and ``args`` payloads; when the skill is in the
    # write-tools set and the call succeeded, we extract its path.
    _files_touched: list[str] = []
    _files_seen: set[str] = set()
    _subagent_write_tools: frozenset[str] = frozenset(
        {
            "write_text_file",
            "append_text_file",
            "edit_text_file",
            "edit_file",
            "multi_edit_file",
            "propose_patch",
        }
    )

    # ── Sub-agent identity for visibility (codename + avatar) ──
    # Computed once per call so the spawn / finish events agree on
    # the same display name. Frontend receives these on the
    # ``subagent_spawned`` event and renders a tile in the Workbench
    # panel.
    _role_label = (role or agent_id or "agent").strip().lower()
    _codename = _codename_for_role(_role_label)
    _avatar = _avatar_for_role(_role_label)
    # Authoritative role identity from the built-in catalog (co-located with
    # the role's tool allowlist). Free-form labels that don't resolve to a
    # BUILTIN_ROLES entry leave these empty; the frontend falls back to its own
    # role-name / description mapping. Lazy import mirrors `_dispatch` so the
    # module load graph stays acyclic.
    _role_display_name = ""
    _role_description = ""
    try:
        from runtime.execution.suckers.ephemeral_agents import get_role_display

        _role_display = get_role_display(_role_label)
        if _role_display is not None:
            _role_display_name, _role_description = _role_display
    except Exception:  # noqa: BLE001 — identity enrichment is best-effort
        pass
    _spawn_started_at = time.time()

    # ── Thread-scoped memory key ──
    # Resolves the thread_id that the per-role memory uses for context
    # continuity. Priority: explicit context > session attribute. Empty
    # thread_id means stateless (no history record/replay) — matches the
    # previous behaviour for callers that don't supply a thread.
    _memory_thread_id = ""
    if isinstance(context, dict):
        _ctx_thread = context.get("thread_id") or context.get("caller_thread_id")
        if isinstance(_ctx_thread, str) and _ctx_thread.strip():
            _memory_thread_id = _ctx_thread.strip()
    if not _memory_thread_id and session is not None:
        _sess_thread = getattr(session, "thread_id", None)
        if isinstance(_sess_thread, str) and _sess_thread.strip():
            _memory_thread_id = _sess_thread.strip()
    # Stamp it back into context so _compose_system_prompt sees it
    # (it reads from context first, then falls back to session).
    if _memory_thread_id:
        if context is None:
            context = {}
        context.setdefault("thread_id", _memory_thread_id)

    # Durable session continuation (dsh continuable subagents). Unknown
    # session ids fail loud before any runner work; the transcript of a
    # known session is injected so the child continues instead of
    # re-researching from scratch.
    _active_session: dict[str, Any] = {"session": None, "session_id": None}
    try:
        from runtime.execution.subagents.sessions import (
            get_subagent_session_store,
        )
    except ImportError:  # pragma: no cover - sessions module absent
        get_subagent_session_store = None  # type: ignore[assignment]
    _session_store = get_subagent_session_store() if get_subagent_session_store else None
    _session_owner_actor_id = ""
    _session_tenant_id = ""
    if isinstance(context, dict):
        _session_owner_actor_id = str(
            context.get("owner_actor_id") or context.get("actor_id") or context.get("actor") or ""
        ).strip()
        _session_tenant_id = str(context.get("tenant_id") or "").strip()
    if continue_session_id:
        # Session continuation is scoped to the spawning thread: a session
        # created by another thread must read as unknown (cross-tenant IDOR
        # guard — mirrors the owner-binding on control sessions/terminals).
        loaded = (
            _session_store.get(
                continue_session_id,
                scope_thread_id=_memory_thread_id,
                owner_actor_id=_session_owner_actor_id or None,
                tenant_id=_session_tenant_id or None,
            )
            if _session_store
            else None
        )
        if loaded is None:
            return {
                "agent_id": agent_id,
                "output": "",
                "success": False,
                "error": f"unknown subagent session {continue_session_id!r}",
                "session_error": "unknown_session",
                "session_id": continue_session_id,
            }
        _active_session["session"] = loaded
        _active_session["session_id"] = loaded.session_id
        transcript = _session_store.transcript_prompt(loaded) if _session_store is not None else ""
        if transcript:
            prompt = prompt + "\n\n" + transcript
    elif _session_store is not None:
        created = _session_store.create(
            agent_id=agent_id,
            thread_id=_memory_thread_id,
            owner_actor_id=_session_owner_actor_id,
            tenant_id=_session_tenant_id,
        )
        _active_session["session"] = created
        _active_session["session_id"] = created.session_id

    # ── Prompt-injection taint inheritance ──
    # call_subagent's body runs in the spawning parent's thread, but the
    # actual sub-agent runs behind an inner ThreadPoolExecutor (below) whose
    # worker starts with a FRESH taint contextvar. Capture the parent's taint
    # HERE and stamp it into the context that flows to the runner, so an
    # injection-tainted parent cannot launder a risky action through a
    # delegated sub-agent. Honored at the sub-agent's react-loop start.
    try:
        from runtime.safety.validation.prompt_injection import (
            current_injection_taint,
        )

        _taint = current_injection_taint()
        if _taint and _taint != "none":
            if context is None:
                context = {}
            context.setdefault("_inherited_injection_taint", _taint)
    except Exception:  # noqa: BLE001 - taint propagation is best-effort
        pass

    # Emit lifecycle: spawn. Caller's emitter (typically the realtime
    # gateway) forwards this onto the WS so the frontend sees a new
    # sub-agent card the moment we start. Best-effort — emitter
    # exceptions never block the run.
    _spawn_event = {
        "type": "subagent_spawned",
        "agent_id": agent_id,
        "requested_agent_id": _requested_agent_id,
        "parent_tool_use_id": _parent_tool_use_id or None,
        "role": _role_label,
        "codename": _codename,
        "avatar": _avatar,
        "role_display_name": _role_display_name,
        "role_description": _role_description,
        "prompt_preview": (prompt[:MAX_SUBAGENT_MISSION_CHARS] if isinstance(prompt, str) else ""),
        "use_cheap_model": bool(use_cheap_model),
        "started_at": _spawn_started_at,
    }
    _attach_trace_fields(_spawn_event, _trace_context)
    _safe_emit(event_emitter, _spawn_event)
    # Mirror onto the genome journal so frontend timeline can render
    # a sub-agent tile from the spawn moment, independent of the
    # in-memory emitter being wired through to the realtime gateway.
    _safe_journal_emit(_spawn_event)
    _publish_bus_lifecycle("subagent_spawned", _spawn_event, session)
    try:
        from runtime.safety.hooks import dispatch_subagent_start

        dispatch_subagent_start(
            thread_id=_memory_thread_id,
            agent_id=agent_id,
            subagent_type=_role_label or agent_id,
            prompt_preview=(prompt[:500] if isinstance(prompt, str) else ""),
            session_id=str(_active_session.get("session_id") or ""),
        )
    except Exception:  # noqa: BLE001 — hooks are best-effort, never break the call
        pass

    def _tracking_emitter(event: dict) -> None:
        # Once the parent turn redirects, the old execution generation is
        # closed. A cooperative child may still emit its final bookkeeping
        # event while unwinding; suppress it so no late progress can appear
        # inside the new trajectory.
        if _child_source.is_cancelled:
            return
        rnd = event.get("round")
        if isinstance(rnd, int) and rnd > _rounds_state["max_round"]:
            _rounds_state["max_round"] = rnd
        # Annotate per-tool events with the sub-agent identity so the
        # frontend timeline can group them under the same tile as the
        # spawn event.
        try:
            _attach_trace_fields(event, _trace_context)
            if event.get("type") in {"sub_tool_start", "sub_tool_end"}:
                event.setdefault("agent_id", agent_id)
                event.setdefault("requested_agent_id", _requested_agent_id)
                event.setdefault("parent_tool_use_id", _parent_tool_use_id or None)
                event.setdefault("subagent_codename", _codename)
                event.setdefault("subagent_avatar", _avatar)
        except Exception:  # noqa: BLE001 — annotation is best-effort
            pass
        # File-touch tracking: best-effort, never breaks the call.
        try:
            if event.get("type") == "sub_tool_end" and event.get("status") == "success":
                name = event.get("skill") or event.get("name") or ""
                if name in _subagent_write_tools:
                    args = event.get("args") or {}
                    path = args.get("path") if isinstance(args, dict) else None
                    if isinstance(path, str) and path and path not in _files_seen:
                        _files_seen.add(path)
                        _files_touched.append(path)
        except Exception:  # noqa: BLE001 — telemetry must never crash subagent
            pass
        _safe_emit(event_emitter, event)

    # Cancellation propagation.
    #
    # A subagent runs inside the caller's turn, so if the user
    # interrupts the parent turn (or the parent's token gets cancelled
    # for any reason), the subagent's ReAct loop should stop too.
    # We create a linked child source and install it on the worker
    # thread via ``scoped_cancellation`` so every ``current_cancellation_token()``
    # call inside the subagent sees the inherited signal.
    from runtime.safety.approval.cancellation import (
        CancellationSource,
        CancellationToken,
        current_cancellation_token,
        scoped_cancellation,
    )

    _parent_token = current_cancellation_token()
    _child_source = CancellationSource()

    # Parent cancel → cancel child. If the parent is the never-token
    # (no ambient handler), ``on_cancelled`` is a no-op.
    def _cancel_child(reason: str) -> None:
        _child_source.cancel(reason=reason or "parent cancelled")

    _unlink_parent = _parent_token.on_cancelled(_cancel_child)

    def _do_call() -> dict[str, Any]:
        # Always pass _tracking_emitter so round counting works even when
        # the caller didn't supply an external event_emitter. The tracking
        # wrapper is a no-op for the external emitter when it's None.
        # Install the linked child token as the ambient cancellation
        # token for this worker thread so subagent code below polling
        # ``current_cancellation_token()`` sees parent-driven cancels.
        # Also layer ``extra_denied_paths`` onto the ambient denylist
        # for the duration of the sub-agent run so a parent can
        # tighten what its researcher / critic / explorer sub-agent
        # is allowed to read (e.g. "don't touch .env this turn").
        denylist_token = None
        if extra_denied_paths:
            try:
                from runtime.safety.auth.path_denylist import (
                    pop_turn_denylist,
                    push_turn_denylist,
                )

                denylist_token = push_turn_denylist(extra_denied_paths)
            except ImportError:
                denylist_token = None
        run_session = session
        # Sub-agent threading identity: resolve the lineage + coordination
        # roots (``root_thread_id`` for the event bus, ``blackboard_root_turn_id``
        # for blackboard continuity) and stamp them on the run Session. The
        # child KEEPS the parent's thread_id by default so journal/trace stays
        # attributed to the main conversation; the independent-thread identity
        # (flip_thread_id) is the explicit opt-in for full sub-agent threading.
        _flip_thread = bool((context or {}).get("flip_subagent_thread", False))
        _extra_meta: dict[str, Any] = {}
        if _locked_root:
            _extra_meta["_locked_write_root"] = _locked_root
        elif session is not None:
            # A realtime child may flip to its own thread id.  The default
            # chat/artifact scope is derived from ``Session.thread_id``, so a
            # flipped child would otherwise resolve relative file tools into
            # a fresh, empty child workspace instead of the parent's visible
            # ``output/final`` folder.  Pin the parent's artifact root before
            # changing identity; project sessions still use their inherited
            # ``workspace_path`` as the primary read root.
            try:
                from runtime.platform.process.scope import thread_artifact_root

                _parent_meta = getattr(session, "metadata", None) or {}
                _parent_artifact_root = _parent_meta.get("_artifact_output_root")
                if not (isinstance(_parent_artifact_root, str) and _parent_artifact_root.strip()):
                    _parent_thread_id = str(
                        getattr(session, "thread_id", None)
                        or getattr(session, "conversation_id", None)
                        or ""
                    ).strip()
                    if _parent_thread_id:
                        _parent_artifact_root = str(thread_artifact_root(_parent_thread_id))
                if isinstance(_parent_artifact_root, str) and _parent_artifact_root.strip():
                    _extra_meta["_artifact_output_root"] = _parent_artifact_root.strip()
            except (ImportError, OSError, ValueError) as exc:
                _log.debug("could not preserve parent artifact root", exc_info=exc)
        # Stamp the per-child codename onto the run Session so the typed
        # event-bus helpers (which key lanes by codename) can attribute tool /
        # conclude / fail events to the right sub-agent thread — even when
        # several parallel children share the same role. Ambient per-call
        # identity, read from ``session.metadata`` inside the child run.
        if _codename:
            _extra_meta["subagent_codename"] = _codename
        _extra_meta["subagent_agent_id"] = _requested_agent_id
        _extra_meta["subagent_resolved_agent_id"] = agent_id
        if _parent_tool_use_id:
            _extra_meta["_active_parent_tool_use_id"] = _parent_tool_use_id
        _extra_meta["subagent_role"] = _role_label
        _extra_meta["subagent_avatar"] = _avatar
        from runtime.platform.process.session import Session

        if session is not None and isinstance(session, Session):
            from runtime.execution.subagents.threading import (
                bind_subagent_session,
                forge_subagent_thread,
            )

            binding = forge_subagent_thread(
                session,
                agent_id=agent_id,
                role=role,
                persist=_flip_thread,
            )
            run_session = bind_subagent_session(
                session,
                binding,
                extra_metadata=_extra_meta or None,
                flip_thread_id=_flip_thread,
            )
        elif _locked_root:
            # No ambient Session but a locked write root was requested:
            # carry it on a bare Session so the ephemeral chokepoint still
            # confines writes (original behaviour).
            run_session = Session(metadata={"_locked_write_root": _locked_root})
        scope_token = None
        if run_session is not None:
            # Bind on THIS worker thread so blackboard skills see the parent's
            # turn and the ephemeral chokepoint carries any locked write root.
            from runtime.platform.process.session import _current_session

            scope_token = _current_session.set(run_session)
        try:
            with scoped_cancellation(_child_source.token):
                # Child→parent report lane (dsh ``tool-subagent-report``):
                # stamp the continuable session id into the dispatch context
                # so the in-process runner can expose the child's ``report``
                # tool. Only present when a durable session exists; one-shot
                # children and remote providers never see it.
                #
                # A react-driven child is the exception: the main react loop
                # cannot bind a per-session report handler (react_drive
                # refuses such dispatches), so stamping the id here would
                # silently force every react child onto the mini-loop and
                # starve the react-drive end-state model. React children run
                # on the main loop without the in-run report tool; explicit
                # report lanes (caller-supplied ``subagent_session_id`` /
                # ``subagent_report_delivery``) still take the mini-loop.
                dispatch_context = context
                _react_driven = bool(
                    (context or {}).get("react_loop_subagent")
                    and (context or {}).get("react_stack") is not None
                )
                if _active_session["session_id"] and not _react_driven:
                    dispatch_context = {
                        **(context or {}),
                        "subagent_session_id": _active_session["session_id"],
                    }
                from runtime.execution.subagents._ambient import (
                    subagent_session_scope,
                )

                with subagent_session_scope(_active_session["session_id"]):
                    return _dispatch(
                        agent_id=agent_id,
                        prompt=prompt,
                        context=dispatch_context,
                        timeout_s=timeout_s,
                        session=run_session,
                        event_emitter=_tracking_emitter,
                        use_cheap_model=use_cheap_model,
                        runner=runner,
                    )
        finally:
            if scope_token is not None:
                from runtime.platform.process.session import _current_session

                _current_session.reset(scope_token)
            if denylist_token is not None:
                try:
                    from runtime.safety.auth.path_denylist import (
                        pop_turn_denylist,
                    )

                    pop_turn_denylist(denylist_token)
                except ImportError:  # noqa: BLE001 — denylist is optional, skip if missing
                    pass

    # Auto-retry wrapper. When the first call hits the round cap with
    # partial output, give it ONE more shot with a continuation prompt
    # that includes the partial work so the agent can finish from where
    # it left off rather than restart. This recovers ~80% of "almost
    # done" scenarios that previously surfaced as a bare 400 to the user.
    #
    # We do NOT retry generic failures (router error / tool exception)
    # because those are likely deterministic — retrying would just burn
    # more budget without changing the outcome.
    _retry_disabled = bool((context or {}).get("disable_auto_retry", False))

    def _do_call_with_retry() -> dict[str, Any]:
        first = _do_call()
        if not isinstance(first, dict):
            return first
        # Only retry round-cap exhaustion with partial work
        if (
            _retry_disabled
            or not first.get("round_cap_exceeded")
            or not (first.get("output") or "").strip()
        ):
            return first
        partial = str(first.get("output") or "").strip()
        original_rounds = int(first.get("rounds_completed") or 0)
        _log.info(
            "subagent auto-retry · agent_id=%s rounds_first=%d partial_chars=%d",
            agent_id,
            original_rounds,
            len(partial),
        )
        # Reset round tracking so the retry's iteration_count reflects
        # the second attempt only (caller's _augment uses _rounds_state).
        _rounds_state["max_round"] = 0
        # Continuation prompt: include partial output and a clear "finish"
        # directive so the agent doesn't restart from scratch.
        # Declare nonlocal BEFORE use in the f-string.
        nonlocal prompt
        original_prompt = prompt
        continuation_prompt = (
            f"{original_prompt}\n\n"
            "---\n\n"
            "## CONTINUATION\n\n"
            f"You ran out of rounds on the first attempt ({original_rounds} "
            "rounds). Your partial work is shown below. Finish the task — do "
            "NOT restart from scratch; build on what you have. Wrap up "
            "with a final answer rather than collecting more data unless "
            "absolutely necessary.\n\n"
            "### Partial output so far\n\n"
            f"{partial}\n\n"
            "### Finish from here:"
        )
        # Mutate the closed-over prompt for the retry call. We must
        # restore the original prompt before the function returns so the
        # `record_turn` invocation later sees the user's original ask, not
        # the synthetic continuation.
        prompt = continuation_prompt
        try:
            second = _do_call()
        finally:
            prompt = original_prompt
        if not isinstance(second, dict):
            return first
        # Stitch: prefer the retry's output but mark that a retry happened.
        second.setdefault("output", "")
        if not str(second.get("output") or "").strip() and partial:
            # Retry produced nothing usable → keep first call's partial
            # but propagate the retry-attempted flag.
            second["output"] = partial
            second["success"] = first.get("success", False)
            second["error"] = first.get("error")
        second["retry_attempted"] = True
        second["original_rounds"] = original_rounds
        if not second.get("success") and second.get("output", "").strip():
            # Even if cap hit again, we have content — surface as
            # partial-success rather than total failure.
            second.setdefault("partial", True)
        return second

    def _do_call_with_schema() -> dict[str, Any]:
        """Run the call, then enforce ``output_schema`` on the reply.

        No-op when no schema was requested. Otherwise validate the reply and,
        on a mismatch, re-ask the model (a single ``_do_call`` per retry, so it
        stays bounded) with a correction prompt that names the error. The raw
        ``output`` is always preserved; ``parsed`` / ``schema_ok`` /
        ``schema_error`` describe the structured outcome.
        """
        result = _do_call_with_retry()
        if not output_schema or not isinstance(result, dict):
            return result

        nonlocal prompt
        base_prompt = prompt
        attempts = 0
        try:
            while True:
                raw = str(result.get("output") or "")
                ok, parsed, err = coerce_schema_output(raw, output_schema)
                if ok:
                    result["parsed"] = parsed
                    result["schema_ok"] = True
                    result.pop("schema_error", None)
                    return result
                # Don't burn retries on a failed dispatch (router/tool error):
                # the empty/error output won't get better by re-asking.
                if attempts >= int(schema_max_retries) or not result.get("success"):
                    result["schema_ok"] = False
                    result["schema_error"] = err
                    _log.info(
                        "subagent %s schema mismatch after %d attempt(s): %s",
                        agent_id,
                        attempts,
                        err,
                    )
                    return result
                attempts += 1
                prompt = base_prompt + schema_correction(err, output_schema, raw)
                result = _do_call()
                if not isinstance(result, dict):
                    return result
        finally:
            prompt = base_prompt

    def _augment(result: dict[str, Any]) -> dict[str, Any]:
        """Attach context-isolation telemetry to the subagent result.

        Adds ``iteration_count`` (rounds the subagent ran), and
        ``files_touched`` (paths it wrote to). These give the caller
        a structured envelope without leaking the subagent's
        intermediate steps into the parent's message history.

        Also injects the spawn-time codename / avatar / role so a
        downstream caller (e.g. ``call_agent_parallel``) can include
        them in the synthesised reply. The frontend already received
        these via the ``subagent_spawned`` event, but having them on
        the return value makes synchronous testing + transcripts
        self-describing.
        """
        if not isinstance(result, dict):
            return result
        _attach_trace_fields(result, _trace_context)
        result.setdefault("iteration_count", _rounds_state["max_round"])
        result.setdefault("files_touched", list(_files_touched))
        result.setdefault("codename", _codename)
        result.setdefault("avatar", _avatar)
        result.setdefault("role", _role_label)
        result.setdefault("requested_agent_id", _requested_agent_id)
        # Lifecycle: finish. Mirrors the spawn event so the frontend
        # can mark the tile complete + show duration / iteration /
        # files-touched stats. ``ok`` is the canonical success flag;
        # we infer it from the result envelope when not explicit.
        elapsed = max(0.0, time.time() - _spawn_started_at)
        ok = bool(result.get("success", result.get("ok", True))) and not result.get("error")
        # Structured log so operators can see subagent outcomes without
        # tailing a generic INFO firehose. Includes round_cap_exceeded
        # so the log surface differentiates "ran out of rounds" from
        # "tool failure" / "router error".
        _log.info(
            "subagent finish · agent_id=%s role=%s ok=%s rounds=%d files=%d duration=%.2fs%s%s",
            agent_id,
            _role_label,
            ok,
            _rounds_state["max_round"],
            len(_files_touched),
            elapsed,
            " · ROUND_CAP_EXCEEDED" if result.get("round_cap_exceeded") else "",
            f" · error={result.get('error')!r}" if result.get("error") else "",
        )
        # Record this turn into the per-thread, per-role memory bucket so
        # the next call to the same role in this thread can reference it
        # ("researcher, dig deeper on that patent" sees what "researcher,
        # find Eight Sleep patents" produced). Only fires when a thread_id
        # is present; stateless callers stay stateless.
        if _memory_thread_id:
            from runtime.execution.subagents.memory import record_turn

            record_turn(
                thread_id=_memory_thread_id,
                role_id=agent_id,
                prompt=prompt,
                output=result.get("output", ""),
                success=ok,
                rounds=_rounds_state["max_round"],
                error=result.get("error", ""),
            )
        # Durable session: attach the session id to every augmented result
        # (so the caller can continue even after a rejection) and append the
        # turn for calls that actually ran.
        if _active_session["session_id"]:
            result["session_id"] = _active_session["session_id"]
            if result.get("status") != "rejected":
                from runtime.execution.subagents.sessions import (
                    get_subagent_session_store,
                )

                _session_store = get_subagent_session_store()
                if _session_store is not None:
                    _session_store.append_turn(
                        _active_session["session_id"],
                        prompt=prompt,
                        output=result.get("output", ""),
                        success=ok,
                        rounds=_rounds_state["max_round"],
                        error=result.get("error", ""),
                    )
                    # Journal the turn's completion (dsh session-log
                    # invariant): a resume path can report the session's
                    # effort/outcome without replaying every chunk. Best-
                    # effort — telemetry loss never breaks the bridge.
                    try:
                        from runtime.execution.suckers._ephemeral_events import (
                            _emit_sub_session_summary,
                        )

                        _emit_sub_session_summary(
                            _active_session["session_id"],
                            agent_id=agent_id,
                            rounds=_rounds_state["max_round"],
                            success=ok,
                            error=result.get("error", "") or "",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    # Child→parent report lane (dsh ``tool-subagent-report``):
                    # a continuable child's transcript is NOT automatically
                    # visible to the parent, so undelivered reports are
                    # attached to the result and acked once delivered.
                    _pending = _session_store.pending_reports(_active_session["session_id"])
                    if _pending:
                        result["pending_reports"] = [
                            {
                                "index": index,
                                "content": report.content,
                                "delivery": report.delivery,
                            }
                            for index, report in _pending
                        ]
                        result["reports_prompt"] = _session_store.reports_prompt(
                            _session_store.get(_active_session["session_id"])
                        )
                        _session_store.mark_reports_delivered(_active_session["session_id"])
        try:
            from runtime.memory.learning.subagent_review import (
                queue_subagent_review_candidate,
            )

            result["review_candidate"] = queue_subagent_review_candidate(
                agent_id=agent_id,
                role=_role_label,
                prompt=prompt,
                result=result,
                context=context,
                session=session,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "subagent review candidate queue skipped · agent_id=%s error=%s",
                agent_id,
                exc,
            )
        _finish_event = {
            "type": "subagent_finished",
            "agent_id": agent_id,
            "requested_agent_id": _requested_agent_id,
            "parent_tool_use_id": _parent_tool_use_id or None,
            "role": _role_label,
            "codename": _codename,
            "avatar": _avatar,
            "ok": ok,
            "duration_s": round(elapsed, 2),
            "iteration_count": _rounds_state["max_round"],
            "files_touched": list(_files_touched),
            "error": result.get("error"),
            "status": result.get("status"),
            # Carry the sub-agent's actual answer text so the workbench can
            # render a readable final message instead of dumping the whole
            # result envelope as JSON.
            "output": result.get("output", ""),
        }
        _attach_trace_fields(_finish_event, _trace_context)
        _safe_emit(event_emitter, _finish_event)
        _safe_journal_emit(_finish_event)
        _publish_bus_lifecycle("subagent_finished", _finish_event, session)
        try:
            from runtime.safety.hooks import dispatch_subagent_stop

            dispatch_subagent_stop(
                thread_id=_memory_thread_id,
                agent_id=agent_id,
                subagent_type=_role_label or agent_id,
                session_id=str(_active_session.get("session_id") or ""),
                ok=bool(ok),
                duration_ms=round(float(elapsed) * 1000),
                output_preview=(
                    result.get("output", "")[:500] if isinstance(result.get("output"), str) else ""
                ),
            )
        except Exception:  # noqa: BLE001 — hooks are best-effort, never break the call
            pass
        return result

    # Cost ceiling gate: when ECHO_MAX_COST_USD is set, refuse to spawn once
    # the process-level ledger (UsagePricing) reports the ceiling is breached.
    # Import failure is non-fatal — missing budget module never blocks spawning.
    try:
        from runtime.platform.budget import UsagePricing

        _pricing = UsagePricing.get()
        if _pricing.is_over_budget():
            _cost_reject: dict[str, Any] = {
                "status": "rejected",
                "error": (
                    f"subagent spawn refused: cumulative cost ceiling reached "
                    f"(${_pricing.total_cost_usd:.4f} ≥ "
                    f"${_pricing.config.budget_usd:.2f}) — "
                    "raise ECHO_MAX_COST_USD or unset to disable"
                ),
                "agent_id": agent_id,
                "role": _role_label,
                "codename": _codename,
                "avatar": _avatar,
                "output": "",
                "success": False,
                "rounds_completed": 0,
                "iteration_count": 0,
                "files_touched": [],
            }
            _log.warning(
                "subagent spawn refused (cost ceiling $%.2f reached, spent $%.4f) · agent_id=%s",
                _pricing.config.budget_usd or 0,
                _pricing.total_cost_usd,
                agent_id,
            )
            return _augment(_cost_reject)
    except Exception:  # noqa: BLE001
        pass  # budget check failure is non-fatal

    # Concurrency guard: hold a slot for the whole child run (see the helpers
    # at module top). Over the global cap → refuse to spawn, fail-closed.
    if not _acquire_subagent_slot():
        _reject = {
            "status": "rejected",
            "error": (
                f"subagent concurrency cap reached "
                f"({MAX_ACTIVE_SUBAGENTS} active) — refused to spawn '{agent_id}'"
            ),
            "agent_id": agent_id,
            "role": _role_label,
            "codename": _codename,
            "avatar": _avatar,
            "output": "",
            "success": False,
            "rounds_completed": 0,
            "iteration_count": 0,
            "files_touched": [],
        }
        _log.warning(
            "subagent spawn refused (cap %d reached) · agent_id=%s role=%s",
            MAX_ACTIVE_SUBAGENTS,
            agent_id,
            _role_label,
        )
        return _augment(_reject)
    try:
        slot_release_deferred = False
        # Preserve the direct-call path for non-request callers that have no
        # cancellable parent. Live turns use a worker even without an explicit
        # timeout, allowing the caller to return promptly when the user
        # redirects while a non-cooperative child is still unwinding.
        monitor_parent = _parent_token is not CancellationToken.none()
        if timeout_seconds is None and not monitor_parent:
            return _augment(_do_call_with_schema())

        # Monitored path: run in a thread so we can enforce both a wall-clock
        # limit and parent cancellation.
        # We use shutdown(wait=False) to avoid blocking forever if the worker
        # thread is stuck (Python threads cannot be killed cleanly).
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_do_call_with_schema)

        def _defer_slot_until_worker_finishes() -> None:
            """Keep the global slot occupied while a timed-out thread unwinds.

            ``future.cancel()`` cannot stop a thread that already started.
            Releasing the slot in that case lets a retry spawn alongside the
            old worker, so two generations can write the same workspace.
            The callback is safe when the future has already completed:
            ``add_done_callback`` invokes it synchronously in that case.
            """
            nonlocal slot_release_deferred
            if slot_release_deferred or future.done():
                return
            slot_release_deferred = True
            future.add_done_callback(lambda _future: _release_subagent_slot())

        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        try:
            while True:
                # Cancellation wins over a simultaneously arriving success:
                # this is the generation fence that keeps late child output
                # out of the redirected parent turn.
                if _child_source.is_cancelled:
                    future.cancel()
                    _defer_slot_until_worker_finishes()
                    reason = _child_source.token.reason or "parent cancelled"
                    elapsed = max(0.0, time.time() - _spawn_started_at)
                    _cancel_event = {
                        "type": "subagent_finished",
                        "agent_id": agent_id,
                        "requested_agent_id": _requested_agent_id,
                        "parent_tool_use_id": _parent_tool_use_id or None,
                        "role": _role_label,
                        "codename": _codename,
                        "avatar": _avatar,
                        "ok": False,
                        "duration_s": round(elapsed, 2),
                        "iteration_count": _rounds_state["max_round"],
                        "files_touched": list(_files_touched),
                        "error": f"subagent cancelled: {reason}",
                        "status": "cancelled",
                        "cancelled": True,
                        "cancellation_reason": reason,
                    }
                    _attach_trace_fields(_cancel_event, _trace_context)
                    _safe_emit(event_emitter, _cancel_event)
                    _safe_journal_emit(_cancel_event)
                    _publish_bus_lifecycle("subagent_finished", _cancel_event, session)
                    return _attach_trace_fields(
                        {
                            "status": "cancelled",
                            "error": f"subagent cancelled: {reason}",
                            "cancelled": True,
                            "cancellation_reason": reason,
                            "agent_id": agent_id,
                            "role": _role_label,
                            "codename": _codename,
                            "avatar": _avatar,
                            "output": "",
                            "success": False,
                            "rounds_completed": _rounds_state["max_round"],
                            "iteration_count": _rounds_state["max_round"],
                            "files_touched": list(_files_touched),
                        },
                        _trace_context,
                    )

                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise concurrent.futures.TimeoutError
                wait_for = 0.05 if remaining is None else min(0.05, remaining)
                try:
                    result = future.result(timeout=wait_for)
                except concurrent.futures.TimeoutError:
                    continue
                if _child_source.is_cancelled:
                    continue
                return _augment(result)
        except concurrent.futures.TimeoutError:
            # Signal cancellation to the subagent so any poll-aware loop
            # (ReAct, subprocess waits) unwinds gracefully. ``future.cancel``
            # only works if the task hasn't started — the token gives us
            # co-operative shutdown even on running tasks.
            _child_source.cancel(reason="subagent timeout")
            future.cancel()
            _defer_slot_until_worker_finishes()
            rounds = _rounds_state["max_round"]
            _log.warning(
                "subagent %s timed out after %ss (rounds_completed=%d)",
                agent_id,
                timeout_seconds,
                rounds,
            )
            # Emit the subagent_finished lifecycle event for the timeout
            # path too — frontend needs to mark the tile as failed.
            elapsed = max(0.0, time.time() - _spawn_started_at)
            _timeout_event = {
                "type": "subagent_finished",
                "agent_id": agent_id,
                "requested_agent_id": _requested_agent_id,
                "parent_tool_use_id": _parent_tool_use_id or None,
                "role": _role_label,
                "codename": _codename,
                "avatar": _avatar,
                "ok": False,
                "duration_s": round(elapsed, 2),
                "iteration_count": rounds,
                "files_touched": list(_files_touched),
                "error": f"subagent timed out after {timeout_seconds}s",
                "status": "timeout",
            }
            _attach_trace_fields(_timeout_event, _trace_context)
            _safe_emit(event_emitter, _timeout_event)
            _safe_journal_emit(_timeout_event)
            _publish_bus_lifecycle("subagent_finished", _timeout_event, session)
            return _attach_trace_fields(
                {
                    "status": "timeout",
                    "error": f"subagent timed out after {timeout_seconds}s",
                    "agent_id": agent_id,
                    "role": _role_label,
                    "codename": _codename,
                    "avatar": _avatar,
                    "output": "",
                    "success": False,
                    "rounds_completed": rounds,
                    "iteration_count": rounds,
                    "files_touched": list(_files_touched),
                },
                _trace_context,
            )
        finally:
            executor.shutdown(wait=False)
    finally:
        _unlink_parent()
        # A monitored worker that timed out/cancelled while already running
        # owns the slot until its thread actually exits.  This prevents a
        # retry from running concurrently with the old generation.
        if not slot_release_deferred:
            _release_subagent_slot()


def _dispatch(
    *,
    agent_id: str,
    prompt: str,
    context: dict[str, Any] | None,
    timeout_s: int,
    session: Any,
    event_emitter: Callable[[dict], None] | None,
    use_cheap_model: bool = False,
    runner: SubAgentRunner | None = None,
) -> dict[str, Any]:
    """Inner dispatch — runs in the caller's thread or a worker thread."""
    _log.info(
        "subagent dispatch · agent_id=%s prompt_len=%d timeout=%ds",
        agent_id,
        len(prompt),
        timeout_s,
    )
    from runtime.execution.suckers.ephemeral_agents import (
        EphemeralRoleDef,
        is_ephemeral_role,
        run_ephemeral_definition,
        run_ephemeral_role,
    )

    registry = _REGISTRY
    if registry is not None and registry.has(agent_id):
        definition = registry.get(agent_id)
        merged_context: dict[str, Any] = {
            **(context or {}),
            "subagent_source_path": definition.source_path,
            "subagent_scope": definition.scope,
        }
        if definition.model:
            merged_context.setdefault("model_name", definition.model)
        if use_cheap_model and "model_name" not in merged_context:
            cheap = _resolve_cheap_subagent_model()
            if cheap:
                merged_context["model_name"] = cheap
        if event_emitter is not None:
            merged_context["event_emitter"] = event_emitter
        if definition.backend:
            partner_result = _dispatch_partner(definition, prompt, timeout_s)
            if partner_result is not None:
                return partner_result
            # The partner exists but has no stable headless invocation yet —
            # fall back to the in-process loop (dsh provider vocabulary: an
            # unsupported provider degrades to the default transport).
        return run_ephemeral_definition(
            EphemeralRoleDef(
                id=definition.name,
                display_name=definition.name,
                description=definition.description,
                system_prompt=definition.system_prompt,
                share_context=True,
                share_memory=True,
                tool_allowlist=definition.tools,
            ),
            prompt,
            session=session,
            context=merged_context,
            timeout_s=timeout_s,
        )

    if is_ephemeral_role(agent_id):
        merged_eph: dict[str, Any] = dict(context or {})
        if use_cheap_model and "model_name" not in merged_eph:
            cheap = _resolve_cheap_subagent_model()
            if cheap:
                merged_eph["model_name"] = cheap
        if event_emitter is not None:
            merged_eph["event_emitter"] = event_emitter
        return run_ephemeral_role(
            agent_id,
            prompt,
            session=session,
            context=merged_eph,
            timeout_s=timeout_s,
        )

    selected_runner = runner if runner is not None else _RUNNER
    if selected_runner is None:
        return {
            "agent_id": agent_id,
            "output": "",
            "success": False,
            "error": (
                "sub-agent runner not configured; call set_sub_agent_runner(fn) during bootstrap"
            ),
        }

    merged_ctx: dict[str, Any] = dict(context or {})
    merged_ctx["timeout_s"] = timeout_s
    if use_cheap_model and "model_name" not in merged_ctx:
        cheap = _resolve_cheap_subagent_model()
        if cheap:
            merged_ctx["model_name"] = cheap
    if event_emitter is not None:
        merged_ctx["event_emitter"] = event_emitter
    if session is not None:
        caller_thread = getattr(session, "thread_id", None)
        if caller_thread:
            merged_ctx.setdefault("caller_thread_id", caller_thread)
        # Propagate actor identity so the subagent's journal events,
        # rate-limit buckets, and memory ACL checks all run as the
        # caller. Without this, subagents execute as ``actor=None``
        # and escape the per-user enforcement applied to the parent
        # turn.
        caller_actor = getattr(session, "actor", None) or getattr(session, "actor_id", None)
        if caller_actor:
            merged_ctx.setdefault("actor", caller_actor)
        # Pass the full session through for downstream runners that
        # want to re-scope (e.g. to use session_scope for nested
        # journal events). Opt-in via a dedicated key so we don't
        # accidentally shadow user-supplied ``session`` in context.
        merged_ctx.setdefault("caller_session", session)

    try:
        output = selected_runner(prompt, subagent_name=agent_id, context=merged_ctx)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "subagent %s runner raised %s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return {
            "agent_id": agent_id,
            "output": "",
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "agent_id": agent_id,
        "output": str(output) if output is not None else "",
        "success": True,
        "error": None,
    }


def _dispatch_partner(
    definition: Any,
    prompt: str,
    timeout_s: int,
) -> dict[str, Any] | None:
    """Reject persisted definitions that still name the retired CLI backend.

    External model access is installed through model-provider plugins.  We fail
    explicitly instead of probing the host or silently falling back to another
    model, so old configuration cannot resurrect local CLI execution.
    """
    del prompt, timeout_s
    return {
        "agent_id": definition.name,
        "output": "",
        "success": False,
        "error": (
            f"legacy CLI backend {definition.backend!r} has been removed; "
            "install and configure a model-provider plugin instead"
        ),
        "backend": definition.backend,
        "failure_kind": "legacy_cli_backend_removed",
    }
