"""Dense coverage for the setup wizard (audit Q-05)."""

from __future__ import annotations

import runtime.platform.lifecycle.setup_wizard as sw_mod
from runtime.platform.lifecycle.setup_wizard import SetupWizard, _config_to_yaml_dict


def _wizard(tmp_path, **kw):
    return SetupWizard(output_path=tmp_path / "config.yaml", **kw)


def test_run_non_interactive(tmp_path) -> None:
    out = _wizard(tmp_path, non_interactive=True).run()
    assert out.exists()
    text = out.read_text()
    assert "static" in text


def test_run_interactive_static(tmp_path, monkeypatch) -> None:
    w = _wizard(tmp_path)
    answers = iter(["1", "n", ""])  # static, journal off, extra keys empty
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    out = w.run()
    assert out.exists()
    assert "static" in out.read_text()
    assert "mock/planner" in out.read_text()


def test_run_interactive_llm(tmp_path, monkeypatch) -> None:
    w = _wizard(tmp_path)
    answers = iter(["2", "2", "sk-abc123", "k1, k2", "y"])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    out = w.run()
    text = out.read_text()
    assert "claude-sonnet-4-5-20250514" in text
    assert "sk-abc123" in text
    assert "k1" in text and "k2" in text
    assert "events.jsonl" in text


def test_ask_model_custom_and_default(tmp_path, monkeypatch) -> None:
    w = _wizard(tmp_path)
    answers = iter(["6", "my-model"])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    assert w._ask_model("llm") == "my-model"
    assert w._ask_model("static") == "mock/planner"
    # default model when blank
    answers = iter([""])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers) or default)
    assert w._ask_model("llm") == "claude-haiku-4-5-20251001"


def test_ask_api_key_env_and_ollama(tmp_path, monkeypatch) -> None:
    w = _wizard(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert w._ask_api_key("claude-haiku-4-5-20251001") is None
    assert w._ask_api_key("ollama/llama3.2") is None
    answers = iter(["sk-custom"])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    assert w._ask_api_key("gpt-4o") == "sk-custom"


def test_ask_credential_pool_and_journal(tmp_path, monkeypatch) -> None:
    w = _wizard(tmp_path)
    answers = iter([""])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    assert w._ask_credential_pool(None) == []
    answers = iter(["a, b, , c"])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    assert w._ask_credential_pool(None) == ["a", "b", "c"]
    answers = iter(["yes"])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    assert w._ask_journal() == "events.jsonl"
    answers = iter(["n"])
    monkeypatch.setattr(w, "_input", lambda prompt, default="": next(answers))
    assert w._ask_journal() is None
    assert w._ask_hot_cache() is True


def test_write_json_when_no_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sw_mod, "YAML_AVAILABLE", False)
    out = _wizard(tmp_path, non_interactive=True).run()
    text = out.read_text()
    assert '"type": "static"' in text


def test_input_eof(tmp_path, monkeypatch) -> None:
    w = _wizard(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: (_ for _ in ()).throw(EOFError()))
    assert w._input("prompt", default="dflt") == "dflt"


def test_config_to_yaml_dict_cleans() -> None:
    from runtime.platform.config.schema import AgentConfig

    cfg = AgentConfig(planner={"type": "static", "model": "mock/planner"})
    data = _config_to_yaml_dict(cfg)
    assert data["planner"]["type"] == "static"
    assert data["credential_pool"]["max_retries"] == 3
    assert "journal_file" not in data  # None values are cleaned
    assert isinstance(data, dict)

