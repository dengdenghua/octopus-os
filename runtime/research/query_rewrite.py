"""Rewrite a user question into N search-engine-friendly queries.

Two paths:

- LLM-backed `rewrite_query(question, router=...)` — calls the project's
  `ModelRouter` with the `query_rewrite` prompt template. Robust JSON
  extraction, falls back to the rule-based path on parse/route failure.
- Pure rule-based `rule_based_rewrite(question)` — no LLM needed. Strips
  filler words, optionally tacks on the current year for time-sensitive
  phrasing. Always returns at least the original question.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from runtime.platform.prompts import get_prompt

_logger = logging.getLogger(__name__)

# Filler patterns we strip when there's no LLM. Lowercase compares.
_EN_FILLERS = (
    "how to ",
    "how do i ",
    "tell me about ",
    "what is ",
    "what's ",
    "can you ",
    "could you ",
    "please ",
    "i want to know ",
    "i'd like to know ",
)
_CN_FILLERS = (
    "请问",
    "想知道",
    "告诉我",
    "我想",
    "怎么",
    "如何",
)
_RECENCY_HINTS_EN = ("latest", "recent", "current", "today", "this week", "now")
_RECENCY_HINTS_CN = ("最近", "最新", "当前", "现在", "今年", "今天")


@dataclass(slots=True)
class RewriteResult:
    queries: list[str]
    backend: str  # "llm" | "rule"
    raw_response: str = ""


def rewrite_query(
    question: str,
    *,
    router: Any = None,
    n: int = 3,
    model: str = "claude-haiku-4-5-20251001",
    today: date | None = None,
    max_tokens: int = 256,
) -> RewriteResult:
    """Produce up to `n` search queries for `question`.

    If `router` is None → rule-based. If router call fails or returns
    unparseable text → rule-based fallback (logged at WARNING).
    The original question is always included as the first query, so the
    caller can safely fan out without losing the user's exact phrasing.
    """
    question = (question or "").strip()
    if not question:
        return RewriteResult(queries=[], backend="rule")

    if router is None:
        return RewriteResult(
            queries=rule_based_rewrite(question, n=n),
            backend="rule",
        )

    today_str = (today or date.today()).isoformat()
    prompt = (
        get_prompt("query_rewrite")
        .replace("{question}", question)
        .replace("{n}", str(n))
        .replace("{date}", today_str)
    )

    raw = ""
    try:
        from runtime.sensing.model_router import Message, ModelRequest

        req = ModelRequest(
            model=model,
            messages=[Message(role="user", content=prompt)],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        resp = router.call(req)
        raw = resp.text or ""
    except Exception as e:  # noqa: BLE001
        _logger.warning("query_rewrite LLM call failed: %s — falling back to rules", e)
        return RewriteResult(
            queries=rule_based_rewrite(question, n=n),
            backend="rule",
            raw_response=raw,
        )

    parsed = _parse_query_array(raw)
    if not parsed:
        _logger.warning(
            "query_rewrite LLM returned unparseable text — falling back to rules. raw=%r",
            raw[:200],
        )
        return RewriteResult(
            queries=rule_based_rewrite(question, n=n),
            backend="rule",
            raw_response=raw,
        )

    queries = _dedupe_keep_order([question, *parsed])[:n]
    return RewriteResult(queries=queries, backend="llm", raw_response=raw)


def rule_based_rewrite(question: str, *, n: int = 3) -> list[str]:
    """Cheap, no-LLM rewrites. Original always first."""
    question = (question or "").strip()
    if not question:
        return []

    out: list[str] = [question]

    stripped = _strip_fillers(question)
    if stripped and stripped != question:
        out.append(stripped)

    if _looks_time_sensitive(question):
        year = date.today().year
        base = stripped or question
        candidate = f"{base} {year}"
        out.append(candidate)

    return _dedupe_keep_order(out)[:n]


def _strip_fillers(q: str) -> str:
    low = q.lower()
    for f in _EN_FILLERS:
        if low.startswith(f):
            return q[len(f) :].strip()
    for f in _CN_FILLERS:
        if q.startswith(f):
            return q[len(f) :].strip()
    return q


def _looks_time_sensitive(q: str) -> bool:
    low = q.lower()
    return any(h in low for h in _RECENCY_HINTS_EN) or any(h in q for h in _RECENCY_HINTS_CN)


def _parse_query_array(text: str) -> list[str]:
    """Extract a JSON list-of-strings from arbitrary LLM text."""
    if not text:
        return []
    # Find first balanced `[ ... ]` and parse it.
    start = text.find("[")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        data = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(data, list):
                        return [
                            str(x).strip()
                            for x in data
                            if isinstance(x, (str, int, float)) and str(x).strip()
                        ]
                    break
        start = text.find("[", start + 1)
    # Fallback: line-per-query scrape, but only when the text actually
    # looks like a structured list (each non-empty line begins with a
    # bullet or number). Plain prose must NOT be treated as a query.
    raw_lines = [ln for ln in (line.strip() for line in text.splitlines()) if ln]
    if not raw_lines:
        return []
    bullet_re = re.compile(r"^(?:[-*]|\d+[.)])\s+")
    if not all(bullet_re.match(ln) for ln in raw_lines):
        return []
    lines: list[str] = []
    for raw in raw_lines:
        line = bullet_re.sub("", raw).strip().strip("\"'`")
        if line and len(line.split()) <= 15:
            lines.append(line)
    return lines


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        norm = it.strip()
        key = norm.lower()
        if not norm or key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


__all__ = ["RewriteResult", "rewrite_query", "rule_based_rewrite"]
