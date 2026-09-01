from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root


@dataclass(frozen=True)
class RepoContextCheck:
    id: str
    title: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    weight: int = 1


CHECKS: tuple[RepoContextCheck, ...] = (
    RepoContextCheck(
        id="hybrid_repo_retrieval",
        title="Hybrid repository retrieval",
        paths=(
            "runtime/memory/hemolymph/repo_context.py",
            "runtime/memory/hemolymph/code_index.py",
            "tests/test_repo_context.py",
            "tests/test_code_index.py",
            "docs/adr/009-okf-knowledge-substrate.md",
        ),
        required_terms=(
            "build_codebase_context",
            "collect_codebase_sources",
            "retrieve_repo_context",
            "retrieve_code_context",
            "reciprocal-rank fusion",
            "semantic lane",
            "graph",
            "CJK bigrams",
        ),
    ),
    RepoContextCheck(
        id="source_citation_trace",
        title="Source citation trace",
        paths=(
            "runtime/memory/hemolymph/repo_context.py",
            "tests/test_repo_context.py",
            "runtime/safety/evolution/agent_competitor_scorecard.py",
            "runtime/safety/evolution/_agent_competitor_scorecard_drilldown.py",
        ),
        required_terms=(
            "collect_codebase_sources",
            "_sink",
            "source",
            "path",
            "operator_drilldown",
        ),
    ),
    RepoContextCheck(
        id="agent_instruction_surface",
        title="Agent instruction surface",
        paths=(
            "runtime/sensing/gateway/agents_router.py",
            "agents/_shared/AGENTS.md",
            "agents/coder/agent-core/AGENTS.md",
            "docs/adr/009-okf-knowledge-substrate.md",
        ),
        required_terms=(
            "AGENTS.md",
            "agent-core",
            "repo_context",
            "OKF",
        ),
    ),
    RepoContextCheck(
        id="dirty_worktree_awareness",
        title="Dirty worktree awareness",
        paths=(
            "runtime/safety/evolution/agent_competitor_scorecard.py",
            "runtime/safety/evolution/repo_context_quality.py",
            "tests/test_evolution_modules.py",
        ),
        required_terms=(
            "dirty_worktree",
            "git status --short",
            "uncommitted",
            "conflicted_count",
            "staged_count",
            "preservation_required",
            "operator_drilldown",
        ),
    ),
    RepoContextCheck(
        id="concurrent_workspace_drift_protection",
        title="Concurrent workspace drift protection",
        paths=(
            "runtime/execution/misc/file_write_leases.py",
            "runtime/execution/tool_engine/executor.py",
            "tests/test_runtime_hardening.py",
            "tests/test_file_op_events.py",
        ),
        required_terms=(
            "WorkspaceContentDriftConflict",
            "record_file_read_snapshot",
            "verify_file_unchanged_since_read",
            "record_file_write_snapshot",
            "workspace_content_drift",
            "external_drift_count",
            "re-read before writing",
        ),
    ),
    RepoContextCheck(
        id="memory_context_carryover",
        title="Memory context carryover",
        paths=(
            "runtime/memory/learning/experience_ledger.py",
            "runtime/memory/learning/promotion_applier.py",
            "runtime/sensing/gateway/agent_trace_router.py",
            "tests/test_agent_trace_router.py",
        ),
        required_terms=(
            "experience_ledger",
            "replay_gate",
            "promotion",
            "source_task_ids",
        ),
    ),
)


def compute_repo_context_quality(
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    total_weight = sum(int(row["weight"]) for row in checks)
    passed_weight = sum(int(row["weight"]) for row in checks if row["passed"])
    return {
        "schema": "echo.repo_context_quality.v1",
        "score": round(passed_weight / max(1, total_weight), 3),
        "passed": sum(1 for row in checks if row["passed"]),
        "total": len(checks),
        "ready": all(row["passed"] for row in checks),
        "checks": checks,
        "dirty_worktree": _dirty_worktree(base),
        "next_actions": [str(row["next_action"]) for row in checks if not row["passed"]],
    }


def _check_row(base: Path, check: RepoContextCheck) -> dict[str, Any]:
    paths = [{"path": path, "exists": (base / path).exists()} for path in check.paths]
    text = "\n".join(_read_text(base / row["path"]) for row in paths if row["exists"]).lower()
    missing_paths = [str(row["path"]) for row in paths if not row["exists"]]
    missing_terms = [term for term in check.required_terms if term.lower() not in text]
    return {
        "id": check.id,
        "title": check.title,
        "weight": check.weight,
        "passed": not missing_paths and not missing_terms,
        "paths": paths,
        "missing_paths": missing_paths,
        "required_terms": list(check.required_terms),
        "missing_terms": missing_terms,
        "next_action": f"Complete repo-context quality check: {check.title}.",
    }


def _dirty_worktree(base: Path) -> dict[str, Any]:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(base),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "echo.dirty_worktree_awareness.v1",
            "available": False,
            "uncommitted_count": 0,
            "status_sample": [],
            **classify_dirty_worktree([]),
            "preservation_required": False,
            "safe_to_auto_edit": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    classification = classify_dirty_worktree(lines)
    return {
        "schema": "echo.dirty_worktree_awareness.v1",
        "available": result.returncode == 0,
        "uncommitted_count": len(lines),
        "status_sample": lines[:25],
        **classification,
        "preservation_required": bool(lines),
        "safe_to_auto_edit": result.returncode == 0 and classification["conflicted_count"] == 0,
        "command": "git status --short",
        "error": result.stderr.strip(),
    }


def classify_dirty_worktree(lines: list[str]) -> dict[str, Any]:
    """Classify porcelain short-status rows without losing overlapping risks."""

    conflict_codes = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
    files: list[dict[str, Any]] = []
    counts = {
        "staged_count": 0,
        "unstaged_count": 0,
        "untracked_count": 0,
        "conflicted_count": 0,
        "deleted_count": 0,
        "renamed_count": 0,
    }
    for line in lines:
        if len(line) < 3:
            continue
        index_status, worktree_status = line[0], line[1]
        code = f"{index_status}{worktree_status}"
        path = line[3:].strip()
        conflicted = code in conflict_codes or "U" in code
        untracked = code == "??"
        staged = not conflicted and not untracked and index_status not in {" ", "?"}
        unstaged = not conflicted and not untracked and worktree_status not in {" ", "?"}
        deleted = "D" in code
        renamed = "R" in code or "C" in code
        flags = {
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "conflicted": conflicted,
            "deleted": deleted,
            "renamed": renamed,
        }
        for name, enabled in flags.items():
            if enabled:
                counts[f"{name}_count"] += 1
        files.append({"path": path, "status": code, **flags})
    return {**counts, "files": files[:50]}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = [
    "CHECKS",
    "RepoContextCheck",
    "classify_dirty_worktree",
    "compute_repo_context_quality",
]
