"""Configurable, unified text embedder — remote endpoint vs in-process."""

from __future__ import annotations

import json
import urllib.error

from runtime.memory.hemolymph import embedding_backend as eb


class _Resp:
    def __init__(self, body: str) -> None:
        self._b = body.encode("utf-8")

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a) -> bool:
        return False


def test_model_and_endpoint_from_env(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)
    monkeypatch.delenv("ECHO_EMBED_MODEL", raising=False)
    # fastembed requires the fully-qualified repo id, so the default is the full ID
    assert eb.embed_model() == "sentence-transformers/all-MiniLM-L6-v2"
    assert eb.embed_endpoint() == ""
    monkeypatch.setenv("ECHO_EMBED_URL", "http://127.0.0.1:11434/v1/")
    monkeypatch.setenv("ECHO_EMBED_MODEL", "bge-m3")
    assert eb.embed_endpoint() == "http://127.0.0.1:11434/v1"  # trailing slash stripped
    assert eb.embed_model() == "bge-m3"


def test_backend_info_reports_active_wiring(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)
    assert eb.backend_info()["kind"] == "in_process"
    monkeypatch.setenv("ECHO_EMBED_URL", "http://host/v1")
    info = eb.backend_info()
    assert info["kind"] == "remote"
    assert info["endpoint"] == "http://host/v1"
    assert info["local_only"] is True


def test_remote_embeds_via_openai_shape(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_EMBED_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("ECHO_EMBED_MODEL", "bge-m3")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}))

    monkeypatch.setattr(eb.urllib.request, "urlopen", fake_urlopen)
    out = eb.embed_texts(["hello"])
    assert out == [[0.1, 0.2, 0.3]]
    assert captured["url"].endswith("/v1/embeddings")
    assert captured["body"] == {"model": "bge-m3", "input": ["hello"]}


def test_remote_returns_none_when_endpoint_down(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_EMBED_URL", "http://127.0.0.1:9/v1")

    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(eb.urllib.request, "urlopen", boom)
    assert eb.embed_texts(["x"]) is None  # unreachable → None, no raise


def test_remote_returns_none_on_bad_payload(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_EMBED_URL", "http://host/v1")
    monkeypatch.setattr(eb.urllib.request, "urlopen", lambda *_a, **_k: _Resp('{"oops": 1}'))
    assert eb.embed_texts(["x"]) is None


def test_empty_input_short_circuits(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)
    assert eb.embed_texts([]) == []


def test_available_true_with_remote_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_EMBED_URL", "http://host/v1")
    assert eb.available() is True
    assert eb.get_encoder() is not None  # an .encode-shaped adapter


def test_local_prefers_fastembed(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)

    class _FE:
        def embed(self, texts):
            return [[0.5, 0.5] for _ in texts]

    monkeypatch.setattr(eb, "_fastembed_model", lambda: _FE())
    # sentence-transformers must NOT be consulted when fastembed handles it
    monkeypatch.setattr(eb, "_st_model", lambda: (_ for _ in ()).throw(AssertionError("ST used")))
    assert eb.embed_texts(["x", "y"]) == [[0.5, 0.5], [0.5, 0.5]]


def test_local_falls_back_to_sentence_transformers(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)
    monkeypatch.setattr(eb, "_fastembed_model", lambda: None)

    class _ST:
        def encode(self, texts):
            return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(eb, "_st_model", lambda: _ST())
    assert eb.embed_texts(["a"]) == [[0.1, 0.2]]


def test_no_local_backend_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)
    monkeypatch.setattr(eb, "_fastembed_model", lambda: None)
    monkeypatch.setattr(eb, "_st_model", lambda: None)
    assert eb.embed_texts(["a"]) is None


def test_fastembed_model_none_when_absent(monkeypatch) -> None:
    # fastembed may or may not be installed; the contract is that a failed load
    # degrades to None (never raises) regardless of the environment.
    import builtins

    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "fastembed":
            raise ImportError("fastembed unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    eb._FE_MODEL = None  # force a fresh load attempt
    assert eb._fastembed_model() is None


def test_available_false_when_no_backend(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_EMBED_URL", raising=False)
    monkeypatch.setattr(eb, "_lib_importable", lambda _name: False)
    assert eb.available() is False

