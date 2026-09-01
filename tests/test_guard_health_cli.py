"""Tests for guard_health CLI integration."""

from __future__ import annotations

import pytest


def test_guard_health_command_registered():
    """Verify guard-health subcommand is registered and shows help."""
    # Import from runtime.cli module (not runtime.cli package)
    import runtime.cli as cli_module

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["guard-health", "--help"])
    # --help triggers SystemExit(0)
    assert exc_info.value.code == 0


def test_guard_health_no_telemetry_exits_gracefully(tmp_path):
    """When telemetry file doesn't exist, guard-health should handle it."""
    import runtime.cli as cli_module

    nonexistent = tmp_path / "nonexistent.jsonl"

    # Passing --telemetry to a nonexistent file should either error or return empty
    result = cli_module.main(["guard-health", "--telemetry", str(nonexistent)])
    # Should exit 0 (empty telemetry) or 1 (error), but not crash
    assert result in (0, 1)


def test_guard_health_empty_telemetry_exits_zero(tmp_path, capsys):
    """When telemetry file exists but is empty, should exit 0 with message."""
    import runtime.cli as cli_module

    telemetry_path = tmp_path / "empty_guard_hits.jsonl"
    telemetry_path.write_text("")  # Empty file

    # Guard health should handle empty telemetry gracefully
    result = cli_module.main(["guard-health", "--telemetry", str(telemetry_path)])
    assert result == 0
    captured = capsys.readouterr()
    assert "No guard hits recorded" in captured.out

