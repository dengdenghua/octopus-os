"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.execution.suckers import (
    TIER_THRESHOLDS,
    Skill,
    SkillExpect,
    SkillRegistry,
    SkillTestCase,
    SkillTester,
    SkillTestsFailed,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAssertions:
    def test_output_equals_pass(self):
        s = Skill(
            name="echo_one",
            trusted_source="skill://test/echo_one",
            handler=lambda **kw: 1,
            tests=[
                SkillTestCase(
                    name="returns_one",
                    args={},
                    expect=SkillExpect(output_equals=1),
                )
            ],
        )
        report = SkillTester().run(s)
        assert report.overall_passed is True
        assert report.passed_count == 1

    def test_output_equals_fail(self):
        s = Skill(
            name="echo_zero",
            trusted_source="skill://test/x",
            handler=lambda **kw: 0,
            tests=[
                SkillTestCase(
                    name="should_be_one",
                    args={},
                    expect=SkillExpect(output_equals=1),
                )
            ],
        )
        report = SkillTester().run(s)
        assert not report.overall_passed
        assert "expected" in report.failed[0].reason.lower() or "got" in report.failed[0].reason

    def test_schema_keys(self):
        s = Skill(
            name="dict_output",
            trusted_source="skill://test/x",
            handler=lambda **kw: {"a": 1, "b": 2},
            tests=[
                SkillTestCase(
                    name="has_a_b",
                    args={},
                    expect=SkillExpect(schema_keys=["a", "b"]),
                ),
                SkillTestCase(
                    name="missing_c",
                    args={},
                    expect=SkillExpect(schema_keys=["c"]),
                ),
            ],
        )
        report = SkillTester().run(s)
        assert report.passed_count == 1

    def test_output_contains(self):
        s = Skill(
            name="hello",
            trusted_source="skill://test/x",
            handler=lambda **kw: "hello world foo",
            tests=[
                SkillTestCase(
                    name="has_hello_foo",
                    args={},
                    expect=SkillExpect(output_contains=["hello", "foo"]),
                ),
                SkillTestCase(
                    name="has_bar",
                    args={},
                    expect=SkillExpect(output_contains=["bar"]),
                ),
            ],
        )
        report = SkillTester().run(s)
        assert report.passed_count == 1

    def test_raises_expected_exception(self):
        s = Skill(
            name="boom",
            trusted_source="skill://test/x",
            handler=lambda **kw: (_ for _ in ()).throw(ValueError("boom")),
            tests=[
                SkillTestCase(
                    name="raises_value_error",
                    args={},
                    expect=SkillExpect(raises="ValueError"),
                ),
                SkillTestCase(
                    name="wrong_expected_type",
                    args={},
                    expect=SkillExpect(raises="KeyError"),
                ),
            ],
        )
        report = SkillTester().run(s)
        assert report.passed_count == 1

    def test_unexpected_exception_fails(self):
        s = Skill(
            name="boom",
            trusted_source="skill://test/x",
            handler=lambda **kw: (_ for _ in ()).throw(RuntimeError("x")),
            tests=[
                SkillTestCase(
                    name="no_exception_expected",
                    args={},
                    expect=SkillExpect(output_equals=1),
                )
            ],
        )
        report = SkillTester().run(s)
        assert not report.overall_passed
        assert "unexpected exception" in report.failed[0].reason

    def test_custom_predicate(self):
        s = Skill(
            name="num",
            trusted_source="skill://test/x",
            handler=lambda **kw: 42,
            tests=[
                SkillTestCase(
                    name="is_42",
                    args={},
                    custom_predicate=lambda out: out == 42,
                ),
                SkillTestCase(
                    name="is_neg",
                    args={},
                    custom_predicate=lambda out: out < 0,
                ),
            ],
        )
        report = SkillTester().run(s)
        assert report.passed_count == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTierThresholds:
    def test_thresholds_correct(self):
        assert TIER_THRESHOLDS["golden"] == 1.00
        assert TIER_THRESHOLDS["regression"] == 0.95
        assert TIER_THRESHOLDS["synthesized"] == 0.80

    def test_golden_100_percent_required(self):
        """Implementation note."""
        s = Skill(
            name="mostly_ok",
            trusted_source="skill://test/x",
            handler=lambda x=None, **kw: 1 if x == "a" else 2,
            tests=[
                SkillTestCase(
                    name="a_returns_1",
                    tier="golden",
                    args={"x": "a"},
                    expect=SkillExpect(output_equals=1),
                ),
                SkillTestCase(
                    name="b_returns_1",
                    tier="golden",
                    args={"x": "b"},
                    expect=SkillExpect(output_equals=1),  # Implementation note.
                ),
            ],
        )
        report = SkillTester().run(s)
        assert report.tier_pass_rates["golden"] == 0.5
        assert not report.overall_passed

    def test_regression_allows_95_percent(self):
        """Implementation note."""

        def handler(**kw):
            idx = kw.get("idx", 0)
            return "ok" if idx < 95 else "fail"

        tests = [
            SkillTestCase(
                name=f"case_{i}",
                tier="regression",
                args={"idx": i},
                expect=SkillExpect(output_equals="ok"),
            )
            for i in range(100)
        ]
        s = Skill(
            name="regression_test",
            trusted_source="skill://test/x",
            handler=handler,
            tests=tests,
        )
        report = SkillTester().run(s)
        assert report.tier_pass_rates["regression"] == 0.95
        # Implementation note.
        assert report.overall_passed

    def test_synthesized_allows_80_percent(self):
        def handler(**kw):
            return kw.get("i", 0) < 80

        tests = [
            SkillTestCase(
                name=f"s_{i}",
                tier="synthesized",
                args={"i": i},
                expect=SkillExpect(output_equals=True),
            )
            for i in range(100)
        ]
        s = Skill(
            name="s",
            trusted_source="skill://test/x",
            handler=handler,
            tests=tests,
        )
        report = SkillTester().run(s)
        assert report.tier_pass_rates["synthesized"] == 0.80
        assert report.overall_passed


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRegistryIntegration:
    def test_register_good_skill_succeeds(self):
        r = SkillRegistry()
        s = Skill(
            name="sum2",
            trusted_source="skill://test/sum2",
            handler=lambda a, b, **kw: a + b,
            tests=[
                SkillTestCase(
                    name="one_plus_one",
                    args={"a": 1, "b": 1},
                    expect=SkillExpect(output_equals=2),
                ),
            ],
        )
        report = r.register(s)
        assert report is not None
        assert report.overall_passed
        assert r.has("sum2")
        assert r.last_test_report("sum2") is report

    def test_register_bad_skill_raises(self):
        """Implementation note."""
        r = SkillRegistry()
        s = Skill(
            name="broken",
            trusted_source="skill://test/broken",
            handler=lambda **kw: 0,
            tests=[
                SkillTestCase(
                    name="should_be_one",
                    args={},
                    expect=SkillExpect(output_equals=1),
                ),
            ],
        )
        with pytest.raises(SkillTestsFailed):
            r.register(s)
        assert not r.has("broken")

    def test_verify_tests_false_bypass(self):
        """Implementation note."""
        r = SkillRegistry()
        bad = Skill(
            name="bad",
            trusted_source="skill://test/bad",
            handler=lambda **kw: 0,
            tests=[
                SkillTestCase(
                    name="fails_always",
                    args={},
                    expect=SkillExpect(output_equals=1),
                )
            ],
        )
        r.register(bad, verify_tests=False)
        assert r.has("bad")

    def test_duplicate_skill_name_rejected_by_default(self):
        r = SkillRegistry()
        first = Skill(
            name="same",
            trusted_source="builtin://same",
            handler=lambda **kw: "first",
        )
        second = Skill(
            name="same",
            trusted_source="mcp://srv/same",
            handler=lambda **kw: "second",
        )

        r.register(first, verify_tests=False)
        with pytest.raises(ValueError, match="duplicate skill name"):
            r.register(second, verify_tests=False)
        assert r.get("same").trusted_source == "builtin://same"

    def test_duplicate_skill_name_can_be_explicitly_replaced(self):
        r = SkillRegistry()
        r.register(
            Skill(
                name="same",
                trusted_source="builtin://same",
                handler=lambda **kw: "first",
            ),
            verify_tests=False,
        )
        r.register(
            Skill(
                name="same",
                trusted_source="skill://trusted/same",
                handler=lambda **kw: "second",
            ),
            verify_tests=False,
            replace=True,
        )
        assert r.get("same").trusted_source == "skill://trusted/same"

    def test_skill_without_tests_allowed_in_default_mode(self):
        """Implementation note."""
        r = SkillRegistry()
        s = Skill(
            name="notest",
            trusted_source="skill://test/notest",
            handler=lambda **kw: 1,
        )
        report = r.register(s)
        assert report is None
        assert r.has("notest")

    def test_strict_mode_blocks_skill_without_tests(self):
        """Implementation note."""
        r = SkillRegistry(strict_mode=True)
        s = Skill(
            name="notest",
            trusted_source="skill://test/notest",
            handler=lambda **kw: 1,
        )
        with pytest.raises(SkillTestsFailed):
            r.register(s)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBuiltinsPassTheirOwnTests:
    def test_all_builtins_pass_golden_tests(self):
        """Implementation note."""
        from runtime.execution.suckers.builtins import BUILTIN_NAMES, register_builtins

        r = SkillRegistry()
        register_builtins(r)
        for name in BUILTIN_NAMES:
            report = r.last_test_report(name)
            # Implementation note.
            if report is not None:
                assert report.overall_passed, (
                    f"builtin {name!r} failed its golden tests: {report.failed}"
                )
