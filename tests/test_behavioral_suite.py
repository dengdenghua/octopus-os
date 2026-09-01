from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from benchmarks.behavioral_suite import load_behavioral_suite
from benchmarks.eval_harness import Verdict, run_suite
from benchmarks.run_behavioral_suite import _load_and_bind_provenance


def test_load_behavioral_suite_binds_outcome_grader(tmp_path) -> None:
    manifest = tmp_path / "suite.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "echo.behavioral_surpass_suite.v1",
                "suite_id": "test-suite",
                "cases": [
                    {
                        "id": "exact",
                        "domain": "general_runtime_and_coding",
                        "execution_mode": "real_provider",
                        "allowed_write_paths": ["answer.py", "tests/test_answer.py"],
                        "prompt": "say hello",
                        "rubric": {"grader": "exact_text", "expected": "hello"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cases = load_behavioral_suite(
        manifest,
        grader_factories={
            "exact_text": lambda _case_id, rubric: (
                lambda trajectory: Verdict(
                    passed=trajectory.last_text() == rubric["expected"],
                    score=1.0 if trajectory.last_text() == rubric["expected"] else 0.0,
                    reason="exact text",
                    rubric=rubric,
                )
            )
        },
    )
    report = run_suite(
        cases,
        runner=lambda _prompt: iter([{"kind": "text_delta", "delta": "hello"}]),
        k=3,
    )

    assert report.aggregate_pass_pow_k == 1.0
    assert cases[0].metadata["outcome_grader"] is True
    assert cases[0].metadata["allowed_write_paths"] == [
        "answer.py",
        "tests/test_answer.py",
    ]
    assert len(cases[0].metadata["rubric_digest"]) == 64


def test_load_behavioral_suite_refuses_missing_grader(tmp_path) -> None:
    manifest = tmp_path / "suite.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "echo.behavioral_surpass_suite.v1",
                "suite_id": "test-suite",
                "cases": [
                    {
                        "id": "ungraded",
                        "domain": "general_runtime_and_coding",
                        "execution_mode": "real_provider",
                        "prompt": "do work",
                        "rubric": {"grader": "missing"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no grader factory"):
        load_behavioral_suite(manifest, grader_factories={})


def test_release_provenance_is_bound_to_requested_model_and_config_bytes(tmp_path) -> None:
    config = tmp_path / "behavioral.yaml"
    config.write_text("models: {}\n", encoding="utf-8")
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "schema": "echo.behavioral_system_provenance.v1",
                "system_id": "echo",
                "model": {"expected": "fixed-model", "requested": "fixed-model"},
                "config": {"expected_sha256": digest, "observed_sha256": digest},
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        provenance_file=provenance_path,
        system="echo",
        model="fixed-model",
        echo_config_path=config,
        codex_surface="desktop",
        codex_executable="unused",
    )

    assert _load_and_bind_provenance(args)["model"]["requested"] == "fixed-model"

    args.model = "substituted-model"
    with pytest.raises(ValueError, match="--model must exactly match"):
        _load_and_bind_provenance(args)
    args.model = "fixed-model"
    config.write_text("models: {changed: true}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="config changed"):
        _load_and_bind_provenance(args)



