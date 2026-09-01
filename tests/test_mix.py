"""Tests for the Mix virtual model (echo-mix · mixture-of-agents)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.platform.models import ParsedIntent
from runtime.sensing.gateway.openai_gateway import mix


@pytest.fixture(autouse=True)
def _isolated_mix_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the Mix preset at a scratch path for every test in this file.

    The preset lives in the developer's own ``~/.echo/mix_config.json`` and
    takes precedence over the environment by design, so without this a machine
    with a real preset made these tests assert against whatever models the
    developer happened to have configured — a test that passes or fails based
    on the home directory is not a test.
    """
    monkeypatch.setattr(mix, "_config_path", lambda: tmp_path / "mix_config.json")


@pytest.fixture(autouse=True)
def _no_custom_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic baseline: no custom_models.json, so tag-inferred pools
    resolve to empty and Mix keeps the planner-default behaviour. A real
    catalog on a dev box would otherwise inject tier-tagged models into the
    inferred pool and change the asserted defaults. Tests that exercise the
    tag inference override this fixture per-test."""
    from runtime.platform.models import custom_model_flags

    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: None)


def _install_custom_models(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> None:
    from runtime.platform.models import custom_model_flags

    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: data)


def _intent(goal: str = "What is 2+2?") -> ParsedIntent:
    return ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context={"conversation_messages": [{"role": "user", "content": goal}]},
    )


def _envelope(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "echo": {},
    }


def test_is_mix_model() -> None:
    assert mix.is_mix_model("echo-mix")
    assert mix.is_mix_model("echo-mix:fast")
    assert mix.is_mix_model("ECHO-MIX")
    assert not mix.is_mix_model("echo-agent")
    assert not mix.is_mix_model("gpt-5.5")
    assert not mix.is_mix_model(None)


def test_mix_model_ids_advertises_virtual_model() -> None:
    assert "echo-mix" in mix.mix_model_ids()


def test_proposer_specs_default_count(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.delenv("ECHO_MIX_N", raising=False)
    specs = mix._proposer_specs("echo-mix")
    assert len(specs) == 3
    # default pool → planner-default model ("") with distinct lenses
    assert all(model == "" for model, _ in specs)
    assert len({lens for _, lens in specs}) == 3


def test_proposer_specs_from_env_pool(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_MIX_PROPOSERS", "m1, m2 , m3")
    specs = mix._proposer_specs("echo-mix")
    assert [model for model, _ in specs] == ["m1", "m2", "m3"]


def test_run_mix_chat_injects_drafts_and_annotates(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.delenv("ECHO_MIX_N", raising=False)

    def fake_proposer(stack, intent, agent, model=None, **_kwargs):  # noqa: ANN001
        # echo the injected lens so the three drafts are distinct + non-empty
        lens = intent.user_context["conversation_messages"][0]["content"]
        return (f"draft::{lens[:18]}", {})

    monkeypatch.setattr(mix, "_direct_llm_fallback_with_usage", fake_proposer)

    captured: dict[str, Any] = {}

    def fake_run_chat(stack, intent, model, default_arm, *, optimizer=None, actor=None, agent=None):  # noqa: ANN001
        captured["intent"] = intent
        captured["model"] = model
        return _envelope("final synthesized answer")

    out = mix.run_mix_chat(
        object(),
        _intent(),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=fake_run_chat,
    )

    meta = out["echo"]["mix"]
    assert meta["drafts_used"] == 3
    assert meta["proposers"] == 3
    assert meta["degraded"] is False
    assert out["model"] == "echo-mix"

    # aggregator saw the drafts as a trailing system message + structured copy
    convo = captured["intent"].user_context["conversation_messages"]
    assert convo[-1]["role"] == "system"
    assert "Draft 1" in convo[-1]["content"]
    assert len(captured["intent"].user_context["mix_proposals"]) == 3


def test_run_mix_chat_degrades_when_all_proposers_fail(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.setattr(
        mix,
        "_direct_llm_fallback_with_usage",
        lambda *a, **k: (None, {}),
    )

    captured: dict[str, Any] = {}

    def fake_run_chat(stack, intent, model, default_arm, *, optimizer=None, actor=None, agent=None):  # noqa: ANN001
        captured["intent"] = intent
        return _envelope("plain single-model answer")

    out = mix.run_mix_chat(
        object(),
        _intent(),
        "echo-mix",
        "code_arm",
        actor=None,
        agent=None,
        run_chat=fake_run_chat,
    )

    meta = out["echo"]["mix"]
    assert meta["degraded"] is True
    assert meta["drafts_used"] == 0
    # degraded path runs the ORIGINAL intent — no drafts injected
    assert "mix_proposals" not in (captured["intent"].user_context or {})


def test_mix_sse_frames_emit_valid_openai_stream() -> None:
    frames = list(mix.mix_sse_frames(_envelope("hello world"), "echo-mix"))
    joined = "".join(frames)
    assert '"role": "assistant"' in joined
    assert "hello world" in joined
    assert '"finish_reason": "stop"' in joined
    assert '"model": "echo-mix"' in joined
    assert joined.rstrip().endswith("[DONE]")


def test_mix_config_roundtrip_and_priority(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mix, "_config_path", lambda: tmp_path / "mix_config.json")
    # save validates: trims blanks, caps count at _MAX_PROPOSERS
    saved = mix.save_mix_config({"proposers": ["a", " ", "b"], "aggregator": "agg", "n": 99})
    assert saved["proposers"] == ["a", "b"]
    assert saved["aggregator"] == "agg"
    assert saved["n"] == mix._MAX_PROPOSERS
    # load round-trips
    assert mix.load_mix_config()["proposers"] == ["a", "b"]
    # config WINS over env
    monkeypatch.setenv("ECHO_MIX_PROPOSERS", "x,y,z")
    monkeypatch.setenv("ECHO_MIX_AGGREGATOR", "env-agg")
    assert mix._proposer_pool() == ["a", "b"]
    assert mix._aggregator_model() == "agg"


def test_mix_config_missing_falls_back_to_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mix, "_config_path", lambda: tmp_path / "absent.json")
    monkeypatch.setenv("ECHO_MIX_PROPOSERS", "x,y")
    assert mix._proposer_pool() == ["x", "y"]


# ── Cost-tier inferred pool (economy/balanced → proposers, performance → aggregator) ──


def test_proposer_pool_infers_economy_then_balanced_from_tags(monkeypatch) -> None:
    """With no explicit pool, Mix drafts on the cheap tier: economy entries
    first, then balanced — performance stays out of the draft stage."""
    _install_custom_models(
        monkeypatch,
        {
            "perf": {"id": "kimi-k3", "tier": "performance"},
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
        },
    )
    assert mix._proposer_pool() == ["agnes-2.5-flash", "deepseek-v4-flash"]


def test_proposer_pool_excludes_performance_from_drafts(monkeypatch) -> None:
    """A catalog with only performance-tagged models yields NO inferred
    proposers — the draft stage never wastes the expensive tier."""
    _install_custom_models(
        monkeypatch,
        {
            "kimi": {"id": "kimi-k3", "tier": "performance"},
            "ark": {"id": "ark-code-latest", "tier": "performance"},
        },
    )
    assert mix._proposer_pool() == []


def test_proposer_pool_empty_when_no_tags_declared(monkeypatch) -> None:
    """Untagged entries carry no cost signal → not eligible for the pool."""
    _install_custom_models(
        monkeypatch,
        {"untagged": {"id": "some-model", "base_url": "https://x"}},
    )
    assert mix._proposer_pool() == []


def test_proposer_pool_caps_at_max_proposers(monkeypatch) -> None:
    economy = {f"e{i}": {"id": f"econ-{i}", "tier": "economy"} for i in range(10)}
    _install_custom_models(monkeypatch, economy)
    assert len(mix._proposer_pool()) == mix._MAX_PROPOSERS


def test_aggregator_infers_performance_then_balanced(monkeypatch) -> None:
    """The aggregator prefers the strong performance tier, deterministic
    sorted pick across multiple performance-tagged entries."""
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
            "perf1": {"id": "kimi-k3", "tier": "performance"},
            "perf2": {"id": "ark-code-latest", "tier": "performance"},
        },
    )
    assert mix._aggregator_model() == "ark-code-latest"


def test_aggregator_falls_back_to_balanced_without_performance(monkeypatch) -> None:
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
        },
    )
    assert mix._aggregator_model() == "deepseek-v4-flash"


# ── Complexity-aware aggregator (simple → balanced, complex → performance) ──


def test_aggregator_infers_balanced_for_simple_request(monkeypatch) -> None:
    """A simple (value-tier) request draws the balanced aggregator — not the
    expensive performance tier."""
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
            "perf": {"id": "kimi-k3", "tier": "performance"},
        },
    )
    assert mix._aggregator_model(_intent("What is 2+2?")) == "deepseek-v4-flash"


def test_aggregator_infers_performance_for_complex_request(monkeypatch) -> None:
    """A complex (performance-tier) request draws the strong performance
    aggregator, never demoting to balanced."""
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
            "perf1": {"id": "kimi-k3", "tier": "performance"},
            "perf2": {"id": "ark-code-latest", "tier": "performance"},
        },
    )
    complex_goal = (
        "请系统分析这份技术方案:\n"
        "1. 对比三种架构的吞吐、延迟、成本与运维复杂度\n"
        "2. 评估数据一致性与故障恢复路径\n"
        "3. 给出带风险等级的迁移路线图\n"
        "4. 附上可执行的验证清单"
    )
    assert mix._aggregator_model(_intent(complex_goal)) == "ark-code-latest"


def test_aggregator_simple_without_balanced_escalates_to_performance(monkeypatch) -> None:
    """A simple request with no balanced tier escalates UP to performance —
    the aggregator must stay stronger than the drafters, never dropping to
    the cheap economy tier (matching turn_complexity's never-demote chain)."""
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "perf": {"id": "ark-code-latest", "tier": "performance"},
        },
    )
    assert mix._aggregator_model(_intent("What is 2+2?")) == "ark-code-latest"


def test_aggregator_complex_without_performance_keeps_planner_default(monkeypatch) -> None:
    """A complex request with no performance tier does NOT demote to balanced —
    it keeps the planner default (empty), the same never-demote contract the
    rest of the system uses."""
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
        },
    )
    complex_goal = "长任务:\n" + "\n".join(
        f"第{i}步,逐项核对并落地,涉及多模块跨仓库联动" for i in range(3)
    )
    assert mix._aggregator_model(_intent(complex_goal)) == ""


def test_aggregator_explicit_config_ignores_complexity(monkeypatch) -> None:
    """Explicit aggregator config wins regardless of request complexity."""
    monkeypatch.setenv("ECHO_MIX_AGGREGATOR", "explicit-agg")
    _install_custom_models(
        monkeypatch,
        {
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
            "perf": {"id": "kimi-k3", "tier": "performance"},
        },
    )
    complex_goal = "复杂:\n" + "\n".join(f"第{i}步综合多源数据并交叉验证" for i in range(4))
    assert mix._aggregator_model(_intent(complex_goal)) == "explicit-agg"
    assert mix._aggregator_model(_intent("What is 2+2?")) == "explicit-agg"


def test_aggregator_none_intent_keeps_historical_fallback(monkeypatch) -> None:
    """No intent (no complexity signal) → historical performance→balanced."""
    _install_custom_models(
        monkeypatch,
        {
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
            "perf": {"id": "kimi-k3", "tier": "performance"},
        },
    )
    assert mix._aggregator_model(None) == "kimi-k3"


def test_aggregator_empty_without_performance_or_balanced(monkeypatch) -> None:
    """Economy-only catalog → aggregator stays on the planner default."""
    _install_custom_models(
        monkeypatch,
        {"econ": {"id": "agnes-2.5-flash", "tier": "economy"}},
    )
    assert mix._aggregator_model() == ""


def test_explicit_config_and_env_beat_tag_inference(tmp_path, monkeypatch) -> None:
    """Declared pools/aggregator always win over tag inference — the tags are
    a fallback for an operator who never configured Mix, never an override."""
    _install_custom_models(
        monkeypatch,
        {"econ": {"id": "agnes-2.5-flash", "tier": "economy"}},
    )
    # env wins over tags
    monkeypatch.setenv("ECHO_MIX_PROPOSERS", "env-pool")
    monkeypatch.setenv("ECHO_MIX_AGGREGATOR", "env-agg")
    assert mix._proposer_pool() == ["env-pool"]
    assert mix._aggregator_model() == "env-agg"
    # preset config wins over env + tags
    monkeypatch.setattr(mix, "_config_path", lambda: tmp_path / "mix_config.json")
    mix.save_mix_config({"proposers": ["cfg-pool"], "aggregator": "cfg-agg"})
    assert mix._proposer_pool() == ["cfg-pool"]
    assert mix._aggregator_model() == "cfg-agg"


def test_proposer_specs_uses_tagged_pool(monkeypatch) -> None:
    """End-to-end: the inferred pool becomes the actual proposer model list,
    each with a distinct reasoning lens."""
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.delenv("ECHO_MIX_N", raising=False)
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
        },
    )
    specs = mix._proposer_specs("echo-mix")
    assert [m for m, _ in specs] == ["agnes-2.5-flash", "deepseek-v4-flash"]
    assert len({lens for _, lens in specs}) == 2  # distinct lenses


def test_run_mix_chat_uses_tagged_proposers_and_aggregator(monkeypatch) -> None:
    """The tagged pool flows through a real run_mix_chat: drafts come from the
    cheap tier, and the aggregator is picked complexity-aware — a simple
    request draws balanced, a complex one the performance model."""
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    _install_custom_models(
        monkeypatch,
        {
            "econ": {"id": "agnes-2.5-flash", "tier": "economy"},
            "mid": {"id": "deepseek-v4-flash", "tier": "balanced"},
            "perf": {"id": "kimi-k3", "tier": "performance"},
        },
    )

    seen_proposers: list[str] = []

    def fake_proposer(stack, intent, agent, model=None, **_kwargs):  # noqa: ANN001
        seen_proposers.append(model)
        return (f"draft-from-{model}", {})

    monkeypatch.setattr(mix, "_direct_llm_fallback_with_usage", fake_proposer)

    captured: dict[str, Any] = {}

    def fake_run_chat(stack, intent, model, default_arm, *, optimizer=None, actor=None, agent=None):  # noqa: ANN001
        captured["model"] = model
        return _envelope("synthesized")

    mix.run_mix_chat(
        object(),
        _intent(),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=fake_run_chat,
    )

    assert seen_proposers == ["agnes-2.5-flash", "deepseek-v4-flash"]
    # simple request ("What is 2+2?" → value tier) → balanced aggregator
    assert captured["model"] == "deepseek-v4-flash"

    complex_goal = "跨仓库审计:\n" + "\n".join(
        f"第{i}步,梳理调用链并交叉核对各模块实现" for i in range(3)
    )
    mix.run_mix_chat(
        object(),
        _intent(complex_goal),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=fake_run_chat,
    )
    # complex request (performance tier) → performance aggregator
    assert captured["model"] == "kimi-k3"


# ── Execution-intent proposer skip ───────────────────────────


def test_skip_proposers_detects_execution_verbs_zh() -> None:
    assert mix._skip_proposers_reason(_intent("帮我写一个部署脚本")) == "execution_intent"
    assert mix._skip_proposers_reason(_intent("创建项目并启动服务")) == "execution_intent"
    assert mix._skip_proposers_reason(_intent("修复这个 bug 并提交")) == "execution_intent"


def test_skip_proposers_detects_execution_verbs_en() -> None:
    assert mix._skip_proposers_reason(_intent("Write a python script")) == "execution_intent"
    assert mix._skip_proposers_reason(_intent("deploy the service to prod")) == "execution_intent"
    assert mix._skip_proposers_reason(_intent("fix the failing test")) == "execution_intent"


def test_skip_proposers_keeps_analysis_intent() -> None:
    assert mix._skip_proposers_reason(_intent("比较 kimi 和 deepseek 的优缺点")) is None
    assert mix._skip_proposers_reason(_intent("explain the difference between X and Y")) is None
    assert mix._skip_proposers_reason(_intent("What is 2+2?")) is None
    assert mix._skip_proposers_reason(None) is None  # no goal → no signal
    assert mix._skip_proposers_reason("   ") is None


def test_run_mix_chat_skips_proposers_on_execution_intent(monkeypatch) -> None:
    """An execution-oriented request bypasses the proposer stage entirely —
    the full tool-enabled turn runs directly on the aggregator model, and
    the mix metadata explains why."""
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)

    called_proposer = []

    def fake_proposer(stack, intent, agent, model=None, **_kwargs):  # noqa: ANN001
        called_proposer.append(model)
        return ("should never run", {})

    monkeypatch.setattr(mix, "_direct_llm_fallback_with_usage", fake_proposer)

    captured: dict[str, Any] = {}

    def fake_run_chat(stack, intent, model, default_arm, *, optimizer=None, actor=None, agent=None):  # noqa: ANN001
        captured["intent"] = intent
        captured["model"] = model
        return _envelope("deployed.")

    out = mix.run_mix_chat(
        object(),
        _intent("帮我部署这个服务到生产环境"),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=fake_run_chat,
    )

    assert called_proposer == []  # proposers never ran
    assert captured["model"] == ""  # aggregator model unset → planner default
    meta = out["echo"]["mix"]
    assert meta["skipped_proposers"] == "execution_intent"
    assert meta["proposers"] == 0
    # the original intent runs unmodified — no drafts injected
    assert "mix_proposals" not in (captured["intent"].user_context or {})
    # skip-path still reports the virtual model id
    assert out["model"] == "echo-mix"


def test_run_mix_chat_keeps_proposers_on_analysis_intent(monkeypatch) -> None:
    """A pure analysis request keeps the full mixture — proposers run and the
    aggregator sees their drafts."""
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.delenv("ECHO_MIX_N", raising=False)

    proposer_calls = []

    def fake_proposer(stack, intent, agent, model=None, **_kwargs):  # noqa: ANN001
        lens = intent.user_context["conversation_messages"][0]["content"]
        proposer_calls.append(lens[:18])
        return (f"draft::{lens[:18]}", {})

    monkeypatch.setattr(mix, "_direct_llm_fallback_with_usage", fake_proposer)

    captured: dict[str, Any] = {}

    def fake_run_chat(stack, intent, model, default_arm, *, optimizer=None, actor=None, agent=None):  # noqa: ANN001
        captured["intent"] = intent
        return _envelope("final synthesized answer")

    out = mix.run_mix_chat(
        object(),
        _intent("比较这两个方案的优劣，给出建议"),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=fake_run_chat,
    )

    assert len(proposer_calls) == 3  # full mixture still ran
    assert "skipped_proposers" not in out["echo"]["mix"]
    convo = captured["intent"].user_context["conversation_messages"]
    assert convo[-1]["role"] == "system"  # drafts injected as trailing system msg


def test_aggregator_prompt_forces_action_not_restatement() -> None:
    """The strengthened aggregator prompt tells the synthesizer to COMPLETE
    the request with tools, not just restate the drafts."""
    content = mix._format_proposals(["draft one", "draft two"])
    assert "NOT the deliverable" in content
    assert "actually perform it" in content
    assert "with your tools" in content
    assert "starting points" in content


def test_proposer_calls_pass_max_tokens_cap(monkeypatch) -> None:
    """Proposers are draft-only advisors with no tool access — they must
    not get the ~131K-token ceiling a full agentic turn gets."""
    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.delenv("ECHO_MIX_N", raising=False)

    seen_caps: list[Any] = []

    def fake_proposer(stack, intent, agent, model=None, max_tokens_cap=None, **_kw):  # noqa: ANN001
        seen_caps.append(max_tokens_cap)
        return ("draft", {})

    monkeypatch.setattr(mix, "_direct_llm_fallback_with_usage", fake_proposer)

    mix.run_mix_chat(
        object(),
        _intent(),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=lambda *a, **k: _envelope("final"),  # noqa: ARG005
    )

    assert len(seen_caps) == 3
    assert all(cap == mix._PROPOSER_MAX_TOKENS for cap in seen_caps)
    assert mix._PROPOSER_MAX_TOKENS < 131072  # meaningfully smaller than a full-turn budget


def test_run_mix_chat_bounds_total_wait_on_a_hung_proposer(monkeypatch) -> None:
    """A single hung proposer must not block the whole mix request for the
    model SDK's own (much longer) default timeout — the total stage-1 wait
    is capped, and the hung proposer's draft is simply dropped."""
    import time as _time

    monkeypatch.delenv("ECHO_MIX_PROPOSERS", raising=False)
    monkeypatch.delenv("ECHO_MIX_N", raising=False)
    monkeypatch.setattr(mix, "_PROPOSER_TIMEOUT_SECONDS", 0.2)

    def fake_proposer(stack, intent, agent, model=None, **_kw):  # noqa: ANN001
        lens = intent.user_context["conversation_messages"][0]["content"]
        if "correctness" in lens:  # the first lens — make exactly one hang
            _time.sleep(5.0)
            return ("late-draft", {})
        return (f"draft::{lens[:10]}", {})

    monkeypatch.setattr(mix, "_direct_llm_fallback_with_usage", fake_proposer)

    started = _time.monotonic()
    out = mix.run_mix_chat(
        object(),
        _intent(),
        "echo-mix",
        "code_arm",
        actor="u1",
        agent=None,
        run_chat=lambda *a, **k: _envelope("final"),  # noqa: ARG005
    )
    elapsed = _time.monotonic() - started

    assert elapsed < 2.0  # bounded by the 0.2s timeout, not the 5s sleep
    meta = out["echo"]["mix"]
    assert meta["drafts_used"] == 2  # the hung proposer's draft was dropped
    assert meta["proposers"] == 3

