from __future__ import annotations

import os
import re
from calendar import monthrange
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha1
from html import unescape
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request

from runtime.platform.io import atomic_write_json, read_json_with_backup

_STOPWORDS = {
    "我",
    "想",
    "希望",
    "帮我",
    "关注",
    "跟踪",
    "订阅",
    "每天",
    "每周",
    "一下",
    "以及",
    "和",
    "的",
    "了",
    "the",
    "and",
    "for",
    "with",
    "about",
}


def _default_store_path() -> Path:
    base = Path(os.environ.get("ECHO_HOME", ".echo"))
    return base / "intelligence.json"


def _empty_store() -> dict[str, Any]:
    return {"subscriptions": [], "reports": []}


def _read_store(path: Path) -> dict[str, Any]:
    data = read_json_with_backup(path, default=_empty_store())
    if not isinstance(data, dict):
        return _empty_store()
    return {
        "subscriptions": list(data.get("subscriptions") or []),
        "reports": list(data.get("reports") or []),
    }


def _write_store(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _cadence_seconds(value: Any) -> int:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("hourly", "real-time", "high", "高频", "实时")):
        return 60 * 60
    if any(token in text for token in ("weekly", "week", "每周", "周报")):
        return 7 * 24 * 60 * 60
    if any(token in text for token in ("monthly", "month", "每月", "月报")):
        return 30 * 24 * 60 * 60
    return 24 * 60 * 60


_FIXED_TIMEZONE_FALLBACKS = {
    "Asia/Shanghai": timezone(timedelta(hours=8), "Asia/Shanghai"),
    "PRC": timezone(timedelta(hours=8), "Asia/Shanghai"),
    "Etc/GMT-8": timezone(timedelta(hours=8), "Asia/Shanghai"),
}


def _resolve_timezone(value: Any) -> timezone | ZoneInfo:
    timezone_name = str(value or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return _FIXED_TIMEZONE_FALLBACKS.get(timezone_name, UTC)


def _infer_schedule_time(text: str) -> str:
    match = re.search(r"\b([01]?\d|2[0-3])[:：]([0-5]\d)\b", text)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"
    lower = text.lower()
    if any(token in lower for token in ("晚上", "晚间", "夜间", "evening", "night")):
        return "20:00"
    if any(token in lower for token in ("下午", "pm")):
        return "15:00"
    if any(token in lower for token in ("中午", "noon")):
        return "12:00"
    return "09:00"


def _infer_schedule_day(text: str, cadence: str) -> str:
    if "月" in cadence or "month" in cadence.lower():
        match = re.search(
            r"(?:每月|月|monthly)\s*(\d{1,2})\s*(?:号|日|st|nd|rd|th)?", text, flags=re.I
        )
        if match:
            day = max(1, min(int(match.group(1)), 31))
            return str(day)
        return "1"
    weekday_aliases = {
        "一": "1",
        "1": "1",
        "mon": "1",
        "monday": "1",
        "二": "2",
        "2": "2",
        "tue": "2",
        "tuesday": "2",
        "三": "3",
        "3": "3",
        "wed": "3",
        "wednesday": "3",
        "四": "4",
        "4": "4",
        "thu": "4",
        "thursday": "4",
        "五": "5",
        "5": "5",
        "fri": "5",
        "friday": "5",
        "六": "6",
        "6": "6",
        "sat": "6",
        "saturday": "6",
        "日": "7",
        "天": "7",
        "7": "7",
        "sun": "7",
        "sunday": "7",
    }
    match = re.search(r"(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", text)
    if match:
        return weekday_aliases.get(match.group(1), "1")
    lower = text.lower()
    for key, value in weekday_aliases.items():
        if key.isascii() and key.isalpha() and re.search(rf"\b{re.escape(key)}\b", lower):
            return value
    return "1"


def _schedule_ready(subscription: dict[str, Any], *, now: datetime) -> bool:
    cadence = str(subscription.get("cadence") or "").lower()
    if any(token in cadence for token in ("hourly", "real-time", "high", "高频", "实时")):
        return True
    zone = _resolve_timezone(subscription.get("timezone"))
    local_now = now.astimezone(zone)
    time_text = str(subscription.get("schedule_time") or "09:00").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", time_text)
    hour = int(match.group(1)) if match else 9
    minute = int(match.group(2)) if match else 0
    if local_now.hour < hour or (local_now.hour == hour and local_now.minute < minute):
        return False
    if any(token in cadence for token in ("weekly", "week", "每周", "周报")):
        try:
            target_weekday = int(subscription.get("schedule_day") or 1)
        except (TypeError, ValueError):
            target_weekday = 1
        return local_now.isoweekday() == max(1, min(target_weekday, 7))
    if any(token in cadence for token in ("monthly", "month", "每月", "月报")):
        try:
            target_day = int(subscription.get("schedule_day") or 1)
        except (TypeError, ValueError):
            target_day = 1
        last_day = monthrange(local_now.year, local_now.month)[1]
        return local_now.day == max(1, min(target_day, last_day))
    return True


def _subscription_due(
    subscription: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    if subscription.get("enabled") is False:
        return False
    current = now or datetime.now(UTC)
    if not _schedule_ready(subscription, now=current):
        return False
    last_run = _parse_iso(subscription.get("last_run"))
    if last_run is None:
        return True
    return (current - last_run).total_seconds() >= _cadence_seconds(
        subscription.get("cadence"),
    )


def _split_terms(text: str) -> list[str]:
    normalized = text
    for mark in "，。；、\n\t\r,.;:/|()[]{}<>":
        normalized = normalized.replace(mark, " ")
    terms: list[str] = []
    for raw in normalized.split(" "):
        term = raw.strip(" -_#@!？?：:")
        if len(term) < 2 or term.lower() in _STOPWORDS:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _infer_cadence(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ["实时", "马上", "高频", "hourly", "real-time"]):
        return "高频"
    if any(token in lower for token in ["每周", "weekly", "周报"]):
        return "每周"
    if any(token in lower for token in ["每月", "monthly", "月报"]):
        return "每月"
    return "每天"


def _draft_subscription(goal: str) -> dict[str, Any]:
    terms = _split_terms(goal)
    keywords = terms[:8]
    if not keywords:
        keywords = [goal[:24]]
    topic_terms = keywords[:3]
    topic = " / ".join(topic_terms)
    if len(topic) > 42:
        topic = f"{topic[:39]}..."
    cadence = _infer_cadence(goal)
    return {
        "topic": topic,
        "display_name": topic,
        "keywords": keywords,
        "cadence": cadence,
        "schedule_time": _infer_schedule_time(goal),
        "schedule_day": _infer_schedule_day(goal, cadence),
        "timezone": "Asia/Shanghai",
        "instructions": goal.strip(),
        "sources": ["web", "news"],
    }


def _clean_text(value: Any, *, max_len: int = 500) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clean_text(item, max_len=160)
        if text and text not in out:
            out.append(text)
    return out


def _source_label(source: str) -> str:
    normalized = source.strip().lower()
    aliases = {
        "web": "Web",
        "news": "News",
        "github": "GitHub",
        "github_trending": "GitHub",
        "github-trending": "GitHub",
        "arxiv": "arXiv",
        "paper": "Papers",
        "papers": "Papers",
        "company": "Big Tech",
        "bigtech": "Big Tech",
        "release": "Release Notes",
        "releases": "Release Notes",
        "rss": "RSS",
    }
    if normalized.startswith(("http://", "https://")):
        return "RSS/Web"
    return aliases.get(normalized, source.strip() or "Web")


def _base_query(subscription: dict[str, Any]) -> str:
    topic = _clean_text(
        subscription.get("display_name") or subscription.get("topic"),
        max_len=120,
    )
    keywords = _clean_list(subscription.get("keywords"))[:5]
    pieces = [topic, *keywords]
    seen: set[str] = set()
    compact: list[str] = []
    for piece in pieces:
        key = piece.casefold()
        if piece and key not in seen:
            seen.add(key)
            compact.append(piece)
    return " ".join(compact)[:220] or topic or "AI technology"


def _queries_for_subscription(subscription: dict[str, Any]) -> list[dict[str, str]]:
    base = _base_query(subscription)
    instructions = _clean_text(subscription.get("instructions"), max_len=160)
    sources = _clean_list(subscription.get("sources")) or ["web", "news"]
    queries: list[dict[str, str]] = []

    def add(source: str, query: str) -> None:
        query = _clean_text(query, max_len=260)
        key = (source.casefold(), query.casefold())
        if query and key not in {(q["source"].casefold(), q["query"].casefold()) for q in queries}:
            queries.append({"source": source, "query": query})

    for source in sources:
        normalized = source.lower()
        if normalized.startswith(("http://", "https://")):
            continue
        if normalized in {"github", "github_trending", "github-trending"}:
            add(source, f"{base} GitHub trending repositories releases stars")
        elif normalized in {"arxiv", "paper", "papers"}:
            add(source, f"{base} arXiv paper research")
        elif normalized in {"company", "bigtech", "big-tech"}:
            add(
                source,
                f"{base} OpenAI Anthropic Google DeepMind Meta AI Microsoft Research NVIDIA update",
            )
        elif normalized in {"release", "releases"}:
            add(source, f"{base} release notes changelog product update")
        elif normalized == "news":
            add(source, f"{base} latest news analysis")
        else:
            add(source, f"{base} latest")

    if instructions:
        add("instructions", f"{base} {instructions}")
    return queries[:8]


def _default_search(
    query: str,
    *,
    max_results: int = 5,
    timeout_ms: int = 8000,
) -> dict[str, Any]:
    from runtime.execution.suckers.web_skills import _web_search

    return _web_search(
        query=query,
        max_results=max_results,
        timeout_ms=timeout_ms,
    )


def _default_fetch(url: str, *, timeout_ms: int = 8000) -> dict[str, Any]:
    from runtime.execution.suckers.web_skills import _fetch_url

    return _fetch_url(url=url, timeout_ms=timeout_ms, max_bytes=200_000)


def _normalize_result_item(
    item: dict[str, Any],
    *,
    source: str,
    query: str,
) -> dict[str, Any] | None:
    title = _clean_text(item.get("title") or item.get("name") or item.get("url"), max_len=220)
    url = _clean_text(item.get("url") or item.get("link") or "", max_len=700)
    snippet = _clean_text(
        item.get("snippet")
        or item.get("summary")
        or item.get("content")
        or item.get("description"),
        max_len=700,
    )
    if not title and not snippet:
        return None
    return {
        "id": sha1(
            f"{url}|{title}|{snippet}".encode("utf-8", errors="ignore"), usedforsecurity=False
        ).hexdigest()[:16],
        "title": title or url or "Untitled source",
        "url": url,
        "snippet": snippet,
        "source": _source_label(source),
        "query": query,
    }


def _items_from_search_output(
    output: dict[str, Any], *, source: str, query: str
) -> list[dict[str, Any]]:
    if not isinstance(output, dict):
        return []
    raw_results = output.get("results")
    if not isinstance(raw_results, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        item = _normalize_result_item(raw, source=source, query=query)
        if item:
            items.append(item)
    return items


def _parse_feed_entries(content: str, *, source: str) -> list[dict[str, Any]]:
    if not content.strip():
        return []
    try:
        root = ElementTree.fromstring(content.encode("utf-8"))  # nosec B314 — RSS feed; py3.7+ ET has entity defenses
    except (ConnectionError, TimeoutError, TypeError, ValueError):  # noqa: BLE001
        return []

    entries: list[dict[str, Any]] = []
    for node in list(root.findall(".//item")) + list(root.findall(".//{*}entry")):
        title = ""
        link = ""
        summary = ""
        for child in list(node):
            tag = child.tag.rsplit("}", 1)[-1].lower()
            if tag == "title" and not title:
                title = child.text or ""
            elif tag == "link" and not link:
                link = child.attrib.get("href") or child.text or ""
            elif tag in {"description", "summary", "content"} and not summary:
                summary = child.text or ""
        item = _normalize_result_item(
            {"title": title, "url": link, "snippet": summary},
            source=source,
            query=source,
        )
        if item:
            entries.append(item)
    return entries[:20]


def _score_item(item: dict[str, Any], keywords: list[str]) -> float:
    haystack = f"{item.get('title', '')} {item.get('snippet', '')}".casefold()
    score = 0.0
    for keyword in keywords:
        key = keyword.casefold()
        if key and key in haystack:
            score += 1.0
    if item.get("url"):
        score += 0.25
    source = str(item.get("source") or "").lower()
    if source in {"github", "arxiv", "big tech", "release notes"}:
        score += 0.2
    return score


def _dedupe_items(items: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or item.get("title") or item.get("id") or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        item = dict(item)
        item["score"] = round(_score_item(item, keywords), 2)
        deduped.append(item)
    deduped.sort(key=lambda row: (float(row.get("score") or 0), bool(row.get("url"))), reverse=True)
    return deduped


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['title']}",
        "",
        "## 执行摘要",
        report["summary"],
        "",
        "## 关键发现",
    ]
    findings = report.get("findings") or []
    lines.extend(
        [f"{idx}. {finding}" for idx, finding in enumerate(findings, 1)]
        or ["1. 本轮没有发现可用新情报。"]
    )
    lines.extend(
        [
            "",
            "## 证据与来源",
            "| # | 来源 | 标题 | 摘要 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for idx, item in enumerate((report.get("items") or [])[:12], 1):
        title = _clean_text(item.get("title"), max_len=140).replace("|", "\\|")
        source = _clean_text(item.get("source"), max_len=80).replace("|", "\\|")
        snippet = _clean_text(item.get("snippet"), max_len=180).replace("|", "\\|")
        if item.get("url"):
            title = f"[{title}]({item['url']})"
        lines.append(f"| {idx} | {source} | {title} | {snippet or '无摘要'} |")
    if not report.get("items"):
        lines.append("| 1 | 暂无 | 未采集到结果 | 需要调整关键词或来源 |")
    lines.extend(
        [
            "",
            "## 进化建议",
        ]
    )
    lines.extend(
        [f"- {item}" for item in report.get("recommendations", [])] or ["- 暂无可执行建议。"]
    )
    if report.get("source_errors"):
        lines.extend(["", "## 采集异常"])
        lines.extend([f"- {err}" for err in report["source_errors"][:8]])
    return "\n".join(lines)


def _build_report(
    subscription: dict[str, Any], items: list[dict[str, Any]], source_errors: list[str]
) -> dict[str, Any]:
    now = _now_iso()
    topic = _clean_text(subscription.get("display_name") or subscription.get("topic"), max_len=160)
    keywords = _clean_list(subscription.get("keywords")) or [topic]
    top_items = items[:24]
    findings = [
        f"**{item['title']}**：{item.get('snippet') or '返回了可追踪来源，但没有摘要。'}"
        for item in top_items[:8]
    ]
    if not findings and source_errors:
        findings = ["本轮采集发生异常，未形成可靠情报结论。"]
    elif not findings:
        findings = ["本轮没有发现新的可引用条目。"]
    recommendations = [
        "把高分条目转入深度研究，确认其对产品路线、模型选择或工程架构的影响。",
        "若某类来源长期无结果，调整关键词或补充 RSS/GitHub/arXiv 专用来源。",
    ]
    if any(str(item.get("source")).lower() == "github" for item in top_items):
        recommendations.append(
            "对 GitHub 高热项目做二次筛选：看最近提交、issue 活跃度、license 与可集成性。"
        )
    if any(str(item.get("source")).lower() == "arxiv" for item in top_items):
        recommendations.append("对 arXiv 论文补充方法、实验设置和可复现代码链接，避免只看摘要。")
    summary = (
        f"本轮围绕“{topic}”扫描 {len(top_items)} 条去重情报，"
        f"覆盖 {len(set(item.get('source') for item in top_items))} 类来源。"
    )
    if source_errors:
        summary += f" 另有 {len(source_errors)} 个来源/查询返回异常，已记录供复查。"
    report = {
        "id": f"rpt_{uuid4().hex[:12]}",
        "subscription_id": subscription.get("id"),
        "topic": subscription.get("topic"),
        "title": f"{topic} 情报进化报告",
        "summary": summary,
        "created_at": now,
        "items_analyzed": len(top_items),
        "skills_created": 0,
        "sources_scanned": len(set(item.get("source") for item in top_items)),
        "keywords": keywords,
        "items": top_items,
        "findings": findings,
        "recommendations": recommendations,
        "source_errors": source_errors,
    }
    report["markdown"] = _report_markdown(report)
    return report


def _remember_report(report: dict[str, Any]) -> bool:
    try:
        from runtime.memory.users.user_store import add_fact

        fact = add_fact(
            f"{report.get('title')}: {report.get('summary')}",
            category="intelligence_report",
            source="intelligence",
            scope="global",
            confidence=0.74,
        )
        return fact is not None
    except (OSError, TypeError, ValueError):
        return False


def _run_subscription(
    subscription: dict[str, Any],
    *,
    search_fn: Any = None,
    fetch_fn: Any = None,
    max_results_per_query: int = 5,
) -> dict[str, Any]:
    search = search_fn or _default_search
    fetch = fetch_fn or _default_fetch
    keywords = _clean_list(subscription.get("keywords")) or [_clean_text(subscription.get("topic"))]
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    for query_spec in _queries_for_subscription(subscription):
        query = query_spec["query"]
        source = query_spec["source"]
        try:
            output = search(query, max_results=max_results_per_query)
            if isinstance(output, dict) and output.get("error"):
                errors.append(f"{_source_label(source)}: {output['error']}")
            items.extend(_items_from_search_output(output, source=source, query=query))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{_source_label(source)}: {type(exc).__name__}: {exc}")

    for source in _clean_list(subscription.get("sources")):
        if not source.lower().startswith(("http://", "https://")):
            continue
        try:
            output = fetch(source)
            if isinstance(output, dict) and output.get("error"):
                errors.append(f"{source}: {output['error']}")
                continue
            content = str((output or {}).get("content") or "")
            items.extend(_parse_feed_entries(content, source=source))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}: {type(exc).__name__}: {exc}")

    deduped = _dedupe_items(items, keywords)
    return _build_report(subscription, deduped, errors)


def run_enabled_subscriptions_once(
    store_path: str | Path | None = None,
    *,
    search_fn: Any = None,
    fetch_fn: Any = None,
    remember_reports: bool = True,
    include_disabled: bool = False,
    due_only: bool = False,
    max_subscriptions: int = 10,
    max_results_per_query: int = 5,
) -> dict[str, Any]:
    path = Path(store_path) if store_path is not None else _default_store_path()
    data = _read_store(path)
    reports: list[dict[str, Any]] = []
    checked = 0

    for subscription in data["subscriptions"]:
        checked += 1
        if len(reports) >= max_subscriptions:
            break
        if subscription.get("enabled") is False and not include_disabled:
            continue
        if due_only and not _subscription_due(subscription):
            continue
        report = _run_subscription(
            subscription,
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            max_results_per_query=max_results_per_query,
        )
        report["memory_written"] = _remember_report(report) if remember_reports else False
        subscription["last_run"] = report["created_at"]
        reports.append(report)

    if reports:
        data["reports"] = [*reports, *data["reports"]][:200]
        _write_store(path, data)

    return {
        "ok": True,
        "reports": reports,
        "reports_count": len(reports),
        "subscriptions_checked": checked,
        "due_only": due_only,
    }


def create_intelligence_router(
    store_path: str | Path | None = None,
    *,
    search_fn: Any = None,
    fetch_fn: Any = None,
    remember_reports: bool = True,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    router = APIRouter()
    path = Path(store_path) if store_path is not None else _default_store_path()

    def _auth(request: Request) -> str | None:
        from runtime.safety.auth.principal import require_roles

        principal = require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        return principal.actor_id if principal is not None else None

    @router.get("/api/intelligence/subscriptions")
    def list_subscriptions(request: Request) -> dict[str, Any]:
        _auth(request)
        data = _read_store(path)
        return {"subscriptions": data["subscriptions"]}

    @router.post("/api/intelligence/subscriptions/draft")
    def draft_subscription(
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _auth(request)
        payload = body or {}
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail="goal is required")
        return {"draft": _draft_subscription(goal)}

    @router.post("/api/intelligence/subscriptions")
    def create_subscription(
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _auth(request)
        payload = body or {}
        topic = str(payload.get("topic") or "").strip()
        if not topic:
            raise HTTPException(status_code=400, detail="topic is required")

        keywords = payload.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            keywords = [topic]

        subscription = {
            "id": f"sub_{uuid4().hex[:12]}",
            "topic": topic,
            "display_name": str(payload.get("display_name") or topic).strip() or topic,
            "keywords": [str(item).strip() for item in keywords if str(item).strip()],
            "enabled": bool(payload.get("enabled", True)),
            "last_run": None,
            "created_at": datetime.now(UTC).isoformat(),
            "cadence": str(payload.get("cadence") or "每天").strip() or "每天",
            "schedule_time": str(payload.get("schedule_time") or "00:00").strip() or "00:00",
            "schedule_day": str(payload.get("schedule_day") or "1").strip() or "1",
            "timezone": str(payload.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
            "instructions": str(payload.get("instructions") or "").strip(),
            "sources": payload.get("sources")
            if isinstance(payload.get("sources"), list)
            else ["web", "news"],
        }

        data = _read_store(path)
        existing = {str(item.get("topic", "")).strip().lower() for item in data["subscriptions"]}
        if topic.lower() in existing:
            raise HTTPException(status_code=409, detail="subscription already exists")

        data["subscriptions"].insert(0, subscription)
        _write_store(path, data)
        return subscription

    @router.patch("/api/intelligence/subscriptions/{sub_id}")
    def update_subscription(
        request: Request,
        sub_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _auth(request)
        payload = body or {}
        data = _read_store(path)
        for item in data["subscriptions"]:
            if item.get("id") != sub_id:
                continue
            if "enabled" in payload:
                item["enabled"] = bool(payload["enabled"])
            if "display_name" in payload:
                item["display_name"] = str(payload["display_name"]).strip() or item["display_name"]
            if "keywords" in payload and isinstance(payload["keywords"], list):
                item["keywords"] = [
                    str(keyword).strip() for keyword in payload["keywords"] if str(keyword).strip()
                ]
            if "cadence" in payload:
                item["cadence"] = str(payload["cadence"]).strip() or item.get("cadence", "每天")
            if "schedule_time" in payload:
                item["schedule_time"] = str(payload["schedule_time"]).strip() or item.get(
                    "schedule_time", "09:00"
                )
            if "schedule_day" in payload:
                item["schedule_day"] = str(payload["schedule_day"]).strip() or item.get(
                    "schedule_day", "1"
                )
            if "timezone" in payload:
                item["timezone"] = str(payload["timezone"]).strip() or item.get(
                    "timezone", "Asia/Shanghai"
                )
            if "instructions" in payload:
                item["instructions"] = str(payload["instructions"]).strip()
            if "sources" in payload and isinstance(payload["sources"], list):
                item["sources"] = [
                    str(source).strip() for source in payload["sources"] if str(source).strip()
                ]
            _write_store(path, data)
            return item
        raise HTTPException(status_code=404, detail="subscription not found")

    @router.delete("/api/intelligence/subscriptions/{sub_id}")
    def delete_subscription(request: Request, sub_id: str) -> dict[str, Any]:
        _auth(request)
        data = _read_store(path)
        before = len(data["subscriptions"])
        data["subscriptions"] = [item for item in data["subscriptions"] if item.get("id") != sub_id]
        if len(data["subscriptions"]) == before:
            raise HTTPException(status_code=404, detail="subscription not found")
        _write_store(path, data)
        return {"ok": True, "id": sub_id}

    @router.post("/api/intelligence/subscriptions/{sub_id}/run")
    def run_subscription(
        request: Request,
        sub_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _auth(request)
        data = _read_store(path)
        subscription = next(
            (item for item in data["subscriptions"] if item.get("id") == sub_id),
            None,
        )
        if subscription is None:
            raise HTTPException(status_code=404, detail="subscription not found")
        report = _run_subscription(
            subscription,
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            max_results_per_query=int((body or {}).get("max_results_per_query") or 5),
        )
        report["memory_written"] = _remember_report(report) if remember_reports else False
        subscription["last_run"] = report["created_at"]
        data["reports"] = [report, *data["reports"]][:200]
        _write_store(path, data)
        return {"ok": True, "subscription": subscription, "report": report}

    @router.post("/api/intelligence/run")
    def run_enabled_subscriptions(
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _auth(request)
        payload = body or {}
        return run_enabled_subscriptions_once(
            path,
            search_fn=search_fn,
            fetch_fn=fetch_fn,
            remember_reports=remember_reports,
            include_disabled=bool(payload.get("include_disabled", False)),
            due_only=bool(payload.get("due_only", False)),
            max_subscriptions=int(payload.get("max_subscriptions") or 10),
            max_results_per_query=int(payload.get("max_results_per_query") or 5),
        )

    @router.get("/api/intelligence/reports")
    def list_reports(request: Request, topic: str | None = None) -> dict[str, Any]:
        _auth(request)
        data = _read_store(path)
        reports = data["reports"]
        if topic:
            reports = [item for item in reports if item.get("topic") == topic]
        return {"reports": reports}

    @router.get("/api/intelligence/reports/{report_id}")
    def get_report(request: Request, report_id: str) -> dict[str, Any]:
        _auth(request)
        data = _read_store(path)
        for report in data["reports"]:
            if report.get("id") == report_id:
                return report
        raise HTTPException(status_code=404, detail="report not found")

    return router
