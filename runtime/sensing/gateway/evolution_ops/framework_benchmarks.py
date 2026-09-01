"""Framework benchmarks subsystem for evolution operators."""

from __future__ import annotations

import hashlib
from typing import Any

from .utils import (
    _as_dt,
    _iso,
    _stable_int_id,
    _token_usage_rows,
    _trajectory_outcomes_by_task,
    _trajectory_rows,
    _utcnow,
)


def _model_benchmark_rows(journal: Any) -> list[dict[str, Any]]:
    usage_rows = _token_usage_rows(journal)
    if not usage_rows:
        return []

    outcomes = _trajectory_outcomes_by_task(journal)
    grouped: dict[str, dict[str, Any]] = {}
    for row in usage_rows:
        model = row["model"]
        task_id = row["task_id"]
        bucket = grouped.setdefault(
            model,
            {
                "task_ids": set(),
                "known_task_ids": set(),
                "success_task_ids": set(),
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "last_seen": row["ts"],
            },
        )
        bucket["task_ids"].add(task_id)
        if task_id in outcomes:
            bucket["known_task_ids"].add(task_id)
            if outcomes[task_id]:
                bucket["success_task_ids"].add(task_id)
        bucket["input_tokens"] += int(row["input_tokens"])
        bucket["output_tokens"] += int(row["output_tokens"])
        bucket["cost_usd"] += float(row["cost_usd"])
        if row["ts"] > bucket["last_seen"]:
            bucket["last_seen"] = row["ts"]

    scored: list[dict[str, Any]] = []
    for model, bucket in grouped.items():
        task_count = len(bucket["task_ids"])
        known_count = len(bucket["known_task_ids"])
        success_count = len(bucket["success_task_ids"])
        total_tokens = int(bucket["input_tokens"]) + int(bucket["output_tokens"])
        cost_usd = float(bucket["cost_usd"])
        success_rate = success_count / known_count if known_count else None
        avg_tokens = total_tokens / task_count if task_count else 0.0
        avg_cost = cost_usd / task_count if task_count else 0.0
        score = (
            (success_rate if success_rate is not None else 0.5) * 100.0
            - min(25.0, avg_cost * 250.0)
            - min(15.0, avg_tokens / 20000.0)
        )
        scored.append(
            {
                "model": model,
                "task_count": task_count,
                "known_count": known_count,
                "success_count": success_count,
                "success_rate": success_rate,
                "input_tokens": int(bucket["input_tokens"]),
                "output_tokens": int(bucket["output_tokens"]),
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "avg_tokens": avg_tokens,
                "avg_cost": avg_cost,
                "score": score,
                "last_seen": bucket["last_seen"],
            }
        )

    scored.sort(key=lambda r: (-float(r["score"]), str(r["model"])))
    best_model = scored[0]["model"] if scored else None

    rows: list[dict[str, Any]] = []
    for row in scored:
        is_best = row["model"] == best_model
        status = "recommended" if is_best else "observed"
        if row["known_count"] == 0:
            status = "observed"
        notes = [
            f"tasks: {row['task_count']} ({row['known_count']} with outcomes)",
            (
                "success_rate: "
                + (f"{row['success_rate']:.0%}" if row["success_rate"] is not None else "unknown")
            ),
            f"tokens: {row['total_tokens']} total / {row['avg_tokens']:.0f} avg",
            f"cost: ${row['cost_usd']:.4f} total / ${row['avg_cost']:.4f} avg",
            f"score: {row['score']:.1f}",
        ]
        rows.append(
            {
                "id": _stable_int_id(f"model:{row['model']}"),
                "model_label": row["model"],
                "created_at": _iso(row["last_seen"]),
                "status": status,
                "benchmark_notes": "\n".join(notes),
                "task_count": row["task_count"],
                "known_outcomes": row["known_count"],
                "success_rate": row["success_rate"],
                "avg_cost_usd": round(row["avg_cost"], 6),
                "avg_tokens": round(row["avg_tokens"], 2),
                "score": round(row["score"], 2),
            }
        )
    return rows


def _framework_benchmark_rows(journal: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in _framework_benchmark_pairs(journal):
        a = pair["a"]
        b = pair["b"]
        a_total = int(a["total"])
        b_total = int(b["total"])
        total = a_total + b_total
        a_success = int(a["success"])
        b_success = int(b["success"])
        ties = max(0, total - a_success - b_success)
        a_rate = a_success / a_total if a_total else 0.0
        b_rate = b_success / b_total if b_total else 0.0
        decisive_wins = a_success + b_success
        win_rate_b = b_success / decisive_wins if decisive_wins else 0.5

        if min(a_total, b_total) < 2:
            decision = "insufficient_data"
        elif abs(b_rate - a_rate) < 0.05:
            decision = "tie"
        elif b_rate > a_rate:
            decision = "prefer_b"
        else:
            decision = "prefer_a"

        row_id = _stable_int_id(f"framework:{pair['family_key']}:{a['label']}:{b['label']}")
        rows.append(
            {
                "id": row_id,
                "strategy_a": a["label"],
                "strategy_b": b["label"],
                "base_model": pair["base_model"],
                "strategy_family": pair["strategy_family"],
                "a_wins": a_success,
                "b_wins": b_success,
                "ties": ties,
                "total_tasks": total,
                "win_rate_b": round(win_rate_b, 4),
                "decision": decision,
                "a_assigned": a_total,
                "b_assigned": b_total,
                "a_success_rate": round(a_rate, 4),
                "b_success_rate": round(b_rate, 4),
                "a_avg_steps": round(float(a["steps"]) / a_total, 2) if a_total else 0.0,
                "b_avg_steps": round(float(b["steps"]) / b_total, 2) if b_total else 0.0,
                "last_seen": _iso(max(a["last_seen"], b["last_seen"])),
            }
        )

    rows.sort(
        key=lambda row: (
            str(row["base_model"]),
            str(row["strategy_family"]),
            -int(row["total_tasks"]),
            str(row["strategy_b"]),
        )
    )
    return rows[:100]


def _dispatch_snapshot(journal: Any) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for row in _framework_benchmark_rows(journal):
        key = hashlib.blake2b(
            (
                f"{row['base_model']}:{row['strategy_family']}:"
                f"{row['strategy_a']}:{row['strategy_b']}"
            ).encode(),
            digest_size=8,
        ).hexdigest()
        a_assigned = int(row.get("a_assigned", 0) or 0)
        b_assigned = int(row.get("b_assigned", 0) or 0)
        a_wins = int(row.get("a_wins", 0) or 0)
        b_wins = int(row.get("b_wins", 0) or 0)
        snapshot[key] = {
            "skill_name": str(row["base_model"]),
            "a_assigned": a_assigned,
            "b_assigned": b_assigned,
            "a_reported": a_assigned,
            "b_reported": b_assigned,
            "outcomes": {
                "a_success": a_wins,
                "a_failure": max(0, a_assigned - a_wins),
                "b_success": b_wins,
                "b_failure": max(0, b_assigned - b_wins),
                "b_win_rate_pct": int(round(float(row["win_rate_b"]) * 100)),
            },
        }
    return snapshot


def _framework_benchmark_pairs(journal: Any) -> list[dict[str, Any]]:
    families = _framework_benchmark_families(journal)
    pairs: list[dict[str, Any]] = []
    for family_key, family in families.items():
        buckets = list(family["buckets"].values())
        if len(buckets) < 2:
            continue
        buckets.sort(
            key=lambda bucket: (
                0 if bucket["label"] in {"baseline", "__default__", "default"} else 1,
                -int(bucket["total"]),
                str(bucket["label"]),
            )
        )
        anchor = buckets[0]
        for bucket in buckets[1:]:
            pairs.append(
                {
                    "family_key": family_key,
                    "base_model": family["base_model"],
                    "strategy_family": family["strategy_family"],
                    "a": anchor,
                    "b": bucket,
                }
            )
    return pairs


def _framework_benchmark_families(journal: Any) -> dict[str, dict[str, Any]]:
    families: dict[str, dict[str, Any]] = {}
    for event, traj in _trajectory_rows(journal):
        key_info = _trajectory_experiment_key(traj)
        if key_info is None:
            continue
        family_key, base_model, strategy_family, label = key_info
        fallback_ts = (
            _as_dt(getattr(traj, "completed_at", None))
            or _as_dt(getattr(event, "ts", None))
            or _utcnow()
        )
        family = families.setdefault(
            family_key,
            {
                "base_model": base_model,
                "strategy_family": strategy_family,
                "buckets": {},
            },
        )
        bucket = family["buckets"].setdefault(
            label,
            {
                "label": label,
                "total": 0,
                "success": 0,
                "steps": 0,
                "last_seen": fallback_ts,
            },
        )
        bucket["total"] += 1
        bucket["steps"] += len(getattr(traj, "steps", []) or [])
        outcome = getattr(traj, "outcome", None)
        if bool(getattr(outcome, "success", False)):
            bucket["success"] += 1
        if fallback_ts > bucket["last_seen"]:
            bucket["last_seen"] = fallback_ts
    return families


def _trajectory_experiment_key(traj: Any) -> tuple[str, str, str, str] | None:
    strategy_id = str(getattr(traj, "strategy_id", "") or "default").strip()
    if not strategy_id:
        strategy_id = "default"
    recipe_id = str(getattr(traj, "recipe_id", "") or "").strip()
    if recipe_id:
        base_recipe, variant = _split_recipe_variant(recipe_id)
        family_key = f"recipe:{strategy_id}:{base_recipe}"
        return family_key, base_recipe, strategy_id, variant
    return "strategy:journal", "journal", "strategy", strategy_id


def _split_recipe_variant(recipe_id: str) -> tuple[str, str]:
    if "#" not in recipe_id:
        return recipe_id, "baseline"
    base, variant = recipe_id.split("#", 1)
    base = base.strip() or "unknown_recipe"
    variant = variant.strip() or "baseline"
    return base, variant
