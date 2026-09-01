"""Trusted-controller verifier for ``coding.concurrent-cache``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmarks.trusted_verifier_controller import (
    INFRASTRUCTURE_EXIT,
    WorkerInfrastructureError,
    WorkerLauncher,
    evaluate_concurrent_cache,
)


def _run(workspace: Path, *, launcher: WorkerLauncher | None = None) -> dict[str, object]:
    return evaluate_concurrent_cache(workspace, launcher=launcher)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_concurrent_cache.py WORKSPACE", file=sys.stderr)
        return INFRASTRUCTURE_EXIT
    try:
        result = _run(Path(sys.argv[1]))
    except WorkerInfrastructureError as exc:
        print(f"trusted verifier infrastructure invalid: {exc}", file=sys.stderr)
        return INFRASTRUCTURE_EXIT
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

