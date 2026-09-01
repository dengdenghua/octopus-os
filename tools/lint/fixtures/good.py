"""Implementation note."""


class Planner:
    """Engineering name, not bio."""

    def plan(self, goal):
        return goal


class Task:
    def __init__(self, goal, max_tokens, max_cost_usd):
        self.goal = goal


def make_safe_task():
    return Task(goal="hello", max_tokens=10_000, max_cost_usd=0.10)


def run_with_immunity(sucker, args):
    """Beak.bite() is reachable via immunity.check() in same function."""
    verdict = immunity.check(call=(sucker, args))  # noqa: F821  (fixture)
    if verdict == "allow":
        return beak.bite(sucker, args)  # noqa: F821
    return None
