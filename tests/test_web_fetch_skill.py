"""Tests for the web_fetch skill — extracts answers from URLs via cheap LLM."""

from __future__ import annotations

from typing import Any

import pytest
from runtime.execution.suckers.web_skills import (
    HTTPX_AVAILABLE,
    _web_fetch,
)

# ───────────────────────────── doubles ─────────────────────────────


class _MockResponse:
    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        headers: dict | None = None,
        url: str = "https://mock.example/",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {"content-type": "text/html"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _MockClient:
    def __init__(
        self,
        get_response: _MockResponse | None = None,
        raise_on_get: Exception | None = None,
    ) -> None:
        self._get = get_response
        self._raise = raise_on_get
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url: str, **kw: Any) -> _MockResponse:
        self.calls.append(("GET", url, kw))
        if self._raise:
            raise self._raise
        return self._get or _MockResponse(text="<html>mock</html>", url=url)

    def close(self) -> None:
        pass


class _StubLLMCaller:
    """Mimics runtime.platform.llm_infra.llm_caller.LLMCaller surface used by _web_fetch."""

    def __init__(
        self,
        answer: str = "the rate limit is 1000 req/min",
        meta_error: str | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._answer = answer
        self._meta_error = meta_error
        self._raise = raise_exc
        self.last_user: str | None = None
        self.last_system: str | None = None

    def call(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> tuple[str, dict[str, Any]]:
        self.last_system = system
        self.last_user = user
        if self._raise:
            raise self._raise
        meta: dict[str, Any] = {"model": model or "stub-cheap"}
        if self._meta_error:
            meta["error"] = self._meta_error
            return "", meta
        return self._answer, meta


_SAMPLE_HTML = """<html>
<head><title>API Docs</title></head>
<body>
  <nav>Home | Docs</nav>
  <article>
    <h1>Rate Limits</h1>
    <p>Our API enforces a rate limit of 1000 requests per minute per token,
    which is much more than the default per-IP fallback limit of 60 rpm.</p>
    <p>Burst traffic is allowed up to 1500 rpm for short windows.</p>
  </article>
  <footer>(c) ExampleCo</footer>
</body>
</html>"""


# ───────────────────────────── tests ─────────────────────────────


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestWebFetchValidation:
    def test_empty_url_invalid_argument(self) -> None:
        result = _web_fetch(url="", prompt="what?")
        assert result.get("error_type") == "invalid_argument"
        assert "error" in result

    def test_empty_prompt_invalid_argument(self) -> None:
        result = _web_fetch(url="https://example.com/", prompt="")
        assert result.get("error_type") == "invalid_argument"
        assert "error" in result


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestWebFetchHappyPath:
    def test_returns_just_the_answer(self) -> None:
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text=_SAMPLE_HTML,
                url="https://example.com/limits",
            )
        )
        stub = _StubLLMCaller(answer="the rate limit is 1000 req/min")
        result = _web_fetch(
            url="https://example.com/limits",
            prompt="What is the rate limit?",
            client=client,
            _llm_caller=stub,
        )
        assert result.get("ok") is True
        assert result["answer"] == "the rate limit is 1000 req/min"
        assert result["url"] == "https://example.com/limits"
        assert result["prompt"] == "What is the rate limit?"
        assert result["extracted_chars"] > 0
        # Cheap LLM saw extracted text, not raw HTML.
        assert "1000 requests per minute" in (stub.last_user or "")
        # System prompt anchors "ONLY the answer" semantics.
        assert "ONLY the answer" in (stub.last_system or "")

    def test_question_alias_is_used_as_prompt(self) -> None:
        client = _MockClient(
            get_response=_MockResponse(
                status_code=200,
                text=_SAMPLE_HTML,
                url="https://example.com/limits",
            )
        )
        stub = _StubLLMCaller(answer="the rate limit is 1000 req/min")
        result = _web_fetch(
            url="https://example.com/limits",
            question="What is the rate limit?",
            client=client,
            _llm_caller=stub,
        )

        assert result.get("ok") is True
        assert result["prompt"] == "What is the rate limit?"
        assert "Question: What is the rate limit?" in (stub.last_user or "")

    def test_truncates_to_max_chars(self) -> None:
        big_text = "<html><body><p>" + ("x " * 50000) + "</p></body></html>"
        client = _MockClient(get_response=_MockResponse(status_code=200, text=big_text))
        stub = _StubLLMCaller(answer="ok")
        result = _web_fetch(
            url="https://example.com/",
            prompt="anything",
            client=client,
            _llm_caller=stub,
            max_chars=500,
        )
        assert result.get("ok") is True
        assert result["extracted_chars"] <= 500


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestWebFetchTrafilaturaMissing:
    def test_falls_back_to_regex_extraction(self) -> None:
        # _trafilatura_override=None forces fallback path even if the lib is present.
        client = _MockClient(get_response=_MockResponse(status_code=200, text=_SAMPLE_HTML))
        stub = _StubLLMCaller(answer="1000 rpm")
        result = _web_fetch(
            url="https://example.com/limits",
            prompt="rate limit?",
            client=client,
            _llm_caller=stub,
            _trafilatura_override=None,
        )
        assert result.get("ok") is True
        assert result["answer"] == "1000 rpm"
        # Fallback strips tags but keeps prose.
        assert "1000 requests per minute" in (stub.last_user or "")
        assert "<p>" not in (stub.last_user or "")


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestWebFetchLLMFailure:
    def test_llm_raise_returns_llm_failed(self) -> None:
        client = _MockClient(get_response=_MockResponse(status_code=200, text=_SAMPLE_HTML))
        stub = _StubLLMCaller(raise_exc=RuntimeError("router down"))
        result = _web_fetch(
            url="https://example.com/",
            prompt="anything",
            client=client,
            _llm_caller=stub,
        )
        assert result.get("error_type") == "llm_failed"
        assert "router down" in result["error"]
        # User still gets the raw extract so their effort wasn't wasted.
        assert "fallback_extract" in result
        assert len(result["fallback_extract"]) > 0

    def test_llm_meta_error_returns_llm_failed(self) -> None:
        client = _MockClient(get_response=_MockResponse(status_code=200, text=_SAMPLE_HTML))
        stub = _StubLLMCaller(meta_error="no model resolved")
        result = _web_fetch(
            url="https://example.com/",
            prompt="anything",
            client=client,
            _llm_caller=stub,
        )
        assert result.get("error_type") == "llm_failed"
        assert "fallback_extract" in result


@pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")
class TestWebFetchNetworkError:
    def test_http_error_returns_network_error(self) -> None:
        client = _MockClient(raise_on_get=RuntimeError("connection refused"))
        stub = _StubLLMCaller()
        result = _web_fetch(
            url="https://example.com/",
            prompt="anything",
            client=client,
            _llm_caller=stub,
        )
        assert result.get("error_type") == "network_error"
        assert "connection refused" in result["error"]
