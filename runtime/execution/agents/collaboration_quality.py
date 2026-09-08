"""Auditable quality signals and delivery envelopes for group collaboration.

This is deliberately a structural/evidence rubric, not an LLM-as-judge.  It
improves deterministic arbitration while clearly exposing when a semantic
reviewer is still required.  No weak heuristic is allowed to masquerade as a
fact check.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SCHEMA = "octopus.collaboration_quality.v1"
_DELIVERY_SCHEMA = "octopus.collaboration_delivery.v1"
_SEMANTIC_REVIEW_SCHEMA = "octopus.collaboration_semantic_review.v1"
_SPACE_RE = re.compile(r"\s+")
_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9_+.#/-]{1,}", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\]\[)）。，、；;！!？?]+", re.IGNORECASE)
_FILE_RE = re.compile(
    r"(?<![\w.-])(?:[\w.-]+/)+(?:[\w.-]+\.(?:py|ts|tsx|js|jsx|md|json|ya?ml|toml|sql))"
)
_EVIDENCE_RE = re.compile(
    r"(?:https?://|测试|验证|实测|依据|证据|数据|日志|截图|文档|官方|"
    r"test(?:ed|s)?|verified|evidence|according to|docs?)",
    re.IGNORECASE,
)
_SPECIFIC_RE = re.compile(
    r"(?:\d+(?:\.\d+)?%?|`[^`]+`|步骤|条件|因为|因此|风险|建议|结论|"
    r"if\b|when\b|because\b|therefore\b)",
    re.IGNORECASE,
)
_EVIDENCE_REQUIRED_RE = re.compile(
    r"(?:研究|调研|验证|核实|测试|验收|审查|评审|风险|对比|比较|依据|证据|"
    r"research|verify|validate|test|audit|review|evidence|compare)",
    re.IGNORECASE,
)
_STOP_TERMS = {
    "一个",
    "这个",
    "那个",
    "我们",
    "你们",
    "他们",
    "可以",
    "需要",
    "进行",
    "以及",
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
}


def _terms(text: str) -> set[str]:
    normalized = _SPACE_RE.sub(" ", str(text or "")).lower()
    terms = {term for term in _LATIN_RE.findall(normalized) if term not in _STOP_TERMS}
    for run in _CJK_RE.findall(normalized):
        if len(run) <= 4:
            terms.add(run)
        terms.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return {term for term in terms if term and term not in _STOP_TERMS}


def _bounded_score(value: float) -> int:
    return max(0, min(100, round(value)))


def _relevance_score(query_terms: set[str], reply_terms: set[str]) -> int:
    if not query_terms:
        return 70
    overlap = len(query_terms & reply_terms)
    # A concise answer should not need to repeat every term in a long request.
    denominator = max(1, min(len(query_terms), 8))
    return _bounded_score(25 + 75 * min(1.0, overlap / denominator))


def _evidence_score(text: str) -> int:
    signals = len(_EVIDENCE_RE.findall(text))
    signals += min(2, len(_URL_RE.findall(text))) * 2
    signals += min(2, len(_FILE_RE.findall(text)))
    return _bounded_score(20 + signals * 20) if signals else 20


def _specificity_score(text: str) -> int:
    signals = len(_SPECIFIC_RE.findall(text))
    length_signal = min(35, len(text.strip()) // 5)
    return _bounded_score(25 + min(40, signals * 12) + length_signal)


def _independence_score(reply_terms: set[str], earlier_terms: list[set[str]]) -> int:
    if not earlier_terms or not reply_terms:
        return 100
    similarities: list[float] = []
    for prior in earlier_terms:
        union = reply_terms | prior
        similarities.append(len(reply_terms & prior) / len(union) if union else 0.0)
    return _bounded_score(100 * (1.0 - max(similarities)))


def assess_collaboration_quality(
    message: str,
    replies: list[dict[str, Any]],
    *,
    pattern: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score useful structure and report where semantic review is still needed."""

    query_terms = _terms(message)
    pattern_id = str((pattern or {}).get("id") or "")
    evidence_required = bool(
        _EVIDENCE_REQUIRED_RE.search(str(message or "")) or pattern_id == "adversarial_review"
    )
    outcomes: list[dict[str, Any]] = []
    accepted_terms: list[set[str]] = []
    strong = 0
    evidenced = 0
    for reply in replies:
        agent_id = str(reply.get("agent_id") or "")
        text = str(reply.get("reply") or "").strip()
        if not reply.get("ok") or not text:
            outcomes.append(
                {
                    "response_id": reply.get("response_id"),
                    "agent_id": agent_id,
                    "round": int(reply.get("round") or 1),
                    "status": "rejected",
                    "score": 0,
                    "relevance": 0,
                    "evidence": 0,
                    "specificity": 0,
                    "independence": 0,
                    "requires_semantic_review": False,
                }
            )
            continue
        reply_terms = _terms(text)
        relevance = _relevance_score(query_terms, reply_terms)
        evidence = _evidence_score(text)
        specificity = _specificity_score(text)
        independence = _independence_score(reply_terms, accepted_terms)
        accepted_terms.append(reply_terms)
        if evidence_required:
            score = _bounded_score(
                relevance * 0.35 + evidence * 0.30 + specificity * 0.25 + independence * 0.10
            )
        else:
            score = _bounded_score(
                relevance * 0.45 + specificity * 0.30 + independence * 0.20 + evidence * 0.05
            )
        status = "strong" if score >= 75 else "acceptable" if score >= 50 else "weak"
        strong += int(status == "strong")
        evidenced += int(evidence >= 60)
        outcomes.append(
            {
                "response_id": reply.get("response_id"),
                "agent_id": agent_id,
                "round": int(reply.get("round") or 1),
                "status": status,
                "score": score,
                "relevance": relevance,
                "evidence": evidence,
                "specificity": specificity,
                "independence": independence,
                # Structural evidence signals cannot prove truth. High-stakes
                # outputs remain explicitly queued for a semantic/fact review.
                "requires_semantic_review": evidence_required and evidence < 60,
            }
        )

    answered = sum(1 for outcome in outcomes if outcome["status"] != "rejected")
    semantic_review_required = bool(
        evidence_required and answered and (evidenced < answered or strong == 0)
    )
    return {
        "schema": _SCHEMA,
        "rubric": "deterministic_relevance_evidence_specificity_independence",
        "evidence_required": evidence_required,
        "semantic_review_required": semantic_review_required,
        "answered_count": answered,
        "strong_count": strong,
        "evidenced_count": evidenced,
        "outcomes": outcomes,
    }


def build_collaboration_delivery(
    replies: list[dict[str, Any]],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Return a typed, compact handoff instead of forcing consumers to parse chat."""

    quality_by_response = {
        str(item.get("response_id") or ""): item
        for item in quality.get("outcomes") or []
        if isinstance(item, dict) and str(item.get("response_id") or "")
    }
    contributions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for reply in replies:
        agent_id = str(reply.get("agent_id") or "")
        text = str(reply.get("reply") or "").strip()
        if reply.get("ok") and text:
            contributions.append(
                {
                    "response_id": reply.get("response_id"),
                    "agent_id": agent_id,
                    "display_name": str(reply.get("display_name") or agent_id),
                    "round": int(reply.get("round") or 1),
                    "role": reply.get("pattern_role"),
                    "claim": text[:2000],
                    "evidence_refs": list(
                        dict.fromkeys(
                            [
                                *_URL_RE.findall(text),
                                *_FILE_RE.findall(text),
                            ]
                        )
                    )[:16],
                    "quality": quality_by_response.get(str(reply.get("response_id") or "")),
                }
            )
        else:
            failures.append(
                {
                    "agent_id": agent_id,
                    "display_name": str(reply.get("display_name") or agent_id),
                    "round": int(reply.get("round") or 1),
                    "error": str(reply.get("error") or "empty response")[:1000],
                }
            )
    return {
        "schema": _DELIVERY_SCHEMA,
        "contributions": contributions,
        "failures": failures,
        "semantic_review_required": bool(quality.get("semantic_review_required")),
        "ready": bool(contributions) and not bool(quality.get("semantic_review_required")),
    }


def build_semantic_review_prompt(
    message: str,
    delivery: dict[str, Any],
) -> str:
    """Build a bounded fact/semantic verification contract for one reviewer."""

    review_input = {
        "schema": "octopus.collaboration_semantic_review_input.v1",
        "user_request": str(message or "")[:4000],
        "contributions": [
            {
                "response_id": item.get("response_id"),
                "agent_id": item.get("agent_id"),
                "role": item.get("role"),
                "claim": str(item.get("claim") or "")[:2000],
                "evidence_refs": list(item.get("evidence_refs") or [])[:16],
            }
            for item in delivery.get("contributions") or []
            if isinstance(item, dict)
        ][:64],
    }
    encoded = json.dumps(review_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e")
    return (
        "你是独立验证者。核对每项贡献是否切题、内部一致，并在工具可用时检查证据引用；"
        "不得因为文字流畅就判定事实正确。只输出一个 JSON 对象，不要 Markdown："
        '{"verdict":"pass|needs_revision|insufficient_evidence",'
        '"confidence":0.0,"accepted_response_ids":[],"issues":['
        '{"response_id":"...","code":"...","message":"..."}],"summary":"..."}。'
        "只有所有关键主张都满足用户请求且证据足够时才可 verdict=pass。\n"
        "<semantic-review-input>" + encoded + "</semantic-review-input>"
    )


def _first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    source = str(text or "").strip()
    for index, char in enumerate(source):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(source[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_semantic_review(
    output: str,
    *,
    valid_response_ids: set[str],
    reviewer_agent_id: str | None = None,
) -> dict[str, Any]:
    """Parse fail-closed reviewer output into a stable, bounded envelope."""

    raw = _first_json_object(output)
    if raw is None:
        return {
            "schema": _SEMANTIC_REVIEW_SCHEMA,
            "verdict": "review_failed",
            "confidence": 0.0,
            "accepted_response_ids": [],
            "issues": [{"code": "invalid_json", "message": "reviewer did not return JSON"}],
            "summary": "语义验证器未返回可解析结果",
            "reviewer_agent_id": reviewer_agent_id,
        }
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "needs_revision", "insufficient_evidence"}:
        verdict = "review_failed"
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    accepted = list(
        dict.fromkeys(
            response_id
            for value in (raw.get("accepted_response_ids") or [])
            if (response_id := str(value or "").strip()) in valid_response_ids
        )
    )[:64]
    issues: list[dict[str, str]] = []
    for value in (raw.get("issues") or [])[:64]:
        if not isinstance(value, dict):
            continue
        response_id = str(value.get("response_id") or "").strip()
        issue = {
            "code": str(value.get("code") or "unspecified")[:120],
            "message": str(value.get("message") or "")[:1000],
        }
        if response_id in valid_response_ids:
            issue["response_id"] = response_id
        issues.append(issue)
    if verdict == "pass" and set(accepted) != valid_response_ids:
        verdict = "needs_revision"
        issues.append(
            {
                "code": "incomplete_acceptance",
                "message": "reviewer did not accept every delivered contribution",
            }
        )
    return {
        "schema": _SEMANTIC_REVIEW_SCHEMA,
        "verdict": verdict,
        "confidence": confidence,
        "accepted_response_ids": accepted,
        "issues": issues,
        "summary": str(raw.get("summary") or "")[:2000],
        "reviewer_agent_id": reviewer_agent_id,
    }


def apply_semantic_review(
    quality: dict[str, Any],
    delivery: dict[str, Any],
    review: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach reviewer truth without mutating the deterministic audit record."""

    accepted = set(review.get("accepted_response_ids") or [])
    passed = review.get("verdict") == "pass"
    reviewed_quality = {
        **quality,
        "semantic_review_required": not passed,
        "semantic_review": review,
    }
    contributions = [
        {
            **item,
            "semantic_status": (
                "accepted" if str(item.get("response_id") or "") in accepted else "unverified"
            ),
        }
        for item in delivery.get("contributions") or []
        if isinstance(item, dict)
    ]
    reviewed_delivery = {
        **delivery,
        "contributions": contributions,
        "semantic_review_required": not passed,
        "semantic_review": review,
        "ready": bool(contributions) and passed,
    }
    return reviewed_quality, reviewed_delivery


__all__ = [
    "apply_semantic_review",
    "assess_collaboration_quality",
    "build_collaboration_delivery",
    "build_semantic_review_prompt",
    "parse_semantic_review",
]


