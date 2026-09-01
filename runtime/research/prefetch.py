"""Lightweight source prefetch for deep research jobs."""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from runtime.research.deep_research import (
    ResearchEvidence,
    ResearchJob,
    ResearchPrefetchLog,
    ResearchSource,
)

SearchHandler = Callable[..., dict[str, Any]]
FetchHandler = Callable[..., dict[str, Any]]


@dataclass
class PrefetchResult:
    evidence: list[ResearchEvidence] = field(default_factory=list)
    logs: list[ResearchPrefetchLog] = field(default_factory=list)


class ResearchPrefetcher:
    """Run a small search/fetch pass before subagents start.

    The prefetcher intentionally keeps a tight budget. It seeds an evidence pool
    for workers without replacing their deeper role-specific search.
    """

    def __init__(
        self,
        *,
        search_handler: SearchHandler | None = None,
        fetch_handler: FetchHandler | None = None,
        max_queries: int = 4,
        max_fetches: int = 6,
        max_results_per_query: int = 3,
        timeout_ms: int = 5000,
    ) -> None:
        self.search_handler = search_handler or _default_search_handler()
        self.fetch_handler = fetch_handler or _default_fetch_handler()
        self.max_queries = max(0, max_queries)
        self.max_fetches = max(0, max_fetches)
        self.max_results_per_query = max(1, max_results_per_query)
        self.timeout_ms = timeout_ms
        # 并发搜索的最大 worker 数。web_search 是同步 httpx 调用(每 query
        # 自建 client),线程安全;限制上限避免同时打爆搜索后端限流。
        self.max_concurrent_searches = 4

    def prefetch(self, job: ResearchJob) -> PrefetchResult:
        evidence: list[ResearchEvidence] = []
        logs: list[ResearchPrefetchLog] = []

        # 预取全部待搜索 query(受 max_queries 预算约束),再并发执行,
        # 而不是逐个串行等待——对照 DeepSeek Harness RC.8 的 web_search 并发。
        pending: list[tuple[ResearchSource, str]] = []
        remaining_queries = self.max_queries
        for source in job.sources:
            if not source.enabled:
                continue
            if source.provider == "web_search" and self.search_handler:
                for query in _queries_for_source(source):
                    if remaining_queries <= 0:
                        break
                    remaining_queries -= 1
                    pending.append((source, query))
            elif source.provider == "fetch_url" and self.fetch_handler:
                result = self._fetch_user_urls(source, job)
                evidence.extend(result.evidence)
                logs.extend(result.logs)
            elif source.provider in {"uploaded_file", "local_file", "manual_material"}:
                result = _material_evidence(source, job)
                evidence.extend(result.evidence)
                logs.extend(result.logs)
            else:
                logs.append(
                    _log_for_source(
                        source,
                        action="skip",
                        status="skipped",
                        error=f"provider unavailable: {source.provider}",
                    )
                )

        if pending:
            search_results = self._run_searches(pending, job.topic)
            for result in search_results:
                evidence.extend(result.evidence)
                logs.extend(result.logs)

        return PrefetchResult(evidence=_dedupe_prefetch(evidence), logs=logs)

    def _run_searches(
        self,
        pending: list[tuple[ResearchSource, str]],
        topic: str,
    ) -> list[PrefetchResult]:
        """并发执行多个 web_search query,保持输入顺序返回结果。

        用 ThreadPoolExecutor 并发;每个 query 独立失败互不影响(单个失败
        只产出 error evidence,不拖垮整批)。线程数 = min(配置上限, query 数)。
        """
        workers = min(self.max_concurrent_searches, len(pending))
        if workers <= 1:
            return [self._search(source, topic, query) for source, query in pending]

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="prefetch-search") as pool:
            futures = [pool.submit(self._search, source, topic, query) for source, query in pending]
            results: list[PrefetchResult | None] = []
            for idx, fut in enumerate(futures):
                source, query = pending[idx]
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001 — 兜底:worker 崩溃不拖垮整批
                    results.append(
                        PrefetchResult(
                            evidence=[
                                _error_evidence(
                                    source,
                                    topic,
                                    f"search_worker_error: {type(exc).__name__}: {exc}",
                                )
                            ],
                            logs=[
                                _log_for_source(
                                    source,
                                    action="search",
                                    query=query,
                                    status="failed",
                                    error=f"search_worker_error: {type(exc).__name__}: {exc}",
                                )
                            ],
                        )
                    )
        return [r for r in results if r is not None]

    def _search(
        self,
        source: ResearchSource,
        topic: str,
        query: str,
    ) -> PrefetchResult:
        try:
            payload = self.search_handler(
                query=query,
                max_results=self.max_results_per_query,
                timeout_ms=self.timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"search_error: {type(exc).__name__}: {exc}"
            return PrefetchResult(
                evidence=[_error_evidence(source, topic, message)],
                logs=[
                    _log_for_source(
                        source,
                        action="search",
                        query=query,
                        status="failed",
                        error=message,
                    )
                ],
            )
        if payload.get("error"):
            message = str(payload["error"])
            return PrefetchResult(
                evidence=[_error_evidence(source, topic, message)],
                logs=[
                    _log_for_source(
                        source,
                        action="search",
                        query=query,
                        status="failed",
                        error=message,
                    )
                ],
            )

        backend = payload.get("backend") or "web_search"
        out: list[ResearchEvidence] = []
        results = payload.get("results", [])[: self.max_results_per_query]
        for item in results:
            if not isinstance(item, dict):
                continue
            url = _clean_str(item.get("url"))
            title = _clean_str(item.get("title")) or url or query
            snippet = _clean_str(item.get("snippet") or item.get("content"))
            if not title and not snippet:
                continue
            out.append(
                ResearchEvidence(
                    title=title,
                    url=url or None,
                    source_kind=source.kind,
                    quote_or_summary=(
                        f"Prefetch hit via {backend} for query '{query}': {snippet or title}"
                    ),
                    claim=topic,
                    stance="context",
                    confidence=0.45,
                )
            )
        return PrefetchResult(
            evidence=out,
            logs=[
                _log_for_source(
                    source,
                    action="search",
                    query=query,
                    result_count=len(results),
                    evidence_count=len(out),
                )
            ],
        )

    def _fetch_user_urls(
        self,
        source: ResearchSource,
        job: ResearchJob,
    ) -> PrefetchResult:
        out: list[ResearchEvidence] = []
        logs: list[ResearchPrefetchLog] = []
        fetched = 0
        for material in job.materials:
            if material.kind not in ("url", "site") or not material.url:
                continue
            if fetched >= self.max_fetches:
                break
            fetched += 1
            try:
                payload = self.fetch_handler(
                    url=material.url,
                    timeout_ms=self.timeout_ms,
                    max_bytes=60_000,
                )
            except Exception as exc:  # noqa: BLE001
                message = f"fetch_error: {type(exc).__name__}: {exc}"
                out.append(_error_evidence(source, job.topic, message))
                logs.append(
                    _log_for_source(
                        source,
                        action="fetch",
                        url=material.url,
                        status="failed",
                        error=message,
                    )
                )
                continue
            if payload.get("error"):
                message = str(payload["error"])
                out.append(_error_evidence(source, job.topic, message))
                logs.append(
                    _log_for_source(
                        source,
                        action="fetch",
                        url=material.url,
                        status="failed",
                        error=message,
                    )
                )
                continue
            content = _clean_str(payload.get("content"))[:700]
            status = payload.get("status_code")
            out.append(
                ResearchEvidence(
                    title=material.title or material.url,
                    url=_clean_str(payload.get("url")) or material.url,
                    source_kind=source.kind,
                    quote_or_summary=(
                        f"Fetched user-provided URL"
                        f"{f' (status {status})' if status else ''}: {content}"
                    ),
                    claim=job.topic,
                    stance="context",
                    confidence=0.58,
                )
            )
            logs.append(
                _log_for_source(
                    source,
                    action="fetch",
                    url=material.url,
                    result_count=1,
                    evidence_count=1,
                )
            )
        return PrefetchResult(evidence=out, logs=logs)


def _default_search_handler() -> SearchHandler | None:
    try:
        from runtime.execution.suckers.web_skills import _web_search

        return _web_search
    except Exception:  # pragma: no cover
        return None


def _default_fetch_handler() -> FetchHandler | None:
    try:
        from runtime.execution.suckers.web_skills import _fetch_url

        return _fetch_url
    except Exception:  # pragma: no cover
        return None


def _queries_for_source(source: ResearchSource) -> list[str]:
    bases = source.query_templates or ([source.query_hint] if source.query_hint else [])
    filters = [
        item
        for item in source.site_filters
        if item.startswith("site:") or item.startswith("filetype:")
    ][:3]
    out: list[str] = []
    for base in bases:
        query = " ".join([base, *filters]).strip()
        if query and query not in out:
            out.append(query[:350])
    return out


def _material_evidence(source: ResearchSource, job: ResearchJob) -> PrefetchResult:
    out: list[ResearchEvidence] = []
    logs: list[ResearchPrefetchLog] = []
    for material in job.materials:
        if source.provider == "uploaded_file" and material.kind != "file":
            continue
        if source.provider == "manual_material" and material.kind != "text":
            continue
        if source.provider == "local_file" and not material.path:
            continue
        target = material.path or material.text or material.notes or material.title
        if not target:
            continue
        out.append(
            ResearchEvidence(
                title=material.title or material.id,
                source_kind=source.kind,
                quote_or_summary=f"User material available for extraction: {str(target)[:700]}",
                claim=job.topic,
                stance="context",
                confidence=0.55,
            )
        )
    logs.append(
        _log_for_source(
            source,
            action="material",
            result_count=len(out),
            evidence_count=len(out),
            status="completed" if out else "skipped",
            error=None if out else "no matching user materials",
        )
    )
    return PrefetchResult(evidence=out, logs=logs)


def _log_for_source(
    source: ResearchSource,
    *,
    action: str,
    status: str = "completed",
    query: str | None = None,
    url: str | None = None,
    result_count: int = 0,
    evidence_count: int = 0,
    error: str | None = None,
) -> ResearchPrefetchLog:
    return ResearchPrefetchLog(
        source_id=source.id,
        source_kind=source.kind,
        source_label=source.label,
        provider=source.provider,
        action=action,  # type: ignore[arg-type]
        query=query,
        url=url,
        status=status,  # type: ignore[arg-type]
        result_count=result_count,
        evidence_count=evidence_count,
        error=error,
    )


def _error_evidence(source: ResearchSource, topic: str, message: str) -> ResearchEvidence:
    return ResearchEvidence(
        title=f"{source.label} prefetch note",
        source_kind=source.kind,
        quote_or_summary=f"Prefetch did not complete: {message}",
        claim=topic,
        stance="context",
        confidence=0.2,
    )


def _dedupe_prefetch(evidence: list[ResearchEvidence]) -> list[ResearchEvidence]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ResearchEvidence] = []
    for item in evidence:
        key = (item.url or "", item.claim or "", item.title or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


__all__ = ["PrefetchResult", "ResearchPrefetcher"]
