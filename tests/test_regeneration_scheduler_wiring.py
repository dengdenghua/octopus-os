from __future__ import annotations

from pathlib import Path

import pytest

from runtime.platform.ui import _app_stack
from runtime.safety.recovery.scheduler import RegenerationScheduler


def test_regeneration_scheduler_config_pins_output_to_app_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "runtime-home" / "data"
    launch_cwd = tmp_path / "unrelated-launch-dir"
    later_cwd = tmp_path / "later-cwd"
    launch_cwd.mkdir()
    later_cwd.mkdir()
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        _app_stack.feature_flags,
        "value",
        lambda _name, default: default,
    )
    monkeypatch.setattr(_app_stack.feature_flags, "is_on", lambda _name: True)

    config = _app_stack._regeneration_scheduler_config()
    assert Path(config.output_dir) == data_dir
    assert Path(config.output_dir).is_absolute()
    assert not (launch_cwd / "data").exists()

    # The scheduler captures the canonical absolute path at wiring time, so a
    # later cwd change cannot redirect learned rules/memories or forge output.
    monkeypatch.chdir(later_cwd)
    assert Path(config.output_dir) == data_dir
    assert not (later_cwd / "data").exists()


def test_regeneration_status_does_not_overstate_tenant_auto_learning() -> None:
    learning_scope = RegenerationScheduler().status()["learning_scope"]

    assert learning_scope == {
        "mode": "legacy_unscoped_only",
        "tenant_auto_learning": False,
        "tenant_request_learning": "governed_candidate_only",
    }

