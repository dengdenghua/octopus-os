"""Phase B final: rewrite imports for memory/safety subpackage moves.

memory/<file>           → memory/<group>/<file>     (5 groups, 25 files)
safety/<file>           → safety/<group>/<file>     (3 new groups + hooks, 11 files)
"""
from __future__ import annotations

import sys
from pathlib import Path

# memory: file → new subpackage
MEMORY_GROUPS = {
    "learning": [
        "experience_ledger", "review_queue", "promotion_applier",
        "soul_holdout", "turn_scoring", "deep_evolution",
    ],
    "skills_lib": [
        "skill_library", "skill_curator", "meta_skill",
        "ambient_suggestions", "ambient_suggestions_scheduler",
    ],
    "runtime_state": [
        "hot_cache", "blackboard", "hub", "context_compressor",
        "scope_paths", "file_transactions", "process_timeline",
    ],
    "users": [
        "user_store", "user_preferences", "profile", "mention_history",
    ],
    "diagnostics": [
        "trace_store", "error_classifier", "wiki_compiler",
    ],
}

SAFETY_GROUPS = {
    "approval": [
        "approval_gate", "approval_policy_store", "cancellation", "device_lock",
    ],
    "sandboxing": [
        "sandbox", "container_sandbox",
    ],
    "audit": [
        "audit_chain", "webhook_verify", "trust_gateway",
    ],
    "hooks": [
        "tool_edge_hooks",  # joins existing hooks/ submodules
    ],
}


def build_renames() -> dict[str, str]:
    """Build complete old-FQN → new-FQN map. Longest first matters
    (none collide here, but defensive)."""
    out: dict[str, str] = {}
    for grp, files in MEMORY_GROUPS.items():
        for f in files:
            out[f"runtime.memory.{f}"] = f"runtime.memory.{grp}.{f}"
    for grp, files in SAFETY_GROUPS.items():
        for f in files:
            out[f"runtime.safety.{f}"] = f"runtime.safety.{grp}.{f}"
    return out


RENAMES = build_renames()
ORDERED = sorted(RENAMES.items(), key=lambda kv: -len(kv[0]))

EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".cfg", ".txt", ".ini", ".mermaid"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
             ".qoder", ".codex-logs", ".pytest_cache", ".ruff_cache", "echo_agent.egg-info"}


def should_process(path: Path) -> bool:
    if path.suffix.lower() not in EXTENSIONS:
        return False
    return not (set(path.parts) & SKIP_DIRS)


def rewrite_file(path: Path) -> int:
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    original = content
    total = 0
    for old, new in ORDERED:
        count = content.count(old)
        if count:
            content = content.replace(old, new)
            total += count
    if content != original:
        path.write_text(content, encoding="utf-8")
    return total


def main(root: str) -> None:
    root_path = Path(root)
    files = 0
    total = 0
    for p in root_path.rglob("*"):
        if not p.is_file() or not should_process(p):
            continue
        n = rewrite_file(p)
        if n:
            files += 1
            total += n
            print(f"{n:5d}  {p.relative_to(root_path)}")
    print(f"\nFQN: {files} files, {total} replacements")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
