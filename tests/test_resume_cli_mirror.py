"""Tests for resume_cli mirror integration (P3 cross-machine path).

Verifies the contract:

* ``--mirror-url`` (or ``ECHO_CHECKPOINT_MIRROR_URL`` env) wires
  list/show/resume to the distributed mirror instead of the local
  journal.
* Mirror miss → fall back to journal.
* Mirror hit + journal hit → mirror wins (cross-machine source of truth).
* Resume tags its log line with the source it actually used.
* Mirror failure (build returns None) silently falls back.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from runtime.core.cerebrum import resume_cli


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def _ckpt(
    task_id: str,
    *,
    iteration: int = 5,
    has_final: bool = False,
    summary: str = "",
) -> dict:
    return {
        "event_type": "react_checkpoint",
        "task_id": task_id,
        "ts": "2026-06-01T10:00:00",
        "iteration_completed": iteration,
        "max_iterations": 100,
        "current_phase": "implement",
        "has_final_answer": has_final,
        "steps_snapshot": [],
        "working_set_snapshot": [],
        "progress_summary": summary,
    }


class _FakeMirror:
    """In-memory CheckpointMirror surface."""

    def __init__(self, payloads: dict[str, dict] | None = None) -> None:
        self._payloads: dict[str, dict] = dict(payloads or {})

    def list_tasks(self):
        return sorted(self._payloads.keys())

    def get(self, task_id: str):
        return self._payloads.get(task_id)


@pytest.fixture(autouse=True)
def _reset_mirror_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_CHECKPOINT_MIRROR_URL", raising=False)


# ══════════════════════════════════════════════════════════════════
# CLI plumbing
# ══════════════════════════════════════════════════════════════════


class TestMirrorArgParsing:
    def test_mirror_url_flag_default_none(self) -> None:
        args = resume_cli._parse_args(["list"])
        assert args.mirror_url is None

    def test_mirror_url_flag_explicit(self) -> None:
        args = resume_cli._parse_args(["--mirror-url", "redis://x", "list"])
        assert args.mirror_url == "redis://x"

    def test_resolve_mirror_url_prefers_explicit_arg(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_MIRROR_URL", "redis://env")
        args = resume_cli._parse_args(["--mirror-url", "redis://flag", "list"])
        assert resume_cli._resolve_mirror_url(args) == "redis://flag"

    def test_resolve_mirror_url_falls_back_to_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_MIRROR_URL", "redis://env")
        args = resume_cli._parse_args(["list"])
        assert resume_cli._resolve_mirror_url(args) == "redis://env"

    def test_resolve_mirror_url_blank_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_MIRROR_URL", "   ")
        args = resume_cli._parse_args(["list"])
        assert resume_cli._resolve_mirror_url(args) is None


# ══════════════════════════════════════════════════════════════════
# _build_mirror — factory injection
# ══════════════════════════════════════════════════════════════════


class TestBuildMirror:
    def test_blank_url_returns_none(self) -> None:
        assert resume_cli._build_mirror(None) is None
        assert resume_cli._build_mirror("") is None
        assert resume_cli._build_mirror("   ") is None

    def test_factory_invoked(self) -> None:
        captured: dict[str, str] = {}

        def factory(url: str):
            captured["url"] = url
            return _FakeMirror({"x": _ckpt("x")})

        m = resume_cli._build_mirror("redis://test", factory=factory)
        assert captured["url"] == "redis://test"
        assert m is not None

    def test_factory_failure_returns_none(self) -> None:
        def factory(url: str):
            raise RuntimeError("connection refused")

        assert resume_cli._build_mirror("redis://x", factory=factory) is None


# ══════════════════════════════════════════════════════════════════
# _mirror_resumable / _mirror_get
# ══════════════════════════════════════════════════════════════════


class TestMirrorAccessors:
    def test_resumable_returns_pairs(self) -> None:
        m = _FakeMirror(
            {
                "task-a": _ckpt("task-a", iteration=3),
                "task-b": _ckpt("task-b", iteration=8),
            }
        )
        out = resume_cli._mirror_resumable(m)
        assert len(out) == 2
        ids = [tid for tid, _ in out]
        assert ids == ["task-a", "task-b"]

    def test_resumable_handles_none_mirror(self) -> None:
        assert resume_cli._mirror_resumable(None) == []

    def test_get_returns_payload(self) -> None:
        m = _FakeMirror({"x": _ckpt("x", iteration=99)})
        assert resume_cli._mirror_get(m, "x")["iteration_completed"] == 99

    def test_get_blank_id_none(self) -> None:
        assert resume_cli._mirror_get(_FakeMirror(), "") is None

    def test_resumable_skips_non_dict_payloads(self) -> None:
        class _MisbehavingMirror:
            def list_tasks(self):
                return ["good", "bad"]

            def get(self, k):
                return _ckpt("good") if k == "good" else "not a dict"

        out = resume_cli._mirror_resumable(_MisbehavingMirror())
        assert [tid for tid, _ in out] == ["good"]


# ══════════════════════════════════════════════════════════════════
# main() — list / show with mirror
# ══════════════════════════════════════════════════════════════════


class TestMainListWithMirror:
    def test_list_uses_mirror_when_present(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Mirror has a task; journal does NOT — so a finding here proves
        # the mirror path was consulted.
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [])

        fake = _FakeMirror({"task-from-mirror": _ckpt("task-from-mirror")})
        monkeypatch.setattr(resume_cli, "_build_mirror", lambda url, factory=None: fake)
        rc = resume_cli.main(
            [
                "--journal-path",
                str(path),
                "--mirror-url",
                "redis://x",
                "list",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "task-from-mirror" in out

    def test_list_falls_back_to_journal_when_mirror_empty(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("task-from-journal")])

        # Mirror returns empty list — fallback should kick in.
        monkeypatch.setattr(resume_cli, "_build_mirror", lambda url, factory=None: _FakeMirror({}))
        rc = resume_cli.main(
            [
                "--journal-path",
                str(path),
                "--mirror-url",
                "redis://x",
                "list",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "task-from-journal" in out


class TestMainShowWithMirror:
    def test_show_prefers_mirror_payload(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "j.jsonl"
        # Same task in both, but iteration differs so we can tell sources.
        _write_jsonl(path, [_ckpt("dual", iteration=5)])
        mirror = _FakeMirror({"dual": _ckpt("dual", iteration=99)})
        monkeypatch.setattr(resume_cli, "_build_mirror", lambda url, factory=None: mirror)
        rc = resume_cli.main(
            [
                "--journal-path",
                str(path),
                "--mirror-url",
                "redis://x",
                "show",
                "dual",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        # Mirror's iteration 99 wins.
        assert "99" in out

    def test_show_falls_back_to_journal_on_miss(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("only-journal", iteration=42)])
        # Mirror has nothing for this task.
        monkeypatch.setattr(resume_cli, "_build_mirror", lambda url, factory=None: _FakeMirror({}))
        rc = resume_cli.main(
            [
                "--journal-path",
                str(path),
                "--mirror-url",
                "redis://x",
                "show",
                "only-journal",
            ]
        )
        assert rc == 0
        assert "42" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════
# _resume_task — mirror path
# ══════════════════════════════════════════════════════════════════


class TestResumeMirror:
    def test_resume_uses_mirror_when_url_provided(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Journal empty; mirror has the task — proves mirror path.
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [])

        captured: dict[str, object] = {}

        def fake_runner(stack, intent, agent, **kw):
            captured["called"] = True
            return SimpleNamespace(final_answer="done")

        rc = resume_cli._resume_task(
            "task-x",
            journal_path=path,
            mirror_url="redis://test",
            mirror_factory=lambda url: _FakeMirror(
                {
                    "task-x": _ckpt(
                        "task-x",
                        iteration=10,
                        summary="From mirror.",
                    ),
                }
            ),
            runner=fake_runner,
            stack_builder=lambda **kw: (
                SimpleNamespace(name="planner"),
                SimpleNamespace(name="executor"),
                kw["journal"],
            ),
            journal_loader=lambda p: SimpleNamespace(path=p),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "source=mirror" in out
        assert captured.get("called") is True

    def test_resume_fallback_to_journal_when_mirror_empty(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("task-y", iteration=7)])
        rc = resume_cli._resume_task(
            "task-y",
            journal_path=path,
            mirror_url="redis://test",
            mirror_factory=lambda url: _FakeMirror({}),
            runner=lambda *a, **kw: SimpleNamespace(final_answer="done"),
            stack_builder=lambda **kw: (
                SimpleNamespace(),
                SimpleNamespace(),
                kw["journal"],
            ),
            journal_loader=lambda p: SimpleNamespace(path=p),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "source=journal" in out

    def test_resume_returns_3_when_mirror_says_final(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [])
        rc = resume_cli._resume_task(
            "done-task",
            journal_path=path,
            mirror_url="redis://test",
            mirror_factory=lambda url: _FakeMirror(
                {
                    "done-task": _ckpt("done-task", has_final=True),
                }
            ),
            runner=lambda *a, **kw: None,
            stack_builder=lambda **kw: None,
            journal_loader=lambda p: None,
        )
        assert rc == 3
        assert "already has a final answer" in capsys.readouterr().out

    def test_resume_no_url_uses_journal_only(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "j.jsonl"
        _write_jsonl(path, [_ckpt("task-z", iteration=4)])
        rc = resume_cli._resume_task(
            "task-z",
            journal_path=path,
            mirror_url=None,  # no mirror configured
            runner=lambda *a, **kw: SimpleNamespace(final_answer="ok"),
            stack_builder=lambda **kw: (None, None, kw["journal"]),
            journal_loader=lambda p: SimpleNamespace(path=p),
        )
        assert rc == 0
        assert "source=journal" in capsys.readouterr().out
