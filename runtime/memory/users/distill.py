"""distill · roll raw memory facts up into the six summary buckets.

``user_store.add_fact`` accumulates raw facts and ``memory_router``
exposes them to the UI, but nothing ever wrote the bucket summaries
(``user.workContext`` / ``personalContext`` / ``topOfMind`` and
``history.recentMonths`` / ``earlierContext`` / ``longTermBackground``)
— the buckets shipped empty. This module is that missing writer.

Two paths:

* **Heuristic** (default, zero deps, deterministic): classify facts by
  category + keyword and by age, render compact joined summaries.
* **LLM** (optional): when a ``ModelRouter`` is supplied, each
  non-empty bucket's facts are compressed into one short paragraph.
  Any LLM failure falls back to the heuristic rendering for that
  bucket — distillation must never lose the baseline.

Honesty contract: buckets with no supporting facts keep their existing
summary untouched. We never invent user context.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from . import user_store

_log = logging.getLogger(__name__)

_RECENT_MONTHS_DAYS = 90
_EARLIER_CONTEXT_DAYS = 365
_TOP_OF_MIND_FACTS = 5
_BUCKET_FACTS = 8
_LLM_SUMMARY_MAX_TOKENS = 220

_WORK_KEYWORDS = (
    "工作",
    "项目",
    "代码",
    "部署",
    "仓库",
    "上线",
    "需求",
    "客户",
    "团队",
    "work",
    "project",
    "repo",
    "deploy",
    "code",
    "bug",
    "release",
    "client",
)
_PERSONAL_KEYWORDS = (
    "喜欢",
    "偏好",
    "习惯",
    "生日",
    "家庭",
    "不吃",
    "过敏",
    "称呼",
    "prefer",
    "favorite",
    "like",
    "dislike",
    "birthday",
    "family",
    "personal",
)
_WORK_CATEGORIES = {"work", "project", "task", "code", "job"}
_PERSONAL_CATEGORIES = {"personal", "preference", "profile"}


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError:
        return None


def _fact_text(fact: dict[str, Any]) -> str:
    return " ".join(str(fact.get("content") or "").split()).strip()


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    haystack = text.casefold()
    return any(k.casefold() in haystack for k in keywords)


def _classify_user_bucket(fact: dict[str, Any]) -> str | None:
    """Route a fact to workContext / personalContext, or None."""
    category = str(fact.get("category") or "").casefold()
    text = _fact_text(fact)
    if category in _WORK_CATEGORIES or _matches_any(text, _WORK_KEYWORDS):
        return "workContext"
    if category in _PERSONAL_CATEGORIES or _matches_any(text, _PERSONAL_KEYWORDS):
        return "personalContext"
    return None


def _age_bucket(fact: dict[str, Any], now: datetime) -> str | None:
    """Route a fact to a history bucket by age; None when undated."""
    ts = _parse_ts(fact.get("createdAt"))
    if ts is None:
        return None
    days = (now - ts).days
    if days <= _RECENT_MONTHS_DAYS:
        return "recentMonths"
    if days <= _EARLIER_CONTEXT_DAYS:
        return "earlierContext"
    return "longTermBackground"


def _heuristic_summary(facts: list[dict[str, Any]], *, limit: int) -> str:
    """Join the newest fact texts into one compact line."""
    texts = [t for t in (_fact_text(f) for f in facts[-limit:]) if t]
    return "；".join(texts)


def _llm_summary(router: Any, bucket: str, facts: list[dict[str, Any]], model: str | None) -> str:
    """Compress one bucket's facts via the LLM; raises on failure."""
    from runtime.platform.models.llm import Message, ModelRequest

    lines = "\n".join(f"- {_fact_text(f)}" for f in facts if _fact_text(f))
    req = ModelRequest(
        model=model or getattr(router, "default_model", None) or "default",
        messages=[
            Message(
                role="system",
                content=(
                    "你是记忆蒸馏器。把用户的原始事实列表压缩成一段不超过 "
                    "120 字的第三人称摘要，保留具体偏好与约束，不要编造，"
                    "不要列表，不要解释。只输出摘要文本。"
                ),
            ),
            Message(role="user", content=f"桶：{bucket}\n事实：\n{lines}"),
        ],
        max_tokens=_LLM_SUMMARY_MAX_TOKENS,
        temperature=0.1,
    )
    resp = router.call(req)
    return (getattr(resp, "text", "") or "").strip()


def _bucket_facts(
    facts: list[dict[str, Any]],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Group facts into the six buckets.

    Returns ``{section: {"group": "user"|"history", "facts": [...]}}``
    for buckets that have at least one supporting fact.
    """
    work = [f for f in facts if _classify_user_bucket(f) == "workContext"]
    personal = [f for f in facts if _classify_user_bucket(f) == "personalContext"]
    top = facts[-_TOP_OF_MIND_FACTS:]
    recent, earlier, longterm = [], [], []
    for fact in facts:
        bucket = _age_bucket(fact, now)
        if bucket == "recentMonths":
            recent.append(fact)
        elif bucket == "earlierContext":
            earlier.append(fact)
        elif bucket == "longTermBackground":
            longterm.append(fact)

    out: dict[str, dict[str, Any]] = {}
    for name, group, bucket_facts, limit in (
        ("workContext", "user", work, _BUCKET_FACTS),
        ("personalContext", "user", personal, _BUCKET_FACTS),
        ("topOfMind", "user", top, _TOP_OF_MIND_FACTS),
        ("recentMonths", "history", recent, _BUCKET_FACTS),
        ("earlierContext", "history", earlier, _BUCKET_FACTS),
        ("longTermBackground", "history", longterm, _BUCKET_FACTS),
    ):
        if bucket_facts:
            out[name] = {"group": group, "facts": bucket_facts, "limit": limit}
    return out


def distill_user_memory(
    router: Any | None = None,
    *,
    model: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rewrite the six summary buckets from the current fact list.

    Returns ``{ok, buckets_written, buckets, used_llm}``. Never raises:
    a distillation failure must not take down the caller's scheduler.
    """
    try:
        now_dt = (now or datetime.now(UTC)).astimezone(UTC)
        memory = user_store.read_memory()
        facts = [f for f in memory.get("facts") or [] if isinstance(f, dict)]
        grouped = _bucket_facts(facts, now_dt)

        written: list[str] = []
        used_llm = False
        for name, info in grouped.items():
            bucket_facts = info["facts"]
            summary = _heuristic_summary(bucket_facts, limit=info["limit"])
            if router is not None:
                try:
                    llm_text = _llm_summary(router, name, bucket_facts, model)
                    if llm_text:
                        summary = llm_text
                        used_llm = True
                except Exception:  # noqa: BLE001 — heuristic fallback per bucket
                    _log.debug("memory distill: LLM failed for %s, using heuristic", name)
            if not summary:
                continue
            memory[info["group"]][name] = {
                "summary": summary,
                "updatedAt": now_dt.isoformat(),
            }
            written.append(name)

        if not written:
            return {"ok": True, "buckets_written": 0, "buckets": [], "used_llm": False}

        user_store.write_memory(memory)
        return {
            "ok": True,
            "buckets_written": len(written),
            "buckets": written,
            "used_llm": used_llm,
        }
    except Exception as exc:  # noqa: BLE001 — scheduler-safe contract
        _log.exception("memory distill failed")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "buckets_written": 0,
            "buckets": [],
            "used_llm": False,
        }


__all__ = ["distill_user_memory"]
