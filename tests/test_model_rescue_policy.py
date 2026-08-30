from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from runtime.platform.models import rescue_policy as policy
from runtime.sensing.model_router.rescue_policy import (
    is_retryable_model_error,
    next_custom_model_fallback,
    note_model_stall,
)


def test_retryable_model_error_covers_capacity_and_transport_failures() -> None:
    assert is_retryable_model_error(RuntimeError("http_429: rate limit exceeded"))
    assert is_retryable_model_error(TimeoutError("upstream timeout"))
    assert is_retryable_model_error(ConnectionError("connection reset by peer"))
    assert not is_retryable_model_error(ValueError("invalid request schema"))


def test_custom_model_fallback_prefers_strongest_untried_tool_model(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "custom_models.json"
    config_path.write_text(
        json.dumps(
            {
                "slow": {
                    "models": ["agnes-2.0-flash"],
                    "supports_tool_use": True,
                },
                "fast": {
                    "models": ["kimi-k2.7-code", "small-chat"],
                    "supports_tool_use": True,
                },
                "text-only": {
                    "models": ["reasoning-pro"],
                    "supports_tool_use": False,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "runtime.platform.process.paths.app_paths",
        lambda: SimpleNamespace(custom_models_path=config_path),
    )

    assert (
        next_custom_model_fallback(
            "agnes-2.0-flash",
            {"agnes-2.0-flash"},
        )
        == "kimi-k2.7-code"
    )
    assert (
        next_custom_model_fallback(
            "agnes-2.0-flash",
            {"agnes-2.0-flash", "kimi-k2.7-code"},
            require_tool_use=False,
        )
        == "reasoning-pro"
    )


@pytest.fixture(autouse=True)
def _clear_stall_memory() -> None:
    policy._recent_stall_expiry.clear()
    yield
    policy._recent_stall_expiry.clear()


def test_custom_model_fallback_prefers_a_different_upstream_over_name_score(
    monkeypatch,
    tmp_path,
) -> None:
    """A same-host "code" model must not beat a different-host "flash" model.

    Regression for the death spiral: name-only scoring ranked a sibling on the
    same slow upstream (e.g. another doubao model) above a genuinely different
    provider, so the "failover" routed straight back to the same hung backend.
    """
    config_path = tmp_path / "custom_models.json"
    config_path.write_text(
        json.dumps(
            {
                "kimi-k3": {
                    "models": ["kimi-k3"],
                    "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
                    "supports_tool_use": True,
                },
                "ark-code-latest": {
                    "models": ["ark-code-latest"],
                    "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
                    "supports_tool_use": True,
                },
                "agnes-flash": {
                    "models": ["agnes-2.5-flash"],
                    "base_url": "https://apihub.agnes-ai.com/v1",
                    "supports_tool_use": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "runtime.platform.process.paths.app_paths",
        lambda: SimpleNamespace(custom_models_path=config_path),
    )

    assert next_custom_model_fallback("kimi-k3", {"kimi-k3"}) == "agnes-2.5-flash"


def test_custom_model_fallback_uses_same_upstream_as_last_resort(
    monkeypatch,
    tmp_path,
) -> None:
    """With no alternative provider, still switch model tier on the same host."""
    config_path = tmp_path / "custom_models.json"
    config_path.write_text(
        json.dumps(
            {
                "kimi-k3": {
                    "models": ["kimi-k3"],
                    "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
                    "supports_tool_use": True,
                },
                "ark-code-latest": {
                    "models": ["ark-code-latest"],
                    "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
                    "supports_tool_use": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "runtime.platform.process.paths.app_paths",
        lambda: SimpleNamespace(custom_models_path=config_path),
    )

    assert next_custom_model_fallback("kimi-k3", {"kimi-k3"}) == "ark-code-latest"


def test_custom_model_fallback_skips_a_recently_stalled_model(
    monkeypatch,
    tmp_path,
) -> None:
    """Cross-turn escalation: a model that just stalled is not re-selected."""
    config_path = tmp_path / "custom_models.json"
    config_path.write_text(
        json.dumps(
            {
                "primary": {
                    "models": ["primary-model"],
                    "base_url": "https://a.example/v1",
                    "supports_tool_use": True,
                },
                "slow-sibling": {
                    "models": ["slow-sibling"],
                    "base_url": "https://b.example/v1",
                    "supports_tool_use": True,
                },
                "healthy": {
                    "models": ["healthy-model"],
                    "base_url": "https://c.example/v1",
                    "supports_tool_use": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "runtime.platform.process.paths.app_paths",
        lambda: SimpleNamespace(custom_models_path=config_path),
    )

    # Without the stall memory this returns "slow-sibling" (first untried
    # different-upstream candidate by insertion order); after it stalls once,
    # the next turn must skip straight past it.
    note_model_stall("slow-sibling")
    assert next_custom_model_fallback("primary-model", {"primary-model"}) == "healthy-model"


def test_stall_memory_expires(monkeypatch) -> None:
    note_model_stall("doomed-model", now=1000.0, ttl_s=10.0)
    assert policy._recently_stalled_models(now=1005.0) == {"doomed-model"}
    assert policy._recently_stalled_models(now=1011.0) == set()

