"""Execute a ``TeamTopology`` for one task.

``TeamRunner`` is the bridge between organization-level recipes and the
single-agent ReAct loop. Per protocol:

  * ``sequential`` — call each role's agent once, in canonical order
    (planner → researcher → generator → critic → synthesizer →
    evaluator), passing each output forward as the next role's input
    context. Roles not present in the topology are skipped.

  * ``evaluator_optimizer`` — generator produces a draft, evaluator
    scores it; if the score is below ``quality_threshold``, the
    generator retries with the evaluator's feedback in context.
    Bounded by ``max_iterations``.

The runner is intentionally **transport-agnostic**: it doesn't know
about WebSocket gateways or SSE. It just orchestrates calls into the
single-agent dispatcher provided by the caller (defaults to the
sub-agent bridge).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .topology import CoordinationProtocol, Role, TeamTopology

_logger = logging.getLogger("echo.organization.team_runner")


# Canonical order for sequential protocol — only roles present in
# the topology actually run.
_SEQUENTIAL_ORDER: tuple[Role, ...] = (
    Role.PLANNER,
    Role.RESEARCHER,
    Role.GENERATOR,
    Role.CRITIC,
    Role.SYNTHESIZER,
    Role.EVALUATOR,
)


@dataclass
class RoleOutput:
    role: Role
    agent_id: str
    output: str
    score: float | None = None
    duration_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeamRunResult:
    topology_name: str
    topology_fingerprint: str
    task_bucket: str
    success: bool
    final_output: str
    role_outputs: list[RoleOutput] = field(default_factory=list)
    iterations: int = 1
    total_duration_ms: float = 0.0
    error: str | None = None
    # ① 失败隔离：记录本轮哪些角色失败但其部分产出被保留继续，便于上游
    # 在最终交付上标注"降级"而非整体失败。
    degraded_roles: list[str] = field(default_factory=list)
    # ③ critic 反驳 → generator 重写：本轮实际发生的修订轮数。
    revision_rounds: int = 0

    @property
    def quality_score(self) -> float | None:
        """The last evaluator score, if any.

        For evaluator_optimizer this is the threshold metric. For
        sequential this is whatever the evaluator role (if present)
        emitted last; ``None`` when no evaluator ran.
        """
        for out in reversed(self.role_outputs):
            if out.role == Role.EVALUATOR and out.score is not None:
                return out.score
        return None


# A "role caller" is the unit of agent invocation a TeamRunner uses.
# Default implementation delegates to ``runtime.execution.subagents.bridge``
# but tests / alternative deployments can plug in their own.
RoleCaller = Callable[..., dict[str, Any]]


# Roles whose work is "research-style" (researcher / fact-checker /
# critic / evaluator) auto-route to the cheap subagent model. Heavy
# reasoning roles (planner / generator / synthesizer) keep the parent's
# primary model. This is the team_runner equivalent of the per-spec
# ``cheap`` flag in ``call_agent_parallel``.
def _role_uses_cheap_model(role: Role) -> bool:
    """Return True when ``role`` should run on the cheap subagent model."""
    from runtime.safety.organization.team_role_models import role_uses_cheap

    return role_uses_cheap(role)


def _default_role_caller(
    *,
    agent_id: str,
    prompt: str,
    context: dict[str, Any] | None,
    timeout_seconds: int | None,
    use_cheap_model: bool = False,
    event_emitter: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one role via the existing subagent bridge."""
    from runtime.execution.subagents.bridge import call_subagent

    return call_subagent(
        agent_id=agent_id,
        prompt=prompt,
        context=context or {},
        timeout_seconds=timeout_seconds,
        use_cheap_model=use_cheap_model,
        event_emitter=event_emitter,
    )


def _context_risk_level(context: dict[str, Any]) -> str:
    for key in (
        "task_risk_level",
        "risk_level",
        "approval_risk_level",
        "quality_risk_level",
    ):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "low"


# ── Score parser ──────────────────────────────────────────────


_SCORE_PATTERN = re.compile(
    r"(?:score|quality|rating)[:\s]+([0-9]*\.?[0-9]+)",
    re.IGNORECASE,
)


def _parse_evaluator_score(text: str) -> float | None:
    """Extract a 0..1 quality score from evaluator output.

    Looks for ``score: 0.78`` / ``quality 0.4`` / ``rating: 1.0``.
    Returns ``None`` when no score is found so the caller can decide
    whether to treat that as pass (no signal) or fail (regression).
    """
    if not text:
        return None
    match = _SCORE_PATTERN.search(text)
    if not match:
        return None
    try:
        raw = float(match.group(1))
    except (TypeError, ValueError):
        return None
    # Tolerate evaluators that emit 0-100 instead of 0-1.
    # We only treat values 10-100 as percent (a "score: 5" likely
    # means 5 out of 10 in some rubric — clamp instead of divide).
    if raw > 10.0 and raw <= 100.0:
        raw = raw / 100.0
    return max(0.0, min(1.0, raw))


def _first_role_error(outputs: list[RoleOutput]) -> str | None:
    for output in outputs:
        if output.error:
            return f"{output.role}({output.agent_id}): {output.error}"
    return None


def _has_fatal_role_error(outputs: list[RoleOutput]) -> bool:
    """Whether an error prevented a valid terminal team deliverable.

    ① 失败隔离：单个角色失败不再视为整体致命。只要终态交付（synthesizer，
    否则 evaluator，否则最后一个有产出的角色）存在且成功，就认为团队仍
    交付了可用结果——先前的角色失败会被记录在 ``degraded_roles``，上游可
    以标注"降级交付"。只有终态交付本身缺失/失败才算 fatal。
    """
    terminal = next(
        (
            output
            for output in reversed(outputs)
            if output.role in (Role.SYNTHESIZER, Role.EVALUATOR)
            and output.output
            and not output.error
        ),
        None,
    )
    if terminal is not None:
        return False
    # 没有终态角色成功：若任一角色有非空产出（部分交付）也不算致命，
    # 只有完全没有任何可交付内容才是致命。
    return not any(output.output and not output.error for output in outputs)


def _collect_degraded_roles(outputs: list[RoleOutput]) -> list[str]:
    """① 失败隔离：列出所有失败角色的 role 名（去重、按顺序）。"""
    seen: list[str] = []
    for output in outputs:
        if output.error and str(output.role) not in seen:
            seen.append(str(output.role))
    return seen


# ── Critic 问题判定 + 重写 prompt ───────────────────────────

# 3 critic rebut -> generator rewrite: these words mean the critic flagged substantive issues.
# Pure 'no issues / all fine' verdicts do NOT trigger a rewrite.
_CRITIC_ISSUE_HINTS = (
    "问题",
    "缺失",
    "缺",
    "错误",
    "不准确",
    "需修正",
    "需补充",
    "需要修正",
    "需要补充",
    "建议",
    "风险",
    "漏洞",
    "矛盾",
    "冲突",
    "遗漏",
    "不足",
    "含糊",
    "无法",
    "issue",
    "flaw",
    "error",
    "concern",
    "contradiction",
    "missing",
    "gap",
)
_CRITIC_CLEAN_HINTS = (
    "未发现",
    "没有问题",
    "一切正常",
    "没问题",
    "全部正确",
    "无需",
    "no issues",
    "all good",
    "no problems",
    "looks good",
    "looks correct",
    "all correct",
)


def _critic_has_issues(text: str) -> bool:
    # 3 Heuristic: did the critic flag substantive issues worth a rewrite?
    if not text or not text.strip():
        return False
    low = text.lower()
    if any(hint in low for hint in _CRITIC_CLEAN_HINTS):
        return False
    return any(hint in low for hint in _CRITIC_ISSUE_HINTS)


# ── Runner ────────────────────────────────────────────────────


class TeamRunner:
    """Drive one TeamTopology against one task."""

    def __init__(
        self,
        *,
        role_caller: RoleCaller | None = None,
        timeout_seconds: int | None = 600,
        event_emitter: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._role_caller = role_caller or _default_role_caller
        self._timeout = timeout_seconds
        # Optional sink for live progress events. The realtime gateway
        # passes a thread-safe ``run_coroutine_threadsafe`` shim that
        # marshals every event back to the WS so the user sees the
        # team's progress unfold instead of staring at a blank stream
        # for 60+ seconds while ``run`` blocks. Best-effort: failures
        # in the emitter must not abort the run. Events emitted:
        #   ``team_role_start``  · before each role begins
        #   ``team_role_end``    · after each role finishes
        #   plus everything ``call_subagent`` emits while a role runs:
        #   ``subagent_spawned`` / ``sub_tool_start`` / ``sub_tool_end``
        #   / ``subagent_finished``.
        self._event_emitter = event_emitter

    def _emit(self, event: dict[str, Any]) -> None:
        if self._event_emitter is None:
            return
        try:
            self._event_emitter(event)
        except Exception:  # noqa: BLE001 — telemetry must not crash the run
            _logger.debug("team_runner emitter raised", exc_info=True)

    def run(
        self,
        topology: TeamTopology,
        task: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> TeamRunResult:
        """Execute ``topology`` for ``task`` and return aggregated result."""
        started = time.monotonic()
        result = TeamRunResult(
            topology_name=topology.name,
            topology_fingerprint=topology.fingerprint,
            task_bucket=topology.task_bucket,
            success=False,
            final_output="",
        )

        try:
            if topology.protocol == CoordinationProtocol.SEQUENTIAL:
                self._run_sequential(topology, task, context or {}, result)
            elif topology.protocol == CoordinationProtocol.EVALUATOR_OPTIMIZER:
                self._run_evaluator_optimizer(
                    topology,
                    task,
                    context or {},
                    result,
                )
            elif topology.protocol == CoordinationProtocol.PARALLEL:
                self._run_parallel(topology, task, context or {}, result)
            else:  # pragma: no cover - guard for future protocols
                raise ValueError(f"unknown protocol: {topology.protocol}")
            # Mark success when no role recorded an error and we have output.
            if result.final_output and not _has_fatal_role_error(result.role_outputs):
                result.success = True
            elif result.error is None:
                result.error = _first_role_error(result.role_outputs)
            # ① 失败隔离：无论成败都记录降级角色，便于上游标注"降级交付"。
            if not result.degraded_roles:
                result.degraded_roles = _collect_degraded_roles(result.role_outputs)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("team_runner: topology %s crashed", topology.name)
            result.error = f"{type(exc).__name__}: {exc}"
            result.success = False
        finally:
            result.total_duration_ms = (time.monotonic() - started) * 1000.0

        return result

    # ── Sequential protocol ──────────────────────────────────

    def _run_sequential(
        self,
        topology: TeamTopology,
        task: str,
        context: dict[str, Any],
        result: TeamRunResult,
    ) -> None:
        """① 失败隔离：任一角色失败都不再中断流水线。

        失败角色的部分产出（如果有）与错误信息一起保留并继续传给下游；
        下游 prompt 里会显式标注"上一步失败/降级"，让角色据此规避垃圾交接。
        只有完全没有任何可交付内容才算整体失败（见 ``_has_fatal_role_error``）。
        ③ critic 反驳：若拓扑同时含 critic 与 generator，critic 指出问题后
        会让 generator 基于批评意见重写一版，再交给 synthesizer，循环受
        ``max_iterations`` 约束。
        """
        prior_outputs: list[RoleOutput] = []
        ran_critic: RoleOutput | None = None
        for role in _SEQUENTIAL_ORDER:
            spec = topology.agents.get(role)
            if spec is None:
                continue
            # ③ critic → generator 重写：critic 跑完后，若拓扑里有 generator
            # 且 critic 提出了实质问题，就让 generator 带着批评意见重写。
            if role == Role.SYNTHESIZER and ran_critic is not None:
                gen_spec = topology.agents.get(Role.GENERATOR)
                if gen_spec is not None and _critic_has_issues(ran_critic.output):
                    for _ in range(max(1, topology.max_iterations)):
                        gen_spec2 = topology.agents.get(Role.GENERATOR)
                        if gen_spec2 is None:
                            break
                        self._emit(
                            {
                                "type": "team_revision_start",
                                "topology": topology.name,
                                "round_no": result.revision_rounds + 1,
                                "agent_id": gen_spec2.agent_id,
                            }
                        )
                        revision_prompt = self._compose_critic_revision_prompt(
                            task,
                            prior_outputs,
                            ran_critic,
                            round_no=result.revision_rounds + 1,
                        )
                        revised = self._invoke_role(
                            Role.GENERATOR,
                            gen_spec2,
                            revision_prompt,
                            context,
                            topology,
                        )
                        result.revision_rounds += 1
                        result.role_outputs.append(revised)
                        prior_outputs.append(revised)
                        if revised.error:
                            # 重写失败也隔离：保留部分产出继续，不中断。
                            break
                        # 重写后可选再跑一次 critic 复核（如存在该角色），
                        # 复核无新问题就收手。
                        critic_spec = topology.agents.get(Role.CRITIC)
                        if critic_spec is None:
                            break
                        recheck = self._invoke_role(
                            Role.CRITIC,
                            critic_spec,
                            self._compose_prompt(Role.CRITIC, task, prior_outputs),
                            context,
                            topology,
                        )
                        result.role_outputs.append(recheck)
                        prior_outputs.append(recheck)
                        if not _critic_has_issues(recheck.output):
                            break
                        ran_critic = recheck
            prompt = self._compose_prompt(role, task, prior_outputs)
            role_out = self._invoke_role(role, spec, prompt, context, topology)
            # Auto-parse score on evaluator outputs so the perf log
            # captures quality even under the sequential protocol.
            if role == Role.EVALUATOR and role_out.score is None:
                role_out.score = _parse_evaluator_score(role_out.output)
            result.role_outputs.append(role_out)
            prior_outputs.append(role_out)
            if role == Role.CRITIC:
                ran_critic = role_out
            # ① 失败隔离：角色失败不中断——部分产出与错误一起继续传递。
        if prior_outputs:
            # Final output = last successful role.
            last_ok = next(
                (o for o in reversed(prior_outputs) if o.output and not o.error),
                None,
            )
            result.final_output = (last_ok or prior_outputs[-1]).output
        result.degraded_roles = _collect_degraded_roles(result.role_outputs)

    # ── Evaluator-optimizer protocol ─────────────────────────

    def _run_evaluator_optimizer(
        self,
        topology: TeamTopology,
        task: str,
        context: dict[str, Any],
        result: TeamRunResult,
    ) -> None:
        gen_spec = topology.agents[Role.GENERATOR]
        eval_spec = topology.agents[Role.EVALUATOR]
        last_eval: RoleOutput | None = None
        last_gen: RoleOutput | None = None

        for iteration in range(topology.max_iterations):
            result.iterations = iteration + 1
            gen_prompt = self._compose_generator_prompt(
                task,
                last_gen,
                last_eval,
            )
            last_gen = self._invoke_role(
                Role.GENERATOR,
                gen_spec,
                gen_prompt,
                context,
                topology,
            )
            result.role_outputs.append(last_gen)
            if last_gen.error:
                return

            eval_prompt = self._compose_evaluator_prompt(task, last_gen.output)
            last_eval = self._invoke_role(
                Role.EVALUATOR,
                eval_spec,
                eval_prompt,
                context,
                topology,
            )
            last_eval.score = _parse_evaluator_score(last_eval.output)
            result.role_outputs.append(last_eval)
            if last_eval.error:
                return

            # Accept when no parseable score (no signal — trust generator),
            # or when score clears the threshold.
            if last_eval.score is None or last_eval.score >= topology.quality_threshold:
                result.final_output = last_gen.output
                return

        # All iterations exhausted without clearing threshold —
        # surface the latest generator output as a partial-credit
        # result; the performance log will see the iteration count
        # and the final low score.
        if last_gen is not None:
            result.final_output = last_gen.output

    # ── Parallel protocol ────────────────────────────────────

    def _run_parallel(
        self,
        topology: TeamTopology,
        task: str,
        context: dict[str, Any],
        result: TeamRunResult,
    ) -> None:
        """Run roles in canonical order, fanning out multi-replica roles.

        Roles with ``parallel_replicas > 1`` (e.g. a researcher pool) are
        launched concurrently — one sub-agent per replica — each receiving
        the same prior context plus its own ``replica_index``/``replica_count``.
        Single-replica roles (planner, critic, synthesizer) run once and
        their output feeds the next role, mirroring ``_run_sequential``.

        A failing replica never aborts the pipeline: its ``RoleOutput`` keeps
        the error for observability, its partial output (if any) still feeds
        the synthesizer, and the remaining replicas keep running.
        """
        prior_outputs: list[RoleOutput] = []
        for role in _SEQUENTIAL_ORDER:
            spec = topology.agents.get(role)
            if spec is None:
                continue
            replicas = spec.parallel_replicas or 1
            if replicas <= 1:
                prompt = self._compose_prompt(role, task, prior_outputs)
                role_out = self._invoke_role(role, spec, prompt, context, topology)
                result.role_outputs.append(role_out)
                prior_outputs.append(role_out)
                # ① 失败隔离：单副本角色失败不再中断流水线——部分产出与错误
                # 一起保留并继续传给下游，只有完全无可交付内容才判整体失败。
                continue

            base_prompt = self._compose_prompt(role, task, prior_outputs)
            self._emit(
                {
                    "type": "team_parallel_start",
                    "topology": topology.name,
                    "role": str(role),
                    "agent_id": spec.agent_id,
                    "replicas": replicas,
                }
            )
            with ThreadPoolExecutor(
                max_workers=replicas,
                thread_name_prefix=f"team-par-{role}",
            ) as pool:
                futures = [
                    pool.submit(
                        self._invoke_role,
                        role,
                        spec,
                        base_prompt,
                        context,
                        topology,
                        replica_index=index + 1,
                        replica_count=replicas,
                    )
                    for index in range(replicas)
                ]
                for future in futures:
                    role_out = future.result()
                    result.role_outputs.append(role_out)
                    prior_outputs.append(role_out)
            # ② 并行副本交叉验证：多副本角色（如研究员池）跑完后，若拓扑里有
            # critic，就让 critic 对全部副本产出做一次交叉核查（去重/冲突/
            # 覆盖缺口），把核查结论喂给 synthesizer——副本之间"互相看得见"。
            if replicas > 1 and Role.CRITIC in topology.agents:
                critic_spec = topology.agents[Role.CRITIC]
                cross_prompt = self._compose_cross_check_prompt(
                    task,
                    [o for o in prior_outputs if o.role == role],
                    topology,
                )
                self._emit(
                    {
                        "type": "team_cross_check_start",
                        "topology": topology.name,
                        "role": str(role),
                        "replicas": replicas,
                        "agent_id": critic_spec.agent_id,
                    }
                )
                cross_out = self._invoke_role(
                    Role.CRITIC,
                    critic_spec,
                    cross_prompt,
                    context,
                    topology,
                )
                cross_out.metadata["cross_validation"] = True
                result.role_outputs.append(cross_out)
                prior_outputs.append(cross_out)
        self._emit(
            {
                "type": "team_parallel_end",
                "topology": topology.name,
            }
        )
        if prior_outputs:
            # Final output = last successful role (the synthesizer).
            last_ok = next(
                (o for o in reversed(prior_outputs) if o.output and not o.error),
                None,
            )
            result.final_output = (last_ok or prior_outputs[-1]).output
        result.degraded_roles = _collect_degraded_roles(result.role_outputs)

    # ── Helpers ──────────────────────────────────────────────

    def _invoke_role(
        self,
        role: Role,
        spec: Any,
        prompt: str,
        context: dict[str, Any],
        topology: TeamTopology,
        replica_index: int | None = None,
        replica_count: int | None = None,
    ) -> RoleOutput:
        start = time.monotonic()
        merged_ctx = {
            **context,
            "team_topology": topology.name,
            "team_role": str(role),
        }
        if replica_index is not None:
            merged_ctx["team_replica_index"] = replica_index
            merged_ctx["team_replica_count"] = replica_count or 1
        if spec.system_addendum:
            merged_ctx["system_addendum"] = _render_replica_addendum(
                spec.system_addendum,
                replica_index=replica_index,
                replica_count=replica_count,
            )
        if spec.model:
            merged_ctx["model"] = spec.model
        if spec.temperature is not None:
            merged_ctx["temperature"] = spec.temperature
        route_decision = self._route_decision(spec.agent_id, context)
        merged_ctx["subagent_route_decision"] = route_decision
        if route_decision.get("action") == "block":
            duration = (time.monotonic() - start) * 1000.0
            error = str(route_decision.get("reason") or "subagent blocked by routing policy")
            self._emit(
                {
                    "type": "team_role_blocked",
                    "topology": topology.name,
                    "role": str(role),
                    "agent_id": spec.agent_id,
                    "duration_ms": duration,
                    "route_decision": route_decision,
                    "error": error,
                }
            )
            return RoleOutput(
                role=role,
                agent_id=spec.agent_id,
                output="",
                error=error,
                duration_ms=duration,
                metadata={"subagent_route_decision": route_decision},
            )
        # Cheap-model routing: roles outside the primary set (planner /
        # generator / synthesizer) run on the cheap subagent model unless
        # the spec pinned an explicit ``model`` override above. The
        # bridge respects ``model_name`` already pinned in context, so an
        # explicit ``AgentSpec.model`` always wins over auto-cheap.
        cheap_for_role = _role_uses_cheap_model(role)
        # Emit role-start so the gateway can open an AgentMessageItem
        # in the UI before the model spends 30+ seconds churning. Without
        # this the user sees the first role's output drop in only after
        # ``_role_caller`` returns.
        self._emit(
            {
                "type": "team_role_start",
                "topology": topology.name,
                "role": str(role),
                "agent_id": spec.agent_id,
                "use_cheap_model": cheap_for_role,
                "route_decision": route_decision,
            }
        )
        # Forward subagent lifecycle (spawn / sub_tool_start / sub_tool_end /
        # subagent_finished) to the caller's emitter. Older role callers
        # don't accept ``event_emitter``; the TypeError fallback below keeps
        # those working unchanged.
        caller_emitter = self._event_emitter
        # ── Heartbeat thread ── keep the WS alive while the role runs.
        # The frontend's pong-timeout is 70s; emit a lightweight
        # ``team_heartbeat`` every 15s so there's always traffic on the
        # line even when the subagent is doing a long tool call that
        # doesn't produce ``sub_text_delta`` chunks.
        stop_heartbeat = threading.Event()

        def _heartbeat_loop() -> None:
            elapsed = 0.0
            while not stop_heartbeat.wait(timeout=15.0):
                elapsed = time.monotonic() - start
                self._emit(
                    {
                        "type": "team_heartbeat",
                        "topology": topology.name,
                        "role": str(role),
                        "agent_id": spec.agent_id,
                        "elapsed_s": round(elapsed, 1),
                    }
                )

        hb_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"team-hb-{role}-{spec.agent_id}",
            daemon=True,
        )
        hb_thread.start()
        try:
            try:
                raw = self._role_caller(
                    agent_id=spec.agent_id,
                    prompt=prompt,
                    context=merged_ctx,
                    timeout_seconds=self._timeout,
                    use_cheap_model=cheap_for_role,
                    event_emitter=caller_emitter,
                )
            except TypeError:
                # Pre-emitter / pre-cheap-routing role callers. Try the
                # next-most-recent signature, then the legacy one, so an
                # adapter from any era still works.
                try:
                    raw = self._role_caller(
                        agent_id=spec.agent_id,
                        prompt=prompt,
                        context=merged_ctx,
                        timeout_seconds=self._timeout,
                        use_cheap_model=cheap_for_role,
                    )
                except TypeError:
                    raw = self._role_caller(
                        agent_id=spec.agent_id,
                        prompt=prompt,
                        context=merged_ctx,
                        timeout_seconds=self._timeout,
                    )
        except Exception as exc:  # noqa: BLE001
            duration = (time.monotonic() - start) * 1000.0
            self._emit(
                {
                    "type": "team_role_end",
                    "topology": topology.name,
                    "role": str(role),
                    "agent_id": spec.agent_id,
                    "status": "error",
                    "duration_ms": duration,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return RoleOutput(
                role=role,
                agent_id=spec.agent_id,
                output="",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration,
            )
        finally:
            stop_heartbeat.set()
            hb_thread.join(timeout=2.0)
        output = ""
        error: str | None = None
        if isinstance(raw, dict):
            output = str(raw.get("output", "") or "")
            if not raw.get("success", True):
                error = str(raw.get("error") or "subagent reported failure")
        elif isinstance(raw, str):
            output = raw
        duration = (time.monotonic() - start) * 1000.0
        self._emit(
            {
                "type": "team_role_end",
                "topology": topology.name,
                "role": str(role),
                "agent_id": spec.agent_id,
                "status": "error" if error else "success",
                "duration_ms": duration,
                "output": output,
                "error": error,
                "route_decision": route_decision,
            }
        )
        return RoleOutput(
            role=role,
            agent_id=spec.agent_id,
            output=output,
            error=error,
            duration_ms=duration,
            metadata={"subagent_route_decision": route_decision},
        )

    def _route_decision(
        self,
        agent_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            from runtime.safety.evolution.subagent_routing import (
                decide_subagent_route,
            )

            decision = decide_subagent_route(
                role=agent_id,
                risk_level=_context_risk_level(context),
                review_queue_path=context.get("review_queue_path"),
                subagent_policy_path=context.get("subagent_policy_path"),
                enabled=bool(context.get("enable_subagent_fitness_routing", True)),
            )
            return decision.to_dict()
        except Exception as exc:  # noqa: BLE001
            _logger.debug(
                "subagent fitness routing skipped · agent_id=%s error=%s",
                agent_id,
                exc,
            )
            return {
                "schema": "echo.subagent_route_decision.v1",
                "role": agent_id,
                "action": "allow",
                "reason": "subagent fitness routing unavailable",
                "risk_level": _context_risk_level(context),
                "verdict": "unknown",
                "score": None,
                "confidence": 0.0,
                "evidence_item_ids": [],
            }

    def _compose_prompt(
        self,
        role: Role,
        task: str,
        prior: list[RoleOutput],
    ) -> str:
        if not prior:
            return f"[role: {role}]\n\nTask:\n{task}"
        bits = [f"[role: {role}]", f"Task:\n{task}", ""]
        for p in prior:
            bits.append(f"--- {p.role} output ---")
            bits.append(p.output)
            bits.append("")
        return "\n".join(bits)

    def _compose_generator_prompt(
        self,
        task: str,
        last_gen: RoleOutput | None,
        last_eval: RoleOutput | None,
    ) -> str:
        if last_gen is None or last_eval is None:
            return f"[role: generator]\n\nTask:\n{task}"
        return (
            f"[role: generator · retry]\n\n"
            f"Task:\n{task}\n\n"
            f"--- previous attempt ---\n{last_gen.output}\n\n"
            f"--- evaluator feedback (score: "
            f"{last_eval.score if last_eval.score is not None else 'n/a'}) ---\n"
            f"{last_eval.output}\n\n"
            f"Address the feedback and produce a revised answer."
        )

    def _compose_cross_check_prompt(
        self,
        task: str,
        replica_outputs: list[RoleOutput],
        topology: TeamTopology,
    ) -> str:
        """② 并行副本交叉验证 prompt：让 critic 对研究员池的全部产出做交叉核查。

        Critic 看到所有副本的结果后，指出重复/冲突/覆盖缺口，供 synthesizer
        去重与收敛——副本之间通过 critic 这一"裁判"互相可见，而不是各查各的。
        """
        bits = ["[role: critic · cross-validation]", f"Task:\n{task}", ""]
        for i, out in enumerate(replica_outputs, start=1):
            bits.append(f"--- researcher replica {i} ({out.agent_id}) ---")
            bits.append(out.output if out.output else "(no output)")
            bits.append("")
        bits.append(
            "以上是同一角色并行副本各自独立调研的全部结果。请做交叉核查：\n"
            "1) 找出重复/重叠的结论；\n"
            "2) 找出相互矛盾的地方；\n"
            "3) 指出还没覆盖到的关键缺口。\n"
            "只输出核查结论，逐条列出（重复/冲突/缺口），不要复述各副本内容。"
        )
        return "\n".join(bits)

    def _compose_critic_revision_prompt(
        self,
        task: str,
        prior: list[RoleOutput],
        critic: RoleOutput,
        *,
        round_no: int = 1,
    ) -> str:
        """3 Let the generator rewrite against the critic's specific feedback.

        Hands the previous generator draft plus the critic's concrete criticism
        back to the generator and asks it to address each point — a real
        "critic rebuttal -> generator rewrite" loop instead of passing the
        critic notes as plain text to the synthesizer.
        """
        gen_out = next(
            (o for o in reversed(prior) if o.role == Role.GENERATOR),
            None,
        )
        prev = gen_out.output if gen_out else "(no previous draft)"
        bits = [
            "[role: generator · critic-revision]",
            f"Task:\n{task}",
            "",
            f"--- previous generator output (v{max(0, round_no - 1)}) ---",
            prev,
            "",
            f"--- critic's feedback (round {round_no}) ---",
            critic.output,
            "",
            "Please address each point: what you accept, what you keep and why. "
            "Then produce the revised answer only — do not restate the feedback.",
        ]
        return "\n".join(bits)

    def _compose_evaluator_prompt(self, task: str, generator_output: str) -> str:
        return (
            "[role: evaluator]\n\n"
            "Score the candidate answer on a 0-1 scale and explain why.\n"
            "Begin your reply with: score: <number between 0 and 1>\n\n"
            f"Task:\n{task}\n\n"
            f"Candidate answer:\n{generator_output}"
        )


def _render_replica_addendum(
    addendum: str,
    *,
    replica_index: int | None,
    replica_count: int | None,
) -> str:
    """Fill ``{replica_index}`` / ``{replica_count}`` placeholders in a
    role's system addendum.

    Used by the ``parallel`` protocol so a multi-replica role's prompt can
    tell each concurrent copy which slice of the work it owns (e.g. a
    researcher pool of 3, where replica 2 researches the 2nd sub-problem).
    Only the named placeholders are substituted — any other braces in the
    addendum are left untouched (``.format`` would reject them).
    """
    if replica_index is None:
        return addendum
    return addendum.replace("{replica_index}", str(replica_index)).replace(
        "{replica_count}", str(replica_count or 1)
    )


__all__ = [
    "RoleCaller",
    "RoleOutput",
    "TeamRunResult",
    "TeamRunner",
]
