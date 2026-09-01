"""Implementation note."""

from __future__ import annotations

from pathlib import Path

from tools.lint.invariant_check import (
    ALL_RULES,
    lint_tree,
)

FIXTURES = Path(__file__).parent.parent / "tools" / "lint" / "fixtures"


class TestFixturesTriggerExpectedRules:
    def test_good_fixture_has_no_issues(self):
        issues = lint_tree(FIXTURES / "good.py", ALL_RULES)
        # Implementation note.
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], f"good.py should have no errors, got: {errors}"

    def test_bad_bio_name_triggers_lint_03(self):
        issues = lint_tree(FIXTURES / "bad_bio_name.py", ALL_RULES)
        rule_ids = {i.rule_id for i in issues}
        assert "LINT-03" in rule_ids

    def test_bad_raw_llm_triggers_lint_04(self):
        issues = lint_tree(FIXTURES / "bad_raw_llm.py", ALL_RULES)
        rule_ids = {i.rule_id for i in issues}
        assert "LINT-04" in rule_ids

    def test_bad_task_no_budget_triggers_lint_05(self):
        issues = lint_tree(FIXTURES / "bad_task_no_budget.py", ALL_RULES)
        rule_ids = {i.rule_id for i in issues}
        assert "LINT-05" in rule_ids


class TestSelfLint:
    """Implementation note."""

    def test_package_is_clean(self):
        pkg = Path(__file__).parent.parent / "runtime"
        assert pkg.is_dir()
        issues = lint_tree(pkg, ALL_RULES)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], "runtime/ must pass its own lint. Found: " + "\n".join(
            i.format() for i in errors
        )


class TestImportDirectionRatchet:
    """Locks the import-direction ratchet: baseline must match reality
    (catches a stale baseline locally, not just in CI) and the rule
    logic must actually classify wrong-way edges.
    """

    def test_baseline_is_green(self):
        import subprocess
        import sys

        repo = Path(__file__).parent.parent
        r = subprocess.run(
            [sys.executable, "tools/lint/import_direction_check.py", "--strict"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, (
            "import-direction baseline drifted from reality:\n" + r.stdout + r.stderr
        )

    def test_rule_classifies_edges(self):
        from tools.lint.import_direction_check import _normalize, _violation

        # runtime.* prefix stripped; non-runtime imports ignored.
        assert _normalize("runtime.sensing.gateway.x") == "sensing.gateway.x"
        assert _normalize("os.path") is None

        # Kernel reaching into the web/router layer is a violation.
        assert _violation("core", "sensing.gateway.workflow_editor_router")
        assert _violation("safety", "sensing.model_router")
        # The shared primitives base is always allowed.
        assert _violation("core", "platform.models.primitives") is None
        # platform is the composition root — not a forbidden source.
        assert _violation("platform", "sensing.gateway.cron_router") is None
        # A normal sibling import within allowed direction is fine.
        assert _violation("execution", "core.graph_runtime") is None
