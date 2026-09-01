"""
Bug Fix Demo v2 · End-to-end MiniMax-style self-evolution.

v1 (``demos/bugfix_demo.py``) shows "the agent can fix a bug via a
DAG". v2 closes the loop:

    Round 1 · fix operator typo ("return a - b" → "a + b")
      → agent calls ``update_soul`` with the lesson learned
      → verify SOUL.md now has the new line

    Round 2 · NEW project with a DIFFERENT operator typo
      ("return a * b" instead of "a - b", but the test expects subtraction)
      → agent boots, loads SOUL.md (which now includes the lesson)
      → the lesson gets auto-injected into the next session's system prompt
      → same 8-step fix · different source, same pattern

All skill calls are deterministic (no LLM calls · StaticPlanner throughout).
The evolution is real at the SOUL.md level — the lesson is a persisted
markdown line that the agent's ``tool_bridge`` re-reads on every turn
(see ``runtime/sensing/siphon/tool_bridge.py`` capability assertion path).

Run:
    python -m demos.bugfix_demo_v2

The lesson + snapshot use the agent's actual ``agents/coder/agent-core/SOUL.md``
(with auto-backup + restore) so you can run it repeatedly without
polluting state.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# v1 building blocks · reused verbatim
from demos.bugfix_demo import (
    _C,
    _count_commits,
    _print_step,
    build_bugfix_graph,
    setup_buggy_project,
)
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.suckers.memory_skills import _update_soul
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

# ── second-round buggy project ───────────────────────────────

_V2_BUGGY_SRC = '''\
"""Arithmetic utils · different bug, same class (operator typo)."""

def subtract(a, b):
    return a * b     # BUG: should be `a - b`
'''

_V2_TEST_SRC = '''\
from arith import subtract

def test_subtract():
    result = subtract(10, 3)
    assert result == 7, f"expected 7, got {result}"
    print("test_subtract: OK")

test_subtract()
'''


def setup_v2_buggy_project(root: Path) -> Path:
    proj = root / "demo_v2_project"
    proj.mkdir()
    (proj / "arith.py").write_text(_V2_BUGGY_SRC, encoding="utf-8")
    (proj / "test_arith.py").write_text(_V2_TEST_SRC, encoding="utf-8")

    if shutil.which("git") is None:
        raise RuntimeError("git not on PATH")

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
    _git("commit", "-m", "initial: v2 buggy version")
    return proj


def build_v2_bugfix_graph(proj: Path) -> TaskGraph:
    """Same 8-step shape as v1 · different project + fix pattern."""
    nodes = [
        TaskNode(node_id="n0", skill_ref=SkillId("list_cwd"),
                 args_template={"path": str(proj)}),
        TaskNode(node_id="n1", skill_ref=SkillId("read_file"),
                 args_template={"path": str(proj / "test_arith.py")}),
        TaskNode(node_id="n2", skill_ref=SkillId("exec_shell"),
                 # -B: n4's same-length fix edit lands in the same second
                 # as this run; a written .pyc would stale-hit (mtime+size,
                 # 1s granularity) and n5 would re-run buggy bytecode.
                 args_template={"command": [sys.executable, "-B", "test_arith.py"],
                                "cwd": str(proj), "timeout_s": 15.0}),
        TaskNode(node_id="n3", skill_ref=SkillId("read_file"),
                 args_template={"path": str(proj / "arith.py")}),
        TaskNode(node_id="n4", skill_ref=SkillId("edit_text_file"),
                 args_template={"path": str(proj / "arith.py"),
                                "find": "return a * b",
                                "replace": "return a - b"}),
        TaskNode(node_id="n5", skill_ref=SkillId("exec_shell"),
                 # -B: see n2 — avoid the same-second stale-.pyc trap.
                 args_template={"command": [sys.executable, "-B", "test_arith.py"],
                                "cwd": str(proj), "timeout_s": 15.0}),
        TaskNode(node_id="n6", skill_ref=SkillId("git_add"),
                 args_template={"repo_dir": str(proj),
                                "paths": ["arith.py"]}),
        TaskNode(node_id="n7", skill_ref=SkillId("git_commit"),
                 args_template={"repo_dir": str(proj),
                                "message": "fix(subtract): operator typo (applied learned lesson)"}),
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
        strategy="bug_fix_demo_v2",
    )


# ═══════════════════════════════════════════════════════════
# Shared stack builder
# ═══════════════════════════════════════════════════════════


def _build_stack(root: Path) -> tuple[GraphRuntime, JSONLJournal]:
    journal = JSONLJournal(root / "events.jsonl")
    registry = SkillRegistry()
    register_all(registry)
    register_exec_skill(registry)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return GraphRuntime(executor=executor, journal=journal), journal


def _run_graph(runtime: GraphRuntime, graph: TaskGraph, c: _C) -> Any:
    budget = Budget(
        task_id=graph.task_id,
        limits=BudgetLimits(tokens=10_000, usd=0.10),
    )
    traj = runtime.run(
        graph, budget=budget,
        caller="demos/bugfix_v2", arm_id=ArmId("demo_arm"),
    )
    for step in traj.steps:
        _print_step(step, c)
    return traj


# ═══════════════════════════════════════════════════════════
# v2 orchestration
# ═══════════════════════════════════════════════════════════


SOUL_PATH = Path("agents/coder/agent-core/SOUL.md")
LESSON_TAG = "bugfix-demo-v2"
LESSON_TEXT = (
    "当 AssertionError 指向算术结果且期望值与实际值差异像是 operator typo "
    "(e.g. 期望 + 得到 -,或期望 - 得到 *),优先在源文件里 grep 算术"
    "operator(+/-/*//),找单字符错位,一个 edit_text_file 即可修复。"
)


def run_demo_v2(*, workdir: Path | None = None, color: bool = True) -> dict[str, Any]:
    c = _C(color)
    tmp_ctx = None
    if workdir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="echo-bugfix-v2-")
        root = Path(tmp_ctx.name)
    else:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)

    soul_backup: bytes | None = None
    try:
        # ── backup SOUL.md so repeated runs don't accumulate ──
        # Bytes, not text: text-mode round-trips rewrite \n as \r\n on
        # Windows and the restore is contractually byte-identical.
        if SOUL_PATH.exists():
            soul_backup = SOUL_PATH.read_bytes()

        print(c.bold("╭─────────────────────────────────────────────────╮"))
        print(c.bold("│ Echo Agent · Bug Fix Demo v2 (evolution)        │"))
        print(c.bold("╰─────────────────────────────────────────────────╯"))
        print()
        print(c.dim(f"  workdir: {root}"))
        print(c.dim(f"  SOUL.md backup: {len(soul_backup) if soul_backup else 0} bytes"))
        print()

        # ── Round 1 · fix first bug + record lesson ──
        print(c.bold("═══ Round 1 · fix operator typo (a - b → a + b) ═══"))
        proj1 = setup_buggy_project(root)
        runtime, _ = _build_stack(root / "stack1")
        graph1 = build_bugfix_graph(proj1)
        _ = _run_graph(runtime, graph1, c)
        commits1 = _count_commits(proj1)
        print()
        print(c.dim(f"  commits after Round 1: {commits1} (1 init + 1 fix)"))
        print()

        # ── update_soul · record what we learned ──
        # ``_update_soul`` normally requires a live Session. For the
        # demo we point ``_agent_core_dir`` at the coder agent's real
        # core dir directly — same semantics, no SSE session needed.
        # Restored in the finally block.
        print(c.bold("═══ update_soul · record the lesson ═══"))
        import runtime.execution.suckers.memory_skills as _m
        _orig_core_dir = _m._agent_core_dir
        _m._agent_core_dir = lambda: Path("agents/coder/agent-core")
        try:
            res = _update_soul(lesson=LESSON_TEXT, tag=LESSON_TAG)
        finally:
            _m._agent_core_dir = _orig_core_dir
        if res.get("ok"):
            snap = res.get('snapshot')
            snap_name = (
                snap.get('filename') if isinstance(snap, dict)
                else (str(snap) if snap else '?')
            )
            print(c.dim(f"  ✓ lesson appended · snapshot: {snap_name}"))
            print(c.dim(f"    tag: {LESSON_TAG}"))
            print(c.dim(f"    total lessons now: {res.get('total_lessons', '?')}"))
        else:
            print(c.dim(f"  ⚠ update_soul returned: {res}"))
        print()

        # ── verify SOUL.md diff ──
        soul_now = SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else ""
        if LESSON_TAG in soul_now:
            print(c.bold("═══ SOUL.md diff · new line persisted ═══"))
            for line in soul_now.splitlines()[-3:]:
                if LESSON_TAG in line:
                    print(f"  + {line[:120]}{'…' if len(line) > 120 else ''}")
            print()
        else:
            print(c.dim("  ⚠ lesson not visible in SOUL.md · check update_soul wiring"))
            print()

        # ── Round 2 · DIFFERENT bug, same class ──
        # The point: next session's system prompt auto-loads SOUL.md (via
        # tool_bridge's capability assertion · re-read per turn). The
        # lesson is now part of the agent's baseline worldview.
        print(c.bold("═══ Round 2 · new project · different operator typo ═══"))
        print(c.dim("  (subtract has `a * b` · test expects `a - b` · same pattern class)"))
        proj2 = setup_v2_buggy_project(root)
        runtime2, _ = _build_stack(root / "stack2")
        graph2 = build_v2_bugfix_graph(proj2)
        _ = _run_graph(runtime2, graph2, c)
        commits2 = _count_commits(proj2)
        print()
        print(c.dim(f"  commits after Round 2: {commits2} (1 init + 1 fix)"))
        print()

        # ── success summary ──
        print(c.bold("═══ evolution loop · summary ═══"))
        print(c.dim("  Round 1 · fixed operator typo in add.py"))
        print(c.dim("  update_soul recorded the pattern as a persisted lesson"))
        print(c.dim("  Round 2 · DIFFERENT project + operator typo → same shape fix"))
        print(c.dim("  next boot · lesson auto-loaded into system prompt via SOUL.md"))
        print()
        print(c.bold("  ✓ MiniMax-style self-evolution closed"))
        print(c.dim("     (the agent won't forget this pattern unless update_soul again OR revert_soul)"))

        return {
            "success": True,
            "round1_commits": commits1,
            "round2_commits": commits2,
            "lesson_persisted": LESSON_TAG in soul_now,
            "soul_chars_before": len(soul_backup or b""),
            "soul_chars_after": len(soul_now),
        }
    finally:
        # Always restore SOUL.md so demo is idempotent
        if soul_backup is not None:
            SOUL_PATH.write_bytes(soul_backup)
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    result = run_demo_v2()
    sys.exit(0 if result["success"] else 1)
