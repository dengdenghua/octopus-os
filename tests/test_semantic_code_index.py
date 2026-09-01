"""Read-only semantic search over the persisted KB index (numpy-free)."""

from __future__ import annotations

import array
import sqlite3
from pathlib import Path

from runtime.memory.hemolymph import semantic_code_index as sci


def test_returns_none_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_CODEBASE_SEMANTIC", "0")
    assert sci.search_persisted("anything", db_path=tmp_path / "x.db") is None


def test_returns_none_without_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ECHO_CODEBASE_SEMANTIC", raising=False)
    assert sci.search_persisted("q", db_path=tmp_path / "missing.db") is None


def test_returns_none_for_empty_query(tmp_path: Path) -> None:
    assert sci.search_persisted("   ", db_path=tmp_path / "x.db") is None


def _write_db(path: Path, rows: list[tuple[str, str, list[float]]]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE code_chunks (path TEXT, chunk TEXT, embedding BLOB)")
    for p, c, vec in rows:
        conn.execute(
            "INSERT INTO code_chunks VALUES (?,?,?)",
            (p, c, array.array("f", vec).tobytes()),
        )
    conn.commit()
    conn.close()


def test_ranks_by_cosine_numpy_free(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ECHO_CODEBASE_SEMANTIC", raising=False)
    db = tmp_path / "code_index.db"
    _write_db(
        db,
        [
            ("auth.py", "# auth\ndef login(): ...", [1.0, 0.0, 0.0]),
            ("math.py", "# math\ndef add(): ...", [0.0, 1.0, 0.0]),
        ],
    )
    # the query embeds onto the auth axis → auth.py wins (no shared token needed)
    monkeypatch.setattr(sci, "embed_texts", lambda xs: [[1.0, 0.0, 0.0] for _ in xs])
    res = sci.search_persisted("how does sign-in work", top_k=2, db_path=db)
    assert res is not None
    assert res[0]["path"] == "auth.py"
    assert res[0]["score"] >= res[1]["score"]


def test_returns_none_without_embedding_backend(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ECHO_CODEBASE_SEMANTIC", raising=False)
    db = tmp_path / "code_index.db"
    _write_db(db, [("a.py", "x", [1.0, 0.0])])
    monkeypatch.setattr(sci, "embed_texts", lambda _xs: None)  # no backend available
    assert sci.search_persisted("q", db_path=db) is None

