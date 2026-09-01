"""Generic semantic ranker — embed path + lexical fallback (never worse than keyword)."""

from __future__ import annotations

import runtime.memory.hemolymph.embedding_backend as eb
from runtime.memory.hemolymph.semantic_rank import rank


def test_lexical_fallback_when_no_embedder(monkeypatch) -> None:
    # Force the embedder off → lexical path.
    monkeypatch.setattr(eb, "embed_texts", lambda _texts: None)
    out = rank(
        "打开微信发消息",
        ["微信发送消息", "打开相机拍照", "调节屏幕亮度"],
    )
    assert out["backend"] == "lexical"
    # The WeChat-message skill (most token overlap) ranks first.
    assert out["ranked"][0]["index"] == 0
    assert out["ranked"][0]["score"] > 0


def test_empty_inputs() -> None:
    assert rank("", ["a", "b"])["ranked"] == []
    assert rank("x", [])["ranked"] == []


def test_top_k_truncates(monkeypatch) -> None:
    monkeypatch.setattr(eb, "embed_texts", lambda _texts: None)
    out = rank("发消息", ["发消息给朋友", "拍照", "导航", "发短信"], top_k=2)
    assert len(out["ranked"]) == 2


def test_index_maps_back_to_input(monkeypatch) -> None:
    monkeypatch.setattr(eb, "embed_texts", lambda _texts: None)
    cands = ["完全无关的天气", "打开设置页面", "设置里调亮度"]
    out = rank("去设置调亮度", cands)
    # Every returned index is valid + best is a 设置 candidate.
    assert all(0 <= r["index"] < len(cands) for r in out["ranked"])
    assert out["ranked"][0]["index"] in (1, 2)


def test_embed_path_used_when_available(monkeypatch) -> None:
    # Fake embedder: 2-d vectors; query close to candidate 1.
    def _fake(texts: list[str]) -> list[list[float]]:
        table = {
            "q": [1.0, 0.0],
            "near": [0.96, 0.28],
            "far": [0.0, 1.0],
        }
        return [table[t] for t in texts]

    monkeypatch.setattr(eb, "embed_texts", _fake)
    out = rank("q", ["far", "near"])
    assert out["backend"] == "embed"
    assert out["ranked"][0]["text"] == "near"  # higher cosine


def test_router_exposes_routes() -> None:
    from runtime.sensing.gateway.retrieve_router import create_retrieve_router

    router = create_retrieve_router()
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/retrieve/rank" in paths
    assert "/api/retrieve/backend" in paths

