"""Lint: ``ServerMethod`` enum (Python) and reducer.ts ConversationEvent
methods (TypeScript) must agree.

The protocol is the wire contract between Python backend and TypeScript
frontend. Since they're maintained as separate files, drift is real:
adding a method to ``runtime/protocol/events.py:ServerMethod`` without
adding the corresponding handler to
``frontend/src/core/realtime/reducer.ts`` (or vice versa) leaves clients
silently ignoring server pushes.

This linter:
  1. Extracts string values from ``ServerMethod`` (and ``ClientMethod``)
     enums in the Python source.
  2. Extracts ``method: "..."`` literals from the TypeScript reducer's
     ``ConversationEvent`` discriminated union.
  3. Reports any value present on one side but not the other.

Some methods are intentionally one-sided:
  * ``ClientMethod`` values are sent FROM the frontend to the backend,
    so the reducer doesn't handle them — they're skipped.
  * Some ``ServerMethod`` values are RPC-style requests (REQ_*) that the
    frontend handles via separate ``request`` channel, not the reducer.
  * A few are "reserved but not yet emitted" — the per-method comment
    in events.py marks these.

The linter accepts a curated KNOWN_RESERVED set for the documented
not-yet-emitted methods and otherwise demands parity.

Run::

    python tools/lint/protocol_method_drift.py            # report
    python tools/lint/protocol_method_drift.py --strict   # exit 1 on drift
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVENTS_PY = REPO_ROOT / "runtime" / "protocol" / "events.py"
REDUCER_TS = REPO_ROOT / "frontend" / "src" / "core" / "realtime" / "reducer.ts"

# Methods present in ServerMethod that the reducer is NOT expected to
# handle — these go through other client paths (RPC requests, error
# channel, model events outside the conversation event stream).
SERVER_METHODS_NOT_IN_REDUCER: frozenset[str] = frozenset(
    {
        # Server-initiated requests handled by approval / RPC bridge,
        # not the conversation reducer.
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "item/planMode/exitRequest",
        "mcpServer/elicitation/request",
        # Cross-cutting concerns
        "error",
        "model/rerouted",
        # Reserved but currently not emitted on the wire (see comments in
        # events.py). Reducer handler may exist for forward compat — we
        # don't fail on these being "missing on wire" but do detect their
        # case-by-case status.
        "item/fileChange/outputDelta",
        "item/fileChange/hunkDelta",
    }
)

# Reducer events synthesized locally while replaying the persisted event log.
# They never cross the server-to-client wire, so adding them to ServerMethod
# would incorrectly advertise a backend protocol method.
REDUCER_METHODS_NOT_ON_WIRE: frozenset[str] = frozenset(
    {
        "turn/finalized",
    }
)


def _python_enum_values(module_path: Path, class_name: str) -> set[str]:
    """Return the string values of a StrEnum subclass in the given module."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != class_name:
            continue
        values: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                target = stmt.targets[0] if stmt.targets else None
                if (
                    isinstance(target, ast.Name)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    values.add(stmt.value.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                values.add(stmt.value.value)
        return values
    return set()


_REDUCER_METHOD_RE = re.compile(r'method:\s*"([^"]+)"')


def _reducer_methods(reducer_path: Path) -> set[str]:
    """Return all ``method: "..."`` literals in the reducer source.

    Captures handlers in the ConversationEvent discriminated union AND
    any switch-case method strings.
    """
    text = reducer_path.read_text(encoding="utf-8")
    return set(_REDUCER_METHOD_RE.findall(text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on drift")
    args = parser.parse_args()

    if not EVENTS_PY.is_file():
        print(f"events.py not found at {EVENTS_PY}", file=sys.stderr)
        return 1
    if not REDUCER_TS.is_file():
        # Frontend may live in a separate repo (post-split). Skip
        # gracefully instead of failing — the Python-side enum is
        # still validated by other linters.
        print(f"SKIP · reducer.ts not found at {REDUCER_TS} (frontend may be in a separate repo)")
        return 0

    server_methods = _python_enum_values(EVENTS_PY, "ServerMethod")
    reducer_methods = _reducer_methods(REDUCER_TS)

    if not server_methods:
        print("Could not extract ServerMethod values; aborting.", file=sys.stderr)
        return 1
    if not reducer_methods:
        print("Could not extract reducer method literals; aborting.", file=sys.stderr)
        return 1

    expected_in_reducer = server_methods - SERVER_METHODS_NOT_IN_REDUCER

    # Drift: ServerMethod values that should be handled by the reducer
    # but aren't.
    missing_in_reducer = expected_in_reducer - reducer_methods
    # Drift: reducer method literals that don't exist as server enum
    # values (typos, stale handlers).
    unknown_in_reducer = reducer_methods - server_methods - REDUCER_METHODS_NOT_ON_WIRE

    issues: list[str] = []
    if missing_in_reducer:
        issues.append(
            f"\n{len(missing_in_reducer)} ServerMethod value(s) NOT handled by reducer.ts:"
        )
        for v in sorted(missing_in_reducer):
            issues.append(f"  - {v}")
        issues.append(
            "  (add a case to reducer.ts ConversationEvent or, if intentional, "
            "list it in SERVER_METHODS_NOT_IN_REDUCER)"
        )
    if unknown_in_reducer:
        issues.append(
            f"\n{len(unknown_in_reducer)} reducer method literal(s) NOT in ServerMethod enum:"
        )
        for v in sorted(unknown_in_reducer):
            issues.append(f"  - {v}")
        issues.append(
            "  (add to runtime/protocol/events.py:ServerMethod, fix typo, or remove from reducer)"
        )

    if not issues:
        print(
            f"OK · {len(server_methods)} ServerMethod values, "
            f"{len(reducer_methods)} reducer literals, no drift "
            f"(excluding {len(SERVER_METHODS_NOT_IN_REDUCER)} server-only and "
            f"{len(REDUCER_METHODS_NOT_ON_WIRE)} replay-only)."
        )
        return 0

    for line in issues:
        print(line)

    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
