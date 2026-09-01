"""Audit Log Signing — tamper-evident HMAC chain.

Each audit record is signed with HMAC-SHA256 over
``(prev_mac || record_bytes)`` so any mid-log modification is
detectable on verification. This is the classic "hash chain" or
"MAC chain" pattern used by AWS CloudTrail, Kubernetes audit logs,
and most compliance-grade journaling systems.

Why not just sign each record independently?
--------------------------------------------
Per-record signing catches *mutation* of a record but not
*deletion* or *reordering* — an attacker can drop or reshuffle
signed records without breaking any individual MAC. Chaining links
record N's MAC to record N-1, so removing a record breaks every
downstream MAC.

Design
------
* **HMAC-SHA256** over ``prev_mac + payload_bytes``. Genesis record
  uses a zero ``prev_mac``.
* **Stable serialization** — canonical JSON with sorted keys + no
  whitespace so two encoders agree. Mirrors what AWS CloudTrail does.
* **Secret rotation** — ``keys`` is a ``dict[key_id → secret]``.
  Signer uses the "active" key; verifier looks up the key recorded
  in the entry's ``key_id``. Rotating keys is a matter of flipping
  ``active_key_id`` and keeping the old secret in ``keys`` until
  all audit-log consumers have re-indexed.
* **No external deps** — pure ``hmac`` + ``hashlib``.

File format (JSONL)
-------------------
One ``AuditEntry`` per line::

    {"seq": 0, "ts": "...", "kind": "step", "payload": {...},
     "key_id": "v1", "prev_mac": "", "mac": "abc..."}

``seq`` is a monotonically-increasing integer the signer assigns.
``prev_mac`` is empty ("") for the genesis record.

Usage
-----

    from runtime.safety.audit.audit_chain import AuditChain

    chain = AuditChain(
        path=Path("data/audit.jsonl"),
        keys={"v1": b"secret-from-env"},
        active_key_id="v1",
    )
    chain.append(kind="step", payload={"step_id": 7, "ok": True})

    # Later, perhaps in a different process:
    report = chain.verify()
    if not report.ok:
        alarm(report.broken_at)

Integration points
------------------
* Journal write path — chain every ``FileOpEvent`` / ``StepEvent``.
* /audit router — expose ``verify()`` as a ``/api/audit/verify``
  endpoint so SRE can run tamper checks on demand.
* Health readiness — add a ``audit_chain_check`` that calls
  ``verify(sample=100)`` every minute (ring-check the tail).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.safety.audit_chain")


# ── Canonical JSON ──────────────────────────────────────────


def canonical_bytes(value: Any) -> bytes:
    """Serialize ``value`` to canonical JSON bytes.

    * ``sort_keys=True`` — deterministic key order
    * ``separators=(",", ":")`` — no whitespace
    * ``ensure_ascii=False`` — preserve non-ASCII to match UTF-8
      input-byte length
    * ``default=str`` — fallback to ``str()`` for datetime/UUID/etc.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


# ── Entry ───────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditEntry:
    """One signed audit record."""

    seq: int
    ts: str
    kind: str
    payload: dict[str, Any]
    key_id: str
    prev_mac: str
    mac: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "payload": dict(self.payload),
            "key_id": self.key_id,
            "prev_mac": self.prev_mac,
            "mac": self.mac,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AuditEntry:
        return cls(
            seq=int(raw["seq"]),
            ts=str(raw["ts"]),
            kind=str(raw["kind"]),
            payload=dict(raw.get("payload") or {}),
            key_id=str(raw["key_id"]),
            prev_mac=str(raw.get("prev_mac", "")),
            mac=str(raw["mac"]),
        )


# ── Verification report ─────────────────────────────────────


@dataclass
class VerifyReport:
    """Result of a chain verification pass."""

    ok: bool
    entries_checked: int
    broken_at: int | None = None  # seq number of first bad entry
    error: str = ""
    # Extra diagnostics for the admin UI.
    details: list[str] = field(default_factory=list)


# ── Signer helpers ──────────────────────────────────────────


def _compute_mac(key: bytes, prev_mac_hex: str, payload_bytes: bytes) -> str:
    """HMAC-SHA256 over (prev_mac_bytes || payload_bytes)."""
    prev_bytes = bytes.fromhex(prev_mac_hex) if prev_mac_hex else b""
    h = hmac.new(key, prev_bytes + payload_bytes, hashlib.sha256)
    return h.hexdigest()


# ── AuditChain ──────────────────────────────────────────────


class AuditChain:
    """Append-only tamper-evident audit log.

    Parameters
    ----------
    path:
        Where the JSONL file lives. Parent directory is created on
        first ``append()``.
    keys:
        ``{key_id: secret_bytes}`` for signing and verification.
        At least one entry required.
    active_key_id:
        Key id used when signing new entries. Must be a key in ``keys``.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        keys: dict[str, bytes],
        active_key_id: str,
    ) -> None:
        if not keys:
            raise ValueError("at least one key required")
        if active_key_id not in keys:
            raise ValueError(
                f"active_key_id {active_key_id!r} not in keys",
            )
        self.path = Path(path)
        self._keys = dict(keys)
        self._active = active_key_id
        self._lock = threading.Lock()
        # Cache the tail so we don't re-read the entire file on every append.
        self._last_mac: str | None = None
        self._next_seq: int | None = None

    # ── public API ──────────────────────────────────────────

    def append(self, *, kind: str, payload: dict[str, Any]) -> AuditEntry:
        """Sign and append a new entry.

        Thread-safe. Returns the created ``AuditEntry``.
        """
        with self._lock:
            if self._last_mac is None:
                self._prime_cache()
            seq = self._next_seq or 0
            ts = datetime.now(UTC).isoformat()
            key_id = self._active
            body = {
                "seq": seq,
                "ts": ts,
                "kind": kind,
                "payload": dict(payload),
                "key_id": key_id,
            }
            body_bytes = canonical_bytes(body)
            mac = _compute_mac(
                self._keys[key_id],
                self._last_mac or "",
                body_bytes,
            )
            entry = AuditEntry(
                seq=seq,
                ts=ts,
                kind=kind,
                payload=dict(payload),
                key_id=key_id,
                prev_mac=self._last_mac or "",
                mac=mac,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False))
                f.write("\n")
            self._last_mac = mac
            self._next_seq = seq + 1
            return entry

    def verify(self, *, limit: int | None = None) -> VerifyReport:
        """Re-read the file from the start and verify every MAC.

        ``limit`` caps how many entries to read (useful for a fast
        ring-check on the tail).
        """
        if not self.path.exists():
            return VerifyReport(ok=True, entries_checked=0)
        entries_checked = 0
        prev_mac = ""
        expected_seq = 0
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if limit is not None and entries_checked >= limit:
                        break
                    try:
                        raw = json.loads(line)
                        entry = AuditEntry.from_dict(raw)
                    except (ValueError, KeyError) as exc:
                        return VerifyReport(
                            ok=False,
                            entries_checked=entries_checked,
                            broken_at=expected_seq,
                            error=f"malformed line at seq={expected_seq}: {exc}",
                        )
                    if entry.seq != expected_seq:
                        return VerifyReport(
                            ok=False,
                            entries_checked=entries_checked,
                            broken_at=entry.seq,
                            error=(f"seq gap: expected {expected_seq} got {entry.seq}"),
                        )
                    if entry.prev_mac != prev_mac:
                        return VerifyReport(
                            ok=False,
                            entries_checked=entries_checked,
                            broken_at=entry.seq,
                            error=(
                                f"prev_mac mismatch at seq={entry.seq}: "
                                f"expected {prev_mac!r} got {entry.prev_mac!r}"
                            ),
                        )
                    key = self._keys.get(entry.key_id)
                    if key is None:
                        return VerifyReport(
                            ok=False,
                            entries_checked=entries_checked,
                            broken_at=entry.seq,
                            error=(f"unknown key_id {entry.key_id!r} at seq={entry.seq}"),
                        )
                    body = {
                        "seq": entry.seq,
                        "ts": entry.ts,
                        "kind": entry.kind,
                        "payload": entry.payload,
                        "key_id": entry.key_id,
                    }
                    expected_mac = _compute_mac(
                        key,
                        entry.prev_mac,
                        canonical_bytes(body),
                    )
                    if not hmac.compare_digest(expected_mac, entry.mac):
                        return VerifyReport(
                            ok=False,
                            entries_checked=entries_checked,
                            broken_at=entry.seq,
                            error=f"MAC mismatch at seq={entry.seq}",
                        )
                    prev_mac = entry.mac
                    expected_seq += 1
                    entries_checked += 1
        except Exception as exc:  # noqa: BLE001
            return VerifyReport(
                ok=False,
                entries_checked=entries_checked,
                error=f"{type(exc).__name__}: {exc}",
            )
        return VerifyReport(ok=True, entries_checked=entries_checked)

    def rotate_key(self, *, new_key_id: str, new_secret: bytes) -> None:
        """Install a new active key. Old keys stay in ``keys`` so
        verification of pre-rotation entries still works.
        """
        with self._lock:
            self._keys[new_key_id] = new_secret
            self._active = new_key_id

    def tail(self, n: int = 10) -> list[AuditEntry]:
        """Return the last ``n`` entries (may be fewer)."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        result: list[AuditEntry] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                result.append(AuditEntry.from_dict(json.loads(line)))
            except (ValueError, KeyError):
                continue
        return result

    # ── internals ───────────────────────────────────────────

    def _prime_cache(self) -> None:
        """Walk the file once on first use to find the tail."""
        last_mac = ""
        next_seq = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        last_mac = str(raw.get("mac", ""))
                        next_seq = int(raw.get("seq", -1)) + 1
                    except (ValueError, KeyError):
                        continue
        self._last_mac = last_mac
        self._next_seq = next_seq


__all__ = [
    "AuditChain",
    "AuditEntry",
    "VerifyReport",
    "canonical_bytes",
]
