"""Tests for the declarative tool-edge hook system.

These hooks execute real child processes via ``SandboxRunner``. Each
test writes a small Python hook script into ``tmp_path`` and points
the config at it, then asserts the runner's outcome. End-to-end rather
than mocked so the ``exit 0`` / JSON veto convention is verified on
the actual wire.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from runtime.safety.hooks.tool_edge_hooks import (
    ToolEdgeHookConfig,
    ToolEdgeHookRunner,
    ToolEdgeHookSpec,
    _parse_tool_edge_payload,
    load_tool_edge_hook_config,
)


def _write_hook_script(tmp: Path, body: str) -> Path:
    script = tmp / "hook.py"
    script.write_text(body, encoding="utf-8")
    return script


class TestConfigParse:
    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_tool_edge_hook_config(tmp_path / "nope.json").hooks == ()

    def test_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "hooks.json"
        target.write_text(
            json.dumps(
                {
                    "hooks": [
                        {
                            "event": "preToolUse",
                            "match": {"tool": "exec_shell"},
                            "command": ["python", "-c", "pass"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        cfg = load_tool_edge_hook_config(target)
        assert len(cfg.hooks) == 1
        assert cfg.hooks[0].event == "preToolUse"
        assert cfg.hooks[0].tool == "exec_shell"

    def test_unknown_event_skipped(self) -> None:
        cfg = _parse_tool_edge_payload(
            {
                "hooks": [
                    {"event": "nope", "command": ["x"]},
                    {"event": "preToolUse", "command": ["y"]},
                ]
            }
        )
        assert len(cfg.hooks) == 1
        assert cfg.hooks[0].command == ("y",)

    def test_malformed_hooks_ignored(self) -> None:
        cfg = _parse_tool_edge_payload(
            {
                "hooks": [
                    "not a dict",
                    {"event": "preToolUse"},
                    {"event": "preToolUse", "command": []},
                ]
            }
        )
        assert cfg.hooks == ()


class TestPreHookAllow:
    def test_exit_zero_allows(self, tmp_path: Path) -> None:
        script = _write_hook_script(tmp_path, "print('ok')\n")
        runner = ToolEdgeHookRunner(
            ToolEdgeHookConfig(
                hooks=(
                    ToolEdgeHookSpec(
                        event="preToolUse",
                        command=(sys.executable, str(script)),
                        tool="*",
                    ),
                )
            )
        )
        outcome = runner.run_pre(
            tool_name="exec_shell",
            args_preview="ls",
            thread_id="t",
            workspace=tmp_path,
        )
        assert outcome.allow is True

    def test_no_match_returns_allow(self, tmp_path: Path) -> None:
        runner = ToolEdgeHookRunner(
            ToolEdgeHookConfig(
                hooks=(
                    ToolEdgeHookSpec(
                        event="preToolUse",
                        command=("python", "-c", "pass"),
                        tool="git_*",
                    ),
                )
            )
        )
        outcome = runner.run_pre(
            tool_name="exec_shell",
            args_preview="",
            thread_id="t",
            workspace=tmp_path,
        )
        assert outcome.allow is True
        assert outcome.spec is None


class TestPreHookVeto:
    def test_nonzero_exit_vetoes(self, tmp_path: Path) -> None:
        script = _write_hook_script(
            tmp_path,
            "import sys; sys.stderr.write('nope'); sys.exit(3)\n",
        )
        runner = ToolEdgeHookRunner(
            ToolEdgeHookConfig(
                hooks=(
                    ToolEdgeHookSpec(
                        event="preToolUse",
                        command=(sys.executable, str(script)),
                        tool="exec_shell",
                    ),
                )
            )
        )
        outcome = runner.run_pre(
            tool_name="exec_shell",
            args_preview="ls",
            thread_id="t",
            workspace=tmp_path,
        )
        assert outcome.allow is False
        assert "nope" in outcome.reason
        assert outcome.exit_code == 3

    def test_json_declaration_vetoes(self, tmp_path: Path) -> None:
        script = _write_hook_script(
            tmp_path,
            'import json; print(json.dumps({"allow": False, "reason": "blocked"}))\n',
        )
        runner = ToolEdgeHookRunner(
            ToolEdgeHookConfig(
                hooks=(
                    ToolEdgeHookSpec(
                        event="preToolUse",
                        command=(sys.executable, str(script)),
                        tool="*",
                    ),
                )
            )
        )
        outcome = runner.run_pre(
            tool_name="exec_shell",
            args_preview="rm -rf /",
            thread_id="t",
            workspace=tmp_path,
        )
        assert outcome.allow is False
        assert outcome.reason == "blocked"

    def test_args_contains_filter(self, tmp_path: Path) -> None:
        veto = _write_hook_script(
            tmp_path,
            'print(\'{"allow": false, "reason": "rm blocked"}\')\n',
        )
        runner = ToolEdgeHookRunner(
            ToolEdgeHookConfig(
                hooks=(
                    ToolEdgeHookSpec(
                        event="preToolUse",
                        command=(sys.executable, str(veto)),
                        tool="exec_shell",
                        args_contains="rm -rf",
                    ),
                )
            )
        )
        assert (
            runner.run_pre(
                tool_name="exec_shell",
                args_preview="ls -la",
                thread_id="t",
                workspace=tmp_path,
            ).allow
            is True
        )
        assert (
            runner.run_pre(
                tool_name="exec_shell",
                args_preview="rm -rf /",
                thread_id="t",
                workspace=tmp_path,
            ).allow
            is False
        )


class TestPostHook:
    def test_post_hook_fires_but_does_not_veto(self, tmp_path: Path) -> None:
        marker = tmp_path / "fired.flag"
        script = _write_hook_script(
            tmp_path,
            f"open(r{str(marker)!r}, 'w').write('yes')\n",
        )
        runner = ToolEdgeHookRunner(
            ToolEdgeHookConfig(
                hooks=(
                    ToolEdgeHookSpec(
                        event="postToolUse",
                        command=(sys.executable, str(script)),
                        tool="*",
                    ),
                )
            )
        )
        outcomes = list(
            runner.run_post(
                tool_name="exec_shell",
                args_preview="ls",
                result_preview="file1\nfile2",
                thread_id="t",
                workspace=tmp_path,
            )
        )
        assert len(outcomes) == 1
        assert outcomes[0].allow is True
        assert marker.read_text(encoding="utf-8") == "yes"
