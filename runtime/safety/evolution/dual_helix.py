"""Task-level pairing evidence for Echo ↔ Codex evolution.

This module never launches a second model or mutates a workspace. It pairs
already completed, trusted runtime samples by normalized goal fingerprint so
the later shadow runner can use measured outcomes without fabricating scores.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from runtime.safety.evolution.proposal_ledger import ProposalRecord


def _engine(record: ProposalRecord) -> str | None:
    value = str(record.metadata.get("engine") or "").strip().lower()
    return value if value in {"echo", "codex"} else None


def _success(record: ProposalRecord) -> bool:
    return record.kind == "turn_success"


def build_dual_helix_evidence(
    records: Iterable[ProposalRecord], *, limit: int = 20
) -> dict[str, Any]:
    samples = [record for record in records if _engine(record) is not None]
    groups: dict[str, dict[str, list[ProposalRecord]]] = defaultdict(
        lambda: {"echo": [], "codex": []}
    )
    for record in samples:
        fingerprint = str(record.metadata.get("goal_fingerprint") or "").strip()
        engine = _engine(record)
        if fingerprint and engine:
            groups[fingerprint][engine].append(record)

    pairs: list[dict[str, Any]] = []
    echo_wins = codex_wins = ties = 0
    for fingerprint, strands in groups.items():
        if not strands["echo"] or not strands["codex"]:
            continue
        echo = strands["echo"][-1]
        codex = strands["codex"][-1]
        echo_ok, codex_ok = _success(echo), _success(codex)
        winner = "tie"
        if echo_ok and not codex_ok:
            winner = "echo"
            echo_wins += 1
        elif codex_ok and not echo_ok:
            winner = "codex"
            codex_wins += 1
        else:
            ties += 1
        pairs.append(
            {
                "goal_fingerprint": fingerprint,
                "goal": str(echo.metadata.get("goal") or codex.metadata.get("goal") or ""),
                "winner": winner,
                "echo": _sample_wire(echo),
                "codex": _sample_wire(codex),
            }
        )

    pairs.sort(
        key=lambda item: max(item["echo"]["ts"], item["codex"]["ts"]),
        reverse=True,
    )
    strand_stats: dict[str, dict[str, Any]] = {}
    for engine in ("echo", "codex"):
        strand = [record for record in samples if _engine(record) == engine]
        successes = sum(1 for record in strand if _success(record))
        strand_stats[engine] = {
            "samples": len(strand),
            "successes": successes,
            "success_rate": round(successes / len(strand), 3) if strand else None,
        }
    paired_count = len(pairs)
    decisive_count = echo_wins + codex_wins
    return {
        "ok": True,
        "schema": "echo.dual_helix_evidence.v1",
        "paired_count": paired_count,
        "unpaired_count": max(0, len(groups) - paired_count),
        "echo_wins": echo_wins,
        "codex_wins": codex_wins,
        "ties": ties,
        "echo_win_rate": (round(echo_wins / decisive_count, 3) if decisive_count else None),
        "strands": strand_stats,
        "pairs": pairs[: max(1, limit)],
    }


def _sample_wire(record: ProposalRecord) -> dict[str, Any]:
    return {
        "proposal_id": record.proposal_id,
        "outcome": "success" if _success(record) else "failure",
        "model": record.model,
        "ts": record.ts,
        "verification_count": int(record.metadata.get("verification_count") or 0),
        "has_code_changes": bool(record.metadata.get("has_code_changes")),
    }


__all__ = ["build_dual_helix_evidence"]
