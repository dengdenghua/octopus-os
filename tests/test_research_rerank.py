"""Tests for BM25 + Cohere rerank backends."""

from __future__ import annotations

import pytest

from runtime.research.citations import SourceEntry
from runtime.research.rerank import (
    _bm25_scores,
    _resolve_backend,
    _tokenize,
    rerank,
)

# ═══════════════════════════════════════════════════════════
# Tokenizer
# ═══════════════════════════════════════════════════════════


class TestTokenize:
    def test_ascii_words_lowercased(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_numbers_kept_as_words(self):
        assert _tokenize("python3.11 and 42") == ["python3", "11", "and", "42"]

    def test_cjk_split_per_char(self):
        toks = _tokenize("列表排序")
        assert toks == ["列", "表", "排", "序"]

    def test_mixed_cjk_and_ascii(self):
        toks = _tokenize("Python 列表 sort")
        assert toks == ["python", "列", "表", "sort"]

    def test_punctuation_stripped(self):
        assert _tokenize("hello, world! -- foo.bar") == ["hello", "world", "foo", "bar"]

    def test_empty_input(self):
        assert _tokenize("") == []


# ═══════════════════════════════════════════════════════════
# BM25 scoring
# ═══════════════════════════════════════════════════════════


class TestBM25Scoring:
    def test_ranks_relevant_doc_first_en(self):
        srcs = [
            SourceEntry(url="a", title="Python list sort", content="Use sorted() to order a list."),
            SourceEntry(url="b", title="Gardening", content="Plant tomatoes."),
            SourceEntry(url="c", title="Python dict iteration", content="Iterate with .items()."),
        ]
        scores = _bm25_scores("python list sort", srcs)
        assert scores[0] > scores[2] > scores[1]

    def test_ranks_relevant_doc_first_cn(self):
        srcs = [
            SourceEntry(url="1", title="Python 列表排序", content="使用 sorted 按 key 排序"),
            SourceEntry(url="2", title="番茄种植", content="番茄需要阳光"),
            SourceEntry(url="3", title="Python 字典遍历", content="用 items 遍历"),
        ]
        scores = _bm25_scores("python 列表怎么排序", srcs)
        assert scores[0] == max(scores)
        assert scores[1] == min(scores)

    def test_empty_query_returns_zeros(self):
        srcs = [SourceEntry(url="a", title="x", content="y")]
        assert _bm25_scores("", srcs) == [0.0]

    def test_empty_doc_gets_zero(self):
        srcs = [
            SourceEntry(url="a", title="", snippet="", content=""),
            SourceEntry(url="b", title="python", content="python"),
        ]
        scores = _bm25_scores("python", srcs)
        assert scores[0] == 0.0
        assert scores[1] > 0.0

    def test_tf_rewards_repetition_with_diminishing_return(self):
        srcs = [
            SourceEntry(url="a", title="", content="python"),
            SourceEntry(url="b", title="", content="python python python"),
        ]
        scores = _bm25_scores("python", srcs)
        assert scores[1] > scores[0]
        # BM25 saturates: 3x term frequency is NOT 3x score.
        assert scores[1] < 3 * scores[0]


# ═══════════════════════════════════════════════════════════
# rerank() public API
# ═══════════════════════════════════════════════════════════


class TestRerankPublic:
    def test_returns_sorted_by_score(self):
        srcs = [
            SourceEntry(url="a", title="unrelated", content="gardening"),
            SourceEntry(url="b", title="python sort", content="sorted()"),
            SourceEntry(url="c", title="also python", content="python language"),
        ]
        r = rerank("python sort", srcs, backend="bm25")
        assert r.backend == "bm25"
        assert r.hits[0].source.url == "b"
        assert [h.rank for h in r.hits] == [0, 1, 2]

    def test_top_k_truncates(self):
        srcs = [SourceEntry(url=str(i), title=f"doc {i}", content="python") for i in range(10)]
        r = rerank("python", srcs, top_k=3, backend="bm25")
        assert len(r.hits) == 3

    def test_accepts_dict_input(self):
        srcs = [
            {"url": "a", "title": "python", "snippet": "list sort"},
            {"url": "b", "title": "go", "snippet": "slice"},
        ]
        r = rerank("python list", srcs, backend="bm25")
        assert r.hits[0].source.url == "a"

    def test_empty_sources(self):
        r = rerank("query", [], backend="bm25")
        assert r.hits == []
        assert r.backend == "bm25"

    def test_sources_property(self):
        srcs = [SourceEntry(url="a", title="python"), SourceEntry(url="b", title="ruby")]
        r = rerank("python", srcs, backend="bm25")
        assert [s.url for s in r.sources] == [h.source.url for h in r.hits]


# ═══════════════════════════════════════════════════════════
# Backend routing
# ═══════════════════════════════════════════════════════════


class TestBackendRouting:
    def test_default_is_bm25(self, monkeypatch):
        monkeypatch.delenv("RERANK_BACKEND", raising=False)
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        assert _resolve_backend() == "bm25"

    def test_cohere_key_auto_selects(self, monkeypatch):
        monkeypatch.delenv("RERANK_BACKEND", raising=False)
        monkeypatch.setenv("COHERE_API_KEY", "fake")
        assert _resolve_backend() == "cohere"

    def test_explicit_env_wins(self, monkeypatch):
        monkeypatch.setenv("RERANK_BACKEND", "bm25")
        monkeypatch.setenv("COHERE_API_KEY", "fake")
        assert _resolve_backend() == "bm25"

    def test_explicit_arg_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "fake")
        # No client supplied → cohere call would fail network, but we pass
        # backend="bm25" explicitly, which must bypass cohere entirely.
        srcs = [SourceEntry(url="a", title="python"), SourceEntry(url="b", title="ruby")]
        r = rerank("python", srcs, backend="bm25")
        assert r.backend == "bm25"

    def test_cohere_without_key_falls_back_to_bm25(self, monkeypatch):
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        srcs = [SourceEntry(url="a", title="python")]
        r = rerank("python", srcs, backend="cohere")
        assert r.backend == "bm25"


# ═══════════════════════════════════════════════════════════
# Cohere backend (mocked)
# ═══════════════════════════════════════════════════════════


class _MockResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data or {}
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _MockClient:
    def __init__(self, post_response=None, raise_on_post=None):
        self._post = post_response
        self._raise = raise_on_post
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self._raise:
            raise self._raise
        return self._post or _MockResponse()


class TestCohereBackend:
    def test_uses_cohere_scores_when_available(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "fake-key")
        # Cohere returns results with (index, relevance_score). We invert the
        # natural bm25 order to verify the rerank actually respects Cohere.
        client = _MockClient(
            post_response=_MockResponse(
                json_data={
                    "results": [
                        {"index": 0, "relevance_score": 0.1},
                        {"index": 1, "relevance_score": 0.9},
                    ]
                }
            )
        )
        srcs = [
            SourceEntry(url="a", title="python list sort", content="sorted() usage"),
            SourceEntry(url="b", title="gardening", content="tomato planting"),
        ]
        r = rerank("python list sort", srcs, client=client, backend="cohere")
        assert r.backend == "cohere"
        assert r.hits[0].source.url == "b"  # cohere overrode obvious choice
        assert pytest.approx(r.hits[0].score) == 0.9

    def test_cohere_network_failure_falls_back_to_bm25(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "fake-key")
        client = _MockClient(raise_on_post=RuntimeError("boom"))
        srcs = [SourceEntry(url="a", title="python list")]
        r = rerank("python", srcs, client=client, backend="cohere")
        assert r.backend == "bm25"
        assert r.hits[0].source.url == "a"

    def test_cohere_sends_auth_header_and_query(self, monkeypatch):
        monkeypatch.setenv("COHERE_API_KEY", "secret")
        client = _MockClient(post_response=_MockResponse(json_data={"results": []}))
        srcs = [SourceEntry(url="a", title="t", content="c")]
        rerank("q", srcs, client=client, backend="cohere")
        assert len(client.calls) == 1
        url, kw = client.calls[0]
        assert "cohere.com" in url
        assert kw["headers"]["Authorization"] == "Bearer secret"
        assert kw["json"]["query"] == "q"
        assert kw["json"]["documents"] == ["t c"]
