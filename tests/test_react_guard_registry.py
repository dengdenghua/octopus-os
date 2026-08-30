"""Tests for the data-driven guard registry (evaluate_guards / GUARD_REGISTRY).

These verify the dispatch framework itself — that priority ordering is
preserved, that disabled specs are skipped, and that the security
cluster fires before quality guards. The individual guard behaviors
are covered by the per-guard test files; here we test the harness.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    GUARD_REGISTRY,
    GuardContext,
    GuardSpec,
    evaluate_guards,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    thought: str = "",
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=thought,
        action=action,
        observation=observation,
    )


class TestRegistryShape:
    def test_registry_nonempty(self) -> None:
        assert len(GUARD_REGISTRY) >= 35

    def test_all_labels_unique(self) -> None:
        labels = [spec.label for spec in GUARD_REGISTRY]
        assert len(labels) == len(set(labels))

    def test_security_cluster_first(self) -> None:
        # The first five specs must be the security guards, in order.
        first_five = [spec.label for spec in GUARD_REGISTRY[:5]]
        assert first_five == [
            "secret-leak guard",
            "destructive-call guard",
            "dynamic-exec guard",
            "shell-injection guard",
            "unsafe-deser guard",
        ]

    def test_completion_guard_last(self) -> None:
        assert GUARD_REGISTRY[-1].label == "code-mode guard"

    def test_every_spec_has_category(self) -> None:
        valid = {
            "security",
            "protocol",
            "verification",
            "test-quality",
            "code-smell",
            "research",
        }
        for spec in GUARD_REGISTRY:
            assert spec.category in valid, f"{spec.label} has bad category {spec.category}"


class TestEvaluateGuards:
    def test_clean_trajectory_returns_none(self) -> None:
        # A simple read-only inspection with a todo checklist completed.
        steps = [
            _step(
                1,
                action='todo_write({"items": [{"content": "Read", "status": "completed"}]})',
                observation="ok",
            ),
            _step(2, action='read_file({"path": "runtime/foo.py"})', observation="contents"),
        ]
        ctx = GuardContext(
            steps=steps,
            final_answer="Reviewed the file; no changes needed.",
            is_code_mode=False,  # non-code mode → only protocol guards apply
        )
        # Non-code-mode, todo complete → nothing fires.
        assert evaluate_guards(ctx) is None

    def test_secret_fires_first(self) -> None:
        # A trajectory that trips BOTH secret-leak (§34) and print-in-prod
        # (§44) must report the secret guard — higher priority.
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "print(x)\\nAPI_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        ctx = GuardContext(
            steps=steps,
            final_answer="done",
            is_code_mode=True,
        )
        hit = evaluate_guards(ctx)
        assert hit is not None
        label, _msg = hit
        assert label == "secret-leak guard"

    def test_disabled_spec_skipped(self) -> None:
        # Build a registry where the would-be-firing guard is disabled;
        # evaluate must fall through to the next firing guard or None.
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        ctx = GuardContext(steps=steps, final_answer="done", is_code_mode=True)
        # Sanity: with the real registry the secret guard fires.
        assert evaluate_guards(ctx)[0] == "secret-leak guard"
        # Now disable just that spec.
        patched = [
            GuardSpec(s.label, s.category, s.invoke, enabled=False)
            if s.label == "secret-leak guard"
            else s
            for s in GUARD_REGISTRY
        ]
        hit = evaluate_guards(ctx, registry=patched)
        # Either nothing else fires, or some lower-priority guard does —
        # but it must NOT be the disabled secret guard.
        if hit is not None:
            assert hit[0] != "secret-leak guard"

    def test_non_code_mode_still_runs_security(self) -> None:
        # Quality/code-style guards are mode-scoped, but credential leakage
        # must remain blocked in every mode.
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        ctx = GuardContext(
            steps=steps,
            final_answer="done",
            is_code_mode=False,
            todo_protocol_required=False,
        )
        hit = evaluate_guards(ctx)
        assert hit is not None
        assert hit[0] == "secret-leak guard"

    def test_empty_registry_returns_none(self) -> None:
        ctx = GuardContext(steps=[_step(1)], final_answer="done", is_code_mode=True)
        assert evaluate_guards(ctx, registry=[]) is None
