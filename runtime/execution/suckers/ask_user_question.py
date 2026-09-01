"""
ask_user_question · pause-and-ask skill.

Today the model has ``request_approval`` (yes/no on a tool call). For
broader product decisions ("PostgreSQL or SQLite?", "Vite or Webpack?")
we need a structured-choice question.

This skill emits a ``user_question`` record on the active session's
event emitter. The frontend picks it up and renders a card with the
question + clickable options. When the user replies (whether by
clicking an option or sending a custom "Other" answer), the reply
flows back into the next turn as a normal user message; the model
sees the response and continues.

This implementation is intentionally lightweight: it does NOT block
the current ReAct loop waiting on a future. Mid-turn synchronous
blocking would require an ``ApprovalProvider`` instance in
``session.metadata`` (similar to ``request_approval``), which is a
larger refactor. The marker-based variant matches the
"queued steering" pattern the realtime gateway already supports.

Future enhancement: when the session does carry an
``ApprovalProvider`` (e.g. via a metadata key the runtime sets at
turn start), upgrade to a true mid-turn block.
"""

from __future__ import annotations

from typing import Any

from .registry import Skill, SkillRegistry


def _ask_user_question(
    question: str = "",
    options: list[str] | None = None,
    allow_other: bool = True,
    **_kw: Any,
) -> dict[str, Any]:
    """Pause and ask the user a structured multiple-choice question.

    The model emits this when it needs a product decision the user is
    in the best position to make. The UI renders a card with the
    listed options; the user's reply becomes the next user message.

    Returns a structured ack so the model knows the question was
    posted (not the answer — that arrives as the next user turn).
    """
    if not isinstance(question, str) or not question.strip():
        return {
            "ok": False,
            "error": "question must be a non-empty string",
            "error_type": "invalid_argument",
        }
    if options is None:
        return {
            "ok": False,
            "error": "options must be a list of 2..6 strings",
            "error_type": "invalid_argument",
        }
    if not isinstance(options, list):
        return {
            "ok": False,
            "error": "options must be a list",
            "error_type": "invalid_argument",
        }
    cleaned: list[str] = []
    for opt in options:
        if not isinstance(opt, str):
            continue
        s = opt.strip()
        if s:
            cleaned.append(s)
    if not (2 <= len(cleaned) <= 6):
        return {
            "ok": False,
            "error": (f"options must contain 2..6 non-empty strings (got {len(cleaned)} valid)"),
            "error_type": "invalid_argument",
        }

    # Best-effort: emit a structured event on the active session
    # emitter so the frontend can render a question card. Falls back
    # silently when no emitter is wired (test / headless mode).
    posted = False
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        if session is not None:
            emitter = (session.metadata or {}).get("event_emitter")
            if callable(emitter):
                emitter(
                    {
                        "type": "user_question",
                        "question": question.strip(),
                        "options": cleaned,
                        "allow_other": bool(allow_other),
                    }
                )
                posted = True
    except Exception:  # noqa: BLE001 — best-effort emit must not fail the skill
        posted = False

    return {
        "ok": True,
        "posted": posted,
        "question": question.strip(),
        "options": cleaned,
        "allow_other": bool(allow_other),
        # Signal to the model that it should yield this turn — the
        # reply will arrive as the next user message.
        "yield_turn": True,
        "instructions": (
            "Question posted to the user. End this turn with a brief "
            "Final Answer that names the question and lists the "
            "options; the user's reply will be the next message."
        ),
    }


def register_ask_user_question_skill(registry: SkillRegistry) -> int:
    """Register the ask_user_question skill. Returns 1."""
    registry.register(
        Skill(
            name="ask_user_question",
            description=(
                "用途: 暂停并以结构化卡片向用户提一个 2-6 选项的问题, "
                "仅用于【多个并列方案的产品决策】——选项之间是真正的取舍, "
                "且用户最有资格拍板。例: 用哪个数据库、用哪个框架、走哪种部署方式。\n"
                "何时不用 (重要):\n"
                " - 开放任务的方向/范围澄清 (如 '你想调研哪方面'、'聚焦哪个领域') "
                "——不要用本工具反问。先按最合理的理解做一轮, 并在开头一句话亮明假设 "
                "('我按 X 理解, 不对请说'), 让用户在看到产出后纠偏, 而不是上来就问。\n"
                " - 单一文件改动、信息查询: 用 web_search / read_file。\n"
                " - yes/no 工具批准: 用 request_approval。\n"
                "关键参数: question (字符串, 非空); options (2-6 字符串列表, "
                "每项是一个互斥的具体方案); allow_other (默认 True, 允许用户填自定义答案)。\n"
                '示例: ask_user_question({"question":"用哪个数据库?",'
                '"options":["PostgreSQL","SQLite","MySQL"]})'
            ),
            affinity=["interaction", "ui", "ask_user"],
            cost_profile="low",
            trusted_source="skill://public/ask_user_question",
            handler=_ask_user_question,
            tests=[],
        )
    )
    return 1


__all__ = [
    "_ask_user_question",
    "register_ask_user_question_skill",
]
