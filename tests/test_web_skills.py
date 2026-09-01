"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.reach_skills import REACH_SKILL_NAMES
from runtime.execution.suckers.web_skills import (
    HTTPX_AVAILABLE,
    TRAFILATURA_AVAILABLE,
    WEB_SKILL_NAMES,
    _brave_search,
    _ddg_search,
    _fetch_url,
    _resolve_backend,
    _searxng_search,
    _serper_search,
    _tavily_search,
    _web_search,
    register_web_skills,
)


class _MockResponse:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        json_data: dict | None = None,
        headers: dict | None = None,
        url: str = "https://mock.example/",
    ):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {"content-type": "text/html"}
        self._json = json_data

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _MockClient:
    """duck-typed httpx client for tests."""

    def __init__(
        self,
        get_response: _MockResponse | None = None,
        post_response: _MockResponse | None = None,
        raise_on_get: Exception | None = None,
    ):
        self._get = get_response
        self._post = post_response
        self._raise = raise_on_get
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url: str, **kw):
        self.calls.append(("GET", url, kw))
        if self._raise:
            raise self._raise
        return self._get or _MockResponse(text="mock body")

    def post(self, url: str, **kw):
        self.calls.append(("POST", url, kw))
        return self._post or _MockResponse(text="")


# ═══════════════════════════════════════════════════════════
# fetch_url
# ═══════════════════════════════════════════════════════════


class TestFetchUrl:
    def test_missing_url_returns_error(self):
        result = _fetch_url(url="")
        assert "error" in result

    def test_basic_fetch_with_mock_client(self):
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text="<html>hello</html>",
                url="https://example.com/",
                headers={"content-type": "text/html"},
            )
        )
        result = _fetch_url(url="https://example.com/", client=client)
        assert result["status_code"] == 200
        assert result["content"] == "<html>hello</html>"
        assert result["truncated"] is False
        assert len(client.calls) == 1

    def test_truncation(self):
        big_body = "x" * 200_000
        client = _MockClient(get_response=_MockResponse(status_code=200, text=big_body))
        result = _fetch_url(url="https://x.com/", client=client, max_bytes=1000)
        assert result["truncated"] is True
        assert len(result["content"]) == 1000

    def test_exception_captured(self):
        client = _MockClient(raise_on_get=RuntimeError("network down"))
        result = _fetch_url(url="https://x.com/", client=client)
        assert "error" in result
        assert "network down" in result["error"]

    def test_default_no_extract(self):
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text="<html><body><p>hi</p></body></html>",
                url="https://example.com/",
            )
        )
        result = _fetch_url(url="https://example.com/", client=client)
        assert result["extracted"] is False
        assert "<p>hi</p>" in result["content"]


_ARTICLE_HTML = """<html>
<head>
  <title>Sample Article Title</title>
  <meta name="author" content="Jane Doe">
  <meta name="description" content="A short description">
</head>
<body>
  <nav>Home | About | Contact</nav>
  <article>
    <h1>Breaking News</h1>
    <p>This is the main article body that describes an important event unfolding
    across the industry with many details worth preserving for readers.</p>
    <p>A second paragraph with more context and analysis to make trafilatura
    confident this is real content rather than boilerplate noise.</p>
  </article>
  <footer>Copyright 2026 ExampleCo</footer>
</body>
</html>"""


@pytest.mark.skipif(not TRAFILATURA_AVAILABLE, reason="trafilatura not installed")
class TestFetchUrlExtract:
    def test_extract_returns_clean_text_and_metadata(self):
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text=_ARTICLE_HTML,
                url="https://example.com/article",
            )
        )
        result = _fetch_url(url="https://example.com/article", client=client, extract=True)
        assert result["extracted"] is True
        assert "main article body" in result["content"]
        assert "Home | About | Contact" not in result["content"]
        assert "Copyright 2026" not in result["content"]
        assert result["metadata"].get("title") in ("Sample Article Title", "Breaking News")
        assert result["metadata"].get("author") == "Jane Doe"

    def test_extract_truncation_applies_to_clean_text(self):
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text=_ARTICLE_HTML,
                url="https://example.com/article",
            )
        )
        result = _fetch_url(
            url="https://example.com/article",
            client=client,
            extract=True,
            max_bytes=50,
        )
        assert result["extracted"] is True
        assert result["truncated"] is True
        assert len(result["content"]) == 50

    def test_extract_falls_back_when_no_main_content(self):
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text="<html><body></body></html>",
                url="https://example.com/empty",
            )
        )
        result = _fetch_url(url="https://example.com/empty", client=client, extract=True)
        assert result["extracted"] is False
        assert result.get("extract_failed") == "no_main_content"
        assert "<html>" in result["content"]


# ═══════════════════════════════════════════════════════════
# DDG search
# ═══════════════════════════════════════════════════════════


_DDG_SAMPLE_HTML = """
<html>
  <div class="result">
    <a class="result__a" href="https://example.com/page1">First Result</a>
    <a class="result__snippet">A short description of first result.</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://example.com/page2">Second <b>Result</b></a>
    <a class="result__snippet">Snippet for second.</a>
  </div>
</html>
"""


class TestDDGSearch:
    def test_parses_results(self):
        client = _MockClient(post_response=_MockResponse(status_code=200, text=_DDG_SAMPLE_HTML))
        result = _ddg_search(client, "test query", max_results=5)
        assert result["backend"] == "ddg"
        assert result["query"] == "test query"
        assert len(result["results"]) == 2
        assert result["results"][0]["title"] == "First Result"
        assert result["results"][0]["url"] == "https://example.com/page1"
        assert "first result" in result["results"][0]["snippet"].lower()

    def test_cleans_html_tags_from_title(self):
        client = _MockClient(post_response=_MockResponse(status_code=200, text=_DDG_SAMPLE_HTML))
        result = _ddg_search(client, "q", max_results=5)
        # Implementation note.
        assert "<b>" not in result["results"][1]["title"]

    def test_max_results_cap(self):
        client = _MockClient(post_response=_MockResponse(status_code=200, text=_DDG_SAMPLE_HTML))
        result = _ddg_search(client, "q", max_results=1)
        assert len(result["results"]) == 1


# ═══════════════════════════════════════════════════════════
# Tavily search
# ═══════════════════════════════════════════════════════════


class TestTavilySearch:
    def test_parses_tavily_response(self):
        tavily_json = {
            "results": [
                {
                    "title": "AI News",
                    "url": "https://example.com/ai",
                    "content": "Breaking AI update: ...",
                }
            ]
        }
        client = _MockClient(post_response=_MockResponse(status_code=200, json_data=tavily_json))
        result = _tavily_search(client, "fake-key", "ai news", max_results=5)
        assert result["backend"] == "tavily"
        assert result["results"][0]["title"] == "AI News"
        assert "Breaking AI" in result["results"][0]["snippet"]


class TestWebSearchRouting:
    def test_routes_to_tavily_when_key_set(self, monkeypatch):
        monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "fake-test-key")
        client = _MockClient(
            post_response=_MockResponse(
                status_code=200,
                json_data={"results": []},
            )
        )
        result = _web_search(query="hi", client=client)
        assert result["backend"] == "tavily"

    def test_routes_to_ddg_when_no_key(self, monkeypatch):
        monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        client = _MockClient(post_response=_MockResponse(status_code=200, text=_DDG_SAMPLE_HTML))
        result = _web_search(query="hi", client=client)
        assert result["backend"] == "ddg"

    def test_empty_query_returns_error(self):
        result = _web_search(query="")
        assert "error" in result


# ═══════════════════════════════════════════════════════════
# Brave / Serper / SearXNG
# ═══════════════════════════════════════════════════════════


class TestBraveSearch:
    def test_parses_brave_response(self):
        brave_json = {
            "web": {
                "results": [
                    {
                        "title": "Brave Result",
                        "url": "https://example.com/brave",
                        "description": "Privacy-respecting result.",
                    }
                ]
            }
        }
        client = _MockClient(get_response=_MockResponse(status_code=200, json_data=brave_json))
        result = _brave_search(client, "fake-key", "q", max_results=5)
        assert result["backend"] == "brave"
        assert result["results"][0]["url"] == "https://example.com/brave"
        assert "Privacy" in result["results"][0]["snippet"]

    def test_brave_sends_auth_header(self):
        client = _MockClient(
            get_response=_MockResponse(status_code=200, json_data={"web": {"results": []}})
        )
        _brave_search(client, "secret-key", "q", max_results=3)
        assert len(client.calls) == 1
        method, url, kw = client.calls[0]
        assert method == "GET"
        assert "brave.com" in url
        assert kw["headers"]["X-Subscription-Token"] == "secret-key"


class TestSerperSearch:
    def test_parses_serper_response(self):
        serper_json = {
            "organic": [
                {
                    "title": "Serper Hit",
                    "link": "https://example.com/serper",
                    "snippet": "Google-backed snippet.",
                }
            ]
        }
        client = _MockClient(post_response=_MockResponse(status_code=200, json_data=serper_json))
        result = _serper_search(client, "fake-key", "q", max_results=5)
        assert result["backend"] == "serper"
        assert result["results"][0]["url"] == "https://example.com/serper"
        assert "Google-backed" in result["results"][0]["snippet"]


class TestSearxngSearch:
    def test_parses_searxng_response(self):
        sx_json = {
            "results": [
                {
                    "title": "SearXNG Hit",
                    "url": "https://example.com/sx",
                    "content": "Aggregated snippet.",
                }
            ]
        }
        client = _MockClient(get_response=_MockResponse(status_code=200, json_data=sx_json))
        result = _searxng_search(client, "https://searx.example/", "q", max_results=5)
        assert result["backend"] == "searxng"
        assert result["results"][0]["url"] == "https://example.com/sx"

    def test_searxng_strips_trailing_slash(self):
        client = _MockClient(get_response=_MockResponse(status_code=200, json_data={"results": []}))
        _searxng_search(client, "https://searx.example/", "q", max_results=1)
        method, url, _ = client.calls[0]
        assert url == "https://searx.example/search"


class TestResolveBackend:
    def test_explicit_env_wins(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_BACKEND", "brave")
        monkeypatch.setenv("TAVILY_API_KEY", "tk")
        assert _resolve_backend() == "brave"

    def test_priority_order(self, monkeypatch):
        for k in (
            "WEB_SEARCH_BACKEND",
            "TAVILY_API_KEY",
            "BRAVE_API_KEY",
            "SERPER_API_KEY",
            "SEARXNG_URL",
        ):
            monkeypatch.delenv(k, raising=False)
        assert _resolve_backend() == "ddg"
        monkeypatch.setenv("SEARXNG_URL", "https://x")
        assert _resolve_backend() == "searxng"
        monkeypatch.setenv("SERPER_API_KEY", "k")
        assert _resolve_backend() == "serper"
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        assert _resolve_backend() == "brave"
        monkeypatch.setenv("TAVILY_API_KEY", "k")
        assert _resolve_backend() == "tavily"

    def test_explicit_backend_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tk")
        monkeypatch.setenv("BRAVE_API_KEY", "bk")
        client = _MockClient(
            get_response=_MockResponse(status_code=200, json_data={"web": {"results": []}})
        )
        result = _web_search(query="hi", client=client, backend="brave")
        assert result["backend"] == "brave"

    def test_missing_key_for_chosen_backend(self, monkeypatch):
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        result = _web_search(query="hi", client=_MockClient(), backend="brave")
        assert result["error"] == "brave_missing_key"

    def test_unknown_backend_returns_error(self):
        result = _web_search(query="hi", client=_MockClient(), backend="bogus")
        assert "unknown_backend" in result["error"]


# ═══════════════════════════════════════════════════════════
# Registry integration
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestRegistryIntegration:
    def test_register_returns_count(self):
        r = SkillRegistry()
        n = register_web_skills(r)
        assert n == len(WEB_SKILL_NAMES) + len(REACH_SKILL_NAMES)
        for name in WEB_SKILL_NAMES + REACH_SKILL_NAMES:
            assert r.has(name)

    def test_golden_tests_pass(self):
        r = SkillRegistry()
        register_web_skills(r)
        for name in WEB_SKILL_NAMES:
            report = r.last_test_report(name)
            assert report is not None
            assert report.overall_passed, f"{name} failed golden: {report.failed}"

    def test_register_all_includes_web(self):
        from runtime.execution.suckers.builtins import BUILTIN_NAMES, register_all

        r = SkillRegistry()
        total = register_all(r)
        assert total >= len(BUILTIN_NAMES) + len(WEB_SKILL_NAMES) + len(REACH_SKILL_NAMES)
        assert r.has("fetch_url")
        assert r.has("web_search")
        assert r.has("web_fetch")


@pytest.mark.skipif(HTTPX_AVAILABLE, reason="only runs when httpx is missing")
class TestNoHttpxGraceful:
    def test_register_returns_zero_without_httpx(self):
        r = SkillRegistry()
        assert register_web_skills(r) == 0
