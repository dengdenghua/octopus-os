"""Browser session hardening: profile permissions + crash sentinel."""

import os
import stat
import sys
from pathlib import Path

import pytest

from runtime.platform.ui.browser_router import (
    _SESSION_SENTINEL_NAME,
    mark_session_active,
    mark_session_closed,
    secure_profile_dir,
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_profile_dir_is_owner_only(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o755)

    secure_profile_dir(profile)

    mode = stat.S_IMODE(os.stat(profile).st_mode)
    assert mode == 0o700


class TestCrashSentinel:
    def test_first_launch_is_clean(self, tmp_path: Path) -> None:
        assert mark_session_active(tmp_path) is False
        assert (tmp_path / _SESSION_SENTINEL_NAME).exists()

    def test_clean_shutdown_clears_sentinel(self, tmp_path: Path) -> None:
        mark_session_active(tmp_path)
        mark_session_closed(tmp_path)
        assert not (tmp_path / _SESSION_SENTINEL_NAME).exists()
        # Next launch sees a clean state again.
        assert mark_session_active(tmp_path) is False

    def test_stale_sentinel_reports_crash(self, tmp_path: Path) -> None:
        mark_session_active(tmp_path)
        # No mark_session_closed — process died. Relaunch detects it.
        assert mark_session_active(tmp_path) is True

    def test_close_without_profile_dir_is_noop(self) -> None:
        mark_session_closed(None)
        mark_session_closed("")

    def test_accepts_string_paths(self, tmp_path: Path) -> None:
        mark_session_active(tmp_path)
        mark_session_closed(str(tmp_path))
        assert not (tmp_path / _SESSION_SENTINEL_NAME).exists()
