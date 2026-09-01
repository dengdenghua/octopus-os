"""Keyless snapshot testing, adapted from dsh's snapshot culture.

A snapshot is a recorded transcript of a real runnable example: run the
same deterministic flow once in record mode, check the transcript into
git, then replay it forever. The same test that produces the data also
compares it against the stored snapshot and fails with a first-diff
report when behavior drifts.

Modes:

* compare (default) — read ``tests/snapshots/<nodeid>.<name>.json`` and
  assert the normalized payload matches.
* record — ``pytest --snapshot-update`` or ``ECHO_SNAPSHOT=record``
  writes the snapshot. Recording is an explicit, reviewable event;
  a missing snapshot in compare mode is a failure, never an auto-write.

Use ``normalize`` (types) + ``scrub`` (volatile keys) + ``rebase``
(volatile path prefixes) to make a transcript replayable before calling
``Snapshotter.match``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_GIT_SHA_RE = re.compile(r"\[(?:main|master) [0-9a-f]{7,40}\]")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


_DEFAULT_SCRUB_KEYS = frozenset(
    {
        "event_id",
        "ts",
        "call_id",
        "task_id",
        "arm_id",
        "conversation_id",
        "id",
        "created_at",
        "updated_at",
        "seq",
        "latency_ms",
        "duration_ms",
        "elapsed_ms",
        "output_hash",
        "content_hash",
        "signature_hash",
        "commit",
        "hash",
        "sha",
        "short_sha",
        "commit_sha",
    }
)


def normalize(value: Any) -> Any:
    """Map runtime types to stable JSON primitives.

    Datetimes lose sub-second precision (a transcript must not depend on
    wall-clock), UUIDs become strings, Paths become strings, bytes decode
    lossily.
    """

    if isinstance(value, (datetime, date)):
        return value.isoformat(timespec="seconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


def scrub(value: Any, *, keys: set[str] | frozenset[str] | None = None) -> Any:
    """Drop volatile envelope keys (ids, timestamps) from a transcript."""

    dropped = set(_DEFAULT_SCRUB_KEYS) | (set(keys) if keys else set())
    if isinstance(value, dict):
        return {key: scrub(item, keys=dropped) for key, item in value.items() if key not in dropped}
    if isinstance(value, (list, tuple)):
        return [scrub(item, keys=dropped) for item in value]
    return value


def rebase(value: Any, mapping: dict[str, str]) -> Any:
    """Replace volatile absolute-path prefixes with stable placeholders.

    ``mapping`` maps the real prefix to its placeholder, e.g.
    ``{str(tmp_path): "{workdir}"}``. Replacement happens before any
    other normalization so ordering is irrelevant to callers.
    """

    if isinstance(value, str):
        out = value
        for old, new in mapping.items():
            out = out.replace(old, new)
        return out
    if isinstance(value, dict):
        return {str(key): rebase(item, mapping) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [rebase(item, mapping) for item in value]
    return value


def snapshot_filename(nodeid: str, name: str) -> Path:
    """Stable per-test snapshot path from a pytest node id."""

    safe = nodeid.replace("::", ".").replace("/", "_").replace(":", "-")
    return SNAPSHOT_DIR / f"{safe}.{name}.json"


def _stabilize(value: Any) -> Any:
    """Replace wall-clock timestamps (ISO-8601 strings) with a placeholder.

    Journal events carry timestamps in string form after JSON parsing, so
    type-level ``normalize`` cannot reach them. A transcript must not depend
    on when it was recorded, so any ISO timestamp is pinned to ``{ts}``.
    """

    if isinstance(value, str):
        out = _ISO_TS_RE.sub("{ts}", value)
        out = _GIT_SHA_RE.sub("[{branch} {sha}]", out)
        return _UUID_RE.sub("{uuid}", out)
    if isinstance(value, dict):
        return {str(key): _stabilize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stabilize(item) for item in value]
    return value


def _first_diff(a: Any, b: Any, path: str = "$") -> str | None:
    if a == b:
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                return f"{path}.{key}: missing in recorded (present in actual)"
            if key not in b:
                return f"{path}.{key}: missing in actual (present in recorded)"
            found = _first_diff(a[key], b[key], f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        for index, (left, right) in enumerate(zip(a, b, strict=False)):
            found = _first_diff(left, right, f"{path}[{index}]")
            if found is not None:
                return found
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        return None
    return f"{path}: recorded={a!r} actual={b!r}"


class Snapshotter:
    """Per-test snapshot recorder/compare."""

    def __init__(self, nodeid: str, *, update: bool | None = None) -> None:
        self.nodeid = nodeid
        if update is None:
            update = os.environ.get("ECHO_SNAPSHOT") == "record"
        self.update = update

    def match(
        self,
        name: str,
        data: Any,
        *,
        scrub_keys: set[str] | None = None,
        rebase_map: dict[str, str] | None = None,
    ) -> None:
        """Record (update mode) or compare (default) one snapshot."""

        payload = _stabilize(normalize(data))
        if rebase_map:
            payload = rebase(payload, rebase_map)
        if scrub_keys:
            payload = scrub(payload, keys=scrub_keys)
        path = snapshot_filename(self.nodeid, name)
        if self.update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return
        if not path.exists():
            raise AssertionError(
                f"snapshot missing: {path}\n"
                "Record it with `pytest --snapshot-update` after reviewing the transcript."
            )
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored != payload:
            diff = _first_diff(stored, payload)
            raise AssertionError(
                f"snapshot drift: {path}\nfirst difference: {diff}\n"
                "Review the change; re-record with `pytest --snapshot-update` only if intended."
            )

