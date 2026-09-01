from __future__ import annotations

import json
from pathlib import Path

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.crawler_skills import (
    CRAWLER_SKILL_NAMES,
    _crawl_site,
    register_crawler_skills,
)


class _MockResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        url: str = "http://127.0.0.1/",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {"content-type": "text/html"}


class _MockClient:
    def __init__(self, responses: dict[str, _MockResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str, **_kw):
        self.calls.append(url)
        return self.responses.get(
            url,
            _MockResponse(status_code=404, text="not found", url=url),
        )

    def close(self) -> None:
        self.closed = True


class _FakeBrowserResponse:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {"content-type": "text/html"}


class _FakeBrowserPage:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.url = ""
        self.goto_calls: list[str] = []
        self.wait_calls: list[int] = []

    def goto(self, url: str, timeout: int = 0, wait_until: str = ""):
        self.goto_calls.append(url)
        self.url = url
        return _FakeBrowserResponse()

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_calls.append(ms)

    def content(self) -> str:
        return self.pages.get(self.url, "<html><title>Missing</title></html>")


def _no_sleep(_seconds: float) -> None:
    return None


def test_missing_start_url_returns_error() -> None:
    result = _crawl_site(start_url="")
    assert "error" in result
    assert result["pages"] == []


def test_crawl_site_follows_same_domain_links_and_writes_jsonl(tmp_path: Path) -> None:
    start = "http://127.0.0.1/"
    page_a = "http://127.0.0.1/a"
    outside = "http://127.0.0.2/out"
    out = tmp_path / "crawl.jsonl"
    client = _MockClient(
        {
            "http://127.0.0.1/robots.txt": _MockResponse(
                text="User-agent: *\nAllow: /\n",
                url="http://127.0.0.1/robots.txt",
            ),
            start: _MockResponse(
                text=(
                    "<html><title>Home</title><body>"
                    f"<a href='{page_a}'>A</a>"
                    f"<a href='{outside}'>Out</a>"
                    "</body></html>"
                ),
                url=start,
            ),
            page_a: _MockResponse(
                text="<html><title>A</title><body>alpha</body></html>",
                url=page_a,
            ),
        }
    )

    result = _crawl_site(
        start_url=start,
        client=client,
        max_pages=5,
        max_depth=1,
        delay_ms=0,
        output_path=str(out),
        allow_private=True,
        sleep_fn=_no_sleep,
        extract=False,
    )

    assert result["pages_crawled"] == 2
    assert result["output_path"] == str(out.resolve())
    assert any(item["reason"] == "outside_seed_domain" for item in result["skipped"])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert [row["title"] for row in rows] == ["Home", "A"]


def test_crawl_site_respects_robots_disallow(tmp_path: Path) -> None:
    start = "http://127.0.0.1/"
    private = "http://127.0.0.1/private"
    client = _MockClient(
        {
            "http://127.0.0.1/robots.txt": _MockResponse(
                text="User-agent: *\nDisallow: /private\n",
                url="http://127.0.0.1/robots.txt",
            ),
            start: _MockResponse(
                text=f"<html><title>Home</title><a href='{private}'>Private</a></html>",
                url=start,
            ),
            private: _MockResponse(
                text="<html><title>Private</title>secret</html>",
                url=private,
            ),
        }
    )

    result = _crawl_site(
        start_url=start,
        client=client,
        max_pages=5,
        max_depth=1,
        delay_ms=0,
        output_path=str(tmp_path / "crawl.jsonl"),
        allow_private=True,
        sleep_fn=_no_sleep,
        extract=False,
    )

    assert result["pages_crawled"] == 1
    assert any(item["url"] == private for item in result["skipped"])
    assert any(item["reason"] == "robots_disallow" for item in result["skipped"])
    assert private not in [url for url in client.calls if url != "http://127.0.0.1/robots.txt"]


def test_crawl_site_browser_mode_uses_rendered_links(tmp_path: Path) -> None:
    start = "http://127.0.0.1/"
    rendered = "http://127.0.0.1/rendered"
    client = _MockClient(
        {
            "http://127.0.0.1/robots.txt": _MockResponse(
                text="User-agent: *\nAllow: /\n",
                url="http://127.0.0.1/robots.txt",
            ),
        }
    )
    page = _FakeBrowserPage(
        {
            start: (
                "<html><title>Rendered Home</title><body>"
                f"<a href='{rendered}'>Rendered</a>"
                "</body></html>"
            ),
            rendered: "<html><title>Rendered Detail</title><body>ready</body></html>",
        }
    )

    result = _crawl_site(
        start_url=start,
        client=client,
        browser_page=page,
        render_mode="browser",
        browser_wait_ms=10,
        max_pages=5,
        max_depth=1,
        delay_ms=0,
        output_path=str(tmp_path / "crawl.jsonl"),
        allow_private=True,
        sleep_fn=_no_sleep,
        extract=False,
    )

    assert result["pages_crawled"] == 2
    assert page.goto_calls == [start, rendered]
    assert page.wait_calls == [10, 10]
    assert result["pages"][0]["renderer"] == "browser"
    assert result["pages"][0]["title"] == "Rendered Home"


def test_crawl_site_auto_mode_falls_back_to_browser_for_js_shell(tmp_path: Path) -> None:
    start = "http://127.0.0.1/"
    rendered = "http://127.0.0.1/rendered"
    client = _MockClient(
        {
            "http://127.0.0.1/robots.txt": _MockResponse(
                text="User-agent: *\nAllow: /\n",
                url="http://127.0.0.1/robots.txt",
            ),
            start: _MockResponse(
                text="<html><title>Shell</title><body><div id='root'></div><script></script></body></html>",
                url=start,
            ),
            rendered: _MockResponse(
                text="<html><title>HTTP Detail</title><body>detail</body></html>",
                url=rendered,
            ),
        }
    )
    page = _FakeBrowserPage(
        {
            start: (
                "<html><title>Rendered Shell</title><body>"
                f"<a href='{rendered}'>Rendered</a>"
                "</body></html>"
            ),
        }
    )

    result = _crawl_site(
        start_url=start,
        client=client,
        browser_page=page,
        render_mode="auto",
        browser_wait_ms=0,
        max_pages=5,
        max_depth=1,
        delay_ms=0,
        output_path=str(tmp_path / "crawl.jsonl"),
        allow_private=True,
        sleep_fn=_no_sleep,
        extract=False,
    )

    assert result["pages_crawled"] == 2
    assert page.goto_calls == [start]
    assert result["pages"][0]["renderer"] == "browser"
    assert result["pages"][1]["renderer"] == "http"
    assert result["pages"][0]["links_preview"] == [rendered]


def test_crawl_site_blocks_private_by_default() -> None:
    result = _crawl_site(start_url="http://127.0.0.1/")
    assert result["blocked"] is True
    assert "ssrf_blocked" in result["error"]


def test_register_crawler_skills(monkeypatch) -> None:
    from runtime.execution.suckers import crawler_skills

    monkeypatch.setattr(crawler_skills, "HTTPX_AVAILABLE", True)
    reg = SkillRegistry()
    count = register_crawler_skills(reg)
    assert count == len(CRAWLER_SKILL_NAMES)
    assert reg.has("crawl_site")
    assert reg.get("crawl_site").cost_profile == "mid"


def test_register_returns_zero_without_httpx(monkeypatch) -> None:
    from runtime.execution.suckers import crawler_skills

    monkeypatch.setattr(crawler_skills, "HTTPX_AVAILABLE", False)
    reg = SkillRegistry()
    assert register_crawler_skills(reg) == 0
    assert not reg.has("crawl_site")
