"""Cross-system trajectory reader for head-to-head behavioral bundles.

The two runners emit ``tool_start`` payloads in different shapes:

* ``RealtimeTrialRunner`` (echo): ``{"tool_name": "browser_navigate", ...}``
  — the flat field ``Trajectory.tool_names()`` was written for.
* ``CodexCliTrialRunner`` (codex ``exec --json``): the tool is nested as
  ``{"item": {"type": "command_execution", "command": "...", ...},
  "tool_name": "command_execution"}`` for local commands, and
  ``{"item": {"type": "mcp_tool_call", ...}}`` for plugin/MCP tools
  (browser, computer-use, …). ``item.tool_name`` is often absent.

Reading a codex trajectory with the flat accessor therefore yields a list
of empty strings, which silently misreports "agent called nothing". This
module normalizes both shapes so cross-system analysis compares like with
like, and prints a per-case breakdown for bundle post-mortems.

Usage:
    python -m benchmarks.analyze_trajectory <system-evidence.json> [<other.json> ...]
    python -m benchmarks.analyze_trajectory --case browser.dynamic-crud run.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# MCP / plugin tool types that a bare ``command_execution`` count would miss.
_MCP_KINDS = {"mcp_tool_call", "function_call", "tool_use"}
# Item types that mean "a sub-agent was spawned" across both wire dialects.
_SUBAGENT_MARKERS = ("subagent", "call_agent", "spawn", "delegate")


def normalized_tool_name(step: dict[str, Any]) -> str | None:
    """Best-effort tool name for one ``tool_start`` step, both dialects.

    Returns None for steps that aren't tool starts. Never returns "" — an
    empty string here is the exact bug this module exists to avoid.
    """
    if step.get("kind") != "tool_start":
        return None
    payload = step.get("payload")
    if not isinstance(payload, dict):
        return None

    flat = payload.get("tool_name") or payload.get("name")
    item = payload.get("item")
    item = item if isinstance(item, dict) else {}

    # A command_execution wrapper is uninformative on its own — the real
    # action is the shell command; surface it so "ran shell" is visible but
    # distinct from an MCP tool. MCP/plugin calls carry their name on the
    # item (or, failing that, the item type).
    item_type = item.get("type")
    if item_type == "commandExecution" and item.get("command"):
        return str(item["command"])
    if item_type == "command_execution" or flat == "command_execution":
        return "command_execution"
    if item_type in _MCP_KINDS:
        return str(
            item.get("tool_name") or item.get("name") or item.get("server_label") or item_type
        )
    name = flat or item.get("tool_name") or item.get("name") or item_type
    return str(name) if name else "unknown_tool"


def summarize_trajectory(traj: dict[str, Any]) -> dict[str, Any]:
    steps = traj.get("steps") or traj.get("trajectory", {}).get("steps") or []
    kinds: Counter[str] = Counter(str(s.get("kind")) for s in steps)
    tools: Counter[str] = Counter()
    for s in steps:
        name = normalized_tool_name(s)
        if name:
            tools[name] += 1
    used_mcp = any(
        (s.get("payload", {}).get("item") or {}).get("type") in _MCP_KINDS
        for s in steps
        if isinstance(s.get("payload"), dict)
    )
    subagents = sum(1 for s in steps if _mentions_subagent(s))
    return {
        "steps": len(steps),
        "kinds": dict(kinds),
        "tools": dict(tools.most_common()),
        "used_mcp": used_mcp,
        "subagent_starts": subagents,
    }


def _mentions_subagent(step: dict[str, Any]) -> bool:
    blob = json.dumps(step.get("payload") or {}, ensure_ascii=False).lower()
    return any(marker in blob for marker in _SUBAGENT_MARKERS) and step.get("kind") in {
        "tool_start",
        "phase_start",
        "subagent_start",
    }


def _load_cases(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (system_id, version, cases) from a system-evidence json."""
    d = json.loads(path.read_text(encoding="utf-8"))
    system = d.get("system") if isinstance(d.get("system"), dict) else d
    cases = system.get("cases") or d.get("cases") or []
    return (
        str(system.get("system_id") or d.get("system_id") or path.stem),
        str(system.get("version") or ""),
        cases,
    )


def _resolve_artifact(entry: dict[str, Any], base: Path) -> dict[str, Any] | None:
    for art in entry.get("artifacts", []):
        p = Path(art.get("path", ""))
        for candidate in (p, base.parent / p, Path.cwd() / p):
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def analyze(paths: Sequence[Path], case_filter: str | None) -> int:
    for path in paths:
        if not path.exists():
            print(f"! not found: {path}", file=sys.stderr)
            continue
        system_id, version, cases = _load_cases(path)
        label = f"{system_id}" + (f" ({version})" if version else "")
        passed = sum(1 for c in cases if c.get("passes") == c.get("k"))
        print(f"\n=== {label} · {passed}/{len(cases)} ===")
        for c in cases:
            cid = c.get("id")
            if case_filter and cid != case_filter:
                continue
            ok = c.get("passes") == c.get("k")
            mark = "✅" if ok else "❌"
            traj = _resolve_artifact(c, path)
            if traj is None:
                print(f"  {mark} {cid:36} (no artifact on disk)")
                continue
            s = summarize_trajectory(traj)
            verdict = traj.get("verdict") or {}
            reason = str(verdict.get("reason") or "").strip()
            print(
                f"  {mark} {cid:36} {s['steps']:2}步 · "
                f"mcp={'y' if s['used_mcp'] else 'n'} · "
                f"subagents={s['subagent_starts']}"
            )
            if s["tools"]:
                top = ", ".join(f"{k}×{v}" for k, v in list(s["tools"].items())[:6])
                print(f"       tools: {top}")
            if not ok and reason:
                print(f"       verdict: {reason[:100]}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--case", default=None, help="only this case id")
    args = parser.parse_args(argv)
    return analyze(args.paths, args.case)


if __name__ == "__main__":
    raise SystemExit(main())


