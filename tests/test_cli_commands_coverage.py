"""Dense coverage for runtime/_cli_commands (audit Q-05)."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from runtime import _cli_commands as cc


def _run(fn, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


# ── run_status ──────────────────────────────────────────────


def test_run_status_prints_title() -> None:
    rc, out = _run(cc.run_status, color=False)
    assert rc == 0
    assert "status" in out.lower() or "echo" in out.lower()


# ── run_bb (cross-process blackboard) ───────────────────────


def test_run_bb_requires_db_and_turn(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_BLACKBOARD_DB", raising=False)
    monkeypatch.delenv("ECHO_TURN_ID", raising=False)
    args = argparse.Namespace(turn=None, bb_op="keys", key="", value="")
    rc, out = _run(cc.run_bb, args)
    assert rc == 2 and "ECHO_BLACKBOARD_DB" in out

    monkeypatch.setenv("ECHO_BLACKBOARD_DB", "/tmp/nonexistent_bb.db")
    rc2, out2 = _run(cc.run_bb, argparse.Namespace(turn=None, bb_op="keys", key="", value=""))
    assert rc2 == 2 and "turn id required" in out2


def test_run_bb_set_get_keys_snapshot(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "bb.db"
    monkeypatch.setenv("ECHO_BLACKBOARD_DB", str(db))
    monkeypatch.setenv("ECHO_TURN_ID", "t1")

    rc, out = _run(
        cc.run_bb,
        argparse.Namespace(turn=None, bb_op="set", key="answer", value="42", agent_id=None),
    )
    assert rc == 0 and "answer set" in out

    rc2, out2 = _run(
        cc.run_bb, argparse.Namespace(turn=None, bb_op="get", key="answer", value="", agent_id=None)
    )
    assert rc2 == 0 and out2.strip() == "42"

    rc3, _ = _run(
        cc.run_bb, argparse.Namespace(turn=None, bb_op="keys", key="", value="", agent_id=None)
    )
    assert rc3 == 0

    rc4, out4 = _run(
        cc.run_bb, argparse.Namespace(turn=None, bb_op="snapshot", key="", value="", agent_id=None)
    )
    assert rc4 == 0
    data = json.loads(out4)
    assert data.get("answer") == "42"

    rc5, _ = _run(
        cc.run_bb,
        argparse.Namespace(turn=None, bb_op="get", key="missing", value="", agent_id=None),
    )
    assert rc5 == 1

    rc6, _ = _run(
        cc.run_bb, argparse.Namespace(turn=None, bb_op="bogus", key="", value="", agent_id=None)
    )
    assert rc6 == 2


# ── run_skills ──────────────────────────────────────────────


def test_run_skills_list_and_search_empty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_SKILLS_DIR", str(tmp_path / "skills"))
    rc, out = _run(
        cc.run_skills,
        argparse.Namespace(
            skills_op="list", query="", limit=10, name="", allow_dangerous=False, overwrite=False
        ),
        color=False,
    )
    assert rc == 0

    rc2, out2 = _run(
        cc.run_skills,
        argparse.Namespace(
            skills_op="search",
            query="xyz",
            limit=10,
            name="",
            allow_dangerous=False,
            overwrite=False,
        ),
        color=False,
    )
    assert rc2 == 0

    rc3, _ = _run(
        cc.run_skills,
        argparse.Namespace(
            skills_op="bogus", query="", limit=10, name="", allow_dangerous=False, overwrite=False
        ),
        color=False,
    )
    assert rc3 == 2


def test_run_skills_requires_market_op() -> None:
    rc, _ = _run(
        cc.run_skills,
        argparse.Namespace(
            skills_op=None, query="", limit=10, name="", allow_dangerous=False, overwrite=False
        ),
        color=False,
    )
    assert rc in (0, 2)


# ── run_backup / run_restore (round-trip) ───────────────────


def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    base = tmp_path / "octo"
    base.mkdir(parents=True)
    (base / "config.yaml").write_text("name: test\n", encoding="utf-8")
    artifact = tmp_path / "backup.tar.gz"
    rc, _ = _run(cc.run_backup, output=artifact, base_dir=str(base), color=False)
    assert rc == 0
    assert artifact.exists(), "no backup artifact produced"

    restored_base = tmp_path / "restored"
    rc2, _ = _run(cc.run_restore, input_path=artifact, base_dir=str(restored_base), color=False)
    assert rc2 == 0
    assert (restored_base / "config.yaml").exists(), "restore did not recreate config"


def test_run_kg_and_wiki_missing_journal(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    rc, out = _run(cc.run_kg, from_journal=missing, color=False)
    assert rc == 2
    rc2, _ = _run(cc.run_wiki, from_journal=missing, color=False)
    assert rc2 == 2


def test_run_export(tmp_path: Path) -> None:
    base = tmp_path / "octo"
    base.mkdir()
    (base / "config.yaml").write_text("name: test\n", encoding="utf-8")
    out = tmp_path / "export.json"
    rc, _ = _run(cc.run_export, output=out, base_dir=str(base), color=False)
    assert rc == 0
    assert out.exists()


def test_run_setup_delegates_to_wizard(monkeypatch, tmp_path: Path) -> None:
    recorded: dict = {}

    class _FakeWizard:
        def __init__(self, output_path, non_interactive):
            recorded["output"] = output_path
            recorded["non_interactive"] = non_interactive

        def run(self):
            recorded["ran"] = True

    import runtime.platform.lifecycle.setup_wizard as sw

    monkeypatch.setattr(sw, "SetupWizard", _FakeWizard)
    out = tmp_path / "cfg.yaml"
    rc, _ = _run(cc.run_setup, output=out, non_interactive=True, color=False)
    assert rc == 0
    assert recorded["output"] == out
    assert recorded["non_interactive"] is True
    assert recorded["ran"] is True


def test_run_ui_import_error(tmp_path: Path, monkeypatch) -> None:

    monkeypatch.setitem(sys.modules, "uvicorn", None)  # import uvicorn -> ImportError
    rc, out = _run(cc.run_ui, host="127.0.0.1", port=8000, journal_path=tmp_path / "j.jsonl")
    assert rc == 2


def test_run_ui_starts_server(tmp_path: Path, monkeypatch) -> None:

    ran: dict = {}
    fake_uvicorn = type("Uv", (), {"run": staticmethod(lambda _app, **kw: ran.update(kw))})
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr("runtime.platform.ui.create_app", lambda **kw: object())
    rc, out = _run(cc.run_ui, host="127.0.0.1", port=8000, journal_path=tmp_path / "j.jsonl")
    assert rc == 0
    assert ran.get("port") == 8000
    assert ran.get("ws") == "websockets-sansio"


def test_run_quickstart_with_force(monkeypatch, tmp_path: Path) -> None:
    setup_calls: list[Path] = []
    monkeypatch.setattr(cc, "run_setup", lambda **kw: setup_calls.append(kw["output"]) or 0)
    monkeypatch.setattr(cc, "run_doctor", lambda **kw: 0)
    monkeypatch.setattr(cc, "run_serve", lambda **kw: 0)

    out = tmp_path / "cfg.yaml"
    rc, text = _run(
        cc.run_quickstart,
        output=out,
        non_interactive=True,
        force=True,
        host="127.0.0.1",
        port=8000,
        serve=False,
        learn_interval_s=3600,
        color=False,
    )
    assert rc == 0
    assert setup_calls == [out]
    assert "ready" in text


def test_run_quickstart_doctor_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cc, "run_setup", lambda **kw: 0)
    monkeypatch.setattr(cc, "run_doctor", lambda **kw: 1)
    out = tmp_path / "cfg.yaml"
    rc, text = _run(
        cc.run_quickstart,
        output=out,
        non_interactive=True,
        force=True,
        host="127.0.0.1",
        port=8000,
        serve=False,
        learn_interval_s=3600,
        color=False,
    )
    assert rc == 1
    assert "doctor" in text.lower()


def test_run_kg_empty_journal(tmp_path: Path) -> None:
    j = tmp_path / "j.jsonl"
    j.write_text("", encoding="utf-8")
    rc, out = _run(cc.run_kg, from_journal=j, color=False)
    assert rc == 0
    assert "knowledge graph" in out.lower()
    rc2, _ = _run(cc.run_kg, from_journal=j, color=False, neighbors="x")
    assert rc2 == 0

