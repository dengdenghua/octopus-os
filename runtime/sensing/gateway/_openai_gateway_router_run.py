from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    ParsedIntent,
)

from ._openai_gateway_router_synthesize import synthesize_reply
from .openai_formatting import chat_completion_envelope as _chat_completion_envelope
from .openai_gateway.context_manager import (
    _conversation_messages_payload,
    _profile_memories_payload,
    _runtime_soul_for_agent,
)
from .openai_gateway.stream_handler import _direct_llm_fallback
from .openai_gateway.turn_context import (
    candidate_outcome_for_trajectory,
    prepare_chat_turn,
    settle_candidate_outcomes,
)


def _run_chat(
    stack: Any,
    intent: ParsedIntent,
    model: str,
    default_arm: str,
    *,
    optimizer: Any = None,
    actor: str | None = None,
    agent: Any = None,
    force_deep: bool = False,
    conversation_id: str | None = None,
    tenant_id: str | None = None,
    owner_actor_id: str | None = None,
) -> dict[str, Any]:
    task_id = uuid4()
    variant_name: str | None = None
    from runtime.platform.process.session import session_scope
    from runtime.safety.approval.cancellation import OperationCancelled

    turn_session = prepare_chat_turn(
        stack,
        turn_id=str(task_id),
        actor=actor,
        agent=agent,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        owner_actor_id=owner_actor_id,
    ).session

    # Hint the planner/optimizer that the caller wants a real multi-step run
    # (not a trivial direct answer), so the graph is worth tracing/replaying.
    if force_deep and isinstance(getattr(intent, "user_context", None), dict):
        intent.user_context["force_deep"] = True

    # Planning must share the same Session as execution. Governed role and
    # prompt candidates are selected during plan assembly; binding only the
    # executor made canary routing impossible on this compatibility surface.
    with session_scope(turn_session):
        plan_kwargs: dict[str, Any] = {}
        if agent is not None:
            plan_kwargs["allowed_skills"] = agent.allowed_skill_union()
            runtime_soul = _runtime_soul_for_agent(agent)
            if runtime_soul:
                plan_kwargs["soul"] = runtime_soul
        if model and model not in ("echo-agent", "", None):
            plan_kwargs["model"] = model

        if optimizer is not None:
            try:
                graph = optimizer.plan(intent, task_id=task_id, **plan_kwargs)
                variant_name = optimizer.variant_for_task(task_id)
            except TypeError:
                try:
                    graph = optimizer.plan(intent, task_id=task_id)
                    variant_name = optimizer.variant_for_task(task_id)
                except OperationCancelled:
                    settle_candidate_outcomes(str(task_id), None)
                    raise
                except Exception as e:  # noqa: BLE001
                    settle_candidate_outcomes(str(task_id), False)
                    raise HTTPException(500, f"optimizer plan failed: {e}") from e
            except OperationCancelled:
                settle_candidate_outcomes(str(task_id), None)
                raise
            except Exception as e:  # noqa: BLE001
                settle_candidate_outcomes(str(task_id), False)
                raise HTTPException(500, f"optimizer plan failed: {e}") from e
        else:
            try:
                graph = stack.planner.plan(intent, **plan_kwargs)
            except TypeError:
                try:
                    graph = stack.planner.plan(intent)
                except OperationCancelled:
                    settle_candidate_outcomes(str(task_id), None)
                    raise
                except Exception as e:  # noqa: BLE001
                    reply = _direct_llm_fallback(stack, intent, agent, model=model)
                    settle_candidate_outcomes(str(task_id), False)
                    if reply is not None:
                        return _chat_completion_envelope(
                            reply,
                            model=model,
                            actor=actor,
                            agent=agent,
                            extra={
                                "fallback": f"planner_error: {e}",
                                "deep_requested": force_deep,
                            },
                        )
                    raise HTTPException(500, f"planner failed: {e}") from e
            except OperationCancelled:
                settle_candidate_outcomes(str(task_id), None)
                raise
            except Exception as e:  # noqa: BLE001
                reply = _direct_llm_fallback(stack, intent, agent, model=model)
                settle_candidate_outcomes(str(task_id), False)
                if reply is not None:
                    return _chat_completion_envelope(
                        reply,
                        model=model,
                        actor=actor,
                        agent=agent,
                        extra={"fallback": f"planner_error: {e}", "deep_requested": force_deep},
                    )
                raise HTTPException(500, f"planner failed: {e}") from e

    arm_id_str = default_arm
    if agent is not None and len(agent.arms) > 0:
        first = next(iter(agent.arms))
        arm_id_str = str(first.arm_id)

    budget = Budget(
        task_id=graph.task_id,
        limits=BudgetLimits(tokens=50_000, usd=0.50),
    )
    try:
        with session_scope(turn_session):
            traj = stack.runtime.run(
                graph,
                budget=budget,
                caller=f"arms/{arm_id_str}",
                arm_id=ArmId(arm_id_str),
                actor=actor,
            )
    except OperationCancelled:
        settle_candidate_outcomes(str(task_id), None)
        raise
    except Exception:
        settle_candidate_outcomes(str(task_id), False)
        raise

    candidate_success = candidate_outcome_for_trajectory(traj)
    clean_success = candidate_success is True

    if optimizer is not None:
        try:
            optimizer.record_outcome(task_id, success=clean_success)
        except Exception as _e:  # noqa: BLE001
            _logger = logging.getLogger(__name__)
            _logger.debug("optimizer.record_outcome failed: %s", _e)

    try:
        assistant_text = synthesize_reply(
            stack,
            goal=intent.normalized_goal,
            trajectory=traj,
            model=model,
            agent=agent,
            conversation_messages=_conversation_messages_payload(intent),
            profile_memories=_profile_memories_payload(intent),
            user_context=intent.user_context if intent is not None else None,
        )
    except OperationCancelled:
        settle_candidate_outcomes(str(task_id), None)
        raise
    except Exception:
        settle_candidate_outcomes(str(task_id), False)
        raise
    settle_candidate_outcomes(str(task_id), candidate_success)
    finish_reason = "stop" if traj.outcome.success else "failed"
    planner_usage = getattr(graph, "planner_usage", None)
    if not isinstance(planner_usage, dict):
        planner_usage = {}
    planner_prompt_tokens = int(planner_usage.get("input_tokens") or 0)
    planner_completion_tokens = int(planner_usage.get("output_tokens") or 0)
    completion_tokens = budget.tokens_spent + planner_completion_tokens
    total_tokens = planner_prompt_tokens + completion_tokens

    echo_meta: dict[str, Any] = {
        "task_id": str(graph.task_id),
        "strategy": graph.strategy,
        "step_count": traj.step_count,
        "usd_spent": round(budget.usd_spent, 6),
        "success": traj.outcome.success,
    }
    if planner_usage:
        echo_meta["planner_usage"] = {
            "input_tokens": planner_prompt_tokens,
            "output_tokens": planner_completion_tokens,
        }
    if variant_name is not None:
        echo_meta["variant"] = variant_name
    if force_deep:
        echo_meta["deep_requested"] = True
    if actor is not None:
        echo_meta["actor"] = actor
    if agent is not None:
        echo_meta["agent"] = agent.agent_id

    return {
        "id": f"chatcmpl-{uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": assistant_text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": planner_prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "echo": echo_meta,
    }
