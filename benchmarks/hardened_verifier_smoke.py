"""Provider-free, full-chain smoke for the attested coding verifier.

The ordinary provenance probe validates identities and kernel facilities, but it
does not launch the trusted controller, host supervisor, or isolated candidate
API process.  This module supplies two evaluator-owned known-good candidates so
the complete path and cache verifier chains can be exercised before any model
turn is allowed to start.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

from benchmarks.verifier_sandbox import (
    FixtureInfrastructureError,
    VerifierProcessResult,
    run_hidden_verifier,
)

FULL_CHAIN_SMOKE_SCHEMA = "echo.hardened_verifier_full_chain_smoke.v1"
FULL_CHAIN_SMOKE_VERIFIER_NAMES = frozenset(
    {"verify_concurrent_cache.py", "verify_path_boundary.py"}
)

_TIMEOUT_SECONDS = 60.0
_PYPROJECT_SOURCE = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
_TEST_SOURCE = "def test_evaluator_owned_smoke_regression():\n    assert True\n"
_PATH_SOURCE = """\
from pathlib import Path
from urllib.parse import unquote

class PathBoundaryError(ValueError):
    pass

class FileService:
    def __init__(self, root):
        self.root = Path(root)

    def read_text(self, user_path):
        decoded = user_path
        for _ in range(4):
            updated = unquote(decoded)
            if updated == decoded:
                break
            decoded = updated
        root = self.root.resolve()
        candidate = (root / decoded).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathBoundaryError("path escapes root") from exc
        return candidate.read_text(encoding="utf-8")
"""
_CACHE_SOURCE = """\
import threading

class TTLCache:
    def __init__(self, ttl_seconds, *, clock):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.values = {}
        self.loading = {}
        self.lock = threading.Lock()

    def get_or_load(self, key, loader):
        while True:
            with self.lock:
                cached = self.values.get(key)
                if cached is not None and cached[0] > self.clock():
                    return cached[1]
                event = self.loading.get(key)
                if event is None:
                    event = threading.Event()
                    self.loading[key] = event
                    break
            event.wait()
        try:
            value = loader()
        except BaseException:
            with self.lock:
                self.loading.pop(key).set()
            raise
        with self.lock:
            self.values[key] = (self.clock() + self.ttl_seconds, value)
            self.loading.pop(key).set()
        return value
"""

SmokeFailureCategory = Literal[
    "runner_infrastructure",
    "known_good_rejected",
    "invalid_controller_output",
]


class HardenedVerifierFullChainSmokeError(FixtureInfrastructureError):
    """The full verifier path could not accept an evaluator-owned candidate."""

    def __init__(
        self,
        *,
        case_id: str,
        category: SmokeFailureCategory,
        detail: str,
    ) -> None:
        self.case_id = case_id
        self.category = category
        self.detail = detail[:4000]
        super().__init__(
            f"hardened verifier full-chain smoke {category} for {case_id}: {self.detail}"
        )


@dataclass(frozen=True, slots=True)
class _SmokeCase:
    case_id: str
    verifier_name: str
    module_name: str
    test_name: str
    candidate_source: str
    reason: str
    checks: tuple[str, ...]


_CASES = (
    _SmokeCase(
        case_id="coding.path-boundary",
        verifier_name="verify_path_boundary.py",
        module_name="file_service.py",
        test_name="tests/test_file_service.py",
        candidate_source=_PATH_SOURCE,
        reason="all path-boundary outcomes pass",
        checks=(
            "no unrelated diff",
            "public API unchanged",
            "focused tests added",
            "valid nested path preserved",
            "plain traversal rejected",
            "encoded traversal rejected",
            "double-encoded traversal rejected",
            "symlink escape rejected",
            "controller-owned randomized outcomes validated",
        ),
    ),
    _SmokeCase(
        case_id="coding.concurrent-cache",
        verifier_name="verify_concurrent_cache.py",
        module_name="cache.py",
        test_name="tests/test_cache.py",
        candidate_source=_CACHE_SOURCE,
        reason="all cache outcomes pass",
        checks=(
            "no unrelated diff",
            "public API unchanged",
            "focused tests added",
            "same-key concurrent loads coalesced",
            "live cached value reused",
            "TTL expiry enforced",
            "exceptions are not cached",
            "controller-owned randomized outcomes validated",
        ),
    ),
)


def run_hardened_verifier_full_chain_smoke(repo_root: str | Path) -> dict[str, Any]:
    """Run both fixed known-good candidates through the real attested chain.

    The workspaces are private, disposable evaluator inputs.  No result from
    this function is an engine measurement: even a candidate-shaped rejection
    is infrastructure-invalid because the candidate bytes are fixed here.
    """

    active_case_id = "full-chain"
    try:
        root = Path(repo_root).resolve(strict=True)
        verifier_root = root / "benchmarks" / "verifiers"
        rows: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="echo-hardened-verifier-smoke-") as temporary:
            smoke_root = Path(temporary).resolve(strict=True)
            for case in _CASES:
                active_case_id = case.case_id
                workspace = smoke_root / case.case_id
                _write_workspace(workspace, case)
                verifier = verifier_root / case.verifier_name
                verifier_sha256 = hashlib.sha256(verifier.read_bytes()).hexdigest()
                try:
                    completed = run_hidden_verifier(
                        verifier_source=verifier,
                        argument_templates=("{workspace}",),
                        workspace=workspace,
                        timeout_seconds=_TIMEOUT_SECONDS,
                        expected_source_sha256=verifier_sha256,
                    )
                except FixtureInfrastructureError as exc:
                    raise HardenedVerifierFullChainSmokeError(
                        case_id=case.case_id,
                        category="runner_infrastructure",
                        detail=str(exc),
                    ) from exc
                except Exception as exc:
                    raise HardenedVerifierFullChainSmokeError(
                        case_id=case.case_id,
                        category="runner_infrastructure",
                        detail=f"{type(exc).__name__}: {exc}",
                    ) from exc
                payload = _validated_controller_result(completed, case=case)
                rows.append(
                    {
                        "candidate_sha256": hashlib.sha256(
                            case.candidate_source.encode("utf-8")
                        ).hexdigest(),
                        "case_id": case.case_id,
                        "checks": payload["checks"],
                        "passed": True,
                        "verifier_sha256": verifier_sha256,
                    }
                )
    except HardenedVerifierFullChainSmokeError:
        raise
    except Exception as exc:
        raise HardenedVerifierFullChainSmokeError(
            case_id=active_case_id,
            category="runner_infrastructure",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    return {
        "schema": FULL_CHAIN_SMOKE_SCHEMA,
        "passed": True,
        "cases": rows,
    }


def _write_workspace(workspace: Path, case: _SmokeCase) -> None:
    (workspace / "tests").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(_PYPROJECT_SOURCE, encoding="utf-8")
    (workspace / case.module_name).write_text(case.candidate_source, encoding="utf-8")
    (workspace / case.test_name).write_text(_TEST_SOURCE, encoding="utf-8")


def _validated_controller_result(
    completed: VerifierProcessResult,
    *,
    case: _SmokeCase,
) -> dict[str, Any]:
    if completed.returncode != 0 or completed.timed_out:
        _invalid(case, "trusted controller did not exit cleanly")
    if completed.stderr:
        _invalid(case, "trusted controller emitted stderr")
    lines = completed.stdout.splitlines()
    if len(lines) != 1 or not lines[0].strip():
        _invalid(case, "trusted controller must emit exactly one JSON line")
    try:
        payload = json.loads(
            lines[0],
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (RecursionError, UnicodeError, ValueError) as exc:
        _invalid(case, f"trusted controller JSON is invalid: {exc}")
    if not isinstance(payload, dict) or set(payload) != {"checks", "passed", "reason", "score"}:
        _invalid(case, "trusted controller result shape is invalid")
    if payload["passed"] is False:
        raise HardenedVerifierFullChainSmokeError(
            case_id=case.case_id,
            category="known_good_rejected",
            detail=str(payload["reason"]),
        )
    if payload["passed"] is not True:
        _invalid(case, "trusted controller passed field is not boolean")
    if type(payload["score"]) not in {int, float} or float(payload["score"]) != 1.0:
        _invalid(case, "trusted controller did not award the full fixed score")
    if payload["reason"] != case.reason or payload["checks"] != list(case.checks):
        _invalid(case, "trusted controller checks changed or are incomplete")
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON value: {value}")


def _invalid(case: _SmokeCase, detail: str) -> NoReturn:
    raise HardenedVerifierFullChainSmokeError(
        case_id=case.case_id,
        category="invalid_controller_output",
        detail=detail,
    )


__all__ = [
    "FULL_CHAIN_SMOKE_SCHEMA",
    "FULL_CHAIN_SMOKE_VERIFIER_NAMES",
    "HardenedVerifierFullChainSmokeError",
    "run_hardened_verifier_full_chain_smoke",
]


