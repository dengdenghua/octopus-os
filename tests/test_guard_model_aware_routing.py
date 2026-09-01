"""Test model-aware guard routing integration."""

from runtime.core.cerebrum.react_guard_types import GuardContext
from runtime.core.cerebrum.react_guards import GuardSpec, evaluate_guards


def test_premium_model_skips_code_smell_guards():
    """Premium models (Opus, Sonnet) skip code-smell guards."""

    code_smell_fired = False

    def _code_smell_guard(ctx: GuardContext) -> str:
        nonlocal code_smell_fired
        code_smell_fired = True
        return "magic number detected"

    registry = [
        GuardSpec(
            label="magic-number",
            category="code-smell",
            invoke=_code_smell_guard,
            enabled=True,
        )
    ]

    ctx = GuardContext(
        steps=[],
        final_answer="return 42",
        is_code_mode=False,
        model="claude-opus-5",
    )

    result = evaluate_guards(ctx, registry=registry)

    # Guard should NOT fire for premium model
    assert result is None
    assert not code_smell_fired


def test_cheap_model_applies_code_smell_guards():
    """Cheap models (Haiku, Flash) trigger code-smell guards."""

    code_smell_fired = False

    def _code_smell_guard(ctx: GuardContext) -> str:
        nonlocal code_smell_fired
        code_smell_fired = True
        return "magic number detected"

    registry = [
        GuardSpec(
            label="magic-number",
            category="code-smell",
            invoke=_code_smell_guard,
            enabled=True,
        )
    ]

    ctx = GuardContext(
        steps=[],
        final_answer="return 42",
        is_code_mode=False,
        model="claude-haiku-4.5",
    )

    result = evaluate_guards(ctx, registry=registry)

    # Guard SHOULD fire for cheap model
    assert result == ("magic-number", "magic number detected")
    assert code_smell_fired


def test_security_guards_always_apply():
    """Security guards apply to all models regardless of tier."""

    security_fired = False

    def _security_guard(ctx: GuardContext) -> str:
        nonlocal security_fired
        security_fired = True
        return "unsafe operation detected"

    registry = [
        GuardSpec(
            label="unsafe-exec",
            category="security",
            invoke=_security_guard,
            enabled=True,
        )
    ]

    # Test premium model
    ctx_premium = GuardContext(
        steps=[],
        final_answer="exec(user_input)",
        is_code_mode=False,
        model="claude-opus-5",
    )

    result = evaluate_guards(ctx_premium, registry=registry)
    assert result == ("unsafe-exec", "unsafe operation detected")
    assert security_fired

    # Reset and test cheap model
    security_fired = False
    ctx_cheap = GuardContext(
        steps=[],
        final_answer="exec(user_input)",
        is_code_mode=False,
        model="claude-haiku-4.5",
    )

    result = evaluate_guards(ctx_cheap, registry=registry)
    assert result == ("unsafe-exec", "unsafe operation detected")
    assert security_fired


def test_unknown_model_conservative_applies_code_smell():
    """Unknown models conservatively apply code-smell guards."""

    code_smell_fired = False

    def _code_smell_guard(ctx: GuardContext) -> str:
        nonlocal code_smell_fired
        code_smell_fired = True
        return "long function detected"

    registry = [
        GuardSpec(
            label="long-function",
            category="code-smell",
            invoke=_code_smell_guard,
            enabled=True,
        )
    ]

    ctx = GuardContext(
        steps=[],
        final_answer="def foo():\n" + "    pass\n" * 100,
        is_code_mode=False,
        model="mystery-model-v1",
    )

    result = evaluate_guards(ctx, registry=registry)

    # Unknown model should conservatively apply guards
    assert result == ("long-function", "long function detected")
    assert code_smell_fired


def test_empty_model_string_treated_as_unknown():
    """Empty model string is treated as unknown, guards apply."""

    code_smell_fired = False

    def _code_smell_guard(ctx: GuardContext) -> str:
        nonlocal code_smell_fired
        code_smell_fired = True
        return "code smell"

    registry = [
        GuardSpec(
            label="test-guard",
            category="code-smell",
            invoke=_code_smell_guard,
            enabled=True,
        )
    ]

    ctx = GuardContext(
        steps=[],
        final_answer="code",
        is_code_mode=False,
        model="",  # Empty string
    )

    result = evaluate_guards(ctx, registry=registry)

    # Empty model should conservatively apply guards
    assert result == ("test-guard", "code smell")
    assert code_smell_fired


def test_research_guards_apply_with_model_set():
    """Research-grounding guards (citation / fact-grounding) survive the
    model-aware category filter.

    Regression for the salvage-path gap: react_terminal's forced-convergence
    call passes a non-empty model with no explicit categories, which used to
    collapse the category set to the always-on base (which omitted "research")
    and silently dropped research guards. The always-on base must include
    "research" — evaluate_guards' contract says salvage paths retain
    research-grounding gates.
    """

    research_fired = False

    def _citation_guard(ctx: GuardContext) -> str:
        nonlocal research_fired
        research_fired = True
        return "citation not fetched"

    registry = [
        GuardSpec(
            label="citation-grounding guard",
            category="research",
            invoke=_citation_guard,
            enabled=True,
        )
    ]

    # Premium model: code-smell skipped, but research must still run.
    ctx = GuardContext(
        steps=[],
        final_answer="see [example.com](http://example.com)",
        is_code_mode=False,
        model="claude-opus-5",
    )

    result = evaluate_guards(ctx, registry=registry)
    assert result == ("citation-grounding guard", "citation not fetched")
    assert research_fired


def test_multiple_categories_mixed_behavior():
    """Mixed categories: security always fires, code-smell only for cheap."""

    security_fired = False
    code_smell_fired = False

    def _security_guard(ctx: GuardContext) -> str:
        nonlocal security_fired
        security_fired = True
        return "security issue"

    def _code_smell_guard(ctx: GuardContext) -> str:
        nonlocal code_smell_fired
        code_smell_fired = True
        return "code smell"

    registry = [
        GuardSpec(
            label="security-guard",
            category="security",
            invoke=_security_guard,
            enabled=True,
        ),
        GuardSpec(
            label="code-smell-guard",
            category="code-smell",
            invoke=_code_smell_guard,
            enabled=True,
        ),
    ]

    # Premium model: only security fires
    ctx_premium = GuardContext(
        steps=[],
        final_answer="bad code",
        is_code_mode=False,
        model="claude-sonnet-5",
    )

    result = evaluate_guards(ctx_premium, registry=registry)
    assert result == ("security-guard", "security issue")
    assert security_fired
    assert not code_smell_fired  # Code-smell skipped for premium

    # Reset and test cheap model: both fire, security first
    security_fired = False
    code_smell_fired = False

    ctx_cheap = GuardContext(
        steps=[],
        final_answer="bad code",
        is_code_mode=False,
        model="gemini-flash-2.0",
    )

    result = evaluate_guards(ctx_cheap, registry=registry)
    assert result == ("security-guard", "security issue")
    assert security_fired
    # Code-smell would fire too but security has higher priority

