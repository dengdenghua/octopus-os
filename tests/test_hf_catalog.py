"""Live HF GGUF catalog — pure parsing + cache/fallback logic (no network)."""

from __future__ import annotations

from runtime.sensing.model_router import hf_catalog as hf


def test_parse_params_b() -> None:
    assert hf._parse_params_b("bartowski/Qwen2.5-7B-Instruct-GGUF") == 7.0
    assert hf._parse_params_b("unsloth/gemma-4-12b-it-GGUF") == 12.0
    assert hf._parse_params_b("Phi-3.5-mini-3.8B") == 3.8
    # No size token → 0 (caller relies on measured size instead).
    assert hf._parse_params_b("antirez/deepseek-v4-gguf") == 0.0
    # Picks the largest plausible size, ignores junk.
    assert hf._parse_params_b("MoE-8x7B-GGUF") == 7.0


def test_family_of() -> None:
    assert hf._family_of("unsloth/Qwen3.5-9B-GGUF") == "qwen"
    assert hf._family_of("meta-llama/Llama-3.1-8B") == "llama"
    assert hf._family_of("google/gemma-4-12b") == "gemma"
    assert hf._family_of("some/unknown-model") == "other"


def test_quant_of() -> None:
    assert hf._quant_of("Qwen2.5-7B-Instruct-Q4_K_M.gguf") == "Q4_K_M"
    assert hf._quant_of("model.Q8_0.gguf") == "Q8_0"
    assert hf._quant_of("model.f16.gguf") == "F16"
    assert hf._quant_of("model.gguf") is None


def test_base_key_collapses_quant_variants() -> None:
    a = hf._base_key("bartowski/Qwen2.5-7B-Instruct-GGUF")
    b = hf._base_key("unsloth/Qwen2.5-7B-Instruct-GGUF")
    # Same base model from two quantizers → same key (dedup).
    assert a == b
    # Quant suffixes don't change the key.
    assert hf._base_key("x/Model-7B-Q4_K_M-GGUF") == hf._base_key("x/Model-7B-Q8_0-GGUF")


def test_pick_quant_prefers_q4km() -> None:
    assert hf._pick_quant({"Q8_0": 8.0, "Q4_K_M": 4.5, "Q2_K": 2.0}) == "Q4_K_M"
    # Falls through the preference list when q4_K_M is absent.
    assert hf._pick_quant({"Q8_0": 8.0, "Q5_K_M": 5.0}) == "Q5_K_M"
    assert hf._pick_quant({}) is None


def test_exclude_filter_drops_non_chat_gguf() -> None:
    assert hf._EXCLUDE_RE.search("mixedbread-ai/mxbai-embed-large-v1")
    assert hf._EXCLUDE_RE.search("BAAI/bge-reranker-v2-gguf")
    assert hf._EXCLUDE_RE.search("openai/whisper-large-v3")
    # A normal chat LLM is not excluded.
    assert not hf._EXCLUDE_RE.search("unsloth/Qwen3.5-9B-GGUF")


def test_dynamic_catalog_serves_fresh_cache(monkeypatch) -> None:
    spec = hf.ModelSpec(tag="hf.co/x:Q4_K_M", label="X", params_b=7.0, family="qwen", arch_rank=9)
    monkeypatch.setattr(hf, "_disk_loaded", True)
    # 1e12 is far in the future → always "fresh"; Date.now isn't mocked here, real time is fine.
    monkeypatch.setattr(hf, "_cache", {"ts": 1e12, "specs": [spec]})
    assert hf.dynamic_catalog() == [spec]


def test_dynamic_catalog_cold_returns_none_without_blocking(monkeypatch) -> None:
    monkeypatch.setattr(hf, "_disk_loaded", True)
    monkeypatch.setattr(hf, "_cache", {"ts": 0.0, "specs": None})
    # Don't spawn the background fetch in the test.
    monkeypatch.setattr(hf, "_maybe_refresh", lambda: None)
    assert hf.dynamic_catalog() is None

