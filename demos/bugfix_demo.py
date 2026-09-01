"""Implementation note."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.suckers.write_skills import register_exec_skill
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import JSONLJournal
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    SkillId,
    TaskGraph,
    TaskNode,
    WorkflowEdge,
)
from runtime.safety.auth import TrustEngine

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


_BUGGY_SRC = '''\
"""Arithmetic utils. There is one bug."""

def add(a, b):
    return a - b     # BUG: should be `a + b`
'''

_TEST_SRC = '''\
from add import add

def test_add():
    result = add(2, 3)
    assert result == 5, f"expected 5, got {result}"
    print("test_add: OK")

test_add()
'''


def setup_buggy_project(root: Path) -> Path:
    """Create a minimal buggy Python project + `git init`."""
    root.mkdir(parents=True, exist_ok=True)
    proj = root / "demo_project"
    proj.mkdir()
    (proj / "add.py").write_text(_BUGGY_SRC, encoding="utf-8")
    (proj / "test_add.py").write_text(_TEST_SRC, encoding="utf-8")

    if shutil.which("git") is None:
        raise RuntimeError("git not on PATH · demo requires git")

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(proj), *args],
            check=True, capture_output=True, text=True,
        )

    _git("init", "-b", "main")
    _git("config", "user.email", "demo@echo-agent.local")
    _git("config", "user.name", "demo-bot")
    _git("config", "commit.gpgsign", "false")
    _git("add", ".")
    _git("commit", "-m", "initial: buggy version")
    return proj


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def build_bugfix_graph(proj: Path) -> TaskGraph:
    """Implementation note."""
    nodes = [
        TaskNode(
            node_id="n0",
            skill_ref=SkillId("list_cwd"),
            args_template={"path": str(proj)},
        ),
        TaskNode(
            node_id="n1",
            skill_ref=SkillId("read_file"),
            args_template={"path": str(proj / "test_add.py")},
        ),
        TaskNode(
            node_id="n2",
            skill_ref=SkillId("exec_shell"),
            # This is the red phase of the red/green repair loop.  A failing
            # test is expected evidence and must not terminate the fix graph.
            continue_on_failure=True,
            args_template={
                # -B is load-bearing: the n4 fix edit is same-length and
                # lands in the same wall-clock second as this run, so a
                # written .pyc would stale-hit (CPython invalidates on
                # mtime+size at 1-second granularity) and n5 would re-run
                # the buggy bytecode — making the demo "fail" after a
                # correct fix. Don't write bytecode here.
                "command": [sys.executable, "-B", "test_add.py"],
                "cwd": str(proj),
                "timeout_s": 15.0,
            },
        ),
        TaskNode(
            node_id="n3",
            skill_ref=SkillId("read_file"),
            args_template={"path": str(proj / "add.py")},
        ),
        TaskNode(
            node_id="n4",
            skill_ref=SkillId("edit_text_file"),
            args_template={
                "path": str(proj / "add.py"),
                "find": "return a - b",
                "replace": "return a + b",
            },
        ),
        TaskNode(
            node_id="n5",
            skill_ref=SkillId("exec_shell"),
            args_template={
                # -B: see n2 — avoid the same-second stale-.pyc trap.
                "command": [sys.executable, "-B", "test_add.py"],
                "cwd": str(proj),
                "timeout_s": 15.0,
            },
        ),
        TaskNode(
            node_id="n6",
            skill_ref=SkillId("git_add"),
            args_template={
                "repo_dir": str(proj),
                "paths": ["add.py"],
            },
        ),
        TaskNode(
            node_id="n7",
            skill_ref=SkillId("git_commit"),
            args_template={
                "repo_dir": str(proj),
                "message": "fix(add): use + instead of - (demo auto-fix)",
            },
        ),
    ]
    edges = [
        WorkflowEdge(from_node=a, to_node=b)
        for a, b in zip(
            ["n0", "n1", "n2", "n3", "n4", "n5", "n6"],
            ["n1", "n2", "n3", "n4", "n5", "n6", "n7"],
            strict=True,
        )
    ]
    return TaskGraph(
        nodes=nodes,
        edges=edges,
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="bugfix",
        strategy="bug_fix_demo",
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _print_step(step: Any, c: _C) -> None:
    """Implementation note."""
    status = step.result.status
    node = step.node_id
    skill = step.action.sucker_id

    marker = c.green("✓") if step.success else c.red("✗")
    duration_ms = step.result.cost.latency_ms

    # Implementation note.
    extra = ""
    out = step.result.output
    if isinstance(out, dict):
        if skill == "exec_shell" and "exit_code" in out:
            ec = out["exit_code"]
            tag = c.green("pass") if ec == 0 else c.red(f"fail (exit={ec})")
            extra = f"  · {tag}"
        elif skill == "edit_text_file" and "replaced" in out:
            extra = f"  · replaced {out['replaced']} occurrence(s)"
        elif skill == "git_commit" and "sha" in out:
            extra = f"  · {c.cyan(out['sha'][:10])}"
        elif skill == "git_add" and "added" in out:
            extra = f"  · {out['added']}"

    print(
        f"  {marker} {c.bold(node):<4} {skill:<20} "
        f"{status:<10} {int(duration_ms):>5}ms{extra}"
    )


class _C:
    """Implementation note."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def green(self, s): return self._w("32", s)
    def red(self, s): return self._w("31", s)
    def cyan(self, s): return self._w("36", s)
    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)


def run_demo(
    *,
    workdir: Path | None = None,
    color: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Implementation note."""
    c = _C(color)
    tmp_ctx = None
    if workdir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="echo-bugfix-")
        root = Path(tmp_ctx.name)
    else:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)

    try:
        if verbose:
            print(c.bold("╭─────────────────────────────────────────────────╮"))
            print(c.bold("│ Echo Agent · Bug Fix Demo                       │"))
            print(c.bold("╰─────────────────────────────────────────────────╯"))
            print()
            print(c.dim(f"  workdir: {root}"))

        # Implementation note.
        proj = setup_buggy_project(root)
        commits_before = _count_commits(proj)
        if verbose:
            print(c.dim(f"  project: {proj}"))
            print(c.dim(f"  commits before: {commits_before}"))
            print()

        # Implementation note.
        journal = JSONLJournal(root / "events.jsonl")
        registry = SkillRegistry()
        register_all(registry)
        register_exec_skill(registry)   # opt-in

        executor = ToolExecutor(
            registry=registry,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=journal,
        )
        runtime = GraphRuntime(executor=executor, journal=journal)

        # Implementation note.
        graph = build_bugfix_graph(proj)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )

        if verbose:
            print(c.bold(f"  plan: {len(graph.nodes)} nodes · linear DAG"))
            print()

        # Implementation note.
        traj = runtime.run(
            graph,
            budget=budget,
            caller="demos/bugfix",
            arm_id=ArmId("demo_arm"),
        )

        # Implementation note.
        if verbose:
            print(c.bold("  execution:"))
            for step in traj.steps:
                _print_step(step, c)
            print()

        # Implementation note.
        test_runs: list[int | None] = []
        for step in traj.steps:
            if step.action.sucker_id == "exec_shell":
                out = step.result.output
                if isinstance(out, dict) and "exit_code" in out:
                    test_runs.append(out["exit_code"])
                else:
                    test_runs.append(None)

        commits_after = _count_commits(proj)
        success = (
            traj.outcome.success
            and len(test_runs) >= 2
            and test_runs[0] != 0    # Implementation note.
            and test_runs[1] == 0    # Implementation note.
            and commits_after > commits_before
        )

        if verbose:
            if success:
                print(c.green(c.bold("  ✓ demo succeeded: bug found, fixed, committed")))
            else:
                print(c.red(c.bold("  ✗ demo failed · see above")))
            print(c.dim(
                f"     test runs: {test_runs}  ·  "
                f"commits: {commits_before} → {commits_after}"
            ))
            print(c.dim(f"     journal: {root / 'events.jsonl'}"))
            print()

        return {
            "success": success,
            "steps": list(traj.steps),
            "project_dir": str(proj),
            "commits_before": commits_before,
            "commits_after": commits_after,
            "test_first_run_exit_code": test_runs[0] if len(test_runs) > 0 else None,
            "test_second_run_exit_code": test_runs[1] if len(test_runs) > 1 else None,
            "trajectory": traj,
        }
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def _count_commits(proj: Path) -> int:
    r = subprocess.run(
        ["git", "-C", str(proj), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return 0
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


# ═══════════════════════════════════════════════════════════
# Script entry
# ═══════════════════════════════════════════════════════════


def main() -> int:
    result = run_demo(verbose=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
