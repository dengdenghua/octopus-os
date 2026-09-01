"""File Agent document search — best-effort echo-storage client + skill."""

from __future__ import annotations

import urllib.error

from runtime.execution.suckers import storage_skills as ss
from runtime.execution.suckers.registry import SkillRegistry


class _Resp:
    def __init__(self, body: str) -> None:
        self._b = body.encode("utf-8")

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a) -> bool:
        return False


# ── base url + the raw client ────────────────────────────────────────


def test_base_url_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_STORAGE_URL", "http://host:9000/")
    assert ss._base_url() == "http://host:9000"
    monkeypatch.delenv("ECHO_STORAGE_URL", raising=False)
    assert ss._base_url() == "http://127.0.0.1:8767"  # same default as the frontend


def test_request_returns_none_when_storage_down(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ss.urllib.request, "urlopen", boom)
    assert ss._request("GET", "/v1/manifest") is None  # unreachable → None, no raise


def test_request_parses_json(monkeypatch) -> None:
    monkeypatch.setattr(
        ss.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Resp('{"hits": [], "mode": "privacy"}'),
    )
    assert ss._request("POST", "/v1/search", {"query": "x"}) == {"hits": [], "mode": "privacy"}


# ── the search_documents skill ───────────────────────────────────────


def test_search_missing_query_errors() -> None:
    r = ss._search_documents(query="")
    assert r["ok"] is False
    assert "required" in r["error"]


def test_search_storage_unavailable_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_request", lambda *_a, **_k: None)
    r = ss._search_documents(query="我的嵌入式笔记")
    assert r["ok"] is False
    assert r["available"] is False
    assert "echo-storage" in r["message"]  # tells the user how to enable it
    assert r["hits"] == []


def test_search_returns_cited_hits(monkeypatch) -> None:
    def fake(_method, _path, payload=None, **_k):
        return {
            "query": payload["query"],
            "mode": "efficiency",
            "hits": [
                {
                    "path": "/d/a.pdf",
                    "title": "A",
                    "snippet": "x" * 999,
                    "score": 0.9,
                    "citation": {"page": 3},
                },
                "garbage",  # non-dict hit is dropped
                {"path": "/d/b.md", "title": "B", "snippet": "y", "score": 0.5, "citation": None},
            ],
            "message": None,
        }

    monkeypatch.setattr(ss, "_request", fake)
    r = ss._search_documents(query="嵌入式笔记", top_k=5)
    assert r["ok"] is True and r["available"] is True
    assert r["count"] == 2  # the garbage entry was filtered
    assert r["hits"][0]["path"] == "/d/a.pdf"
    assert len(r["hits"][0]["snippet"]) <= ss._SNIPPET_CAP  # capped
    assert r["hits"][0]["citation"] == {"page": 3}
    assert r["hits"][1]["citation"] == {}  # None normalised to {}


def test_search_clamps_top_k(monkeypatch) -> None:
    seen: dict = {}

    def fake(_method, _path, payload=None, **_k):
        seen["payload"] = payload
        return {"hits": []}

    monkeypatch.setattr(ss, "_request", fake)
    ss._search_documents(query="q", top_k=999)
    assert seen["payload"]["top_k"] == ss._MAX_TOP_K  # clamped


def test_skill_registers() -> None:
    reg = SkillRegistry()
    assert ss.register_storage_skills(reg) == 1
    assert "search_documents" in reg.all_names()

