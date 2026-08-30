from __future__ import annotations

import sys

from benchmarks.eval_harness import EvalCase, run_case
from benchmarks.fixture_grading import IsolatedFixture, SubprocessOutcomeGrader


def test_isolated_fixture_is_graded_before_cleanup(tmp_path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "verify.py").write_text(
        """
import json
from pathlib import Path
passed = Path("answer.txt").read_text(encoding="utf-8") == "correct"
print(json.dumps({"passed": passed, "score": 1.0 if passed else 0.0, "checks": ["answer"]}))
""".strip(),
        encoding="utf-8",
    )
    fixture = IsolatedFixture(template=template, runs_root=tmp_path / "runs")
    rubric = {"grader": "fixture_tests", "checks": ["answer"]}
    grader = SubprocessOutcomeGrader(
        fixture=fixture,
        command=[sys.executable, "verify.py"],
        rubric=rubric,
    )
    workspaces = []

    def runner(_prompt):
        workspace = fixture.workspace()
        workspaces.append(workspace)
        (workspace / "answer.txt").write_text("correct", encoding="utf-8")
        yield {"kind": "text_delta", "delta": "done"}

    result = run_case(
        EvalCase(
            id="fixture",
            prompt="work",
            grader=grader,
            setup=fixture.setup,
            teardown=fixture.teardown,
        ),
        runner=runner,
        k=3,
    )

    assert result.passes == 3
    assert len(set(workspaces)) == 3
    assert all(not workspace.exists() for workspace in workspaces)
    assert result.verdicts[0].rubric["observed_checks"] == ["answer"]


def test_subprocess_outcome_grader_fails_closed_on_invalid_json(tmp_path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "verify.py").write_text("print('not-json')", encoding="utf-8")
    fixture = IsolatedFixture(template=template, runs_root=tmp_path / "runs")
    fixture.setup()
    try:
        verdict = SubprocessOutcomeGrader(
            fixture=fixture,
            command=[sys.executable, "verify.py"],
            rubric={"grader": "fixture_tests"},
        )(None)  # type: ignore[arg-type]
    finally:
        fixture.teardown()

    assert verdict.passed is False
    assert "not JSON" in verdict.reason

