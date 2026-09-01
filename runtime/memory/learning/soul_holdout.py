"""
soul_holdout · pre-flight evaluation gate for ``deep_evolve``.

Holds a small fixed set of evaluation prompts per agent. Before
``deep_evolve(dry_run=False)`` mutates SOUL.md, both the current
SOUL and the proposed-new SOUL are run against the holdout. If
the new SOUL regresses below policy floor, the apply is refused.

Belt-and-suspenders alongside ``runtime.safety.evolution.canary``
(which still does post-apply rollback on running scores).

Holdout file shape · one JSON object per line under
``data/soul_holdout/<agent_id>.jsonl``::

    {"prompt": "...", "expected_signal": "...", "weight": 1.0}

``expected_signal`` is a substring or regex marker the agent's
reply must contain — a rough quality proxy, not a full rubric.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.safety.auth.scope import TenantScope, tenant_scoped_path

_LOG = logging.getLogger("echo.soul_holdout")

_TARGET_AUTO_SEED_COUNT: int = 5
_AUTO_SEED_SCORE_FLOOR: float = 0.85
# Length of the AI reply slice we keep as the expected_signal when
# auto-seeding. Long enough to be discriminating, short enough to be
# robust to small wording drift on rerun.
_AUTO_SEED_SIGNAL_LEN: int = 24


# ═══════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True)
class HoldoutEntry:
    """One holdout prompt + its expected output marker."""

    prompt: str
    expected_signal: str
    weight: float = 1.0


@dataclass(slots=True)
class HoldoutResult:
    """Aggregate result of running a SOUL against the holdout set."""

    pass_rate: float
    detail: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class GatePolicy:
    """Pass/fail thresholds for the holdout gate."""

    floor: float = 0.6
    regression_tolerance: float = 0.05


# ═══════════════════════════════════════════════════════════
# Path helpers
# ═══════════════════════════════════════════════════════════


def _project_root() -> Path:
    from runtime.platform.process.paths import project_root

    return project_root()


def _holdout_path(
    agent_id: str,
    root: Path | None = None,
    scope: TenantScope | None = None,
) -> Path:
    base = root if root is not None else _project_root()
    path = base / "data" / "soul_holdout" / f"{agent_id}.jsonl"
    return tenant_scoped_path(path, scope) if scope is not None else path


def _scores_path(
    agent_id: str,
    root: Path | None = None,
    scope: TenantScope | None = None,
) -> Path:
    base = root if root is not None else _project_root()
    path = base / "agents" / agent_id / "agent-core" / ".scores.jsonl"
    return tenant_scoped_path(path, scope) if scope is not None else path


def _session_path(
    agent_id: str,
    thread_id: str,
    root: Path | None = None,
    scope: TenantScope | None = None,
) -> Path:
    base = root if root is not None else _project_root()
    if scope is None:
        return base / "agents" / agent_id / "sessions" / f"{thread_id}.jsonl"
    opaque_thread = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:32]
    path = base / "agents" / agent_id / "sessions" / f"{opaque_thread}.jsonl"
    return tenant_scoped_path(path, scope)


# ═══════════════════════════════════════════════════════════
# Load + auto-seed
# ═══════════════════════════════════════════════════════════


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return out


def _parse_entries(rows: list[dict[str, Any]]) -> list[HoldoutEntry]:
    out: list[HoldoutEntry] = []
    for row in rows:
        prompt = str(row.get("prompt") or "").strip()
        sig = str(row.get("expected_signal") or "").strip()
        if not prompt or not sig:
            continue
        try:
            weight = float(row.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        out.append(
            HoldoutEntry(
                prompt=prompt,
                expected_signal=sig,
                weight=weight,
            )
        )
    return out


def _score_row_visible(row: dict[str, Any], scope: TenantScope | None) -> bool:
    tenant_id = str(row.get("tenant_id") or "").strip()
    owner_actor_id = str(row.get("owner_actor_id") or "").strip()
    if bool(tenant_id) != bool(owner_actor_id):
        return False
    if scope is None:
        return not tenant_id and not owner_actor_id
    if scope.allow_cross_tenant:
        return True
    return tenant_id == scope.tenant_id and owner_actor_id == scope.actor_id


def load_holdout(
    agent_id: str,
    *,
    journal_root: Path | None = None,
    auto_seed: bool = True,
    scope: TenantScope | None = None,
) -> list[HoldoutEntry]:
    """Load holdout entries for ``agent_id``.

    On a missing file, attempt to auto-seed from recent high-scoring
    turns (≥ 0.85) and return whatever was seeded. If still empty,
    returns ``[]`` so callers can decide to skip the gate.
    """
    if not agent_id:
        return []
    path = _holdout_path(agent_id, root=journal_root, scope=scope)
    if path.exists():
        return _parse_entries(_read_jsonl(path))
    if not auto_seed:
        return []
    seeded = auto_seed_holdout(agent_id, journal_root=journal_root, scope=scope)
    if seeded > 0:
        _LOG.info(
            "auto-seeded %d holdout entries from past successes — review and edit %s",
            seeded,
            path,
        )
        return _parse_entries(_read_jsonl(path))
    return []


def _extract_prompt_and_reply(
    session_path: Path,
) -> tuple[str, str] | None:
    """Best-effort extract (first human content, last AI content)
    from a thread sessions jsonl. Returns None if not extractable."""
    if not session_path.exists():
        return None
    rows = _read_jsonl(session_path)
    prompt = ""
    reply = ""
    for row in rows:
        thread = row.get("thread") or {}
        values = thread.get("values") if isinstance(thread, dict) else {}
        msgs = (values or {}).get("messages") or []
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            mtype = m.get("type")
            content = m.get("content") or ""
            if mtype == "human" and not prompt and isinstance(content, str):
                prompt = content.strip()
            elif mtype == "ai" and isinstance(content, str) and content.strip():
                reply = content.strip()
    if not prompt or not reply:
        return None
    return prompt, reply


def auto_seed_holdout(
    agent_id: str,
    *,
    journal_root: Path | None = None,
    target_count: int = _TARGET_AUTO_SEED_COUNT,
    scope: TenantScope | None = None,
) -> int:
    """Build a holdout file from the agent's recent successes.

    Walks the per-agent ``.scores.jsonl``, picks turns with
    ``score >= 0.85``, and pulls the (prompt, reply) out of the
    matching ``sessions/<thread_id>.jsonl``. The expected_signal is
    the first ~24 chars of the reply (a rough fingerprint).

    Returns the number of entries seeded. Zero when no usable turns
    or no journal data exists.
    """
    if not agent_id:
        return 0
    if scope is not None and scope.allow_cross_tenant:
        # A global operator view cannot safely decide which tenant should own
        # a newly minted holdout.  It may inspect scores, but materialization
        # must target one exact normal scope.
        return 0
    scores = [
        row
        for row in _read_jsonl(_scores_path(agent_id, root=journal_root, scope=scope))
        if _score_row_visible(row, scope) and str(row.get("agent_id") or "") == agent_id
    ]
    if not scores:
        return 0
    # Newest first.
    scores.reverse()
    picked: list[HoldoutEntry] = []
    seen_threads: set[str] = set()
    for s in scores:
        if len(picked) >= target_count:
            break
        try:
            score = float(s.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if score < _AUTO_SEED_SCORE_FLOOR:
            continue
        thread_id = str(s.get("thread_id") or "").strip()
        if not thread_id or thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        sess = _extract_prompt_and_reply(
            _session_path(agent_id, thread_id, root=journal_root, scope=scope),
        )
        if sess is None:
            continue
        prompt, reply = sess
        signal = reply[:_AUTO_SEED_SIGNAL_LEN].strip()
        if not signal:
            continue
        picked.append(
            HoldoutEntry(
                prompt=prompt,
                expected_signal=signal,
                weight=1.0,
            )
        )
    if not picked:
        return 0
    out_path = _holdout_path(agent_id, root=journal_root, scope=scope)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for entry in picked:
                fh.write(
                    json.dumps(
                        {
                            "prompt": entry.prompt,
                            "expected_signal": entry.expected_signal,
                            "weight": entry.weight,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except OSError as exc:
        _LOG.warning("auto_seed_holdout write failed: %s", exc)
        return 0
    return len(picked)


# ═══════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════


def _signal_match(reply: str, signal: str) -> bool:
    """``signal`` matches if it's a substring OR a valid regex hit."""
    if not signal:
        return False
    if signal in reply:
        return True
    try:
        return re.search(signal, reply) is not None
    except re.error:
        return False


def evaluate_against_holdout(
    agent_id: str,
    soul_text: str,
    runner: Callable[[str, str], str],
    entries: list[HoldoutEntry] | None = None,
    *,
    journal_root: Path | None = None,
    scope: TenantScope | None = None,
) -> HoldoutResult:
    """Run each holdout prompt with ``soul_text`` via ``runner``.

    ``runner(soul, prompt) -> reply``. Score per entry is 1.0 when
    the reply contains ``expected_signal`` (substring or regex),
    else 0.0. Aggregate ``pass_rate`` is weighted average.
    """
    items = entries
    if items is None:
        items = load_holdout(agent_id, journal_root=journal_root, scope=scope)
    if not items:
        return HoldoutResult(pass_rate=0.0, detail=[])
    detail: list[dict[str, Any]] = []
    total_weight = 0.0
    weighted_score = 0.0
    for entry in items:
        try:
            reply = runner(soul_text, entry.prompt) or ""
        except Exception as exc:  # noqa: BLE001
            reply = ""
            _LOG.warning("holdout runner raised: %s", exc)
        score = 1.0 if _signal_match(reply, entry.expected_signal) else 0.0
        w = max(0.0, float(entry.weight))
        total_weight += w
        weighted_score += score * w
        detail.append(
            {
                "prompt": entry.prompt,
                "expected_signal": entry.expected_signal,
                "score": score,
                "weight": w,
                "output": reply[:500],
            }
        )
    pass_rate = (weighted_score / total_weight) if total_weight > 0 else 0.0
    return HoldoutResult(pass_rate=pass_rate, detail=detail)


# ═══════════════════════════════════════════════════════════
# Gate
# ═══════════════════════════════════════════════════════════


def gate(
    new_result: HoldoutResult,
    old_result: HoldoutResult,
    policy: GatePolicy | None = None,
) -> tuple[bool, str]:
    """Apply pass/fail policy.

    Allowed iff::
        new.pass_rate >= old.pass_rate - regression_tolerance
        AND new.pass_rate >= floor
    """
    if policy is None:
        policy = GatePolicy()
    new_rate = new_result.pass_rate
    old_rate = old_result.pass_rate
    if new_rate < policy.floor:
        return (
            False,
            f"new pass_rate {new_rate:.2f} below absolute floor {policy.floor:.2f}",
        )
    if new_rate < (old_rate - policy.regression_tolerance):
        return (
            False,
            f"new pass_rate {new_rate:.2f} regresses from old "
            f"{old_rate:.2f} beyond tolerance "
            f"{policy.regression_tolerance:.2f}",
        )
    return (True, f"ok · new={new_rate:.2f} old={old_rate:.2f}")


__all__ = [
    "HoldoutEntry",
    "HoldoutResult",
    "GatePolicy",
    "load_holdout",
    "auto_seed_holdout",
    "evaluate_against_holdout",
    "gate",
]
