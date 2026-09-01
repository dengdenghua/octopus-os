"""Synthesis layer (ADR-009): /api/wiki/ask retrieves wiki context and composes
a grounded, cited answer via the model router — gbrain's "give the answer, not
raw pages". Gated: no model / no relevant context → grounded=False, no LLM call.
"""

from __future__ import annotations

from typing import Any

import runtime.memory.hemolymph.repo_context as rc
import runtime.sensing.gateway.wiki_router as wr


class _Resp:
    text = "Cerebrum turns goals into a TaskGraph. [Cerebrum · 规划器]"


class _Router:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def call(self, request: Any) -> _Resp:
        self.calls.append(request)
        return _Resp()


def test_ask_composes_cited_answer(monkeypatch) -> None:
    sources = [{"kind": "doc", "title": "Cerebrum · 规划器", "path": "c.md"}]
    monkeypatch.setattr(
        rc, "build_codebase_context", lambda q, **k: ("WIKI CONTEXT about the planner", sources)
    )
    router = _Router()
    out = wr._answer_from_wiki("how does the planner work", model_router=router, model="m")
    assert out["grounded"] is True
    assert "Cerebrum" in out["answer"]
    assert out["citations"] == sources
    # the retrieved context was actually handed to the model
    assert any("WIKI CONTEXT" in m.content for m in router.calls[0].messages)


def test_ask_no_model_is_graceful(monkeypatch) -> None:
    monkeypatch.setattr(rc, "build_codebase_context", lambda q, **k: ("ctx", [{"path": "c.md"}]))
    out = wr._answer_from_wiki("q", model_router=None, model=None)
    assert out["grounded"] is False
    assert out["reason"] == "no model configured"
    assert out["citations"] == [{"path": "c.md"}]  # still tells you what it found


def test_ask_no_context_skips_llm(monkeypatch) -> None:
    monkeypatch.setattr(rc, "build_codebase_context", lambda q, **k: ("", []))
    router = _Router()
    out = wr._answer_from_wiki("unrelated", model_router=router, model="m")
    assert out["grounded"] is False
    assert out["reason"] == "no relevant wiki context"
    assert router.calls == []  # no LLM call when the wiki doesn't cover it


def test_ask_model_error_is_swallowed(monkeypatch) -> None:
    monkeypatch.setattr(rc, "build_codebase_context", lambda q, **k: ("ctx", [{"path": "c.md"}]))

    class _Boom:
        def call(self, request: Any) -> Any:
            raise RuntimeError("upstream down")

    out = wr._answer_from_wiki("q", model_router=_Boom(), model="m")
    assert out["grounded"] is False
    assert out["reason"].startswith("model error")


def test_wiki_graph_returns_nodes_and_edges() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.sensing.gateway.wiki_router import create_wiki_router

    app = FastAPI()
    app.include_router(create_wiki_router())
    r = TestClient(app).get("/api/wiki/graph")
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d.get("nodes"), list) and isinstance(d.get("edges"), list)
    assert len(d["nodes"]) > 0  # the repo ships a generated wiki
    for e in d["edges"]:
        assert {"from", "to", "type"} <= e.keys()


def test_wiki_okf_bundle_is_a_valid_tarball() -> None:
    import io
    import tarfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.sensing.gateway.wiki_router import create_wiki_router

    app = FastAPI()
    app.include_router(create_wiki_router())
    r = TestClient(app).get("/api/wiki/okf-bundle")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/gzip"
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        names = tar.getnames()
    assert "index.json" in names  # the OKF manifest is in the bundle
    assert any(n.endswith(".md") for n in names)  # markdown pages too

