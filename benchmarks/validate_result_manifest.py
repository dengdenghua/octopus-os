"""Validate an authoritative behavioral result manifest against its artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _case_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    system = payload.get("system")
    if isinstance(system, dict) and isinstance(system.get("cases"), list):
        return [row for row in system["cases"] if isinstance(row, dict)]
    report = payload.get("report")
    if isinstance(report, dict) and isinstance(report.get("cases"), list):
        return [row for row in report["cases"] if isinstance(row, dict)]
    return []


def validate_manifest(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "echo.behavioral_result_manifest.v1":
        raise ValueError("unsupported result manifest schema")
    status = str(payload.get("status") or "")
    if status != "authoritative_assembled_case_results":
        raise ValueError(f"manifest is not authoritative: {status or 'missing status'}")
    expected_k = int(payload.get("k") or 0)
    artifact_root = path.parent / str(payload.get("artifact_root") or ".")
    cases = payload.get("cases")
    if expected_k < 1 or not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain k and non-empty cases")

    seen: set[str] = set()
    trial_passes = 0
    for entry in cases:
        if not isinstance(entry, dict):
            raise ValueError("manifest case must be an object")
        case_id = str(entry.get("id") or "")
        artifact_name = str(entry.get("artifact") or "")
        if not case_id or case_id in seen:
            raise ValueError(f"missing or duplicate case id: {case_id!r}")
        if artifact_name.endswith(".checkpoint.json"):
            raise ValueError(f"checkpoint cannot be authoritative: {artifact_name}")
        artifact = artifact_root / artifact_name
        if not artifact.is_file():
            raise ValueError(f"missing artifact: {artifact_name}")
        artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
        matches = [
            row
            for row in _case_rows(artifact_payload)
            if (row.get("id") or row.get("case_id")) == case_id
        ]
        if len(matches) != 1:
            raise ValueError(f"artifact does not uniquely contain {case_id}: {artifact_name}")
        row = matches[0]
        if int(row.get("k") or 0) != expected_k:
            raise ValueError(f"k mismatch for {case_id}")
        passes = int(row.get("passes") or 0)
        if passes != int(entry.get("passes") or 0):
            raise ValueError(f"pass count mismatch for {case_id}")
        trial_passes += passes
        seen.add(case_id)

    aggregate = payload.get("aggregate") or {}
    if int(aggregate.get("case_count") or 0) != len(seen):
        raise ValueError("aggregate case_count mismatch")
    if int(aggregate.get("trial_passes") or 0) != trial_passes:
        raise ValueError("aggregate trial_passes mismatch")
    if int(aggregate.get("trial_total") or 0) != len(seen) * expected_k:
        raise ValueError("aggregate trial_total mismatch")
    return {"cases": len(seen), "trial_passes": trial_passes, "k": expected_k}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    summary = validate_manifest(args.manifest)
    print(
        f"OK · {summary['cases']} cases · "
        f"{summary['trial_passes']}/{summary['cases'] * summary['k']} trials"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


