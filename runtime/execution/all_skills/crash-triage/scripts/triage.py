"""Read Echo crash dumps straight off disk and print a triage report.

Why a script instead of ``curl /api/crashes``: the interesting crash is the
one that killed the server, and a dead server answers nothing. This reads
``<data>/crashes/*.json`` directly and works even when the Echo package
itself cannot be imported — a broken ``runtime/`` is a plausible cause of
the crash, so importing it here would make the diagnostic die with patient.

Usage
-----

    python triage.py [--data-dir DIR] [--fingerprint FP]
                     [--json] [--report] [--clear] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

CRASH_DIRNAME = "crashes"


# ────────────────────────────────────────────────────────────────
# Locating the dump directory
# ────────────────────────────────────────────────────────────────


def _looks_like_project_root(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "runtime").is_dir()


def default_data_dir() -> Path:
    env = os.environ.get("ECHO_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    home = os.environ.get("ECHO_HOME", "").strip()
    if home:
        return Path(home).expanduser() / "data"
    try:
        base = Path.cwd().resolve()
    except OSError:
        base = Path(__file__).resolve()
    for candidate in (base, *base.parents):
        if _looks_like_project_root(candidate):
            return candidate / "data"
    return base / "data"


# ────────────────────────────────────────────────────────────────
# Reading
# ────────────────────────────────────────────────────────────────


def load_records(crash_dir: Path) -> list[dict[str, Any]]:
    if not crash_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(crash_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    records.sort(key=lambda r: str(r.get("last_seen_at") or ""), reverse=True)
    return records


# ────────────────────────────────────────────────────────────────
# Diagnosis: rich when the runtime is importable, offline fallback otherwise
# ────────────────────────────────────────────────────────────────

_FALLBACK_RULES: list[tuple[str, tuple[str, ...], str, str]] = [
    (
        "sandbox_delete_guard",
        ("sitecustomize", "_safe_path_unlink", "_check_bulk_delete_guard"),
        "A sandbox delete guard raised SystemExit during cleanup.",
        "Swallow errors around temp-file cleanup so it cannot mask the real one.",
    ),
    (
        "base_exception_escape",
        ("SystemExit", "KeyboardInterrupt", "GeneratorExit"),
        "A BaseException escaped — `except Exception` does not catch these.",
        "Catch the specific BaseException subclass, or let shutdown finish.",
    ),
    (
        "cancelled",
        ("CancelledError",),
        "The task was cancelled — normal during shutdown.",
        "No fix needed unless it happens outside shutdown.",
    ),
    (
        "disk_full",
        ("No space left on device", "ENOSPC"),
        "The device ran out of disk space.",
        "Free space, or find the unbounded write loop.",
    ),
    (
        "permission_denied",
        ("PermissionError", "EACCES", "EPERM"),
        "A file or socket operation was denied.",
        "Check the service user owns the data dir; check unit hardening.",
    ),
    (
        "out_of_memory",
        ("MemoryError", "Cannot allocate memory"),
        "The process ran out of memory or was OOM-killed.",
        "Cap resident/model memory on low-RAM tiers.",
    ),
    (
        "network",
        ("ConnectionRefusedError", "TimeoutError", "Name or service not known"),
        "A network call failed unhandled.",
        "Add a timeout and degrade gracefully offline.",
    ),
]


def _fallback_diagnosis(record: dict[str, Any]) -> dict[str, Any]:
    haystack = "\n".join(
        str(record.get(key) or "") for key in ("exception_type", "message", "traceback")
    )
    for cause, needles, why, fix in _FALLBACK_RULES:
        hits = [n for n in needles if n in haystack]
        if hits:
            return {
                "cause": cause,
                "confidence": "high" if cause != "base_exception_escape" else "medium",
                "explanation": why,
                "suggested_fix": fix,
                "evidence": hits,
                "source": "local-fallback",
            }
    return {
        "cause": "unknown",
        "confidence": "low",
        "explanation": "No local rule matched; read the traceback below.",
        "suggested_fix": "",
        "evidence": [],
        "source": "local-fallback",
    }


def _runtime_diagnosis(record: dict[str, Any]) -> dict[str, Any] | None:
    """Use the shipped analyzer when it can be imported."""
    try:
        from runtime.platform.observability.crash_reporter import (  # noqa: PLC0415
            CrashRecord,
            analyze,
        )
    except Exception:  # noqa: BLE001 - runtime may be the thing that is broken
        return None
    try:
        diagnosis = analyze(CrashRecord.from_wire(record))
    except Exception:  # noqa: BLE001
        return None
    return {
        "cause": diagnosis.cause,
        "confidence": diagnosis.confidence,
        "explanation": diagnosis.explanation,
        "suggested_fix": diagnosis.suggested_fix,
        "evidence": list(diagnosis.evidence),
        "source": "runtime",
    }


def diagnose(record: dict[str, Any], *, offline: bool) -> dict[str, Any]:
    if not offline:
        rich = _runtime_diagnosis(record)
        if rich is not None:
            return rich
    return _fallback_diagnosis(record)


# ────────────────────────────────────────────────────────────────
# Rendering
# ────────────────────────────────────────────────────────────────


def render(records: list[dict[str, Any]], *, offline: bool, with_traceback: bool) -> str:
    lines: list[str] = []
    for record in records:
        diagnosis = diagnose(record, offline=offline)
        lines.append(
            f"崩溃 {record.get('fingerprint', '?')} · {record.get('exception_type', '?')}"
        )
        lines.append(
            f"  次数 {record.get('occurrence_count', 1)}"
            f" · 首次 {record.get('first_seen_at', '?')}"
            f" · 末次 {record.get('last_seen_at', '?')}"
        )
        lines.append(
            f"  source: {record.get('source', '?')}"
            f" · thread: {record.get('thread_name', '')}"
        )
        context = record.get("context") or {}
        if context:
            lines.append(
                "  context: " + ", ".join(f"{k}={v}" for k, v in sorted(context.items()))
            )
        lines.append("")
        lines.append(
            f"  判定: {diagnosis['cause']} (confidence {diagnosis['confidence']}, "
            f"via {diagnosis['source']})"
        )
        lines.append(f"  为什么: {diagnosis['explanation']}")
        if diagnosis["suggested_fix"]:
            lines.append(f"  改哪里: {diagnosis['suggested_fix']}")
        message = str(record.get("message") or "").strip()
        if message:
            lines.append(f"  消息: {message[:400]}")
        if with_traceback:
            tb = str(record.get("traceback") or "").rstrip()
            if tb:
                lines.extend(["", "  traceback:"])
                lines.extend(f"    {line}" for line in tb.splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Triage Echo OS crash dumps stored on disk.",
    )
    parser.add_argument("--data-dir", default=None, help="Echo data directory.")
    parser.add_argument("--fingerprint", default=None, help="Only this crash.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--report",
        action="store_true",
        help="Include the full traceback (bug-report mode).",
    )
    parser.add_argument("--clear", action="store_true", help="Delete every dump.")
    parser.add_argument("--limit", type=int, default=20, help="Max dumps to show.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Never import the Echo runtime; use the built-in rules only.",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    crash_dir = data_dir / CRASH_DIRNAME

    if args.clear:
        removed = 0
        for path in sorted(crash_dir.glob("*.json")):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                print(f"failed to remove {path}: {exc}", file=sys.stderr)
        print(f"removed {removed} crash dump(s) from {crash_dir}")
        return 0

    records = load_records(crash_dir)
    if args.fingerprint:
        records = [r for r in records if str(r.get("fingerprint")) == args.fingerprint]
        if not records:
            print(f"no crash dump with fingerprint {args.fingerprint}", file=sys.stderr)
            return 1
    else:
        records = records[: max(1, args.limit)]

    if not records:
        print(f"no crash dumps in {crash_dir}")
        return 0

    if args.json:
        payload = [
            {**record, "diagnosis": diagnose(record, offline=args.offline)}
            for record in records
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    sys.stdout.write(
        render(records, offline=args.offline, with_traceback=args.report)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
