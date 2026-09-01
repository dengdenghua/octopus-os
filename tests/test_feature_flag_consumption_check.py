"""feature_flag_consumption_check — the registered-but-unread flag ratchet."""

import subprocess
import sys
from pathlib import Path

from tools.lint.feature_flag_consumption_check import (
    _CONSUME_RE,
    _load_baseline,
    _registered_flags,
    _scan,
)

_REPO = Path(__file__).resolve().parent.parent


class TestConsumeRegex:
    def test_matches_is_on_and_value_both_quote_styles(self):
        text = 'is_on("a.b") and value(\'c.d\') or x.is_on("e.f")'
        assert set(_CONSUME_RE.findall(text)) == {"a.b", "c.d", "e.f"}

    def test_ignores_non_flag_and_dynamic_calls(self):
        # variable arg (computed name) and unrelated calls don't match
        assert _CONSUME_RE.findall("foo('a.b'); is_on(name); value(key)") == []


class TestRegisteredFlags:
    def test_extracts_real_dotted_flags(self):
        flags = _registered_flags()
        assert "regeneration.enabled" in flags  # a real registered flag
        assert all("." in f for f in flags)  # all dotted group.flag
        assert len(flags) >= 10


class TestRatchet:
    def test_no_new_unconsumed_flag_beyond_baseline(self):
        # Every currently-dead flag is grandfathered in the baseline, so a NEW
        # registered-but-unread flag would make this set non-empty and fail.
        assert _scan() <= _load_baseline()

    def test_strict_is_green(self):
        r = subprocess.run(
            [
                sys.executable,
                "tools/lint/feature_flag_consumption_check.py",
                "--strict",
            ],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, (
            "feature-flag-consumption baseline drifted:\n" + r.stdout + r.stderr
        )

