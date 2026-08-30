"""Tests for three-tier turn complexity classifier + smart model routing.

Tiers:
  - local       — local model (Ollama / LM Studio / vLLM)
  - value       — cheap cloud (glm-flash / haiku / 4o-mini)
  - performance — frontier cloud (sonnet / opus / gpt-5)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from runtime.core.cerebrum.turn_complexity import (
    _resolve_tier_model,
    estimate_turn_complexity,
    get_tier_config,
    is_smart_routing_enabled,
    select_model_for_complexity,
)


@pytest.fixture(autouse=True)
def _isolate_custom_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tier resolution falls back to the first ``custom_models.json``
    entry when no env/config is set. Tests must run hermetically —
    point ``custom_models_path`` at a non-existent temp file so the
    auto-derive branch returns None and tests see the *true*
    "no operator config" path."""
    fake_path = tmp_path / "custom_models_test_isolated.json"
    # Don't create the file — auto-derive returns None for non-existent.
    try:
        from runtime.platform.process.paths import app_paths

        original = app_paths()
        # Replace just the one attribute. Other paths (logs, plans,
        # etc.) keep their real values so tests that rely on them
        # don't break.

        class _PatchedPaths:
            custom_models_path = fake_path

            def __getattr__(self, name: str) -> object:
                return getattr(original, name)

        patched = _PatchedPaths()
        monkeypatch.setattr(
            "runtime.platform.process.paths.app_paths",
            lambda: patched,
        )
    except (ImportError, AttributeError):
        # platform.paths not importable in this environment —
        # auto-derive will fail open (return None) anyway.
        pass


# ── classifier — mode flags take precedence ───────────────────


def test_empty_returns_local() -> None:
    assert estimate_turn_complexity("") == "local"
    assert estimate_turn_complexity("   ") == "local"


def test_short_chitchat_local() -> None:
    assert estimate_turn_complexity("hi") == "local"
    assert estimate_turn_complexity("ok") == "local"
    assert estimate_turn_complexity("谢谢") == "local"
    assert estimate_turn_complexity("hello") == "local"


def test_explicit_model_returns_performance() -> None:
    assert (
        estimate_turn_complexity(
            "调研 X",
            has_explicit_model=True,
        )
        == "performance"
    )


def test_topology_returns_performance() -> None:
    assert estimate_turn_complexity("做 X", has_topology=True) == "performance"


def test_swarm_mode_returns_performance() -> None:
    assert estimate_turn_complexity("做 X", is_swarm_mode=True) == "performance"


def test_research_mode_returns_performance() -> None:
    assert estimate_turn_complexity("做 X", is_research_mode=True) == "performance"


def test_goal_mode_returns_performance() -> None:
    assert estimate_turn_complexity("ok", is_goal_mode=True) == "performance"


def test_code_mode_returns_performance_even_for_short_text() -> None:
    """Mode flags beat content length: '改一下' in code mode should
    still go performance. This was the bug in the 2-tier impl."""
    assert estimate_turn_complexity("改一下", is_code_mode=True) == "performance"


def test_todo_protocol_required_performance() -> None:
    assert (
        estimate_turn_complexity(
            "做几件事",
            requires_todo_protocol=True,
        )
        == "performance"
    )


def test_tool_intent_returns_value() -> None:
    assert (
        estimate_turn_complexity(
            "查一下天气",
            looks_tool_intent=True,
        )
        == "value"
    )


def test_short_question_returns_value() -> None:
    """Short non-chat non-mode message: value tier is enough."""
    assert estimate_turn_complexity("什么是 OAuth?") == "value"


def test_long_unclassified_defaults_performance() -> None:
    """Conservative default: long messages we can't classify go
    performance. Rather pay than under-deliver."""
    long = "这是一个很长很长的描述" * 30
    assert estimate_turn_complexity(long) == "performance"


def test_multiline_message_treated_performance() -> None:
    msg = "do thing one\ndo thing two"
    assert estimate_turn_complexity(msg) == "performance"


# ── tier resolution ───────────────────────────────────────────


def test_resolve_tier_local_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local tier returns None when no env / config — caller must
    escalate."""
    monkeypatch.delenv("ECHO_MODEL_LOCAL", raising=False)
    assert _resolve_tier_model("local") is None


def test_resolve_tier_local_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_MODEL_LOCAL", "ollama/qwen2.5:7b")
    assert _resolve_tier_model("local") == "ollama/qwen2.5:7b"


def test_resolve_tier_value_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_MODEL_VALUE", raising=False)
    monkeypatch.delenv("ECHO_SMART_ROUTING_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("ECHO_SUBAGENT_CHEAP_MODEL", raising=False)
    # Value tier has a built-in default to handle "I just want it to work".
    assert _resolve_tier_model("value") == "glm-4-flash"


def test_resolve_tier_value_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_MODEL_VALUE", raising=False)
    monkeypatch.setenv("ECHO_SMART_ROUTING_CHEAP_MODEL", "legacy-model")
    assert _resolve_tier_model("value") == "legacy-model"


def test_resolve_tier_performance_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_MODEL_PERFORMANCE", raising=False)
    assert _resolve_tier_model("performance") is None


def test_resolve_tier_performance_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "claude-sonnet-4")
    assert _resolve_tier_model("performance") == "claude-sonnet-4"


# ── entry-reference resolution (bare entry name) ─────────────
# Tier config can be either a plain model name (backward compat)
# OR a bare entry name from ``custom_models.json``. The reference
# is resolved against the entry's ``models`` list — index 0 for
# the value tier, index -1 for the performance tier. The previous
# ``"<entry>:<role>"`` syntax is gone; just point the tier at the
# entry id and the router picks the right slot automatically.


def test_resolve_tier_entry_reference_value_uses_first_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bare entry name in the value tier picks ``models[0]``
    (the cheap slot) from the matching custom-model entry."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(
        json.dumps(
            {
                "openai-prod": {
                    "models": ["gpt-4o-mini", "gpt-4o"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_VALUE", "openai-prod")
    assert _resolve_tier_model("value") == "gpt-4o-mini"


def test_resolve_tier_entry_reference_performance_uses_last_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bare entry name in the performance tier picks ``models[-1]``
    (the strongest slot) so the same entry can power both the value
    and performance tiers without duplicating api keys."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(
        json.dumps(
            {
                "openai-prod": {
                    "models": ["gpt-4o-mini", "gpt-4o"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "openai-prod")
    assert _resolve_tier_model("performance") == "gpt-4o"


def test_resolve_tier_entry_reference_single_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A single-model entry naturally returns the same model for
    both tiers — operators can have one item in the list and still
    cover value + performance."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(
        json.dumps({"solo-entry": {"models": ["only-model"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_VALUE", "solo-entry")
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "solo-entry")
    assert _resolve_tier_model("value") == "only-model"
    assert _resolve_tier_model("performance") == "only-model"


def test_resolve_tier_entry_reference_legacy_model_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Entries persisted before the ``models`` list refactor still
    have only the ``model`` field. The value tier uses it directly;
    the performance tier looks for ``model_performance`` and falls
    back to ``model`` when absent."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(
        json.dumps(
            {
                "legacy-entry": {
                    "model": "gpt-4o-mini",
                    "model_performance": "gpt-4o",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_VALUE", "legacy-entry")
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "legacy-entry")
    assert _resolve_tier_model("value") == "gpt-4o-mini"
    assert _resolve_tier_model("performance") == "gpt-4o"


def test_resolve_tier_entry_reference_missing_entry_passthrough(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A name that isn't in custom_models.json at all falls through
    as a plain model name — a typo in the config string doesn't
    silently escalate to the next tier, the dispatcher will 404
    if the alias isn't a built-in preset either, which is the
    right way to surface the bad config."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_VALUE", "ghost-entry")
    assert _resolve_tier_model("value") == "ghost-entry"


def test_resolve_tier_entry_reference_empty_models_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An entry whose ``models`` list is empty (or only blank
    strings) can't satisfy any tier — return None so the router
    escalates instead of returning a malformed model name."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(
        json.dumps({"openai-prod": {"models": []}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_VALUE", "openai-prod")
    assert _resolve_tier_model("value") is None


def test_resolve_tier_plain_model_name_passthrough(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A string that doesn't match any entry id should be treated
    as a plain model name, not silently swallowed. The dispatcher
    will then route it to a built-in preset or another custom
    entry by exact id match."""
    import json
    from types import SimpleNamespace

    import runtime.platform.process.paths as _paths

    cfg = tmp_path / "custom_models.json"
    cfg.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(
        _paths,
        "app_paths",
        lambda: SimpleNamespace(custom_models_path=cfg),
    )
    monkeypatch.setenv("ECHO_MODEL_VALUE", "gpt-4o-mini")
    assert _resolve_tier_model("value") == "gpt-4o-mini"


def test_get_tier_config_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_MODEL_LOCAL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("ECHO_MODEL_VALUE", "glm-4-flash")
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "claude-sonnet-4")
    snap = get_tier_config()
    assert snap == {
        "local": "ollama/qwen2.5:7b",
        "value": "glm-4-flash",
        "performance": "claude-sonnet-4",
    }


# ── routing decision (3 tier with escalation) ─────────────────


def test_explicit_user_model_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_MODEL_VALUE", "test-cheap")
    routed, reason = select_model_for_complexity(
        "local",
        user_model="claude-sonnet-4",
    )
    assert routed is None
    assert reason == "user_pinned"


def test_smart_routing_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "off")
    routed, reason = select_model_for_complexity("local", user_model=None)
    assert routed is None
    assert reason == "smart_routing_disabled"


def test_local_routes_to_local_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.setenv("ECHO_MODEL_LOCAL", "ollama/qwen2.5:7b")
    routed, reason = select_model_for_complexity("local", user_model=None)
    assert routed == "ollama/qwen2.5:7b"
    assert reason == "smart_routing:local->local"


def test_local_escalates_to_value_when_local_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No local model configured → escalate up to value tier."""
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.delenv("ECHO_MODEL_LOCAL", raising=False)
    monkeypatch.setenv("ECHO_MODEL_VALUE", "glm-4-flash")
    routed, reason = select_model_for_complexity("local", user_model=None)
    assert routed == "glm-4-flash"
    assert "value" in reason
    assert "escalated" in reason


def test_value_routes_to_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.setenv("ECHO_MODEL_VALUE", "haiku")
    routed, reason = select_model_for_complexity("value", user_model=None)
    assert routed == "haiku"
    assert reason == "smart_routing:value->value"


def test_value_escalates_to_performance_when_value_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    # Stub all the value-tier fallbacks to nothing
    for env in (
        "ECHO_MODEL_VALUE",
        "ECHO_SMART_ROUTING_CHEAP_MODEL",
        "ECHO_SUBAGENT_CHEAP_MODEL",
    ):
        monkeypatch.delenv(env, raising=False)
    # Override the built-in default by patching _resolve_tier_model:
    # since value has a built-in default, we need to mock get_tier_config
    # to skip it. Instead test escalation by making value return None
    # via a temporary patch.
    from runtime.core.cerebrum import turn_complexity as tc

    orig = tc._resolve_tier_model

    def stub(t):
        if t == "value":
            return None
        if t == "performance":
            return "sonnet-4"
        return orig(t)

    monkeypatch.setattr(tc, "_resolve_tier_model", stub)
    routed, reason = select_model_for_complexity("value", user_model=None)
    assert routed == "sonnet-4"
    assert "performance" in reason


def test_performance_routes_to_performance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.setenv("ECHO_MODEL_PERFORMANCE", "claude-sonnet-4")
    routed, reason = select_model_for_complexity("performance", user_model=None)
    assert routed == "claude-sonnet-4"
    assert reason == "smart_routing:performance->performance"


def test_performance_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """If performance tier isn't configured we have nowhere to escalate
    to — return None, caller falls back to whatever the runtime's
    default would be."""
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.delenv("ECHO_MODEL_PERFORMANCE", raising=False)
    routed, reason = select_model_for_complexity("performance", user_model=None)
    assert routed is None
    assert "no_tier_configured" in reason


def test_echo_agent_sentinel_treated_as_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.setenv("ECHO_MODEL_VALUE", "glm-4-flash")
    routed, _ = select_model_for_complexity("value", user_model="echo-agent")
    assert routed == "glm-4-flash"


def test_auto_sentinel_treated_as_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    monkeypatch.setenv("ECHO_MODEL_VALUE", "glm-4-flash")
    routed, _ = select_model_for_complexity("value", user_model="auto")
    assert routed == "glm-4-flash"


# ── kill switch ───────────────────────────────────────────────


def test_smart_routing_enabled_default() -> None:
    if "ECHO_SMART_ROUTING" in os.environ:
        del os.environ["ECHO_SMART_ROUTING"]
    assert is_smart_routing_enabled() is True


def test_smart_routing_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("off", "0", "false", "no", "disabled"):
        monkeypatch.setenv("ECHO_SMART_ROUTING", v)
        assert is_smart_routing_enabled() is False


# ── auto-derive from custom_models.json ───────────────────────


def _write_custom_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict,
) -> Path:
    """Override the autouse isolation by pointing custom_models_path
    at a real file we just wrote. Returns the path so tests can
    edit it further."""
    import json

    real_path = tmp_path / "custom_models_real.json"
    real_path.write_text(json.dumps(payload), encoding="utf-8")
    from runtime.platform.process.paths import app_paths

    original = app_paths()

    class _PatchedPaths:
        custom_models_path = real_path

        def __getattr__(self, name: str) -> object:
            return getattr(original, name)

    monkeypatch.setattr(
        "runtime.platform.process.paths.app_paths",
        lambda: _PatchedPaths(),
    )
    return real_path


def test_auto_derive_local_picks_first_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no env/config is set, ``local`` tier auto-derives
    ``models[0]`` from the first custom_models.json entry.
    Operators don't need to set ECHO_MODEL_LOCAL just to
    enable smart routing on a single API key."""
    monkeypatch.delenv("ECHO_MODEL_LOCAL", raising=False)
    _write_custom_models(
        monkeypatch,
        tmp_path,
        {
            "myprovider": {
                "provider": "openai",
                "models": ["small-model", "big-model"],
            },
        },
    )
    assert _resolve_tier_model("local") == "small-model"


def test_auto_derive_performance_picks_last_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ECHO_MODEL_PERFORMANCE", raising=False)
    _write_custom_models(
        monkeypatch,
        tmp_path,
        {
            "myprovider": {
                "provider": "openai",
                "models": ["small-model", "big-model"],
            },
        },
    )
    assert _resolve_tier_model("performance") == "big-model"


def test_auto_derive_value_uses_first_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Value tier auto-derives the cheap slot too, BEFORE falling
    through to glm-4-flash. Custom-model intent beats built-in
    default — operator obviously preferred the imported provider."""
    monkeypatch.delenv("ECHO_MODEL_VALUE", raising=False)
    monkeypatch.delenv("ECHO_SMART_ROUTING_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("ECHO_SUBAGENT_CHEAP_MODEL", raising=False)
    _write_custom_models(
        monkeypatch,
        tmp_path,
        {
            "myprovider": {
                "provider": "openai",
                "models": ["mimo-v2.5", "mimo-v2.5-pro"],
            },
        },
    )
    assert _resolve_tier_model("value") == "mimo-v2.5"


def test_explicit_env_beats_auto_derive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Auto-derive is the lowest-priority fallback. Setting an
    explicit ECHO_MODEL_LOCAL takes precedence."""
    monkeypatch.setenv("ECHO_MODEL_LOCAL", "explicit-pick")
    _write_custom_models(
        monkeypatch,
        tmp_path,
        {
            "myprovider": {
                "provider": "openai",
                "models": ["should-be-ignored", "also-ignored"],
            },
        },
    )
    assert _resolve_tier_model("local") == "explicit-pick"


def test_auto_derive_legacy_single_model_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Older entries that predate the ``models`` list refactor
    still have a single ``model`` field — auto-derive should
    still pick it up rather than skipping the entry."""
    monkeypatch.delenv("ECHO_MODEL_LOCAL", raising=False)
    _write_custom_models(
        monkeypatch,
        tmp_path,
        {
            "legacy-provider": {
                "provider": "openai",
                "model": "single-shot-model",
            },
        },
    )
    assert _resolve_tier_model("local") == "single-shot-model"


def test_auto_derive_skips_malformed_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """First entry has no usable models — the function should
    move on to the next entry rather than returning None."""
    monkeypatch.delenv("ECHO_MODEL_LOCAL", raising=False)
    _write_custom_models(
        monkeypatch,
        tmp_path,
        {
            "broken-provider": {
                "provider": "openai",
                "models": [],  # empty list
            },
            "good-provider": {
                "provider": "openai",
                "models": ["working-model"],
            },
        },
    )
    assert _resolve_tier_model("local") == "working-model"


def test_auto_derive_returns_none_for_value_when_no_custom(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No custom_models.json AND no env → value tier still falls
    through to its built-in glm-4-flash default. Auto-derive's
    None must NOT short-circuit the legacy default."""
    monkeypatch.delenv("ECHO_MODEL_VALUE", raising=False)
    monkeypatch.delenv("ECHO_SMART_ROUTING_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("ECHO_SUBAGENT_CHEAP_MODEL", raising=False)
    # tmp_path file deliberately not created → auto-derive returns
    # None. Should fall through to glm-4-flash default.
    assert _resolve_tier_model("value") == "glm-4-flash"


# ── Short-message window (post-EchoRouter-removal) ──────────


def test_short_single_line_routes_to_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-chitchat, non-tool-intent short single-line message goes
    to value — including strong-analysis phrasing. A heuristic router
    gate once second-guessed this window; real-traffic measurement
    (118 msgs, 1 changed, that 1 a mis-promotion) showed it net-
    negative, so it was removed and the window is a flat → value."""
    monkeypatch.delenv("ECHO_USE_ECHO_ROUTER", raising=False)
    assert (
        estimate_turn_complexity(
            "analyze the architecture and recommend refactoring strategies",
        )
        == "value"
    )
    assert estimate_turn_complexity("什么是Python") == "value"


def test_long_message_routes_to_performance() -> None:
    """A long (>=200 char) or multi-line message stays conservative →
    performance."""
    long_single = "x" * 250
    assert estimate_turn_complexity(long_single) == "performance"
    assert estimate_turn_complexity("line one\nline two\nline three") == "performance"


def test_mode_flags_take_precedence_over_length() -> None:
    """Hard performance cases (code/topology/etc.) short-circuit before
    any length classification — a 3-char code-mode message still goes
    performance."""
    assert estimate_turn_complexity("改一下", is_code_mode=True) == "performance"
    assert estimate_turn_complexity("hi", has_topology=True) == "performance"


# ── select_model × user_pinned × smart_routing ────────────────


def test_user_pinned_wins_over_smart_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit user model short-circuits regardless of smart
    routing state — classifier becomes informational."""
    monkeypatch.setenv("ECHO_SMART_ROUTING", "on")
    model, reason = select_model_for_complexity(
        "performance",
        user_model="my-pinned-model",
    )
    assert model is None
    assert reason == "user_pinned"


def test_smart_routing_off_ignores_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smart routing off → no rewrite even with a performance verdict."""
    monkeypatch.setenv("ECHO_SMART_ROUTING", "off")
    model, reason = select_model_for_complexity("performance", user_model=None)
    assert model is None
    assert reason == "smart_routing_disabled"
