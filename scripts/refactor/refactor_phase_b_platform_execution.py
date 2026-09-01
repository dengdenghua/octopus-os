"""Phase B follow-up: rewrite imports for platform/* and execution/* moves.

platform/<file>          → platform/<group>/<file>     (6 groups, 38 files)
execution/<file>         → execution/misc/<file>       (1 group, 9 files)
"""
from __future__ import annotations

import sys
from pathlib import Path

PLATFORM_GROUPS = {
    "process": [
        "session", "session_executor", "state", "scope", "streaming",
        "paths", "utils", "service_provider", "event_bridge", "eventbus",
        "distributed_lock", "turn_model",
    ],
    "observability": [
        "metrics", "health", "logging_config", "structured_logging",
        "redactor", "doctor",
    ],
    "plugins": [
        "plugin_base", "plugin_compat", "plugin_hub", "plugin_loader",
        "plugins", "skill_market",
    ],
    "lifecycle": [
        "backup", "data_migration", "factory_reset", "setup_wizard", "demo",
    ],
    "llm_infra": [
        "llm_cache", "llm_caller", "budget_tracker",
    ],
    "runtime_policy": [
        "browser_sessions", "capabilities", "feature_flags", "idempotency",
        "identity_filter", "retry", "workspaces",
    ],
}

EXECUTION_GROUPS = {
    "misc": [
        "agent_avatar", "agent_packs", "capability_catalog",
        "capability_permissions", "file_write_leases", "image_generation",
        "multiagent_contracts", "parallel_runner", "skill_policy",
    ],
}


def build_renames() -> dict[str, str]:
    out: dict[str, str] = {}
    for grp, files in PLATFORM_GROUPS.items():
        for f in files:
            out[f"runtime.platform.{f}"] = f"runtime.platform.{grp}.{f}"
    for grp, files in EXECUTION_GROUPS.items():
        for f in files:
            out[f"runtime.execution.{f}"] = f"runtime.execution.{grp}.{f}"
    return out


RENAMES = build_renames()
ORDERED = sorted(RENAMES.items(), key=lambda kv: -len(kv[0]))

EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".cfg", ".txt", ".ini", ".mermaid"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
             ".qoder", ".codex-logs", ".pytest_cache", ".ruff_cache", "echo_agent.egg-info",
             "frontend"}  # IMPORTANT: skip frontend so we don't pull in unrelated TS work


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
    # Two-pass with sentinels: prevents the new path of one rule from
    # being captured as the old path of another rule. Without this,
    # rewriting `runtime.platform.plugins.plugin_loader` → `..plugins.plugin_loader`
    # then triggers the second rule on `..plugins` and yields
    # `..plugins.plugins.plugin_loader`.
    placeholders: dict[str, str] = {}
    for i, (old, new) in enumerate(ORDERED):
        ph = f"\x00OLDPATH{i}\x00"
        if old in content:
            placeholders[ph] = new
            cnt = content.count(old)
            content = content.replace(old, ph)
            total += cnt
    for ph, new in placeholders.items():
        content = content.replace(ph, new)
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
