"""LLM-judge bootstrap wiring (constitution semantic tier).

The gate has consulted ``get_judge()`` all along; what was missing is
the registration at process start. These tests pin flag resolution and
the fail-open registration semantics.
"""

from typing import Any

import pytest
from runtime.safety.validation.bootstrap import (
    llm_judge_enabled,
    maybe_register_llm_judge,
)
from runtime.safety.validation.judge import get_judge, set_judge


@pytest.fixture(autouse=True)
def _reset_judge():
    yield
    set_judge(None)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    # Flag resolution reads config*.yaml from CWD — keep tests away
    # from the repo's real config files.
    monkeypatch.chdir(tmp_path)


class _FakeRouter:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def call(self, req: Any) -> Any:
        self.calls.append(req)

        class _Resp:
            text = '{"action": "allow", "reason": "fine"}'

        return _Resp()


class TestFlagResolution:
    def test_default_is_on(self, monkeypatch):
        monkeypatch.delenv("ECHO_ENABLE_LLM_JUDGE", raising=False)
        assert llm_judge_enabled() is True

    def test_explicit_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("ECHO_ENABLE_LLM_JUDGE", "1")
        assert llm_judge_enabled(False) is False
        monkeypatch.setenv("ECHO_ENABLE_LLM_JUDGE", "0")
        assert llm_judge_enabled(True) is True

    def test_env_var_toggles(self, monkeypatch):
        monkeypatch.setenv("ECHO_ENABLE_LLM_JUDGE", "true")
        assert llm_judge_enabled() is True
        monkeypatch.setenv("ECHO_ENABLE_LLM_JUDGE", "off")
        assert llm_judge_enabled() is False

    def test_yaml_layer(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ECHO_ENABLE_LLM_JUDGE", raising=False)
        (tmp_path / "config.local.yaml").write_text(
            "safety:\n  enable_llm_judge: true\n", encoding="utf-8"
        )
        assert llm_judge_enabled() is True

    def test_config_value_respected_when_env_silent(self, monkeypatch):
        # A flag from the loaded --config (config_value) takes effect
        # when the env var is silent — this is the cross-cwd fix.
        monkeypatch.delenv("ECHO_ENABLE_LLM_JUDGE", raising=False)
        assert llm_judge_enabled(config_value=True) is True
        assert llm_judge_enabled(config_value=False) is False
        assert llm_judge_enabled(config_value=None) is True

    def test_env_overrides_config_value(self, monkeypatch):
        # The env var stays an emergency override above the config file.
        monkeypatch.setenv("ECHO_ENABLE_LLM_JUDGE", "0")
        assert llm_judge_enabled(config_value=True) is False
        monkeypatch.setenv("ECHO_ENABLE_LLM_JUDGE", "1")
        assert llm_judge_enabled(config_value=False) is True


class TestRegistration:
    def test_disabled_registers_nothing(self):
        assert maybe_register_llm_judge(_FakeRouter(), enabled=False) is False

    def test_enabled_without_router_fails_open(self):
        assert maybe_register_llm_judge(None, enabled=True) is False

    def test_enabled_registers_judge_consumed_by_gate(self):
        router = _FakeRouter()
        assert maybe_register_llm_judge(router, enabled=True) is True
        verdict = get_judge()("hello world", "channels:discord:c1", None)
        assert verdict.action == "allow"
        assert len(router.calls) == 1

    def test_model_override_reaches_request(self):
        router = _FakeRouter()
        assert maybe_register_llm_judge(router, enabled=True, model="mock/fast") is True
        get_judge()("hi", "channels:slack:c2", None)
        assert router.calls[0].model == "mock/fast"

    def test_broken_router_fails_open(self):
        class _Broken:
            def call(self, req: Any) -> Any:
                raise RuntimeError("provider down")

        assert maybe_register_llm_judge(_Broken(), enabled=True) is True
        # Judge itself fails open per message — gate keeps flowing.
        verdict = get_judge()("hi", "channels:slack:c3", None)
        assert verdict.action == "allow"
