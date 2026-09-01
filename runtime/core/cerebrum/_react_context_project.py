"""Project rules / git status / project-profile prompt builders.

Extracted from ``react_context.py``. Pure builders — no behaviour change.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


_PROJECT_RULES_FILES = [
    ".echo/rules.md",
    ".cursorrules",
    "CLAUDE.md",
]
_PROJECT_RULES_MAX_BYTES = 8 * 1024


def _load_project_rules(workspace_path: str) -> str:
    from pathlib import Path

    root = Path(workspace_path)
    for name in _PROJECT_RULES_FILES:
        p = root / name
        try:
            if p.is_file() and p.stat().st_size <= _PROJECT_RULES_MAX_BYTES:
                return p.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
    return ""


def _git_status_summary(root: Any) -> str:
    """Compact one-line git status for project-profile injection.

    Format: ``branch=<name> modified=<n> untracked=<n> [ahead=<n>] [behind=<n>] last="<msg>"``.
    Returns "" silently when git isn't available, the path isn't a
    repo, or the subprocess errors. The goal is to give the model the
    same situational awareness a human gets at first glance, without
    adding seconds to turn startup.

    All four git subprocess calls run concurrently via threads, cutting
    worst-case wall time from ~4×timeout to ~1×timeout.
    """
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    try:
        path = Path(root)
        if not (path / ".git").exists():
            return ""
    except (OSError, TypeError, ValueError):
        return ""

    def _git(*args: str, timeout: float = 1.5) -> str:
        try:
            r = subprocess.run(
                ["git", *args],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if r.returncode != 0:
            return ""
        return r.stdout.strip()

    # Run the four independent git queries concurrently to avoid
    # sequential subprocess latency dominating turn startup.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="git-profile") as pool:
        branch_f = pool.submit(_git, "branch", "--show-current")
        porcelain_f = pool.submit(_git, "status", "--porcelain")
        upstream_f = pool.submit(_git, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        last_f = pool.submit(_git, "log", "-1", "--pretty=format:%h %s")
        branch = branch_f.result() or "(detached)"
        porcelain = porcelain_f.result()
        upstream = upstream_f.result()
        last = last_f.result()

    modified = sum(1 for line in porcelain.splitlines() if line and not line.startswith("??"))
    untracked = sum(1 for line in porcelain.splitlines() if line.startswith("??"))

    ahead = behind = 0
    if upstream:
        # Output is "<behind>\t<ahead>" relative to upstream.
        upstream_parts = upstream.split()
        if len(upstream_parts) == 2 and upstream_parts[0].isdigit() and upstream_parts[1].isdigit():
            behind, ahead = int(upstream_parts[0]), int(upstream_parts[1])

    last_short = (last[:60] + "…") if len(last) > 60 else last

    parts: list[str] = [f"branch={branch}"]
    if modified:
        parts.append(f"modified={modified}")
    if untracked:
        parts.append(f"untracked={untracked}")
    if ahead:
        parts.append(f"ahead={ahead}")
    if behind:
        parts.append(f"behind={behind}")
    if last_short:
        parts.append(f'last="{last_short}"')
    return " ".join(parts)


def _build_project_profile_prompt(workspace_path: str, *, include_diagnostics: bool = False) -> str:
    try:
        from pathlib import Path

        from runtime.execution.suckers.verify_skills import detect_project

        profile = detect_project(workspace_path)
        if profile.kind == "unknown":
            return ""

        root = Path(workspace_path)
        lines = [f"项目类型: {profile.kind}"]

        # Git situational awareness — give the model the same first-look
        # context a human would notice (echo optimisation §26).
        # Cheap subprocess (≤ 50 ms typical), all best-effort: missing
        # git, not-a-repo, hung remote — silently skip rather than
        # delaying turn start.
        _git_summary = _git_status_summary(root)
        if _git_summary:
            lines.append(f"git: {_git_summary}")

        if profile.kind.startswith("node"):
            pkg_path = root / "package.json"
            if pkg_path.is_file():
                import json

                try:
                    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                    if pkg.get("name"):
                        lines.append(f"包名: {pkg['name']}")
                    scripts = pkg.get("scripts", {})
                    if scripts:
                        lines.append(f"可用脚本: {', '.join(sorted(scripts.keys())[:12])}")
                    deps = list(pkg.get("dependencies", {}).keys())[:8]
                    if deps:
                        lines.append(f"主要依赖: {', '.join(deps)}")
                    if (root / "tsconfig.json").is_file():
                        lines.append("TypeScript: 已启用")
                    for fw in [
                        "next",
                        "nuxt",
                        "vite",
                        "react",
                        "vue",
                        "svelte",
                        "angular",
                    ]:
                        if fw in pkg.get("dependencies", {}) or fw in pkg.get(
                            "devDependencies", {}
                        ):
                            lines.append(f"框架: {fw}")
                            break
                except (TypeError, ValueError) as exc:
                    _logger.debug("framework detection skipped: %s", exc)

        elif profile.kind == "python":
            for entry in ["src", "app", "main.py", "manage.py", "setup.py"]:
                if (root / entry).exists():
                    lines.append(f"入口: {entry}")
                    break
            if (root / "requirements.txt").is_file():
                lines.append("包管理: requirements.txt")
            elif (root / "pyproject.toml").is_file():
                lines.append("包管理: pyproject.toml")

        elif profile.kind == "rust":
            lines.append("构建: cargo")

        elif profile.kind == "go":
            lines.append("构建: go build")

        if profile.checks:
            check_names = [c["name"] for c in profile.checks]
            lines.append(f"验证命令: {', '.join(check_names)}")

        if include_diagnostics and profile.checks:
            _diag_lines = _collect_initial_diagnostics(profile, workspace_path)
            if _diag_lines:
                lines.append("")
                lines.append("⚠ 当前项目诊断状态 (开始前已知):")
                lines.extend(_diag_lines)

        return "\n".join(lines)
    except (TypeError, ValueError, OSError):
        return ""


def _collect_initial_diagnostics(profile: Any, workspace_path: str) -> list[str]:
    try:
        from runtime.execution.suckers.verify_skills import run_checks

        fast_checks = [
            c for c in profile.checks if c["name"] in ("typecheck", "check", "vet", "syntax")
        ]
        if not fast_checks:
            return []
        fast_profile = profile.__class__(
            kind=profile.kind,
            root=profile.root,
            checks=fast_checks[:1],
        )
        from runtime.execution.suckers.verify_skills import (
            output_indicates_missing_tool,
        )

        results = run_checks(fast_profile, timeout_per_check=15, max_output=2000)
        diag_lines: list[str] = []
        real_failures = 0
        for r in results:
            if r.passed:
                diag_lines.append(f"  ✓ {r.name}: 通过")
                continue
            output = (r.stderr or r.stdout or "").strip()
            # A missing checker (no cargo/go/etc.) is an environment gap,
            # not a code failure — same suppression as the post-write
            # _run_auto_diagnostics path, which previously diverged.
            if output_indicates_missing_tool(output):
                continue
            real_failures += 1
            if len(output) > 800:
                output = output[:800] + "\n  ...(截断)"
            diag_lines.append(f"  ✗ {r.name}: 失败")
            if output:
                for line in output.split("\n")[:12]:
                    diag_lines.append(f"    {line}")
        return diag_lines if real_failures else []
    except (OSError, TypeError, ValueError) as exc:
        _logger.debug("_collect_initial_diagnostics failed: %s", exc)
        return []
