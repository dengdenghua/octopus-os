from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.safety.recovery import gepa_bridge
from runtime.safety.recovery.optimizer_backends import (
    OptimizerRunConfig,
    available_optimizer_backends,
    get_optimizer_backend,
    optimize_with_backend,
)


def test_optimizer_backend_registry_exposes_supported_slots() -> None:
    names = {row["name"] for row in available_optimizer_backends()}

    assert {"native_gepa", "dspy_gepa", "external_gepa"} <= names
    assert get_optimizer_backend("gepa").name == "native_gepa"
    assert get_optimizer_backend("dspy").name == "dspy_gepa"


def test_unknown_optimizer_backend_lists_supported_names() -> None:
    with pytest.raises(ValueError) as exc:
        get_optimizer_backend("mystery")

    text = str(exc.value)
    assert "unknown optimizer backend" in text
    assert "native_gepa" in text


def test_native_backend_delegates_to_existing_gepa_bridge(monkeypatch) -> None:
    captured: dict[str, object] = {}
    fake_result = SimpleNamespace()

    def fake_optimize_for_recipe(**kwargs):
        captured.update(kwargs)
        return fake_result

    monkeypatch.setattr(gepa_bridge, "optimize_for_recipe", fake_optimize_for_recipe)

    result = optimize_with_backend(
        seed_prompt="seed prompt",
        journal=object(),
        router=object(),
        config=OptimizerRunConfig(
            backend="native_gepa",
            recipe_id="planner/main",
            judge_model="judge",
            mutator_model="mutator",
            n_iter=3,
            eval_tasks=2,
            ledger_path="ledger.jsonl",
            trigger="test",
            record_winner=False,
        ),
    )

    assert result is fake_result
    assert result.optimizer_backend == "native_gepa"
    assert captured["seed_prompt"] == "seed prompt"
    assert captured["recipe_id"] == "planner/main"
    assert captured["judge_model"] == "judge"
    assert captured["mutator_model"] == "mutator"
    assert captured["n_iter"] == 3
    assert captured["eval_tasks"] == 2
    assert captured["ledger_path"] == "ledger.jsonl"
    assert captured["trigger"] == "test"
    assert captured["record_winner"] is False
