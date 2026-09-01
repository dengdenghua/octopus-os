"""Tests for ExperienceLedger.recall semantic recall blending.

Covers three paths:
- offline (no embedding backend): pure lexical behaviour is preserved.
- semantic hit: a lexically-disjoint but semantically-related record surfaces.
- graceful degradation: embed_texts returning None falls back to lexical.

Note on markers: ``_token_set`` splits on non-alphanumerics, so underscore-joined
markers would share sub-tokens. The markers below are single opaque words with no
overlapping fragments, guaranteeing zero lexical overlap between query and record.
"""

from __future__ import annotations

from pathlib import Path

from runtime.memory.hemolymph import embedding_backend as eb
from runtime.memory.learning.experience_ledger import ExperienceLedger

# Opaque, mutually disjoint markers (no shared alphanumeric fragments).
REC_MARK = "zqxalpha"
QRY_MARK = "wvbbeta"

QUERY = f"{QRY_MARK} container orchestration failure"


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Both markers map to the same direction -> cosine 1.0; everything else 0.0."""
    out: list[list[float]] = []
    for text in texts:
        if REC_MARK in text or QRY_MARK in text:
            out.append([1.0, 0.0, 0.0])
        else:
            out.append([0.0, 1.0, 0.0])
    return out


def _review(title: str, text: str) -> dict:
    return {
        "task_id": "t1",
        "thread_id": "th1",
        "turn_id": "tu1",
        "agent_id": "a1",
        "learning_candidates": [
            {"title": title, "text": text, "priority": "P1", "memory_bucket": "experience"}
        ],
    }


def _ledger(tmp_path: Path) -> ExperienceLedger:
    led = ExperienceLedger(tmp_path / "experience_ledger.json")
    led.add_from_task_run_review(
        _review("K8s restart", f"{REC_MARK} kubernetes pod restart loop crash")
    )
    led.add_from_task_run_review(_review("Py env", "python virtualenv setup guide"))
    return led


def test_lexical_only_offline(monkeypatch, tmp_path: Path) -> None:
    """No backend -> semantic disabled, and lexically-disjoint query matches nothing."""
    monkeypatch.setattr(eb, "available", lambda: False)
    res = _ledger(tmp_path).recall(QUERY)
    assert res["semantic_enabled"] is False
    assert res["total"] == 0


def test_semantic_hit_surfaces_lexically_disjoint(monkeypatch, tmp_path: Path) -> None:
    """With a backend, the semantically-close record surfaces despite zero token overlap."""
    monkeypatch.setattr(eb, "available", lambda: True)
    monkeypatch.setattr(eb, "embed_texts", _fake_embed)
    res = _ledger(tmp_path).recall(QUERY)
    assert res["semantic_enabled"] is True
    assert res["total"] == 1
    rec = res["records"][0]
    assert REC_MARK in rec["text"]
    assert rec["recall"]["matched_terms"] == []
    assert rec["recall"]["semantic_score"] == 1.0
    assert rec["recall"]["score"] == 1.0
    assert 0.0 < rec["recall"]["rank_score"] < 1.0


def test_semantic_degrade_on_none(monkeypatch, tmp_path: Path) -> None:
    """embed_texts returning None must degrade to the lexical-only behaviour."""
    monkeypatch.setattr(eb, "available", lambda: True)
    monkeypatch.setattr(eb, "embed_texts", lambda texts: None)
    res = _ledger(tmp_path).recall(QUERY)
    assert res["semantic_enabled"] is False
    assert res["total"] == 0


def test_semantic_opt_out_skips_backend(monkeypatch, tmp_path: Path) -> None:
    """semantic=False must not touch the backend at all."""

    def _boom(*_a, **_kw):  # pragma: no cover - must never run
        raise AssertionError("embedding backend must not be consulted")

    monkeypatch.setattr(eb, "available", _boom)
    monkeypatch.setattr(eb, "embed_texts", _boom)
    res = _ledger(tmp_path).recall(QUERY, semantic=False)
    assert res["semantic_enabled"] is False
    assert res["total"] == 0


def test_lexical_match_still_works_with_semantic_on(monkeypatch, tmp_path: Path) -> None:
    """A plain lexical query keeps working when the semantic path is active."""
    monkeypatch.setattr(eb, "available", lambda: True)
    monkeypatch.setattr(eb, "embed_texts", _fake_embed)
    res = _ledger(tmp_path).recall("python virtualenv setup")
    assert res["total"] >= 1
    top = res["records"][0]
    assert "virtualenv" in top["text"]
    assert top["recall"]["matched_terms"]


def test_rrf_ranks_dual_signal_record_first(monkeypatch, tmp_path: Path) -> None:
    """RRF advantage over weighted blend: a record strong in BOTH lanes outranks
    records strong in only one, even when lexical overlap is equal across them.

    QUERY carries QRY_MARK (semantic direction == REC_MARK). rec_good holds the
    marker AND the literal query words; rec_lex holds only the words; rec_sem
    holds only the marker. A magnitude-blended score would let rec_lex (higher
    lexical) beat rec_sem (semantic only); RRF ranks lanes independently so the
    dual-signal record wins regardless of how the single-lane ties fall.
    """
    monkeypatch.setattr(eb, "available", lambda: True)
    monkeypatch.setattr(eb, "embed_texts", _fake_embed)
    led = ExperienceLedger(tmp_path / "ledger.json")
    led.add_from_task_run_review(_review("dual", f"{REC_MARK} container restart crash"))
    led.add_from_task_run_review(_review("lex", "container restart crash loop"))
    led.add_from_task_run_review(_review("sem", f"{REC_MARK} kubernetes pod crash"))
    res = led.recall(QUERY)
    assert res["semantic_enabled"] is True
    assert res["total"] == 3
    assert "dual" in res["records"][0]["title"]
    assert res["records"][0]["recall"]["rank_score"] >= res["records"][1]["recall"]["rank_score"]

