from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from runtime.execution.suckers.browser_launch import (
    launch_chromium,
)
from runtime.platform.io import atomic_write_text
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.path_guard import check_path
from runtime.safety.auth.url_guard import check_url

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

try:
    import trafilatura  # type: ignore[import-untyped]

    TRAFILATURA_AVAILABLE = True
except ImportError:  # pragma: no cover
    TRAFILATURA_AVAILABLE = False
    trafilatura = None  # type: ignore[assignment]

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None  # type: ignore[assignment]


DEFAULT_USER_AGENT = "echo-agent-crawler/0.1"
MAX_PAGES_CAP = 200
MAX_DEPTH_CAP = 10
MAX_BYTES_CAP = 1_000_000
MAX_LINKS_PER_PAGE_CAP = 500


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
            return
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)
                break

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split()).strip()


@dataclass
class _FetchResult:
    url: str
    status_code: int | None
    content_type: str
    text: str
    error: str | None = None
    renderer: str = "http"


@dataclass
class _BrowserRuntime:
    playwright: Any
    browser: Any
    context: Any
    page: Any

    def close(self) -> None:
        for obj in (self.context, self.browser):
            with contextlib.suppress(Exception):
                obj.close()
        with contextlib.suppress(Exception):
            self.playwright.stop()


def _crawl_site(
    start_url: str = "",
    *,
    max_pages: int = 20,
    max_depth: int = 2,
    same_domain: bool = True,
    allowed_domains: list[str] | None = None,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    respect_robots: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
    delay_ms: int = 250,
    timeout_ms: int = 8000,
    max_bytes: int = 200_000,
    max_links_per_page: int = 100,
    extract: bool = True,
    render_mode: str = "http",
    browser_wait_ms: int = 500,
    write_output: bool = True,
    output_path: str = "",
    sandbox_dir: str | None = None,
    allow_private: bool = False,
    client: Any = None,
    browser_page: Any = None,
    sleep_fn: Any = time.sleep,
    **_kw: Any,
) -> dict[str, Any]:
    if not start_url:
        return {"error": "missing start_url", "pages": []}

    verdict = check_url(start_url, allow_private=allow_private)
    if not verdict.allow:
        return {
            "error": f"ssrf_blocked: {verdict.reason}",
            "blocked": True,
            "pages": [],
        }

    parsed_start = urlparse(start_url)
    seed_host = (parsed_start.hostname or "").lower()
    if not seed_host:
        return {"error": "missing_host", "pages": []}

    max_pages = max(1, min(int(max_pages), MAX_PAGES_CAP))
    max_depth = max(0, min(int(max_depth), MAX_DEPTH_CAP))
    delay_ms = max(0, int(delay_ms))
    timeout_ms = max(100, int(timeout_ms))
    max_bytes = max(1_000, min(int(max_bytes), MAX_BYTES_CAP))
    max_links_per_page = max(1, min(int(max_links_per_page), MAX_LINKS_PER_PAGE_CAP))
    browser_wait_ms = max(0, min(int(browser_wait_ms), 30_000))
    user_agent = user_agent.strip() or DEFAULT_USER_AGENT
    render_mode = (render_mode or "http").strip().lower()
    if render_mode not in {"http", "browser", "auto"}:
        return {"error": "render_mode must be one of: http, browser, auto", "pages": []}
    if render_mode == "browser" and browser_page is None and not PLAYWRIGHT_AVAILABLE:
        return {"error": "playwright not installed", "pages": []}

    include_regexes = _compile_patterns(include_patterns or [])
    exclude_regexes = _compile_patterns(exclude_patterns or [])
    allowed = _normalize_domains(allowed_domains or [])

    close_after = False
    if client is None:
        if not HTTPX_AVAILABLE:
            return {"error": "httpx not installed", "pages": []}
        client = httpx.Client(
            timeout=timeout_ms / 1000,
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        close_after = True

    robots_cache: dict[str, RobotFileParser | None] = {}
    queue: deque[tuple[str, int]] = deque([(_normalize_url(start_url), 0)])
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    last_fetch_at = 0.0
    browser_runtime: _BrowserRuntime | None = None

    try:
        while queue and len(records) < max_pages:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)

            skip_reason = _should_skip(
                url,
                seed_host=seed_host,
                same_domain=same_domain,
                allowed_domains=allowed,
                include_regexes=include_regexes,
                exclude_regexes=exclude_regexes,
                allow_private=allow_private,
            )
            if skip_reason:
                skipped.append({"url": url, "reason": skip_reason})
                continue

            if respect_robots and not _robots_can_fetch(
                client,
                url,
                user_agent=user_agent,
                timeout_ms=timeout_ms,
                allow_private=allow_private,
                cache=robots_cache,
            ):
                skipped.append({"url": url, "reason": "robots_disallow"})
                continue

            now = time.monotonic()
            wait_s = (delay_ms / 1000) - (now - last_fetch_at)
            if last_fetch_at and wait_s > 0:
                sleep_fn(wait_s)
            last_fetch_at = time.monotonic()

            if render_mode == "browser":
                page, browser_runtime, browser_err = _ensure_browser_page(
                    browser_page,
                    browser_runtime,
                    user_agent=user_agent,
                )
                if browser_err:
                    records.append(
                        {
                            "url": url,
                            "depth": depth,
                            "status_code": None,
                            "error": browser_err,
                        }
                    )
                    continue
                fetched = _fetch_page_browser(
                    page,
                    url,
                    timeout_ms=timeout_ms,
                    wait_ms=browser_wait_ms,
                    max_bytes=max_bytes,
                )
            else:
                fetched = _fetch_page(
                    client,
                    url,
                    timeout_ms=timeout_ms,
                    max_bytes=max_bytes,
                    user_agent=user_agent,
                    allow_private=allow_private,
                )
                if render_mode == "auto" and _should_try_browser_fallback(fetched):
                    page, browser_runtime, browser_err = _ensure_browser_page(
                        browser_page,
                        browser_runtime,
                        user_agent=user_agent,
                    )
                    if browser_err:
                        skipped.append(
                            {
                                "url": url,
                                "reason": f"browser_fallback_unavailable: {browser_err}",
                            }
                        )
                    else:
                        rendered = _fetch_page_browser(
                            page,
                            url,
                            timeout_ms=timeout_ms,
                            wait_ms=browser_wait_ms,
                            max_bytes=max_bytes,
                        )
                        if rendered.error:
                            skipped.append(
                                {
                                    "url": url,
                                    "reason": f"browser_fallback_failed: {rendered.error}",
                                }
                            )
                        else:
                            fetched = rendered
            if fetched.error:
                records.append(
                    {
                        "url": url,
                        "depth": depth,
                        "status_code": fetched.status_code,
                        "error": fetched.error,
                    }
                )
                continue

            parser = _parse_html(fetched.text)
            extracted = _extract_content(fetched.text, fetched.url) if extract else None
            content = extracted if extracted is not None else fetched.text
            links = _normalize_links(
                base_url=fetched.url,
                hrefs=parser.links,
                max_links=max_links_per_page,
            )

            record = {
                "url": fetched.url,
                "requested_url": url,
                "depth": depth,
                "status_code": fetched.status_code,
                "content_type": fetched.content_type,
                "renderer": fetched.renderer,
                "title": parser.title,
                "extracted": extracted is not None,
                "content_chars": len(content),
                "content": content,
                "links_found": len(links),
                "links": links,
            }
            records.append(record)

            if depth >= max_depth or not _should_follow_links(fetched):
                continue
            for link in links:
                if link not in seen:
                    queue.append((link, depth + 1))
    finally:
        if browser_runtime is not None:
            browser_runtime.close()
        if close_after:
            client.close()

    written_path = ""
    if write_output:
        target, err = _resolve_output_path(output_path, start_url, sandbox_dir)
        if err:
            return {
                "error": err,
                "pages": _preview_records(records),
                "skipped": skipped[:20],
            }
        written_path = str(target)
        lines = [json.dumps(row, ensure_ascii=False) for row in records]
        atomic_write_text(target, "\n".join(lines), keep_backup=True)

    return {
        "start_url": start_url,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "pages_crawled": len(records),
        "queued_remaining": len(queue),
        "skipped_count": len(skipped),
        "output_path": written_path,
        "pages": _preview_records(records),
        "skipped": skipped[:20],
    }


def _fetch_page(
    client: Any,
    url: str,
    *,
    timeout_ms: int,
    max_bytes: int,
    user_agent: str,
    allow_private: bool = False,
) -> _FetchResult:
    try:
        if HTTPX_AVAILABLE and isinstance(client, httpx.Client):
            from runtime.safety.auth.url_guard import safe_httpx_request

            resp = safe_httpx_request(
                "GET",
                url,
                timeout=timeout_ms / 1000,
                headers={"User-Agent": user_agent},
                allow_private=allow_private,
                follow_redirects=False,
            )
        else:
            # Injected clients are test/dedicated transport seams; production
            # httpx clients take the pinned-IP path above.
            resp = client.get(
                url,
                timeout=timeout_ms / 1000,
                headers={"User-Agent": user_agent},
            )
    except Exception as exc:  # noqa: BLE001
        return _FetchResult(url, None, "", "", f"http_error: {type(exc).__name__}: {exc}")

    text = resp.text or ""
    if len(text) > max_bytes:
        text = text[:max_bytes]
    return _FetchResult(
        url=str(getattr(resp, "url", url)),
        status_code=getattr(resp, "status_code", None),
        content_type=(getattr(resp, "headers", {}) or {}).get("content-type", ""),
        text=text,
    )


def _ensure_browser_page(
    page: Any,
    runtime: _BrowserRuntime | None,
    *,
    user_agent: str,
) -> tuple[Any, _BrowserRuntime | None, str | None]:
    if page is not None:
        return page, runtime, None
    if runtime is not None:
        return runtime.page, runtime, None
    if not PLAYWRIGHT_AVAILABLE:
        return None, runtime, "playwright not installed"
    try:
        pw = sync_playwright().start()
        browser = launch_chromium(pw.chromium, headless=True)
        context = browser.new_context(user_agent=user_agent)
        new_page = context.new_page()
        runtime = _BrowserRuntime(
            playwright=pw,
            browser=browser,
            context=context,
            page=new_page,
        )
        return new_page, runtime, None
    except Exception as exc:  # noqa: BLE001
        return None, runtime, f"browser_error: {type(exc).__name__}: {exc}"


def _fetch_page_browser(
    page: Any,
    url: str,
    *,
    timeout_ms: int,
    wait_ms: int,
    max_bytes: int,
) -> _FetchResult:
    try:
        resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        return _FetchResult(
            url=url,
            status_code=None,
            content_type="",
            text="",
            error=f"browser_nav_error: {type(exc).__name__}: {exc}",
            renderer="browser",
        )

    if wait_ms > 0:
        with contextlib.suppress(Exception):
            page.wait_for_timeout(wait_ms)

    try:
        text = page.content() or ""
    except Exception as exc:  # noqa: BLE001
        return _FetchResult(
            url=getattr(page, "url", url),
            status_code=getattr(resp, "status", None) if resp else None,
            content_type="",
            text="",
            error=f"browser_read_error: {type(exc).__name__}: {exc}",
            renderer="browser",
        )

    if len(text) > max_bytes:
        text = text[:max_bytes]
    headers = getattr(resp, "headers", {}) if resp else {}
    return _FetchResult(
        url=str(getattr(page, "url", url)),
        status_code=getattr(resp, "status", None) if resp else None,
        content_type=(headers or {}).get("content-type", "text/html"),
        text=text,
        renderer="browser",
    )


def _should_follow_links(fetched: _FetchResult) -> bool:
    status = fetched.status_code or 0
    content_type = (fetched.content_type or "").lower()
    return 200 <= status < 300 and (
        not content_type or "html" in content_type or "xml" in content_type
    )


def _should_try_browser_fallback(fetched: _FetchResult) -> bool:
    if fetched.error or fetched.renderer == "browser":
        return False
    if not _should_follow_links(fetched):
        return False
    html = fetched.text or ""
    if not html:
        return False
    parser = _parse_html(html)
    if parser.links:
        return False
    lower = html.lower()
    js_markers = (
        "<script",
        'id="root"',
        "id='root'",
        'id="app"',
        "id='app'",
        "__next",
        "data-reactroot",
        "ng-version",
    )
    return any(marker in lower for marker in js_markers)


def _parse_html(html: str) -> _LinkParser:
    parser = _LinkParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001
        return _LinkParser()
    return parser


def _extract_content(html: str, url: str) -> str | None:
    if not TRAFILATURA_AVAILABLE or not html:
        return None
    try:
        return trafilatura.extract(
            html,
            url=url,
            favor_precision=True,
            include_comments=False,
            include_tables=True,
            with_metadata=False,
        )
    except (OSError, ValueError):
        return None


def _normalize_links(base_url: str, hrefs: list[str], max_links: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        normalized = _normalize_url(urljoin(base_url, href))
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= max_links:
            break
    return out


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    url, _fragment = urldefrag(url.strip())
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if not parsed.hostname:
        return ""
    return parsed.geturl()


def _normalize_domains(domains: list[str]) -> set[str]:
    out: set[str] = set()
    for domain in domains:
        value = str(domain).strip().lower()
        if not value:
            continue
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = parsed.hostname or value
        out.add(host.strip(".").lower())
    return out


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        if not pattern:
            continue
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            compiled.append(re.compile(re.escape(pattern)))
    return compiled


def _should_skip(
    url: str,
    *,
    seed_host: str,
    same_domain: bool,
    allowed_domains: set[str],
    include_regexes: list[re.Pattern[str]],
    exclude_regexes: list[re.Pattern[str]],
    allow_private: bool,
) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if allowed_domains:
        if not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
            return "outside_allowed_domains"
    elif same_domain and host != seed_host:
        return "outside_seed_domain"

    if include_regexes and not any(pattern.search(url) for pattern in include_regexes):
        return "include_pattern_miss"
    if exclude_regexes and any(pattern.search(url) for pattern in exclude_regexes):
        return "exclude_pattern_hit"

    verdict = check_url(url, allow_private=allow_private)
    if not verdict.allow:
        return f"unsafe_url: {verdict.reason}"
    return None


def _robots_can_fetch(
    client: Any,
    url: str,
    *,
    user_agent: str,
    timeout_ms: int,
    allow_private: bool,
    cache: dict[str, RobotFileParser | None],
) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        robots_url = f"{origin}/robots.txt"
        verdict = check_url(robots_url, allow_private=allow_private)
        if not verdict.allow:
            cache[origin] = None
        else:
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                if HTTPX_AVAILABLE and isinstance(client, httpx.Client):
                    from runtime.safety.auth.url_guard import safe_httpx_request

                    resp = safe_httpx_request(
                        "GET",
                        robots_url,
                        timeout=timeout_ms / 1000,
                        headers={"User-Agent": user_agent},
                        allow_private=allow_private,
                        follow_redirects=False,
                    )
                else:
                    resp = client.get(
                        robots_url,
                        timeout=timeout_ms / 1000,
                        headers={"User-Agent": user_agent},
                    )
                if getattr(resp, "status_code", 0) == 200:
                    parser.parse((resp.text or "").splitlines())
                    cache[origin] = parser
                else:
                    cache[origin] = None
            except Exception:  # noqa: BLE001
                cache[origin] = None
    parser = cache.get(origin)
    if parser is None:
        return True
    try:
        return bool(parser.can_fetch(user_agent, url))
    except Exception:  # noqa: BLE001
        return True


def _resolve_output_path(
    output_path: str,
    start_url: str,
    sandbox_dir: str | None,
) -> tuple[Path, str | None]:
    target = Path(output_path) if output_path else _default_output_path(start_url)
    if sandbox_dir is not None and not target.is_absolute():
        from runtime.safety.auth.path_guard import normalize_scoped_relative_path

        target = normalize_scoped_relative_path(target, sandbox_dir)
    verdict = check_path(target, sandbox_dir=sandbox_dir)
    if not verdict.allow:
        return target, verdict.reason
    resolved = Path(verdict.resolved) if verdict.resolved else target
    if resolved.suffix.lower() not in {"", ".jsonl"}:
        return resolved, "output_path must be .jsonl"
    if not resolved.suffix:
        resolved = resolved.with_suffix(".jsonl")
    return resolved, None


def _default_output_path(start_url: str) -> Path:
    parsed = urlparse(start_url)
    host = (parsed.hostname or "crawl").replace(".", "_")
    digest = hashlib.sha1(start_url.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return app_paths().data_dir / "crawls" / f"{host}-{digest}.jsonl"


def _preview_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for record in records[:20]:
        item = {k: v for k, v in record.items() if k not in {"content", "links"}}
        content = str(record.get("content") or "")
        if content:
            item["content_preview"] = content[:300]
        item["links_preview"] = list(record.get("links") or [])[:10]
        preview.append(item)
    return preview


CRAWLER_SKILL_NAMES = ["crawl_site"]


def register_crawler_skills(registry: SkillRegistry) -> int:
    if not HTTPX_AVAILABLE:
        return 0

    registry.register(
        Skill(
            name="crawl_site",
            description=(
                "Purpose: crawl from a seed URL with a bounded queue, URL de-dupe, "
                "domain limits, optional robots.txt checks, delay, HTML link discovery, "
                "main-content extraction, and JSONL output. Use this for small-to-medium "
                "site discovery jobs, not for high-volume distributed scraping.\n"
                "Key args: start_url required; max_pages default 20; max_depth default 2; "
                "same_domain default true; allowed_domains optional; include_patterns / "
                "exclude_patterns optional regex or literal filters; respect_robots default true; "
                "delay_ms default 250; render_mode is http|auto|browser (default http; "
                "auto retries likely JS apps with Playwright); browser_wait_ms default 500; "
                "output_path optional .jsonl; sandbox_dir optional.\n"
                'Example: crawl_site({"start_url":"https://example.com/docs",'
                '"max_pages":30,"max_depth":2})'
            ),
            summary="Bounded site crawler with robots, de-dupe, and JSONL output.",
            affinity=["crawler", "web", "scrape"],
            cost_profile="mid",
            trusted_source="skill://public/crawl_site",
            handler=_crawl_site,
            tests=[
                SkillTestCase(
                    name="missing_start_url_returns_error",
                    tier="golden",
                    args={"start_url": ""},
                    expect=SkillExpect(schema_keys=["error", "pages"]),
                ),
            ],
        )
    )
    return len(CRAWLER_SKILL_NAMES)


__all__ = [
    "CRAWLER_SKILL_NAMES",
    "HTTPX_AVAILABLE",
    "_crawl_site",
    "register_crawler_skills",
]
