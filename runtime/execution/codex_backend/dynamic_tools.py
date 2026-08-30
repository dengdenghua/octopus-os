"""Echo-owned dynamic tools for the Codex App Server sidecar.

The App Server process is deliberately started with an empty MCP/plugin/skill
configuration.  This module is the narrow capability bridge back into the
*current Echo turn*: it snapshots the already tenant-filtered, agent-
allowlisted tool catalog, advertises only those JSON schemas, and sends every
``item/tool/call`` request through the existing Echo executor.

That last point is the security boundary.  Dynamic calls do not invoke skill
handlers directly; ``_execute_tool_call`` reaches ``ToolExecutor.execute_step``
and therefore keeps scope injection, audit read-only policy, immunity, hooks,
journaling, runtime capability switches, and tool-result bounds intact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from runtime.execution.misc.skill_policy import (
    coerce_skill_names,
    filter_tool_specs_for_workspace_contract,
    resolve_agent_skill_policy,
    resolve_context_tool_policy,
)
from runtime.execution.tool_engine.native_tool_execution import execute_native_tool_call
from runtime.execution.tool_engine.session_metadata import project_tool_session_metadata
from runtime.execution.tool_spec_builder import build_anthropic_tool_specs
from runtime.platform.process.session import (
    Session,
    current_session,
    parent_tool_use_scope,
    session_scope,
)
from runtime.safety.approval.approval_gate import (
    ApprovalProvider,
    AutoDenyProvider,
    approval_action_for_tool,
)
from runtime.safety.approval.approval_gate import (
    ApprovalRequest as EchoApprovalRequest,
)
from runtime.safety.validation.prompt_injection import (
    current_injection_taint,
    mark_injection_taint,
    set_injection_gate_handled,
)

from .types import ApprovalRequest as AppServerRequest

_logger = logging.getLogger(__name__)

DYNAMIC_TOOL_CALL_METHOD = "item/tool/call"

_MAX_DYNAMIC_TOOLS = 256
_MAX_TOOL_NAME_CHARS = 128
_MAX_DESCRIPTION_CHARS = 4_000
_MAX_FAILURE_CHARS = 8_000
_MAX_RESULT_CACHE = 256
_MAX_ARGUMENT_DEPTH = 16
_MAX_ARGUMENT_NODES = 2_048
_MAX_ARGUMENT_CHARS = 65_536
_MAX_ARGUMENT_STRING_CHARS = 32_768
_MAX_SELECTED_PLUGINS = 32
_SAFE_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SAFE_PLUGIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/\-]{0,127}\Z")
_TAINT_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    advertised_name: str
    skill_name: str
    input_schema: dict[str, Any]
    # Keep the exact frozen registry object alive.  A plugin may replace a
    # same-named skill after catalog creation; executing that new handler
    # against the old schema/approval expectation would be a TOCTOU bug.
    skill_ref: Any
    trusted_source: str
    handler_ref: Any


@dataclass(frozen=True, slots=True)
class CodexDynamicToolCatalog:
    """One immutable per-turn App Server dynamic-tool advertisement."""

    specs: tuple[dict[str, Any], ...]
    names: tuple[str, ...]


def _bounded_text(value: Any, *, limit: int = _MAX_FAILURE_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 14)].rstrip() + "\n…(truncated)"


def dynamic_tool_failure(reason: Any) -> dict[str, Any]:
    """Return the only fail-closed result shape exposed to App Server."""

    message = _bounded_text(reason) or "Echo dynamic tool request was denied"
    return {
        "contentItems": [{"type": "inputText", "text": message}],
        "success": False,
    }


def validate_dynamic_tool_response(value: Any) -> dict[str, Any]:
    """Validate and bound a broker response before it crosses JSON-RPC.

    The broker currently emits text only.  The validator accepts the three
    App Server content variants so future Echo tools can return media
    without weakening the transport boundary.
    """

    if not isinstance(value, Mapping) or type(value.get("success")) is not bool:
        return dynamic_tool_failure("invalid Echo dynamic tool response")
    raw_items = value.get("contentItems")
    if not isinstance(raw_items, list) or len(raw_items) > 16:
        return dynamic_tool_failure("invalid Echo dynamic tool content")
    items: list[dict[str, str]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            return dynamic_tool_failure("invalid Echo dynamic tool content item")
        kind = raw.get("type")
        field = {
            "inputText": "text",
            "inputImage": "imageUrl",
            "inputAudio": "audioUrl",
        }.get(str(kind or ""))
        payload = raw.get(field) if field is not None else None
        if field is None or not isinstance(payload, str) or "\x00" in payload:
            return dynamic_tool_failure("invalid Echo dynamic tool content item")
        # Media payloads can be data URLs, so retain a larger but still bounded
        # ceiling than ordinary text. The App Server client's frame ceiling is
        # the independent final transport guard.
        limit = 512_000 if field != "text" else 100_000
        items.append({"type": str(kind), field: _bounded_text(payload, limit=limit)})
    return {"contentItems": items, "success": bool(value["success"])}


def _advertised_name(skill_name: str, used: set[str]) -> str:
    """Return a provider-safe, deterministic name without losing identity."""

    if _SAFE_TOOL_NAME.fullmatch(skill_name) and skill_name not in used:
        used.add(skill_name)
        return skill_name
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", skill_name).strip("_")[:80] or "tool"
    digest = hashlib.sha256(skill_name.encode("utf-8")).hexdigest()[:12]
    candidate = f"echo_{stem}_{digest}"[:_MAX_TOOL_NAME_CHARS]
    counter = 1
    while candidate in used:
        suffix = f"_{counter}"
        candidate = f"echo_{stem}_{digest}"[: _MAX_TOOL_NAME_CHARS - len(suffix)] + suffix
        counter += 1
    used.add(candidate)
    return candidate


def _metadata_for_bridge(
    context: Mapping[str, Any],
    *,
    tenant_id: str,
    workspace: str,
) -> tuple[dict[str, Any], Session | None]:
    """Carry the standard turn policy into the executor without ambient leaks."""

    explicit_parent = context.get("caller_session")
    parent = explicit_parent if isinstance(explicit_parent, Session) else current_session()
    metadata: dict[str, Any] = {}
    if parent is not None and isinstance(parent.metadata, dict):
        metadata.update(parent.metadata)

    # Reuse the native bridge's established flat/nested context projection.
    # It intentionally excludes arbitrary client keys and privilege overrides.
    metadata.update(project_tool_session_metadata(context))

    # Tenant and workspace are resolved by the server-side role runner, never
    # accepted from an App Server request. They are restamped after projection.
    metadata["tenant_id"] = tenant_id
    metadata["workspace_path"] = workspace
    metadata.setdefault("extra_workspaces", [workspace])
    metadata["enforce_executor_approval"] = True
    return metadata, parent


def _schema_error(schema: Mapping[str, Any], arguments: Any) -> str | None:
    """Validate the object subset emitted by ``tool_spec_builder``.

    The builder intentionally generates a small Draft-07 subset. Keeping the
    validator here equally small avoids making a transitive jsonschema package
    part of the runtime contract while still rejecting missing/unknown fields
    before a handler can observe them.
    """

    if not isinstance(arguments, dict):
        return "dynamic tool arguments must be a JSON object"
    required = schema.get("required")
    if isinstance(required, list):
        missing = [str(name) for name in required if str(name) not in arguments]
        if missing:
            return "missing required argument(s): " + ", ".join(missing[:16])
    properties = schema.get("properties")
    if schema.get("additionalProperties") is False and isinstance(properties, Mapping):
        unknown = [str(name) for name in arguments if name not in properties]
        if unknown:
            return "unknown argument(s): " + ", ".join(unknown[:16])
    return None


def _arguments_error(arguments: Any) -> str | None:
    """Apply an independent JSON resource ceiling before hashing/execution."""

    stack: list[tuple[Any, int]] = [(arguments, 0)]
    seen_containers: set[int] = set()
    nodes = 0
    chars = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_ARGUMENT_NODES:
            return "dynamic tool arguments contain too many values"
        if depth > _MAX_ARGUMENT_DEPTH:
            return "dynamic tool arguments are nested too deeply"
        if value is None or type(value) is bool:
            continue
        if type(value) is int:
            # Count a conservative decimal representation without allocating
            # it.  One gigantic numeric scalar must not evade the aggregate
            # argument-size limit merely because it is not a string.
            chars += max(1, int(value.bit_length() * 0.302) + 2)
            if chars > _MAX_ARGUMENT_CHARS:
                return "dynamic tool arguments are too large"
            continue
        if type(value) is float:
            if not math.isfinite(value):
                return "dynamic tool arguments contain a non-finite number"
            continue
        if isinstance(value, str):
            if "\x00" in value or len(value) > _MAX_ARGUMENT_STRING_CHARS:
                return "dynamic tool argument string is invalid or too large"
            chars += len(value)
        elif isinstance(value, dict):
            identity = id(value)
            if identity in seen_containers:
                return "dynamic tool arguments contain a recursive value"
            seen_containers.add(identity)
            for key, child in value.items():
                if not isinstance(key, str) or "\x00" in key:
                    return "dynamic tool argument keys must be NUL-free strings"
                if len(key) > _MAX_ARGUMENT_STRING_CHARS:
                    return "dynamic tool argument key is too large"
                chars += len(key)
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            identity = id(value)
            if identity in seen_containers:
                return "dynamic tool arguments contain a recursive value"
            seen_containers.add(identity)
            stack.extend((child, depth + 1) for child in value)
        else:
            return "dynamic tool arguments must contain only JSON values"
        if chars > _MAX_ARGUMENT_CHARS:
            return "dynamic tool arguments are too large"
    return None


def _approval_preview(arguments: Mapping[str, Any]) -> str:
    try:
        from runtime.platform.observability.redactor import redact_dict

        value: Any = redact_dict(dict(arguments))
    except (ImportError, TypeError, ValueError):
        value = {key: "<redacted>" for key in arguments}
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:8_000]
    except (TypeError, ValueError):
        return "<unavailable>"


def _effective_tool_policy(agent: Any, context: Mapping[str, Any]):
    """Resolve the ordinary role policy plus this turn's plugin/skill grants."""

    role_policy = resolve_agent_skill_policy(agent)
    role_allowlist: Any = "*" if role_policy.allow_all else role_policy.allowed
    return resolve_context_tool_policy(
        role_allowlist=role_allowlist,
        context=context,
    )


def _selected_plugin_ids(goal: str, context: Mapping[str, Any]) -> tuple[str, ...]:
    """Return bounded, explicit Echo plugin selections for this turn.

    ``plugin_grants`` is the standard delegated-agent context field. Direct
    turns use the same ``@plugin:...`` / ``plugin://...`` mention parser as
    Cerebrum. Values are identifiers only: neither this function nor the
    loader accepts a caller-supplied filesystem root.
    """

    candidates = coerce_skill_names(context.get("plugin_grants"))
    try:
        from runtime.core.cerebrum.input_mentions import parse_input_mentions

        candidates.extend(parse_input_mentions(goal).plugins)
    except (ImportError, AttributeError, TypeError, ValueError):
        pass

    selected: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        plugin_id = str(raw or "").strip()
        canonical = plugin_id.casefold()
        if (
            not _SAFE_PLUGIN_ID.fullmatch(plugin_id)
            or canonical in seen
            or len(selected) >= _MAX_SELECTED_PLUGINS
        ):
            continue
        seen.add(canonical)
        selected.append(plugin_id)
    return tuple(selected)


def _load_selected_plugin_actions(
    registry: Any,
    *,
    goal: str,
    context: Mapping[str, Any],
) -> tuple[str, ...]:
    """Load only prompt actions from explicitly selected Codex plugins.

    The shared loader deliberately ignores plugin MCP servers, apps, commands,
    hooks and agents. We additionally verify every returned action against the
    live tenant-filtered registry and its ``plugin://`` provenance before it
    can become an allowlist grant.
    """

    selected = _selected_plugin_ids(goal, context)
    if not selected:
        return ()
    try:
        from runtime.execution.suckers.codex_plugin_skills import (
            load_codex_plugin_skills,
        )

        report = load_codex_plugin_skills(registry, selected)
    except (ImportError, AttributeError, OSError, TypeError, ValueError) as exc:
        _logger.warning(
            "Selected Codex plugin skills could not be loaded (%s)",
            type(exc).__name__,
        )
        return ()

    actions: list[str] = []
    for load in report.loads:
        if not load.found or load.error:
            continue
        expected_source = f"plugin://{load.plugin_id}/"
        for action in (*load.loaded_actions, *load.already_registered):
            if action in actions:
                continue
            try:
                skill = registry.get(action)
                enabled = registry.is_enabled(action)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            source = str(getattr(skill, "trusted_source", "") or "")
            if enabled and source.startswith(expected_source):
                actions.append(action)
    return tuple(actions)


def _catalog_agent(agent: Any, policy: Any) -> Any:
    """Present an effective policy to the shared tool-spec builder."""

    if policy.allow_all:
        return None
    return SimpleNamespace(
        agent_id=str(getattr(agent, "agent_id", "") or "codex-role"),
        arms=(
            SimpleNamespace(
                arm_id="codex-effective-turn-policy",
                allowed_skills=tuple(policy.allowed),
            ),
        ),
        extra_skills=(),
    )


class CodexDynamicToolBroker:
    """Execute one frozen Echo tool catalog for one exact Codex turn."""

    def __init__(
        self,
        stack: Any,
        agent: Any,
        *,
        context: Mapping[str, Any],
        goal: str,
        outer_thread_id: str,
        outer_turn_id: str,
        workspace: str,
        tenant_id: str,
        principal_id: str,
        approval_provider: ApprovalProvider | None,
        is_interrupted: Any,
        server_auto_approve: bool = False,
    ) -> None:
        executor = getattr(stack, "executor", None)
        registry = getattr(executor, "registry", None)
        if executor is None or registry is None:
            raise ValueError("Codex dynamic tools require the Echo execution stack")
        self._stack = stack
        self._agent = agent
        self._context = dict(context)
        self._outer_thread_id = outer_thread_id
        self._outer_turn_id = outer_turn_id
        self._workspace = workspace
        self._tenant_id = tenant_id
        self._principal_id = principal_id
        self._approval_provider = approval_provider or AutoDenyProvider()
        self._is_interrupted = is_interrupted
        self._server_auto_approve = bool(server_auto_approve)
        self._inner_thread_id: str | None = None
        self._inner_turn_id: str | None = None
        self._lock = asyncio.Lock()
        self._results: OrderedDict[str, tuple[str, str, dict[str, Any]]] = OrderedDict()

        metadata, parent = _metadata_for_bridge(
            self._context,
            tenant_id=tenant_id,
            workspace=workspace,
        )
        self._metadata = metadata
        # Never interpret privilege-looking client metadata as authority.  The
        # caller can opt in only through the explicit server-resolved flag.
        self._metadata.pop("auto_approve", None)
        inherited_taint = (
            str(
                metadata.get("_inherited_injection_taint")
                or metadata.get("injection_taint")
                or "none"
            )
            .strip()
            .lower()
        )
        self._taint = inherited_taint if inherited_taint in _TAINT_ORDER else "none"
        actor = principal_id or (parent.actor if parent is not None else None)
        catalog_session = Session(
            actor=actor,
            agent=agent,
            thread_id=outer_thread_id,
            conversation_id=outer_thread_id,
            turn_id=outer_turn_id,
            metadata=dict(metadata),
        )
        with session_scope(catalog_session):
            plugin_actions = _load_selected_plugin_actions(
                registry,
                goal=goal,
                context=self._context,
            )
            if plugin_actions:
                existing_grants = coerce_skill_names(self._context.get("extra_tool_allowlist"))
                self._context["extra_tool_allowlist"] = list(
                    dict.fromkeys((*existing_grants, *plugin_actions))
                )
            effective_policy = _effective_tool_policy(agent, self._context)
            specs = build_anthropic_tool_specs(
                registry,
                max_skills=_MAX_DYNAMIC_TOOLS,
                # Use the same role + explicit turn-grant union as Echo'
                # standard delegated-agent path. Passing the original agent
                # here would silently drop selected plugin/skill grants.
                agent=_catalog_agent(agent, effective_policy),
                user_context=dict(self._context),
                goal=goal,
            )
            specs, _workspace_contract = filter_tool_specs_for_workspace_contract(
                specs,
                goal,
                user_context=dict(self._context),
            )

        used_names: set[str] = set()
        entries: dict[str, _CatalogEntry] = {}
        advertised_specs: list[dict[str, Any]] = []
        for spec in specs[:_MAX_DYNAMIC_TOOLS]:
            skill_name = str(getattr(spec, "name", "") or "").strip()
            if not skill_name:
                continue
            advertised = _advertised_name(skill_name, used_names)
            schema_value = getattr(spec, "input_schema", None)
            schema = (
                dict(schema_value)
                if isinstance(schema_value, Mapping)
                else {"type": "object", "properties": {}, "additionalProperties": True}
            )
            description = _bounded_text(
                getattr(spec, "description", "") or f"Run Echo skill {skill_name}.",
                limit=_MAX_DESCRIPTION_CHARS,
            )
            try:
                with session_scope(catalog_session):
                    skill = registry.get(skill_name)
            except (AttributeError, KeyError, TypeError, ValueError):
                # A synthetic or concurrently removed spec is not executable
                # through the registry and therefore must not be advertised.
                continue
            entries[advertised] = _CatalogEntry(
                advertised,
                skill_name,
                schema,
                skill,
                str(getattr(skill, "trusted_source", "") or ""),
                getattr(skill, "handler", None),
            )
            advertised_specs.append(
                {
                    "type": "function",
                    "name": advertised,
                    "description": description,
                    "inputSchema": schema,
                }
            )
        self._entries = entries
        self.catalog = CodexDynamicToolCatalog(
            specs=tuple(advertised_specs),
            names=tuple(entries),
        )

    def bind_inner_scope(self, *, thread_id: str, turn_id: str) -> None:
        if not thread_id or not turn_id:
            raise ValueError("inner Codex thread and turn ids must be non-empty")
        self._inner_thread_id = thread_id
        self._inner_turn_id = turn_id

    async def __call__(self, request: AppServerRequest) -> dict[str, Any]:
        async with self._lock:
            return await self._handle(request)

    async def _handle(self, request: AppServerRequest) -> dict[str, Any]:
        if request.method != DYNAMIC_TOOL_CALL_METHOD:
            return dynamic_tool_failure("unsupported App Server request")
        if self._interrupted():
            return dynamic_tool_failure("outer Echo turn was interrupted")
        if self._inner_thread_id is None or self._inner_turn_id is None:
            return dynamic_tool_failure("dynamic tool request arrived before turn binding")

        params = request.params
        if (
            str(params.get("threadId") or "") != self._inner_thread_id
            or str(params.get("turnId") or "") != self._inner_turn_id
        ):
            return dynamic_tool_failure("dynamic tool request is outside the active turn")
        namespace = params.get("namespace")
        if namespace not in (None, "", "echo"):
            return dynamic_tool_failure("dynamic tool namespace is not allowed")
        advertised = str(params.get("tool") or "").strip()
        call_id = str(params.get("callId") or "").strip()
        if not advertised or not call_id:
            return dynamic_tool_failure("dynamic tool and callId are required")
        entry = self._entries.get(advertised)
        if entry is None:
            return dynamic_tool_failure("dynamic tool is not in this turn's Echo catalog")
        arguments = params.get("arguments")
        schema_error = _schema_error(entry.input_schema, arguments)
        if schema_error is not None:
            return dynamic_tool_failure(schema_error)
        assert isinstance(arguments, dict)
        arguments_error = _arguments_error(arguments)
        if arguments_error is not None:
            return dynamic_tool_failure(arguments_error)

        fingerprint = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        cached = self._results.get(call_id)
        if cached is not None:
            cached_tool, cached_fingerprint, cached_result = cached
            if cached_tool != advertised or cached_fingerprint != fingerprint:
                return dynamic_tool_failure("dynamic tool callId was reused with different input")
            self._results.move_to_end(call_id)
            return dict(cached_result)

        # Re-check every mutable gate at dispatch time. This closes revoked
        # plugin/disabled-skill and tenant changes after thread/resume.
        executor = self._stack.executor
        execution_session = self._execution_session(auto_approve=False)
        with session_scope(execution_session):
            registry = executor.registry
            if not registry.has(entry.skill_name):
                return dynamic_tool_failure("dynamic tool is no longer visible to this tenant")
            try:
                if not registry.is_enabled(entry.skill_name):
                    return dynamic_tool_failure("dynamic tool was disabled after catalog creation")
            except (AttributeError, TypeError, ValueError, KeyError):
                return dynamic_tool_failure("dynamic tool enablement could not be verified")
            try:
                live_policy = _effective_tool_policy(self._agent, self._context)
            except (AttributeError, TypeError, ValueError):
                return dynamic_tool_failure("dynamic tool policy could not be verified")
            if not live_policy.allows(entry.skill_name):
                return dynamic_tool_failure("dynamic tool is no longer allowed for this role")
            try:
                current_skill = registry.get(entry.skill_name)
            except (AttributeError, KeyError, TypeError, ValueError):
                return dynamic_tool_failure("dynamic tool implementation could not be verified")
            if (
                current_skill is not entry.skill_ref
                or str(getattr(current_skill, "trusted_source", "") or "") != entry.trusted_source
                or getattr(current_skill, "handler", None) is not entry.handler_ref
            ):
                return dynamic_tool_failure(
                    "dynamic tool implementation changed after catalog creation"
                )

        preview = _approval_preview(arguments)
        risk, action, policy = approval_action_for_tool(
            entry.skill_name,
            preview,
            policy=self._metadata.get("approval_risk_policy"),
        )
        if (
            _TAINT_ORDER.get(self._taint, 0) >= _TAINT_ORDER["medium"]
            and risk.level in {"medium", "high", "critical"}
            and action not in {"deny", "ask", "confirm"}
        ):
            action = "ask"
        auto_approve = self._server_auto_approve
        if action == "deny":
            return dynamic_tool_failure(
                f"denied by Echo approval policy (risk={risk.level}: {risk.reason})"
            )

        approval_handled = False
        if action in {"ask", "confirm"} and not auto_approve:
            outer_request = EchoApprovalRequest(
                thread_id=self._outer_thread_id,
                tool_name=entry.skill_name,
                tool_call_id=call_id,
                args_preview=preview,
                detail=(
                    "Codex requested an Echo dynamic tool "
                    f"(risk={risk.level}: {risk.reason}; action={action}; "
                    f"policy={policy.to_dict()})"
                )[:1_000],
            )
            try:
                decision = await asyncio.to_thread(
                    self._approval_provider.request,
                    outer_request,
                    timeout=120.0,
                )
            except (OSError, RuntimeError, TimeoutError):
                return dynamic_tool_failure("Echo approval failed closed")
            if not decision.approved or self._interrupted():
                return dynamic_tool_failure(decision.reason or "user declined the tool request")
            approval_handled = True
        elif auto_approve:
            approval_handled = True

        try:
            output, is_error, observed_taint = await asyncio.to_thread(
                self._execute_sync,
                entry,
                arguments,
                call_id,
                approval_handled,
            )
        except Exception as exc:  # noqa: BLE001 - tool errors are model observations
            # Handler exceptions routinely include host paths, credentials or
            # provider payloads.  Keep those out of the model-visible result
            # and out of logs; the stable exception class is enough to triage.
            error_type = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:80] or "Error"
            _logger.warning("Echo dynamic tool failed (%s)", error_type)
            result = dynamic_tool_failure(f"Echo dynamic tool failed ({error_type})")
        else:
            if _TAINT_ORDER.get(observed_taint, 0) > _TAINT_ORDER.get(self._taint, 0):
                self._taint = observed_taint
                mark_injection_taint(observed_taint)
            result = validate_dynamic_tool_response(
                {
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": output or ("tool failed" if is_error else "tool completed"),
                        }
                    ],
                    "success": not is_error,
                }
            )

        self._results[call_id] = (advertised, fingerprint, result)
        self._results.move_to_end(call_id)
        while len(self._results) > _MAX_RESULT_CACHE:
            self._results.popitem(last=False)
        return dict(result)

    def _execution_session(self, *, auto_approve: bool) -> Session:
        metadata = dict(self._metadata)
        if auto_approve:
            # The broker has already performed the exact-call approval above.
            # This lets the executor's independent governance check distinguish
            # that fact from an unreviewed direct invocation.
            metadata["auto_approve"] = True
        return Session(
            actor=self._principal_id,
            agent=self._agent,
            thread_id=self._outer_thread_id,
            conversation_id=self._outer_thread_id,
            turn_id=self._outer_turn_id,
            metadata=metadata,
        )

    def _execute_sync(
        self,
        entry: _CatalogEntry,
        arguments: dict[str, Any],
        call_id: str,
        approval_handled: bool,
    ) -> tuple[str, bool, str]:
        session = self._execution_session(auto_approve=approval_handled)
        with session_scope(session), parent_tool_use_scope(call_id):
            if self._taint in _TAINT_ORDER:
                mark_injection_taint(self._taint)
            set_injection_gate_handled(approval_handled)
            try:
                output, is_error = execute_native_tool_call(
                    self._stack,
                    {
                        "id": call_id,
                        "name": entry.skill_name,
                        "arguments": dict(arguments),
                    },
                )
                return output, is_error, current_injection_taint()
            finally:
                set_injection_gate_handled(False)

    def _interrupted(self) -> bool:
        try:
            return bool(self._is_interrupted())
        except Exception:  # noqa: BLE001 - broken cancellation source fails closed
            return True


__all__ = [
    "CodexDynamicToolBroker",
    "CodexDynamicToolCatalog",
    "DYNAMIC_TOOL_CALL_METHOD",
    "dynamic_tool_failure",
    "validate_dynamic_tool_response",
]
