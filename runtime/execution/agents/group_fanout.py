"""Group fan-out — the WeChat "boss speaks, everyone chimes in" experience.

When a message lands in a team room and the user wants the whole group to react
(蜂群 / 冒泡), this fans the message out to each member agent IN PARALLEL. Each
member replies briefly in its own persona — a short "bubble", not a full task
run — and the replies come back per-member so the UI can stream each as its own
group-chat bubble.

Each unit is one of the room's in-process roster agents giving a conversational
reply through the same delegation boundary as an ordinary agent turn.

Honest scope: this is still *conversation*, not a full task graph.  It does,
however, now returns a deterministic arbitration summary so downstream team
surfaces can pick a primary response, classify failures, and decide the next
action without re-parsing bubbles.

Debate (蜂群多轮辩论): pass ``debate_rounds >= 2`` to run a second (or Nth)
round where every member sees the previous round's transcript and is invited to
@-rebut or support specific members — grafting the "成员互见 + @反驳" capability
that persistent team rooms have onto our one-shot fan-out.
"""

from __future__ import annotations

import concurrent.futures as _cf
from collections.abc import Callable
from typing import Any

# Keep the group from getting spammy / expensive: a real group chat has a few
# people chime in, not 20. Also bounds the parallel LLM fan-out cost.
_MAX_FANOUT = 6
_MAX_SCALE_FANOUT = 512
_ARBITRATION_SCHEMA = "echo.group_fanout_arbitration.v1"
_SYNTHESIS_SCHEMA = "echo.group_fanout_synthesis.v1"
_CAPACITY_SCHEMA = "echo.group_fanout_capacity.v1"

# Capacity tier thresholds for _capacity_tier().  These are descriptive
# buckets, not scaling limits — they drive the capacity verdict reported
# back to the UI so team surfaces can reason about fan-out size.
_KIMI_SCALE_MEMBERS = 300
_LARGE_TIER_MEMBERS = 64
_TEAM_TIER_MEMBERS = 16
_ROOM_TIER_MEMBERS = 2

# Hard bound on debate rounds so a hostile cue can't spin up unbounded LLM cost.
_MAX_DEBATE_ROUNDS = 3


def _response_id(turn_id: str | None, index: int, agent_id: str) -> str:
    prefix = str(turn_id or "fanout").strip() or "fanout"
    safe_agent = (
        "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in agent_id).strip("-")
        or "agent"
    )
    return f"{prefix}:resp:{index}:{safe_agent}"


def _score_reply(reply: dict[str, Any]) -> int:
    if not reply.get("ok"):
        return 0
    text = str(reply.get("reply") or "").strip()
    if not text:
        return 40
    # Keep this intentionally boring and deterministic.  The score is a
    # readiness signal, not a quality judgment: successful non-empty replies
    # beat empty successes, and slightly fuller replies win stable ties.
    return 100 + min(20, max(1, len(text) // 40))


def _reply_status(reply: dict[str, Any]) -> str:
    if not reply.get("ok"):
        return "failed"
    if str(reply.get("reply") or "").strip():
        return "answered"
    return "empty"


def _capacity_tier(dispatched_members: int, requested_members: int) -> str:
    if requested_members >= _KIMI_SCALE_MEMBERS:
        return "kimi_scale"
    if dispatched_members >= _LARGE_TIER_MEMBERS:
        return "large"
    if dispatched_members >= _TEAM_TIER_MEMBERS:
        return "team_scale"
    if dispatched_members >= _ROOM_TIER_MEMBERS:
        return "room_scale"
    return "single"


def arbitrate_group_fanout(
    replies: list[dict[str, Any]],
    *,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Build a machine-readable arbitration summary for fan-out replies.

    The fan-out path remains lightweight and persona-oriented, but group/team
    callers still need a reliable answer to "who gave the usable response?" and
    "what should the runtime do next?".  This helper is deterministic and does
    not ask another model to judge the model outputs.
    """
    rows: list[dict[str, Any]] = []
    for index, reply in enumerate(replies):
        agent_id = str(reply.get("agent_id") or "")
        status = _reply_status(reply)
        score = _score_reply(reply)
        row = {
            "response_id": str(reply.get("response_id") or _response_id(turn_id, index, agent_id)),
            "roster_index": index,
            "agent_id": agent_id,
            "display_name": str(reply.get("display_name") or agent_id),
            "status": status,
            "ok": bool(reply.get("ok")),
            "score": score,
            "reply_chars": len(str(reply.get("reply") or "").strip()),
            "round": int(reply.get("round") or 1),
            "error": reply.get("error"),
        }
        if status == "failed":
            row["recommended_action"] = "retry_member"
        elif status == "empty":
            row["recommended_action"] = "ask_member_to_expand"
        else:
            row["recommended_action"] = "use_response"
        rows.append(row)

    ranked = sorted(
        rows,
        key=lambda row: (
            int(row["score"]),
            -int(row["roster_index"]),
        ),
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank

    primary = next((row for row in ranked if row["status"] == "answered"), None)
    answered = [row["agent_id"] for row in rows if row["status"] == "answered"]
    failed = [row["agent_id"] for row in rows if row["status"] == "failed"]
    empty = [row["agent_id"] for row in rows if row["status"] == "empty"]

    if primary and failed:
        next_action = "use_primary_and_retry_failed_members"
    elif primary:
        next_action = "use_primary_response"
    elif empty and not failed:
        next_action = "ask_members_to_expand"
    elif failed:
        next_action = "retry_or_fallback_to_single_agent"
    else:
        next_action = "fallback_to_single_agent"

    return {
        "schema": _ARBITRATION_SCHEMA,
        "turn_id": turn_id,
        "primary_response_id": primary["response_id"] if primary else None,
        "primary_agent_id": primary["agent_id"] if primary else None,
        "recommended_next_action": next_action,
        "answered_agent_ids": answered,
        "failed_agent_ids": failed,
        "empty_agent_ids": empty,
        "rounds": max([int(r.get("round") or 1) for r in rows], default=1),
        "ranking": ranked,
        "outcomes": rows,
    }


def synthesize_group_fanout(
    replies: list[dict[str, Any]],
    arbitration: dict[str, Any],
) -> dict[str, Any]:
    """Produce a structured, replayable synthesis for the fanout result.

    This is intentionally deterministic: the runtime already paid for the
    member replies, so the coordinator can expose a useful delivery envelope
    without another model call. UI/replay/benchmarks can then tell whether the
    swarm produced a primary answer, supporting signals, and retry targets.
    """
    rows = [reply for reply in replies if isinstance(reply, dict)]
    by_agent = {
        str(reply.get("agent_id") or ""): str(reply.get("reply") or "").strip() for reply in rows
    }
    primary_agent_id = str(arbitration.get("primary_agent_id") or "").strip()
    answered = [
        str(agent_id) for agent_id in arbitration.get("answered_agent_ids") or [] if str(agent_id)
    ]
    failed = [
        str(agent_id) for agent_id in arbitration.get("failed_agent_ids") or [] if str(agent_id)
    ]
    empty = [
        str(agent_id) for agent_id in arbitration.get("empty_agent_ids") or [] if str(agent_id)
    ]
    retry_agent_ids = [*failed, *empty]
    supporting_agent_ids = [agent_id for agent_id in answered if agent_id != primary_agent_id]
    primary_reply = by_agent.get(primary_agent_id, "") if primary_agent_id else ""
    return {
        "schema": _SYNTHESIS_SCHEMA,
        "primary_agent_id": primary_agent_id or None,
        "primary_reply": primary_reply[:2000],
        "supporting_agent_ids": supporting_agent_ids,
        "retry_agent_ids": retry_agent_ids,
        "answered_count": len(answered),
        "total_count": len(rows),
        "recommended_next_action": arbitration.get("recommended_next_action"),
        "ready": bool(primary_agent_id and primary_reply),
    }


def build_fanout_prompt(message: str, speaker: str, roster: list[str]) -> str:
    """The per-member instruction: answer the actual question in persona."""
    names = "、".join(roster) if roster else "(只有你)"
    return (
        f"你在一个团队群聊里,群成员有:{names}。\n"
        f"群里有人（{speaker or '老板'}）说:「{message}」\n\n"
        f"请用你自己的人设、第一人称,像在微信群里冒泡那样自然地接一句切题的话"
        f"(1-3 句即可):围绕这句话本身给出你的观点、信息或能直接帮上的具体动作。\n"
        f"硬性要求:\n"
        f"1) 必须切题——直接回应『{message}』这件事,不要跑题到你自己的日常话题或"
        f"泛泛地说'我能帮你'。\n"
        f"2) 不要反问、不要只表态不干活、不要复述别人的话。\n"
        f"3) 不要长篇大论,不要列大纲。"
    )


def build_debate_prompt(
    message: str,
    speaker: str,
    roster: list[str],
    transcript: list[dict[str, Any]],
    *,
    round_no: int = 2,
    mentioned: list[str] | None = None,
) -> str:
    """Round-2+ instruction: everyone sees the prior round and @-rebuts.

    ``transcript`` is ``[{agent_id, display_name, reply}]`` from the previous
    round. ``mentioned`` are display names the boss explicitly @-mentioned in
    the original message — those members are the debate's first targets.
    """
    names = "、".join(roster) if roster else "(只有你)"
    lines = []
    for t in transcript or []:
        who = str(t.get("display_name") or t.get("agent_id") or "?")
        reply = str(t.get("reply") or "").strip()
        if reply:
            lines.append(f"· {who}: {reply}")
    transcript_text = "\n".join(lines) if lines else "(上一轮没有有效发言)"
    mention_note = ""
    if mentioned:
        mention_note = (
            "老板在消息里专门 @ 了这些成员，请优先针对他们的观点展开："
            + "、".join(mentioned)
            + "。\n"
        )
    return (
        f"你在一个团队群聊里,群成员有:{names}。\n"
        f"老板刚才问:「{message}」\n\n"
        f"—— 第 {round_no} 轮 · 成员互见辩论 ——\n"
        f"这是大家上一轮的全部发言（现在所有人都看得到）：\n{transcript_text}\n\n"
        f"{mention_note}"
        f"请用你自己的人设、第一人称回应（1-3 句，必须围绕上面这条消息本身，"
        f"不要跑题到你的日常话题）：\n"
        f"1) 如果你不同意某位成员的看法，用「@对方名字」点名反驳，只驳观点、不人身攻击；\n"
        f"2) 如果你认同某人，可以点名支持并补一句你的角度；\n"
        f"3) 不要复述别人已经说过的话，不要长篇大论。"
    )


def run_group_fanout(
    message: str,
    members: list[dict[str, Any]],
    *,
    agent_caller: Callable[..., dict[str, Any]],
    max_members: int = _MAX_FANOUT,
    max_concurrency: int | None = None,
    scale_mode: str = "safe",
    turn_id: str | None = None,
    debate_rounds: int = 1,
    mentioned: list[str] | None = None,
) -> dict[str, Any]:
    """Fan ``message`` out to each member in parallel; collect persona replies.

    ``members`` is ``[{name|agent_id, display_name?}]``. ``agent_caller`` is the
    one-shot subagent invoker — ``agent_caller(agent_id=..., prompt=...)`` →
    ``{output, success, error}`` (in production: ``delegation_skills._call_agent``).

    When ``debate_rounds >= 2`` the fan-out becomes a multi-round debate: each
    subsequent round feeds every member the previous round's full transcript and
    invites them to @-rebut or support specific members. Replies carry a
    ``round`` field (1-based) so the UI can group/annotate rounds.

    Returns ``{ok, replies:[{agent_id, display_name, reply, ok, error, round}],
    count, spoke, debate:{rounds, transcript, mentioned}, arbitration}``. Order
    follows the roster within each round. Never raises — one member's failure is
    isolated.
    """
    msg = (message or "").strip()
    if not msg:
        return {"ok": False, "error": "message is required", "replies": [], "count": 0, "spoke": 0}
    eligible = [
        m for m in (members or []) if isinstance(m, dict) and (m.get("name") or m.get("agent_id"))
    ]
    requested_members = len(eligible)
    scale = str(scale_mode or "safe").strip().lower()
    if scale not in {"safe", "full"}:
        scale = "safe"
    max_cap = _MAX_SCALE_FANOUT if scale == "full" else max(1, int(max_members or _MAX_FANOUT))
    max_members = max(1, min(int(max_members or _MAX_FANOUT), max_cap))
    clean = eligible[:max_members]
    if not clean:
        return {"ok": False, "error": "no members", "replies": [], "count": 0, "spoke": 0}

    roster = [str(m.get("display_name") or m.get("name") or m.get("agent_id")) for m in clean]
    concurrency_limit = (
        max_members if max_concurrency is None else max(1, int(max_concurrency or 1))
    )
    workers = max(1, min(len(clean), concurrency_limit))
    capacity = {
        "schema": _CAPACITY_SCHEMA,
        "requested_members": requested_members,
        "dispatched_members": len(clean),
        "dropped_members": max(0, requested_members - len(clean)),
        "max_members": max_members,
        "max_concurrency": concurrency_limit,
        "concurrency": workers,
        "scale_mode": scale,
        "capacity_tier": _capacity_tier(len(clean), requested_members),
    }

    # Debate rounds are clamped so a hostile cue can't spin up unbounded cost.
    rounds = max(1, min(int(debate_rounds or 1), _MAX_DEBATE_ROUNDS))

    def _run_round(round_no: int, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def _one(member: dict[str, Any]) -> dict[str, Any]:
            agent_id = str(member.get("name") or member.get("agent_id"))
            display = str(member.get("display_name") or agent_id)
            if round_no == 1:
                prompt = build_fanout_prompt(msg, display, roster)
            else:
                prompt = build_debate_prompt(
                    msg,
                    display,
                    roster,
                    transcript,
                    round_no=round_no,
                    mentioned=mentioned,
                )
            rec: dict[str, Any] = {
                "agent_id": agent_id,
                "display_name": display,
                "reply": "",
                "ok": False,
                "error": None,
                "round": round_no,
            }
            try:
                res = agent_caller(
                    agent_id=agent_id,
                    prompt=prompt,
                    timeout_s=90,
                )
                rec["ok"] = bool(res.get("success"))
                rec["reply"] = str(res.get("output") or "")
                rec["error"] = res.get("error")
            except Exception as exc:  # noqa: BLE001 — one member's failure is isolated
                rec["error"] = f"{type(exc).__name__}: {exc}"
            return rec

        results: list[dict[str, Any]] = []
        with _cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="group-fanout") as pool:
            futures = [pool.submit(_one, m) for m in clean]
            for fut in _cf.as_completed(futures):
                results.append(fut.result())

        order = {str(m.get("name") or m.get("agent_id")): i for i, m in enumerate(clean)}
        results.sort(key=lambda r: order.get(r["agent_id"], len(order)))
        return results

    all_replies: list[dict[str, Any]] = []
    transcript: list[dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        round_replies = _run_round(round_no, transcript)
        # Stable global response ids across rounds.
        for reply in round_replies:
            reply["response_id"] = _response_id(
                turn_id,
                len(all_replies),
                str(reply.get("agent_id") or ""),
            )
        all_replies.extend(round_replies)
        # Feed the next round only the successful, non-empty replies.
        transcript = [
            {
                "agent_id": r["agent_id"],
                "display_name": r["display_name"],
                "reply": r["reply"],
            }
            for r in round_replies
            if r["ok"] and str(r.get("reply") or "").strip()
        ]
        if not transcript:
            break  # nobody spoke this round — no point debating into a void

    spoke = sum(1 for r in all_replies if r["ok"] and r["reply"].strip())
    arbitration = arbitrate_group_fanout(all_replies, turn_id=turn_id)
    synthesis = synthesize_group_fanout(all_replies, arbitration)
    debate = (
        {
            "rounds": max([int(r.get("round") or 1) for r in all_replies], default=1),
            "transcript": transcript,
            "mentioned": mentioned or [],
        }
        if rounds > 1
        else None
    )
    return {
        "ok": spoke > 0,
        "replies": all_replies,
        "count": len(all_replies),
        "spoke": spoke,
        "dropped": capacity["dropped_members"],
        "capacity": capacity,
        "arbitration": arbitration,
        "synthesis": synthesis,
        "debate": debate,
    }
