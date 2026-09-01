"""Fixture that MUST trigger LINT-05."""

# Bring the pipeline Task into scope — the rule only fires on files
# that import ``runtime.platform.models.Task``, so the fixture has to
# do the same (a local Task class with no budget contract is out of
# scope by design).
from runtime.platform.models import Task  # noqa: F401

# A stand-in ``Task`` constructor below uses the name ``Task`` that the
# import above brought in. The rule inspects call sites, not types; the
# missing budget kwargs are what it flags.


def create_demo_task() -> object:
    t = Task(goal="do something")  # type: ignore[call-arg]  # ← should trigger LINT-05
    return t


def create_good_task() -> object:
    t = Task(  # type: ignore[call-arg]
        goal="do something",
        max_tokens=50_000,
        max_cost_usd=0.50,
    )
    return t
