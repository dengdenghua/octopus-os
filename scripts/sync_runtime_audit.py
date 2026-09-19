"""Cross-repo runtime sync auditor (octopus-agent -> octopus-os).

Compares ``runtime/`` between the octopus-agent repo and the octopus-os repo,
classifies every difference, and can mechanically align files whose ONLY
difference is the octopus/echo brand word.

Difference classes:
  identical      - byte-for-byte the same
  branding-only  - differs only by Octopus/octopus/OCTOPUS vs Echo/echo/ECHO;
                   aligning copies the OS (echo) file over the agent file
  real-diff      - substantive divergence; NEVER touched by align
  only-in-os     - file exists only in octopus-os
  only-in-agent  - file exists only in octopus-agent
  binary         - non-text file that differs; reported, never aligned

Usage:
  python scripts/sync_runtime_audit.py                 # audit only
  python scripts/sync_runtime_audit.py --align         # apply branding-only alignment
  python scripts/sync_runtime_audit.py --align --dry-run
  python scripts/sync_runtime_audit.py --json out.json # machine-readable report

Agent repo path is auto-detected: --agent-repo, $OCTOPUS_AGENT_REPO, or D:/echo agent.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

OS_REPO = Path(__file__).resolve().parent.parent
RUNTIME = "runtime"
BRAND_PAIRS = [(b"Octopus", b"Echo"), (b"OCTOPUS", b"ECHO"), (b"octopus", b"echo")]
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json",
    ".sh", ".cmd", ".ps1", ".js", ".ts", ".cjs", ".mjs", ".html", ".css", ".service",
    ".rules", ".conf", ".env", ".gitignore", ".dockerignore",
}


def _default_agent_repo() -> Path:
    env = os.environ.get("OCTOPUS_AGENT_REPO")
    if env:
        return Path(env)
    for candidate in ("D:/echo agent", "D:\\echo agent"):
        p = Path(candidate)
        if (p / RUNTIME).is_dir():
            return p
    raise SystemExit("octopus-agent repo not found; pass --agent-repo or set OCTOPUS_AGENT_REPO")


def _walk(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            out[str(p.relative_to(root)).replace("\\", "/")] = p
    return out


def _is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    if not path.suffix:  # extensionless configs (Dockerfile, Makefile, ...)
        return True
    return False


def _normalize_brand(data: bytes) -> bytes:
    for old, new in BRAND_PAIRS:
        data = data.replace(old, new)
    return data


def _classify(agent_file: Path | None, os_file: Path | None) -> tuple[str, bytes | None]:
    """Return (class, aligned_bytes_or_None)."""
    if agent_file is None:
        return "only-in-os", None
    if os_file is None:
        return "only-in-agent", None
    a, b = agent_file.read_bytes(), os_file.read_bytes()
    if a == b:
        return "identical", None
    if not (_is_text(agent_file) and _is_text(os_file)):
        return "binary", None
    if _normalize_brand(a) == _normalize_brand(b):
        # OS side is the echo-branded mainline: its bytes are the alignment target.
        return "branding-only", b
    if _normalize_brand(a) == b:
        # Agent content, brand-normalized, already equals OS — but the raw
        # bytes differ, meaning OS still carries an octopus word. Report only.
        return "branding-only(os-side)", None
    return "real-diff", None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-repo", type=Path, default=None)
    parser.add_argument("--align", action="store_true", help="copy OS bytes over branding-only agent files")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", dest="json_out", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=200, help="max paths listed per class in the text report")
    args = parser.parse_args(argv)

    agent_repo = args.agent_repo or _default_agent_repo()
    agent_files = _walk(agent_repo / RUNTIME)
    os_files = _walk(OS_REPO / RUNTIME)

    classes: dict[str, list[str]] = {k: [] for k in (
        "identical", "branding-only", "branding-only(os-side)", "real-diff",
        "only-in-os", "only-in-agent", "binary")}
    aligned: list[tuple[str, bytes]] = []

    for rel in sorted(set(agent_files) | set(os_files)):
        cls, aligned_bytes = _classify(agent_files.get(rel), os_files.get(rel))
        classes[cls].append(rel)
        if args.align and aligned_bytes is not None:
            aligned.append((rel, aligned_bytes))

    if args.align:
        written = 0
        for rel, data in aligned:
            target = agent_repo / RUNTIME / rel
            if not args.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            written += 1
        verb = "would rewrite" if args.dry_run else "rewrote"
        print(f"align: {verb} {written} file(s) in {agent_repo / RUNTIME}")
    else:
        print("align: skipped (audit only; pass --align to apply)")

    print(f"\nagent repo : {agent_repo}")
    print(f"os repo    : {OS_REPO}\n")
    total = 0
    for cls in ("identical", "branding-only", "branding-only(os-side)", "real-diff",
                "only-in-os", "only-in-agent", "binary"):
        items = classes[cls]
        total += len(items)
        print(f"{cls:24s} {len(items):5d}")
        for rel in items[: args.limit if cls != "identical" else 0]:
            print(f"    {rel}")
    print(f"{'TOTAL':24s} {total:5d}")

    if args.json_out:
        payload = {
            "agent_repo": str(agent_repo),
            "os_repo": str(OS_REPO),
            "counts": {k: len(v) for k, v in classes.items()},
            "paths": classes,
            "aligned": [rel for rel, _ in aligned],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\njson report: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
