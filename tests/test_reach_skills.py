from __future__ import annotations

import stat
from typing import Any

import pytest

from runtime.execution.suckers.reach_skills import register_reach_skills
from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.reach import browser_adapter
from runtime.platform.reach import collection as collection_module
from runtime.platform.reach.cache import ReachCache
from runtime.platform.reach.channels import rss as rss_channel
from runtime.platform.reach.channels import youtube as youtube_channel
from runtime.platform.reach.channels.github import read_github
from runtime.platform.reach.doctor import diagnose_reach
from runtime.platform.reach.monitoring import platform_monitor
from runtime.platform.reach.quality import rank_and_dedupe
from runtime.platform.reach.router import normalize_platform, platform_read, platform_search


class _Response:
    def __init__(
        self,
        payload: Any,
        status_code: int = 200,
        url: str = "https://x",
        content: bytes = b"",
    ):
        self._payload = payload
        self.status_code = status_code
        self.url = url
        self.content = content

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, responses: list[_Response] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return self.responses.pop(0) if self.responses else _Response({})

    def close(self) -> None:
        return None


def test_reddit_search_uses_native_reddit_shortcut() -> None:
    captured: dict[str, Any] = {}

    def fake_search(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"backend": "searxng", "results": [{"title": "r", "url": "https://reddit.com/r/x"}]}

    result = platform_search(
        platform="reddit",
        query="agent tools",
        client=_Client(),
        web_search=fake_search,
    )

    assert result["platform"] == "reddit"
    assert captured["query"] == "!rd agent tools"


def test_reddit_search_falls_back_to_site_filter() -> None:
    calls: list[str] = []

    def fake_search(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["query"])
        if len(calls) == 1:
            return {"backend": "searxng", "results": []}
        return {"backend": "searxng", "results": [{"url": "https://reddit.com/r/x"}]}

    result = platform_search(
        platform="reddit",
        query="agent tools",
        client=_Client(),
        web_search=fake_search,
    )

    assert calls == ["!rd agent tools", "site:reddit.com agent tools"]
    assert len(result["results"]) == 1


def test_github_search_returns_normalized_repositories() -> None:
    client = _Client(
        [
            _Response(
                {
                    "items": [
                        {
                            "full_name": "octo/reach",
                            "html_url": "https://github.com/octo/reach",
                            "description": "native reach",
                            "stargazers_count": 7,
                            "language": "Python",
                        }
                    ]
                }
            )
        ]
    )

    result = platform_search(platform="github", query="reach", client=client)

    assert result["backend"] == "github_api"
    assert result["results"][0]["metadata"]["stars"] == 7


def test_login_platform_read_returns_browser_handoff() -> None:
    result = platform_read(url="https://www.reddit.com/r/test/comments/abc/post", client=_Client())

    assert result["error"] == "browser_session_required"
    assert result["requires_browser"] is True


def test_login_platform_can_use_active_browser(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        browser_adapter,
        "read_with_browser",
        lambda url, **kwargs: {"ok": True, "url": url, "text": "signed-in content"},
    )

    result = platform_read(
        url="https://www.reddit.com/r/test/comments/abc/post",
        use_browser=True,
        client=_Client(),
    )

    assert result["ok"] is True
    assert result["platform"] == "reddit"
    assert result["text"] == "signed-in content"


def test_rss_has_stdlib_fallback(monkeypatch: Any) -> None:
    monkeypatch.setattr(rss_channel, "feedparser", None)
    xml = b"<rss><channel><title>Feed</title><item><title>One</title><link>https://x/1</link></item></channel></rss>"
    result = rss_channel.read_rss(_Client([_Response({}, content=xml)]), "https://x/feed")

    assert result["backend"] == "stdlib_xml"
    assert result["results"][0]["title"] == "One"


def test_youtube_has_oembed_fallback(monkeypatch: Any) -> None:
    monkeypatch.setattr(youtube_channel, "yt_dlp", None)
    client = _Client([_Response({"title": "Video", "author_name": "Creator"})])

    result = youtube_channel.read_youtube(client, "https://www.youtube.com/watch?v=abcdefghi")

    assert result is not None
    assert result["backend"] == "youtube_oembed"
    assert result["title"] == "Video"


def test_youtube_subtitle_cleanup() -> None:
    raw = """WEBVTT

00:00:00.000 --> 00:00:01.000
<c>Hello &amp; welcome</c>

00:00:01.000 --> 00:00:02.000
Hello &amp; welcome

00:00:02.000 --> 00:00:03.000
Next line
"""

    assert youtube_channel._subtitle_to_text(raw) == "Hello & welcome\nNext line"


def test_github_issue_read_includes_comments() -> None:
    client = _Client(
        [
            _Response(
                {
                    "html_url": "https://github.com/octo/reach/issues/7",
                    "title": "Add native route",
                    "body": "Issue details",
                    "state": "open",
                    "user": {"login": "alice"},
                }
            ),
            _Response(
                [
                    {
                        "user": {"login": "bob"},
                        "body": "Looks good",
                        "created_at": "2026-01-01",
                    }
                ]
            ),
        ]
    )

    result = read_github(client, "https://github.com/octo/reach/issues/7")

    assert result is not None
    assert result["kind"] == "issue"
    assert result["comments"][0]["author"] == "bob"
    assert client.calls[0][0].endswith("/issues/7")


def test_reach_cache_is_bounded_and_marks_hits() -> None:
    cache = ReachCache(max_entries=1)
    cache.put("first", {"ok": True}, 60)
    assert cache.get("first") == {"ok": True, "cached": True, "cache_backend": "memory"}
    cache.put("second", {"ok": True}, 60)
    assert cache.get("first") is None


def test_reach_cache_survives_new_instance(tmp_path: Any) -> None:
    path = tmp_path / "reach.sqlite3"
    ReachCache(path=path).put(("search", "x"), {"ok": True, "results": []}, 60)

    result = ReachCache(path=path).get(("search", "x"))

    assert result is not None
    assert result["cached"] is True
    assert result["cache_backend"] == "sqlite"


def test_search_quality_deduplicates_tracking_urls() -> None:
    results = rank_and_dedupe(
        [
            {"title": "Agent tools", "url": "https://github.com/a/b?utm_source=x"},
            {"title": "Agent tools", "url": "https://github.com/a/b"},
        ],
        "agent tools",
    )

    assert len(results) == 1
    assert results[0]["source_host"] == "github.com"
    assert results[0]["score"] > 2


def test_chinese_platform_aliases() -> None:
    assert normalize_platform("抖音") == "douyin"
    assert normalize_platform("今日头条") == "toutiao"
    assert normalize_platform("豆包") == "doubao"


def test_platform_collect_writes_json(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        collection_module,
        "platform_search",
        lambda **kwargs: {"ok": True, "results": [{"title": kwargs["query"]}]},
    )
    monkeypatch.setattr(
        collection_module,
        "platform_read",
        lambda **kwargs: {"ok": True, "url": kwargs["url"]},
    )
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    output = tmp_path / "data" / "reach" / "collections" / "collection.json"

    result = collection_module.platform_collect(
        platform="reddit",
        queries=["agents"],
        urls=["https://reddit.com/r/agents"],
        output_path="collection.json",
    )

    assert result["ok"] is True
    assert result["search_count"] == 1
    assert result["output_path"] == str(output)
    assert output.exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_platform_collect_confines_output_path(tmp_path: Any, monkeypatch: Any) -> None:
    """``output_path`` is model-supplied, so it must not escape the collections root."""
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    monkeypatch.setattr(collection_module, "platform_search", lambda **kwargs: {"ok": True})
    escape = tmp_path / "pwned.json"

    for candidate in (str(escape), "../../../../pwned.json", "a/../../../pwned.json"):
        with pytest.raises(ValueError, match="escapes the collections root"):
            collection_module.platform_collect(queries=["agents"], output_path=candidate)

    assert not escape.exists()


def test_platform_monitor_creates_cron_task(monkeypatch: Any) -> None:
    from runtime.execution.suckers import cron_skills

    captured: dict[str, Any] = {}

    def fake_schedule(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "task_id": kwargs["name"]}

    monkeypatch.setattr(cron_skills, "_schedule_task", fake_schedule)

    result = platform_monitor(
        platform="reddit",
        queries=["agent tools"],
        cron_expression="0 */6 * * *",
    )

    assert result["ok"] is True
    assert result["monitor"]["queries"] == ["agent tools"]
    assert captured["cron_expression"] == "0 */6 * * *"
    assert "platform_collect" in captured["prompt"]


def test_doctor_reports_all_channels(monkeypatch: Any) -> None:
    monkeypatch.setenv("SEARXNG_URL", "https://search.example")
    client = _Client([_Response({}, 200), _Response({}, 200)])

    result = diagnose_reach(client=client)

    platforms = {row["platform"] for row in result["channels"]}
    assert {
        "web",
        "github",
        "youtube",
        "bilibili",
        "rss",
        "reddit",
        "x",
        "xiaohongshu",
    } <= platforms


def test_reach_skills_register() -> None:
    registry = SkillRegistry()

    assert register_reach_skills(registry) == 5
    assert registry.has("platform_search")
    assert registry.has("platform_read")
    assert registry.has("platform_collect")
    assert registry.has("platform_monitor")
    assert registry.has("reach_doctor")

