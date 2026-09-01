"""Regression tests for §37 / §38 / §40 — the safety/blast-radius guards.

* §37: ``_new_destructive_call_guard`` — new shutil.rmtree / os.remove /
  Path.unlink / shell rm-rf without paired test edit.
* §38: ``_sleep_in_production_guard`` — new time.sleep / asyncio.sleep
  in non-test runtime code.
* §40: ``_full_file_rewrite_guard`` — write_text_file overwriting an
  existing > 100-line file without a prior surgical edit on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.core.cerebrum.react_guards import (
    _full_file_rewrite_guard,
    _new_destructive_call_guard,
    _sleep_in_production_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _detect_destructive_calls_in_payload,
    _payload_has_sleep_call,
    _step_introduces_destructive_call,
    _step_introduces_sleep,
    _step_is_full_file_rewrite_attempt,
    _step_is_surgical_edit_on,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    thought: str = "",
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=thought,
        action=action,
        observation=observation,
    )


# ══════════════════════════════════════════════════════════════════
# §37 — destructive-call guard
# ══════════════════════════════════════════════════════════════════


class TestDetectDestructiveCalls:
    def test_shutil_rmtree(self) -> None:
        hits = _detect_destructive_calls_in_payload("shutil.rmtree(target)")
        assert any("rmtree" in h for h in hits)

    def test_os_remove(self) -> None:
        hits = _detect_destructive_calls_in_payload("os.remove(path)")
        assert any("os.remove" in h for h in hits)

    def test_path_unlink(self) -> None:
        hits = _detect_destructive_calls_in_payload("Path('x').unlink()")
        assert any("unlink" in h for h in hits)

    def test_shell_rm_rf(self) -> None:
        hits = _detect_destructive_calls_in_payload(
            'subprocess.run("rm -rf /tmp/foo", shell=True)',
        )
        assert any("rm -rf" in h for h in hits)

    def test_clean_code_silent(self) -> None:
        assert _detect_destructive_calls_in_payload("def hello(): return 1") == []


class TestStepIntroducesDestructiveCall:
    def test_runtime_new_rmtree(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x", '
                '"new_string": "import shutil\\nshutil.rmtree(target)"})'
            ),
        )
        labels = _step_introduces_destructive_call(step)
        assert labels
        assert any("rmtree" in label for label in labels)

    def test_pre_existing_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "shutil.rmtree(a)", '
                '"new_string": "shutil.rmtree(a)  # tweak"})'
            ),
        )
        assert _step_introduces_destructive_call(step) == []

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "shutil.rmtree(tmp)"})'
            ),
        )
        assert _step_introduces_destructive_call(step) == []

    def test_non_python_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "scripts/cleanup.sh", '
                '"old_string": "x", "new_string": "rm -rf /tmp/foo"})'
            ),
        )
        assert _step_introduces_destructive_call(step) == []


class TestNewDestructiveCallGuard:
    def test_non_code_mode_still_blocks_destructive_call(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "shutil.rmtree(target)"})'
                ),
            ),
        ]
        msg = _new_destructive_call_guard(steps, "done", is_code_mode=False)
        assert msg is not None
        assert "shutil.rmtree" in msg

    def test_no_destructive_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _new_destructive_call_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_destructive_no_test_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "shutil.rmtree(target)"})'
                ),
            ),
        ]
        msg = _new_destructive_call_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "destructive" in msg.lower()
        assert "runtime/foo.py" in msg

    def test_destructive_with_test_silent(self) -> None:
        # ANY test write in trajectory = guard quiet (mirror of §20).
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "shutil.rmtree(target)"})'
                ),
            ),
            _step(
                2,
                action='write_text_file({"path": "tests/test_cleanup.py", "content": "def test_x(): pass\\n"})',
            ),
        ]
        assert (
            _new_destructive_call_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "shutil.rmtree(t)"})'
                ),
            ),
        ]
        assert (
            _new_destructive_call_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §38 — time.sleep in production guard
# ══════════════════════════════════════════════════════════════════


class TestPayloadHasSleepCall:
    def test_time_sleep(self) -> None:
        assert _payload_has_sleep_call("time.sleep(1)")

    def test_asyncio_sleep(self) -> None:
        assert _payload_has_sleep_call("await asyncio.sleep(0.5)")

    def test_neutral_sleep_word_silent(self) -> None:
        # ``sleep`` in a comment / string isn't a call.
        assert not _payload_has_sleep_call("# we don't sleep here")

    def test_method_named_sleep_silent(self) -> None:
        # Custom sleep method on a class — not stdlib.
        assert not _payload_has_sleep_call("scheduler.sleep(0.1)")


class TestStepIntroducesSleep:
    def test_runtime_new_sleep_detected(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "import time\\nx = 1\\ntime.sleep(0.5)"})'
            ),
        )
        assert _step_introduces_sleep(step)

    def test_test_path_skipped(self) -> None:
        # Tests legitimately use sleep for timing assertions.
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_x.py", '
                '"old_string": "x", "new_string": "time.sleep(0.1)"})'
            ),
        )
        assert not _step_introduces_sleep(step)

    def test_pre_existing_sleep_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "time.sleep(0.5)", '
                '"new_string": "time.sleep(1.0)"})'
            ),
        )
        assert not _step_introduces_sleep(step)


class TestSleepInProductionGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "time.sleep(1)"})'
                ),
            ),
        ]
        assert (
            _sleep_in_production_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_sleep_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _sleep_in_production_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_new_sleep_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", "new_string": "time.sleep(0.5)\\nx = 1"})'
                ),
            ),
        ]
        msg = _sleep_in_production_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "sleep" in msg.lower()
        assert "runtime/foo.py" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "time.sleep(1)"})'
                ),
            ),
        ]
        assert (
            _sleep_in_production_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §40 — full-file rewrite guard
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def fake_repo_with_existing_file(tmp_path: Path) -> Path:
    """Create a fake repo where runtime/big.py is a 200-line file
    and runtime/small.py is a 5-line file."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "big.py").write_text("\n".join(["x = 1"] * 200), encoding="utf-8")
    (runtime / "small.py").write_text("y = 1\n", encoding="utf-8")
    return tmp_path


class TestStepIsFullFileRewriteAttempt:
    def test_overwrite_big_existing_file_fires(
        self,
        fake_repo_with_existing_file: Path,
    ) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/big.py", "content": "x = 2\\n"})',
        )
        is_rewrite, path, lines = _step_is_full_file_rewrite_attempt(
            step,
            repo_root=str(fake_repo_with_existing_file),
        )
        assert is_rewrite
        assert path == "runtime/big.py"
        assert lines >= 100

    def test_overwrite_small_existing_file_silent(
        self,
        fake_repo_with_existing_file: Path,
    ) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/small.py", "content": "y = 2\\n"})',
        )
        is_rewrite, _path, _lines = _step_is_full_file_rewrite_attempt(
            step,
            repo_root=str(fake_repo_with_existing_file),
        )
        assert not is_rewrite

    def test_create_new_file_silent(
        self,
        fake_repo_with_existing_file: Path,
    ) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/brand_new.py", "content": "z = 1\\n"})',
        )
        is_rewrite, _path, _lines = _step_is_full_file_rewrite_attempt(
            step,
            repo_root=str(fake_repo_with_existing_file),
        )
        assert not is_rewrite

    def test_edit_file_not_rewrite(
        self,
        fake_repo_with_existing_file: Path,
    ) -> None:
        step = _step(
            1,
            action='edit_file({"path": "runtime/big.py", "old_string": "x = 1", "new_string": "x = 2"})',
        )
        is_rewrite, _path, _lines = _step_is_full_file_rewrite_attempt(
            step,
            repo_root=str(fake_repo_with_existing_file),
        )
        assert not is_rewrite


class TestStepIsSurgicalEditOn:
    def test_edit_file_match(self) -> None:
        step = _step(
            1,
            action='edit_file({"path": "runtime/big.py", "old_string": "x", "new_string": "y"})',
        )
        assert _step_is_surgical_edit_on(step, target_path="runtime/big.py")

    def test_write_text_file_no_match(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/big.py", "content": "x"})',
        )
        assert not _step_is_surgical_edit_on(step, target_path="runtime/big.py")

    def test_different_path_no_match(self) -> None:
        step = _step(
            1,
            action='edit_file({"path": "runtime/other.py", "old_string": "x", "new_string": "y"})',
        )
        assert not _step_is_surgical_edit_on(step, target_path="runtime/big.py")


class TestFullFileRewriteGuard:
    def test_non_code_mode_silent(
        self,
        fake_repo_with_existing_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(fake_repo_with_existing_file)
        steps = [
            _step(1, action='write_text_file({"path": "runtime/big.py", "content": "x = 2\\n"})'),
        ]
        assert (
            _full_file_rewrite_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_full_rewrite_no_prior_edit_fires(
        self,
        fake_repo_with_existing_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(fake_repo_with_existing_file)
        steps = [
            _step(1, action='write_text_file({"path": "runtime/big.py", "content": "x = 2\\n"})'),
        ]
        msg = _full_file_rewrite_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "runtime/big.py" in msg

    def test_full_rewrite_with_prior_edit_silent(
        self,
        fake_repo_with_existing_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(fake_repo_with_existing_file)
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/big.py", "old_string": "x = 1", "new_string": "x = 2"})',
            ),
            _step(2, action='write_text_file({"path": "runtime/big.py", "content": "x = 3\\n"})'),
        ]
        assert (
            _full_file_rewrite_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_new_file_silent(
        self,
        fake_repo_with_existing_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(fake_repo_with_existing_file)
        steps = [
            _step(
                1, action='write_text_file({"path": "runtime/brand_new.py", "content": "z = 1\\n"})'
            ),
        ]
        assert (
            _full_file_rewrite_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(
        self,
        fake_repo_with_existing_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(fake_repo_with_existing_file)
        steps = [
            _step(1, action='write_text_file({"path": "runtime/big.py", "content": "x = 2\\n"})'),
        ]
        assert (
            _full_file_rewrite_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
