"""
Integration tests for ``runtime/platform/ui/app.py`` config endpoints.

Purpose
-------

These tests are **refactor guard-rails**. The endpoints exercised here
are about to be extracted out of the monolithic ``create_app`` into
their own ``create_config_router`` factory (see ``todos`` list). Without
integration coverage, that split is indistinguishable from "silently
broken in production" — there are no existing tests for these routes.

Endpoints covered
-----------------

    GET    /api/config/identity-lock          · privacy filter state
    PUT    /api/config/identity-lock          · admin toggle
    GET    /api/providers                     · LLM provider caps
    GET    /api/config/custom-models          · list
    PUT    /api/config/custom-models/{id}     · upsert + persist
    DELETE /api/config/custom-models/{id}     · remove + persist
    GET    /api/llm-models                    · merged list (Echo + custom)

Design notes
------------

* ``chdir(tmp_path)`` — the app hard-codes ``Path("data/custom_models.json")``
  relative to CWD. Redirecting CWD keeps each test hermetic (no cross-
  test pollution, no real repo data touched) and simultaneously locks
  the current on-disk persistence contract. If the future refactor
  moves the path elsewhere, these tests catch it.
* ``identity_filter_reset`` — the module holds a process-wide
  ``_RUNTIME_OVERRIDE``. Tests share the same Python process, so one
  leaving the filter off would cascade. Explicit cleanup.
* No stack injected — ``_register_custom_model`` degrades gracefully
  when ``stack.planner.router`` is absent (returns ``{"ok": False}``).
  That's the path we exercise: the persistence side works regardless.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui.app import create_app
from runtime.sensing.gateway.config_router import create_config_router
from runtime.sensing.model_router import ModelDispatchRouter, ModelRouter

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect CWD so ``Path("data/custom_models.json")`` lands in a
    scratch dir. Restoration is handled by monkeypatch."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def identity_filter_reset() -> Iterator[None]:
    """The identity-filter module keeps a process-wide runtime override.
    Clear it before AND after each test so no cross-test bleed."""
    from runtime.platform import identity_filter as _idf

    _idf.set_runtime_lock(None)
    yield
    _idf.set_runtime_lock(None)


@pytest.fixture
def client(isolated_cwd: Path) -> TestClient:
    """TestClient over a minimally-configured app — no stack, no
    agent registry, just enough to serve the config endpoints."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def secured_client(isolated_cwd: Path) -> tuple[TestClient, dict[str, str]]:
    """Same app surface, but with auth required so router-level config
    auth can be pinned independently of any outer middleware."""
    from runtime.safety.auth import Identity, IdentityStore

    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    store.add(
        Identity(actor_id="admin", roles=("admin",)),
        api_key_plaintext="sk-admin",
    )
    app = create_app(
        cocoloop_require_auth=True,
        cocoloop_identity_store=store,
    )
    return TestClient(app), {
        "user": "Bearer sk-alice",
        "admin": "Bearer sk-admin",
    }


# ═══════════════════════════════════════════════════════════
# GET /api/config/identity-lock
# ═══════════════════════════════════════════════════════════


class TestIdentityLockGet:
    def test_default_state_is_locked(self, client: TestClient) -> None:
        r = client.get("/api/config/identity-lock")
        assert r.status_code == 200
        data = r.json()
        assert data["locked"] is True
        assert data["source"] == "default"
        # Three documented unlock paths should be reported so the UI
        # can tell users how to bypass. Regression-safety for the
        # settings page that lists them verbatim.
        assert isinstance(data["unlock_paths"], list)
        assert len(data["unlock_paths"]) >= 3

    def test_runtime_override_reported_as_runtime(
        self,
        client: TestClient,
    ) -> None:
        from runtime.platform import identity_filter as _idf

        _idf.set_runtime_lock(False)
        r = client.get("/api/config/identity-lock")
        assert r.status_code == 200
        data = r.json()
        assert data["locked"] is False
        assert data["source"] == "runtime"


# ═══════════════════════════════════════════════════════════
# PUT /api/config/identity-lock
# ═══════════════════════════════════════════════════════════


class TestIdentityLockPut:
    def test_set_locked_false(self, client: TestClient) -> None:
        r = client.put(
            "/api/config/identity-lock",
            json={"locked": False},
        )
        assert r.status_code == 200
        assert r.json()["locked"] is False
        assert r.json()["source"] == "runtime"

    def test_set_locked_true(self, client: TestClient) -> None:
        client.put("/api/config/identity-lock", json={"locked": False})
        r = client.put(
            "/api/config/identity-lock",
            json={"locked": True},
        )
        assert r.status_code == 200
        assert r.json()["locked"] is True

    def test_null_clears_runtime_override(self, client: TestClient) -> None:
        """``null`` reverts to env/default. Important: without this
        path, a UI toggle is a one-way door — you can flip but never
        "forget" the override."""
        client.put("/api/config/identity-lock", json={"locked": False})
        r = client.put(
            "/api/config/identity-lock",
            json={"locked": None},
        )
        assert r.status_code == 200
        assert r.json()["source"] == "default"

    def test_rejects_non_bool_value(self, client: TestClient) -> None:
        r = client.put(
            "/api/config/identity-lock",
            json={"locked": "yes"},
        )
        assert r.status_code == 400


class TestConfigAuth:
    def test_identity_lock_requires_auth_when_enabled(
        self,
        secured_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = secured_client

        assert client.get("/api/config/identity-lock").status_code == 401
        assert (
            client.get(
                "/api/config/identity-lock",
                headers={"Authorization": headers["user"]},
            ).status_code
            == 200
        )

    def test_custom_model_put_requires_auth_when_enabled(
        self,
        secured_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = secured_client
        payload = {"provider": "anthropic", "model": "claude-sonnet-4-6"}

        assert (
            client.put(
                "/api/config/custom-models/claude-mirror",
                json=payload,
            ).status_code
            == 401
        )
        assert (
            client.put(
                "/api/config/custom-models/claude-mirror",
                json=payload,
                headers={"Authorization": headers["user"]},
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/config/custom-models/claude-mirror",
                json=payload,
                headers={"Authorization": headers["admin"]},
            ).status_code
            == 200
        )


# ═══════════════════════════════════════════════════════════
# GET /api/providers
# ═══════════════════════════════════════════════════════════


class TestProviders:
    def test_returns_capability_list(self, client: TestClient) -> None:
        r = client.get("/api/providers")
        assert r.status_code == 200
        data = r.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)
        # Each entry should have the documented capability fields.
        # We assert shape, not membership — the set of providers that
        # resolves depends on which optional SDKs are installed in the
        # test env (anthropic / google.genai / etc may be missing).
        for entry in data["providers"]:
            assert "name" in entry
            assert "supports_vision" in entry
            assert "supports_tool_use" in entry
            assert "supports_streaming" in entry
            assert "supports_prompt_cache" in entry

    def test_anthropic_always_present(self, client: TestClient) -> None:
        """Anthropic SDK ships as a core dep (agents use Claude by
        default), so it MUST resolve. Other providers are optional."""
        r = client.get("/api/providers")
        names = {p["name"] for p in r.json()["providers"]}
        assert "anthropic" in names


# ═══════════════════════════════════════════════════════════
# GET /api/config/custom-models · list
# ═══════════════════════════════════════════════════════════


class TestCustomModelsList:
    def test_empty_on_fresh_start(self, client: TestClient) -> None:
        r = client.get("/api/config/custom-models")
        assert r.status_code == 200
        assert r.json() == {"models": []}


# ═══════════════════════════════════════════════════════════
# PUT /api/config/custom-models/{id} · upsert
# ═══════════════════════════════════════════════════════════


class TestCustomModelsUpsert:
    def test_create_persists_to_disk(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        payload = {
            "name": "claude-mirror",
            "provider": "anthropic",
            "base_url": "https://mirror.example.com",
            "api_key": "sk-test",
            "model": "claude-sonnet-4-6",
            "max_tokens": 12000,
            "context_window": 256_000,
            "enable_1m_context": True,
            "supports_thinking": True,
            "default_reasoning_effort": "high",
            "supports_vision": False,
            "supports_tool_use": True,
            "omit_sampling_parameters": True,
            "compat_profile": "kimi_coding",
            "thinking_request_style": "none",
            "drop_tool_choice": True,
            "strict_tool_schema": True,
            "max_temperature": 0.2,
            "unsupported_request_fields": ["parallel_tool_calls"],
            "default_headers": {"X-Test": "yes"},
        }
        r = client.put(
            "/api/config/custom-models/claude-mirror",
            json=payload,
        )
        assert r.status_code == 200
        body = r.json()
        # api_key MUST NOT be echoed back — privacy invariant.
        assert "api_key" not in body["model"]
        assert "default_headers" not in body["model"]
        # But the presence flag should be true so the UI shows "set".
        assert body["model"]["has_api_key"] is True
        assert body["model"]["has_default_headers"] is True
        assert body["model"]["default_header_names"] == ["X-Test"]
        # Persisted file exists at the documented location.
        persisted = isolated_cwd / "data" / "custom_models.json"
        assert persisted.exists()
        # And it contains the full secret (not the wire form).
        import json

        stored = json.loads(persisted.read_text(encoding="utf-8"))
        assert stored["claude-mirror"]["api_key"] == "sk-test"
        assert "max_tokens" not in stored["claude-mirror"]
        assert stored["claude-mirror"]["context_window"] == 256_000
        assert stored["claude-mirror"]["enable_1m_context"] is True
        assert stored["claude-mirror"]["supports_thinking"] is True
        assert stored["claude-mirror"]["default_reasoning_effort"] == "high"
        assert stored["claude-mirror"]["supports_vision"] is False
        assert stored["claude-mirror"]["supports_tool_use"] is True
        assert stored["claude-mirror"]["omit_sampling_parameters"] is True
        assert stored["claude-mirror"]["compat_profile"] == "kimi_coding"
        assert stored["claude-mirror"]["thinking_request_style"] == "none"
        assert stored["claude-mirror"]["drop_tool_choice"] is True
        assert stored["claude-mirror"]["strict_tool_schema"] is True
        assert stored["claude-mirror"]["max_temperature"] == 0.2
        assert stored["claude-mirror"]["unsupported_request_fields"] == [
            "parallel_tool_calls",
        ]
        assert stored["claude-mirror"]["default_headers"] == {"X-Test": "yes"}

    def test_update_preserves_prior_api_key(
        self,
        client: TestClient,
    ) -> None:
        """PUT without api_key should NOT wipe the existing secret.
        This is the UX: user opens the form, toggles something minor,
        submits — they didn't retype the secret but shouldn't lose it."""
        client.put(
            "/api/config/custom-models/mid1",
            json={
                "name": "m1",
                "provider": "openai",
                "base_url": "https://x.test",
                "api_key": "sk-original",
                "model": "gpt-4",
            },
        )
        # Second PUT with no api_key
        r = client.put(
            "/api/config/custom-models/mid1",
            json={"name": "m1-renamed", "provider": "openai"},
        )
        assert r.status_code == 200
        assert r.json()["model"]["has_api_key"] is True

        # Verify by reading list
        listing = client.get("/api/config/custom-models").json()
        entry = next(m for m in listing["models"] if m["id"] == "mid1")
        assert entry["has_api_key"] is True
        assert entry["name"] == "m1-renamed"

    def test_update_can_clear_headers_and_false_capabilities(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/mid2",
            json={
                "name": "m2",
                "provider": "openai",
                "base_url": "https://x.test",
                "api_key": "sk",
                "model": "gpt-4",
                "supports_thinking": True,
                "supports_vision": True,
                "default_headers": {"X-Route": "a"},
            },
        )
        r = client.put(
            "/api/config/custom-models/mid2",
            json={
                "supports_thinking": False,
                "supports_vision": False,
                "default_headers": {},
            },
        )
        assert r.status_code == 200
        listing = client.get("/api/config/custom-models").json()
        entry = next(m for m in listing["models"] if m["id"] == "mid2")
        assert entry["supports_thinking"] is False
        assert entry["supports_vision"] is False
        assert "default_headers" not in entry
        assert entry["default_header_names"] == []
        assert entry["has_default_headers"] is False

    def test_default_reasoning_effort_roundtrip(
        self,
        client: TestClient,
    ) -> None:
        """default_reasoning_effort persists, survives partial PUTs,
        is cleared by explicit null, and rejects malformed values."""
        client.put(
            "/api/config/custom-models/mid3",
            json={
                "name": "m3",
                "provider": "openai",
                "base_url": "https://x.test",
                "api_key": "sk",
                "model": "gpt-4",
                "default_reasoning_effort": "high",
            },
        )
        # Partial PUT without the field keeps the prior value.
        client.put(
            "/api/config/custom-models/mid3",
            json={"name": "m3-renamed", "provider": "openai"},
        )
        listing = client.get("/api/config/custom-models").json()
        entry = next(m for m in listing["models"] if m["id"] == "mid3")
        assert entry["default_reasoning_effort"] == "high"

        # Explicit null clears the declaration (back to built-in default).
        client.put(
            "/api/config/custom-models/mid3",
            json={"default_reasoning_effort": None},
        )
        listing = client.get("/api/config/custom-models").json()
        entry = next(m for m in listing["models"] if m["id"] == "mid3")
        assert entry["default_reasoning_effort"] is None

        # Malformed values never persist — the runtime would fall back
        # to built-in defaults instead of sending garbage upstream.
        client.put(
            "/api/config/custom-models/mid4",
            json={
                "name": "m4",
                "provider": "openai",
                "base_url": "https://x.test",
                "api_key": "sk",
                "model": "gpt-4",
                "default_reasoning_effort": "bogus",
            },
        )
        listing = client.get("/api/config/custom-models").json()
        entry = next(m for m in listing["models"] if m["id"] == "mid4")
        assert entry["default_reasoning_effort"] is None

    def test_custom_model_list_never_echoes_header_secret_values(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/secret-headers",
            json={
                "name": "secret-headers",
                "provider": "openai",
                "base_url": "https://x.test",
                "api_key": "sk-model-secret",
                "model": "gpt-4",
                "default_headers": {
                    "Authorization": "Bearer header-secret",
                    "X-Route-Token": "route-secret",
                },
            },
        )

        data = client.get("/api/config/custom-models").json()
        blob = repr(data)
        entry = next(m for m in data["models"] if m["id"] == "secret-headers")
        assert "api_key" not in entry
        assert "default_headers" not in entry
        assert entry["has_api_key"] is True
        assert entry["has_default_headers"] is True
        assert entry["default_header_names"] == [
            "Authorization",
            "X-Route-Token",
        ]
        assert "sk-model-secret" not in blob
        assert "header-secret" not in blob
        assert "route-secret" not in blob

    def test_registers_entry_id_and_concrete_model_ids(
        self,
        isolated_cwd: Path,
    ) -> None:
        class _Fallback(ModelRouter):
            def call(self, request):
                raise AssertionError("fallback should not be called")

        dispatcher = ModelDispatchRouter(fallback=_Fallback())
        stack = SimpleNamespace(planner=SimpleNamespace(router=dispatcher))
        app = FastAPI()
        app.include_router(create_config_router(stack=stack).router)
        client = TestClient(app)

        r = client.put(
            "/api/config/custom-models/kimi-code",
            json={
                "name": "Kimi Code",
                "provider": "openai",
                "base_url": "https://api.kimi.com/coding/v1",
                "api_key": "sk-test",
                "models": ["kimi-for-coding", "kimi-for-coding-fast"],
                "display_name": "K2.7 Code",
                "supports_tool_use": True,
                "omit_sampling_parameters": True,
                "enable_1m_context": True,
            },
        )
        assert r.status_code == 200
        assert dispatcher.has("kimi-code")
        assert dispatcher.has("kimi-for-coding")
        assert dispatcher.has("kimi-for-coding-fast")
        assert dispatcher.has("kimi-for-coding::1m")
        assert dispatcher.has("kimi-for-coding-fast::1m")

        r = client.put(
            "/api/config/custom-models/kimi-code",
            json={"models": ["kimi-for-coding-v2"]},
        )
        assert r.status_code == 200
        assert dispatcher.has("kimi-code")
        assert not dispatcher.has("kimi-for-coding")
        assert not dispatcher.has("kimi-for-coding-fast")
        assert not dispatcher.has("kimi-for-coding::1m")
        assert not dispatcher.has("kimi-for-coding-fast::1m")
        assert dispatcher.has("kimi-for-coding-v2")
        assert dispatcher.has("kimi-for-coding-v2::1m")

        r = client.delete("/api/config/custom-models/kimi-code")
        assert r.status_code == 200
        assert r.json()["removed"] is True
        assert not dispatcher.has("kimi-code")
        assert not dispatcher.has("kimi-for-coding-v2")
        assert not dispatcher.has("kimi-for-coding-v2::1m")

    def test_selection_ids_dispatch_exact_endpoint_variant_and_profile(
        self,
        isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.sensing.model_router.models import ModelRequest, ModelResponse

        class _Fallback(ModelRouter):
            def call(self, request):
                raise AssertionError("fallback should not be called")

        class _RecordingOpenAI(ModelRouter):
            def __init__(
                self,
                *,
                base_url: str,
                default_model: str,
                **_kwargs,
            ) -> None:
                self.base_url = base_url
                self.default_model = default_model

            def call(self, request):
                return ModelResponse(
                    text=self.base_url,
                    model=request.model,
                    provider="recording",
                )

        monkeypatch.setattr(
            "runtime.sensing.model_router.openai_router.OpenAIModelRouter",
            _RecordingOpenAI,
        )
        dispatcher = ModelDispatchRouter(fallback=_Fallback())
        stack = SimpleNamespace(planner=SimpleNamespace(router=dispatcher))
        app = FastAPI()
        app.include_router(create_config_router(stack=stack).router)
        client = TestClient(app)

        for entry_id, base_url, models in (
            (
                "primary",
                "https://primary.example/v1",
                ["economy-model", "shared-model"],
            ),
            ("backup", "https://backup.example/v1", ["shared-model"]),
        ):
            response = client.put(
                f"/api/config/custom-models/{entry_id}",
                json={
                    "provider": "openai",
                    "base_url": base_url,
                    "api_key": "not-exposed",
                    "models": models,
                    "enable_1m_context": True,
                },
            )
            assert response.status_code == 200

        rows = [
            row
            for row in client.get("/api/llm-models").json()["models"]
            if row.get("entry_id") in {"primary", "backup"}
        ]
        assert len(rows) == 6
        assert len({row["selection_id"] for row in rows}) == 6
        assert all("not-exposed" not in row["selection_id"] for row in rows)
        configured = client.get("/api/config/custom-models").json()["models"]
        configured_ids = {
            selection_id
            for entry in configured
            if entry["id"] in {"primary", "backup"}
            for selection_id in entry["selection_ids"]
        }
        assert configured_ids == {row["selection_id"] for row in rows}

        expected = {
            ("primary", "economy-model", "default"): "primary.example",
            ("primary", "economy-model", "1m"): "primary.example",
            ("primary", "shared-model", "default"): "primary.example",
            ("primary", "shared-model", "1m"): "primary.example",
            ("backup", "shared-model", "default"): "backup.example",
            ("backup", "shared-model", "1m"): "backup.example",
        }
        for row in rows:
            key = (row["entry_id"], row["model"], row["context_profile"])
            response = dispatcher.call(
                ModelRequest(model=row["selection_id"], messages=[]),
            )
            assert response.model == row["model"]
            assert expected[key] in response.text

        # Entry aliases and concrete model aliases are retained for old clients.
        assert dispatcher.has("primary")
        assert dispatcher.has("economy-model")
        assert dispatcher.has("shared-model")
        assert dispatcher.has("shared-model::1m")

        # Deleting the entry that most recently claimed a legacy alias must
        # restore that alias to the remaining entry. Stored threads and older
        # API clients can still carry the bare upstream id instead of the new
        # row-level selection id.
        assert client.delete("/api/config/custom-models/backup").status_code == 200
        assert dispatcher.has("shared-model")
        assert dispatcher.has("shared-model::1m")
        legacy = dispatcher.call(ModelRequest(model="shared-model", messages=[]))
        assert legacy.text == "https://primary.example/v1"
        assert legacy.model == "shared-model"


class TestCustomModelsExternalEdits:
    """The file is hand-edited while the server runs.

    ``custom_models.json`` is where an operator sets base urls and api
    keys, so it gets edited by hand — and the router only reads it at
    startup. A save that wrote the boot-time snapshot therefore erased
    every edit made since boot. These pin the merge that replaced it.
    """

    def _write(self, cwd: Path, data: dict) -> Path:
        path = cwd / "data" / "custom_models.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _read(self, cwd: Path) -> dict:
        return json.loads((cwd / "data" / "custom_models.json").read_text(encoding="utf-8"))

    def test_concurrent_upserts_persist_every_entry_atomically(
        self,
        isolated_cwd: Path,
    ) -> None:
        persisted = isolated_cwd / "data" / "custom_models.json"
        config = create_config_router(custom_models_path=persisted)
        endpoint = next(
            route.endpoint
            for route in config.router.routes
            if route.path == "/api/config/custom-models/{model_id}" and "PUT" in route.methods
        )

        def _upsert(index: int) -> None:
            endpoint(
                model_id=f"concurrent-{index}",
                body={
                    "base_url": f"https://model-{index}.example/v1",
                    "models": [f"model-{index}"],
                },
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_upsert, range(24)))

        stored = json.loads(persisted.read_text(encoding="utf-8"))
        assert set(stored) == {f"concurrent-{index}" for index in range(24)}
        assert not list(persisted.parent.glob(f".{persisted.name}.*.tmp"))

    def test_an_unrelated_hand_added_entry_survives_an_upsert(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        # Appears only after the router has already loaded, exactly as a
        # hand edit to a running server does.
        self._write(isolated_cwd, {"typed-by-hand": {"base_url": "https://x/v1"}})

        r = client.put(
            "/api/config/custom-models/added-via-api",
            json={"name": "added-via-api", "base_url": "https://y/v1"},
        )
        assert r.status_code == 200

        on_disk = self._read(isolated_cwd)
        assert "added-via-api" in on_disk
        assert on_disk["typed-by-hand"]["base_url"] == "https://x/v1"

    def test_a_hand_edited_api_key_is_not_reverted(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        r = client.put(
            "/api/config/custom-models/relay",
            json={"name": "relay", "base_url": "https://r/v1", "api_key": "sk-old"},
        )
        assert r.status_code == 200

        # Operator rotates the key by hand, then touches something else.
        data = self._read(isolated_cwd)
        data["relay"]["api_key"] = "sk-rotated"
        self._write(isolated_cwd, data)

        client.put(
            "/api/config/custom-models/other",
            json={"name": "other", "base_url": "https://o/v1"},
        )

        # The rotated key belongs to an id we did not touch in this
        # request, so the merge must leave it alone.
        assert self._read(isolated_cwd)["relay"]["api_key"] == "sk-rotated"

    def test_a_deleted_entry_stays_deleted(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        client.put(
            "/api/config/custom-models/doomed",
            json={"name": "doomed", "base_url": "https://d/v1"},
        )
        assert client.delete("/api/config/custom-models/doomed").status_code == 200
        assert "doomed" not in self._read(isolated_cwd)

        # A later save merges over disk; the delete must not come back.
        client.put(
            "/api/config/custom-models/keeper",
            json={"name": "keeper", "base_url": "https://k/v1"},
        )
        on_disk = self._read(isolated_cwd)
        assert "doomed" not in on_disk
        assert "keeper" in on_disk

    def test_re_adding_a_deleted_id_retracts_the_delete(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        client.put(
            "/api/config/custom-models/flip",
            json={"name": "flip", "base_url": "https://1/v1"},
        )
        client.delete("/api/config/custom-models/flip")
        client.put(
            "/api/config/custom-models/flip",
            json={"name": "flip", "base_url": "https://2/v1"},
        )

        on_disk = self._read(isolated_cwd)
        assert on_disk["flip"]["base_url"] == "https://2/v1"


# ═══════════════════════════════════════════════════════════
# GET /api/config/custom-models/compat-diagnostics
# ═══════════════════════════════════════════════════════════


class TestCustomModelCompatDiagnostics:
    def test_builtin_openai_compat_profile_catalog_is_dry_run_matrix(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/config/openai-compat-profiles")
        assert r.status_code == 200
        data = r.json()
        assert data["schema"] == "echo.openai_compat_profile_catalog.v1"
        assert data["total"] >= 10
        assert data["live_smoke"]["schema"] == ("echo.openai_compat_live_smoke_readiness.v1")
        assert data["live_smoke"]["provider_count"] == data["total"]
        assert data["live_smoke"]["chat_smoke_enabled"] is False
        assert data["live_smoke"]["runnable_chat_provider_count"] == 0

        by_id = {row["id"]: row for row in data["diagnostics"]}
        assert {
            "deepseek",
            "kimi",
            "kimi_coding",
            "qwen",
            "glm",
            "doubao",
            "minimax",
            "hunyuan",
            "baichuan",
            "siliconflow",
        }.issubset(by_id)

        kimi_coding = by_id["kimi_coding"]
        assert kimi_coding["built_in"] is True
        assert kimi_coding["applicable"] is True
        assert kimi_coding["has_api_key"] is False
        assert kimi_coding["sample_model"] == "K2.7-Code"
        assert kimi_coding["smoke_provider_configured"] is True
        assert kimi_coding["resolver_check"] == {
            "base_url_resolves_to": "kimi_coding",
            "model_resolves_to": "kimi_coding",
            "model_alias_mismatch": False,
            "passed": True,
        }
        assert kimi_coding["upstreams"][0]["model"] == "K2.7-Code"
        assert kimi_coding["upstreams"][0]["profile"] == "kimi_coding"
        assert "drop_sampling_parameters" in kimi_coding["upstreams"][0]["normalization_hints"]
        assert (
            "parallel_tool_calls" in kimi_coding["upstreams"][0]["normalization"]["removed_fields"]
        )
        assert kimi_coding["upstreams"][0]["dry_run"] is True
        assert kimi_coding["upstreams"][0]["risk_level"] in {"medium", "high"}
        assert "sampling_parameters_removed" in kimi_coding["upstreams"][0]["risk_reasons"]
        assert kimi_coding["upstreams"][0]["request_contract"]["schema"] == (
            "echo.openai_compat_request_contract_probe.v1"
        )
        assert kimi_coding["upstreams"][0]["request_contract"]["contract_ready"] is True
        assert (
            "parallel_tool_calls"
            in kimi_coding["upstreams"][0]["request_contract"]["removed_fields"]
        )
        assert (
            "strict_provider_may_drop_optional_features"
            in kimi_coding["upstreams"][0]["risk_reasons"]
        )
        capability_status = {
            item["capability"]: item["status"]
            for item in kimi_coding["upstreams"][0]["capability_matrix"]
        }
        assert capability_status["chat_completion"] == "pass"
        assert capability_status["streaming"] == "warn"
        assert capability_status["tool_calling"] == "warn"
        assert capability_status["reasoning_request"] == "warn"
        assert capability_status["usage_accounting"] == "warn"
        assert capability_status["fallback_retries"] == "pass"
        assert {
            "frequency_penalty",
            "presence_penalty",
            "reasoning_effort",
            "temperature",
            "thinking",
            "top_p",
        }.issubset(set(kimi_coding["upstreams"][0]["normalization"]["removed_fields"]))

        qwen = by_id["qwen"]["upstreams"][0]
        assert "strict_tool_schema" in qwen["normalization_hints"]
        assert "tools" in qwen["normalization"]["changed_fields"]
        assert "parallel_tool_calls" in qwen["normalization"]["removed_fields"]
        assert qwen["risk_level"] == "medium"
        assert "tool_schema_normalized" in qwen["risk_reasons"]
        assert "core_request_field_removed" not in qwen["risk_reasons"]
        qwen_capabilities = {item["capability"]: item for item in qwen["capability_matrix"]}
        assert qwen_capabilities["tool_calling"]["status"] == "warn"
        assert "tool schema normalized" in qwen_capabilities["tool_calling"]["notes"]
        qwen_reasons = {item["reason"] for item in qwen["fallback_retries"]}
        assert "rename_max_tokens" in qwen_reasons

        siliconflow = by_id["siliconflow"]
        assert siliconflow["resolver_check"] == {
            "base_url_resolves_to": "siliconflow",
            "model_resolves_to": "deepseek",
            "model_alias_mismatch": True,
            "passed": True,
        }

    def test_openai_compat_profile_catalog_reports_live_smoke_readiness(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_LIVE_MODEL_SMOKE", "1")
        monkeypatch.setenv("ECHO_LIVE_MODEL_TOOL_SMOKE", "1")
        monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-secret")
        monkeypatch.setenv("KIMI_SMOKE_MODEL", "moonshot-v1-auto")

        r = client.get("/api/config/openai-compat-profiles")
        assert r.status_code == 200
        data = r.json()
        smoke = data["live_smoke"]
        assert "sk-kimi-secret" not in repr(smoke)
        assert smoke["chat_smoke_enabled"] is True
        assert smoke["tool_smoke_enabled"] is True
        assert smoke["configured_provider_count"] >= 1
        assert smoke["runnable_chat_provider_count"] >= 1

        by_id = {row["id"]: row for row in smoke["providers"]}
        kimi = by_id["kimi"]
        assert kimi["has_api_key"] is True
        assert kimi["configured_api_key_env"] == "KIMI_API_KEY"
        assert kimi["model"] == "moonshot-v1-auto"
        assert kimi["uses_default_model"] is False
        assert kimi["chat_smoke_runnable"] is True
        assert kimi["tool_smoke_runnable"] is True

        deepseek = by_id["deepseek"]
        assert deepseek["has_api_key"] is False
        assert deepseek["configured_api_key_env"] == ""
        assert deepseek["chat_smoke_runnable"] is False

    def test_openai_compat_diagnostics_are_dry_run_and_secret_safe(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/kimi-code",
            json={
                "name": "Kimi Code",
                "provider": "openai",
                "base_url": "https://api.kimi.com/coding/v1",
                "api_key": "sk-realish-secret",
                "models": ["kimi-k2.7-code"],
                "compat_profile": "kimi_coding",
                "drop_tool_choice": True,
                "strict_tool_schema": True,
                "unsupported_request_fields": ["parallel_tool_calls"],
                "default_headers": {
                    "User-Agent": "EchoSmoke/1.0",
                    "X-Route-Token": "route-secret",
                },
            },
        )

        r = client.get("/api/config/custom-models/compat-diagnostics")
        assert r.status_code == 200
        data = r.json()
        assert data["schema"] == "echo.openai_compat_diagnostics.v1"
        assert data["total"] == 1

        row = data["diagnostics"][0]
        assert row["id"] == "kimi-code"
        assert row["applicable"] is True
        assert row["has_api_key"] is True
        assert row["default_header_names"] == ["User-Agent", "X-Route-Token"]
        blob = repr(data)
        assert "sk-realish-secret" not in blob
        assert "route-secret" not in blob

        upstream = row["upstreams"][0]
        assert upstream["profile"] == "kimi_coding"
        assert upstream["profile_summary"]["id"] == "kimi_coding"
        assert upstream["compat_score"] == upstream["profile_summary"]["compat_score"]
        assert "drop_sampling_parameters" in upstream["normalization_hints"]
        assert "strict_tool_schema" in upstream["normalization_hints"]
        assert any("coding endpoint" in note for note in upstream["compatibility_notes"])
        assert upstream["dry_run"] is True
        assert upstream["risk_level"] == "high"
        assert "tool_schema_normalized" in upstream["risk_reasons"]
        assert "tool_calling_control_removed" in upstream["risk_reasons"]
        assert upstream["request_contract"]["profile_id"] == "kimi_coding"
        assert upstream["request_contract"]["contract_ready"] is True
        assert (
            upstream["request_contract"]["normalized_payload"]
            == upstream["normalization"]["payload"]
        )
        assert upstream["request_contract"]["fallback_retries"] == upstream["fallback_retries"]
        capability_matrix = {item["capability"]: item for item in upstream["capability_matrix"]}
        assert capability_matrix["chat_completion"]["status"] == "pass"
        assert capability_matrix["tool_calling"]["status"] == "warn"
        assert capability_matrix["structured_output"]["status"] == "warn"
        assert capability_matrix["fallback_retries"]["status"] == "pass"
        assert "dry_run_representative_400" in capability_matrix["fallback_retries"]["notes"]
        assert upstream["strict_tool_schema"] is True
        removed = set(upstream["normalization"]["removed_fields"])
        assert {
            "frequency_penalty",
            "parallel_tool_calls",
            "presence_penalty",
            "reasoning_effort",
            "temperature",
            "thinking",
            "tool_choice",
            "top_p",
        }.issubset(removed)
        assert "temperature" not in upstream["normalization"]["normalized_fields"]
        assert "parallel_tool_calls" not in upstream["normalization"]["payload"]

        reasons = {item["reason"] for item in upstream["fallback_retries"]}
        assert "rename_max_tokens" in reasons

    def test_compat_diagnostics_marks_non_openai_entries_not_applicable(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/claude-mirror",
            json={
                "name": "Claude Mirror",
                "provider": "anthropic",
                "api_key": "sk-claude-secret",
                "models": ["claude-sonnet-4-6"],
            },
        )

        r = client.get(
            "/api/config/custom-models/compat-diagnostics",
            params={"model_id": "claude-mirror"},
        )
        assert r.status_code == 200
        row = r.json()["diagnostics"][0]
        assert row["applicable"] is False
        assert row["reason"] == "provider is not OpenAI-compatible"
        assert "sk-claude-secret" not in repr(row)


# ═══════════════════════════════════════════════════════════
# DELETE /api/config/custom-models/{id}
# ═══════════════════════════════════════════════════════════


class TestCustomModelsDelete:
    def test_delete_removes_from_list_and_disk(
        self,
        client: TestClient,
        isolated_cwd: Path,
    ) -> None:
        client.put(
            "/api/config/custom-models/delme",
            json={
                "name": "x",
                "provider": "openai",
                "base_url": "https://x.test",
                "api_key": "sk",
                "model": "gpt-4",
            },
        )
        r = client.delete("/api/config/custom-models/delme")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # List is empty again
        assert client.get("/api/config/custom-models").json() == {
            "models": [],
        }
        # Disk reflects the delete
        import json

        persisted = isolated_cwd / "data" / "custom_models.json"
        stored = json.loads(persisted.read_text(encoding="utf-8"))
        assert "delme" not in stored

    def test_delete_missing_is_idempotent(
        self,
        client: TestClient,
    ) -> None:
        """Double-delete shouldn't 500. UI could race two Delete clicks
        and we want the second to be a no-op rather than an error."""
        r = client.delete("/api/config/custom-models/never-existed")
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ═══════════════════════════════════════════════════════════
# GET /api/llm-models · custom models appear in merged list
# ═══════════════════════════════════════════════════════════


class TestLlmModelsMerge:
    def test_one_million_context_is_explicit_and_auto_detected(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/deepseek",
            json={
                "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
                "base_url": "https://example.test/v1",
                "api_key": "sk-x",
            },
        )
        rows = [
            row
            for row in client.get("/api/llm-models").json()["models"]
            if row.get("entry_id") == "deepseek"
        ]
        assert len(rows) == 4
        default_rows = [row for row in rows if row["context_profile"] == "default"]
        one_million_rows = [row for row in rows if row["context_profile"] == "1m"]
        # The default profile reports whatever the upstream really has —
        # for these ids the models.dev snapshot says 1M, which is the
        # point of the auto-detection. It used to be a flat 256k guess
        # here while context budgeting already resolved the true window,
        # so the UI understated it. What this test pins is the profile
        # split, not a specific number.
        assert {row["context_window"] for row in default_rows} == {1_000_000}
        assert {row["context_window"] for row in one_million_rows} == {1_000_000}
        assert all(row["id"].endswith("::1m") for row in one_million_rows)
        assert {row["model"] for row in default_rows} == {row["model"] for row in one_million_rows}

    def test_custom_model_appears_in_merged_list(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/mirror-x",
            json={
                "name": "Mirror X",
                "provider": "anthropic",
                "base_url": "https://mirror.test",
                "api_key": "sk-x",
                "model": "claude-sonnet-4-6",
                "display_name": "Mirror X",
                "supports_thinking": True,
                "supports_vision": True,
            },
        )
        r = client.get("/api/llm-models")
        assert r.status_code == 200
        data = r.json()
        # The merged endpoint must include the custom model alongside
        # Echo-native presets. Shape sanity:
        assert "models" in data or "data" in data or isinstance(data, dict)
        # Loosely assert the custom id is somewhere in the response
        # (the exact structure isn't locked — depends on configured
        # custom models, which we're not mocking here).
        blob = repr(data)
        assert "mirror-x" in blob or "Mirror X" in blob
        assert "supports_thinking" in blob

    def test_llm_models_do_not_advertise_retired_bundled_presets(
        self,
        client: TestClient,
    ) -> None:
        """The model picker only exposes models from active providers or
        explicit custom configuration, not retired bundled presets.
        """
        r = client.get("/api/llm-models")
        assert r.status_code == 200
        models = r.json()["models"]
        ids = {row.get("id") for row in models}
        names = {row.get("display_name") for row in models}
        assert "minimax-m2.5" not in ids
        assert "MiniMax M2.5" not in names
        assert "kimi-k2.5" not in ids
        assert "Kimi K2.5" not in names

    def test_custom_model_flags_appear_in_merged_list(
        self,
        client: TestClient,
    ) -> None:
        client.put(
            "/api/config/custom-models/kimi-code",
            json={
                "name": "Kimi Code",
                "provider": "openai",
                "base_url": "https://api.kimi.com/coding/v1",
                "api_key": "sk-x",
                "models": ["kimi-for-coding"],
                "display_name": "K2.7 Code",
                "omit_sampling_parameters": True,
                "compat_profile": "kimi_coding",
                "thinking_request_style": "none",
                "drop_tool_choice": True,
                "strict_tool_schema": True,
                "max_temperature": 0.2,
            },
        )

        data = client.get("/api/llm-models").json()
        rows = [row for row in data["models"] if row.get("entry_id") == "kimi-code"]
        assert rows
        assert rows[0]["id"] == "kimi-for-coding"
        assert rows[0]["display_name"] == "K2.7 Code"
        assert rows[0]["supports_tool_use"] is True
        assert rows[0]["omit_sampling_parameters"] is True
        assert rows[0]["compat_profile"] == "kimi_coding"
        assert rows[0]["thinking_request_style"] == "none"
        assert rows[0]["drop_tool_choice"] is True
        assert rows[0]["strict_tool_schema"] is True
        assert rows[0]["max_temperature"] == 0.2


# ═══════════════════════════════════════════════════════════
# Startup rehydration · custom models survive process restart
# ═══════════════════════════════════════════════════════════


class TestStartupHydration:
    def test_disk_state_loaded_on_create_app(
        self,
        isolated_cwd: Path,
    ) -> None:
        """Create an app, add a model, throw the app away, create a
        new one — the new app must see the model. This is the
        persistence contract the docstring promises."""
        app1 = create_app()
        client1 = TestClient(app1)
        client1.put(
            "/api/config/custom-models/persist-me",
            json={
                "name": "persist-me",
                "provider": "openai",
                "base_url": "https://p.test",
                "api_key": "sk-p",
                "model": "gpt-4",
            },
        )

        # Fresh app — reads from the same data/custom_models.json
        app2 = create_app()
        client2 = TestClient(app2)
        listing = client2.get("/api/config/custom-models").json()
        ids = {m["id"] for m in listing["models"]}
        assert "persist-me" in ids


# ═══════════════════════════════════════════════════════════
# GET /api/feature-flags
# ═══════════════════════════════════════════════════════════


class TestFeatureFlagsEndpoint:
    def test_lists_registered_flags(self, client: TestClient) -> None:
        r = client.get("/api/feature-flags")
        assert r.status_code == 200
        data = r.json()
        assert "flags" in data
        names = {entry["name"] for entry in data["flags"]}
        # A few canonical flags that must always be present.
        assert "regeneration.enabled" in names
        assert "safety.invariants_enabled" in names
        assert "ui.ambient_suggestions" in names

    def test_each_entry_has_full_schema(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/feature-flags")
        for entry in r.json()["flags"]:
            assert set(entry.keys()) >= {
                "name",
                "value",
                "source",
                "default",
                "description",
                "experimental",
                "primary_env",
                "legacy_env",
            }

    def test_reload_endpoint_returns_fresh_snapshot(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        # Default for camouflage.enabled is False; flip via env then
        # reload through the endpoint and confirm the new value
        # comes through.
        monkeypatch.setenv("ECHO_FF_CAMOUFLAGE_ENABLED", "1")
        r = client.post("/api/feature-flags/reload")
        assert r.status_code == 200
        entry = next(e for e in r.json()["flags"] if e["name"] == "camouflage.enabled")
        assert entry["value"] is True
        assert entry["source"] == "env"


# ═══════════════════════════════════════════════════════════
# POST /api/config/custom-models/test · vision auto-detection
# ═══════════════════════════════════════════════════════════


class TestCustomModelTestVisionProbe:
    """The test endpoint probes whether the model accepts image input
    and reports it as ``supports_vision`` so the UI can gate the vision
    toggle. The probe helper maps an image-canary outcome to a tri-state:
    accepted → True, upstream ``4xx`` → False (model has no vision),
    transport failure → None (inconclusive)."""

    def test_probe_helper_tri_state(self) -> None:
        from runtime.platform.models.llm import ModelResponse
        from runtime.sensing.gateway._config_endpoints_custom_models import (
            _probe_vision_support,
        )
        from runtime.sensing.model_router.openai_router import OpenAIRouterError

        class VisionAccepted:
            def call(self, request):  # noqa: ARG002
                return ModelResponse(text="ok")

        class VisionRejected:
            def call(self, request):  # noqa: ARG002
                raise OpenAIRouterError("http_400: this model does not support image input")

        class VisionInconclusive:
            def call(self, request):  # noqa: ARG002
                raise TimeoutError("upstream timed out")

        assert _probe_vision_support(VisionAccepted(), model="m") is True
        assert _probe_vision_support(VisionRejected(), model="m") is False
        assert _probe_vision_support(VisionInconclusive(), model="m") is None

    def test_probe_helper_builds_image_canary(self) -> None:
        from runtime.platform.models.llm import ModelResponse
        from runtime.sensing.gateway._config_endpoints_custom_models import (
            _probe_vision_support,
        )

        captured: dict[str, object] = {}

        class CapturingRouter:
            def call(self, request):  # noqa: ARG002
                captured["request"] = request
                return ModelResponse(text="ok")

        result = _probe_vision_support(CapturingRouter(), model="deepseek-chat")

        assert result is True
        req = captured["request"]
        assert req.model == "deepseek-chat"  # type: ignore[attr-defined]
        assert req.images_b64  # type: ignore[attr-defined]
        assert len(req.messages) == 1  # type: ignore[attr-defined]

    def test_test_endpoint_reports_supports_vision(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.models.llm import ModelResponse
        from runtime.sensing.gateway import _config_endpoints_custom_models as endpoints
        from runtime.sensing.model_router.openai_router import OpenAIModelRouter

        # Stub the router so no network is hit; the text ping and the
        # vision canary both resolve to a plain "pong".
        monkeypatch.setattr(
            OpenAIModelRouter,
            "call",
            lambda self, request: ModelResponse(text="pong"),  # noqa: ARG005
        )
        # Pin the probe outcome so the assertion targets the wiring,
        # not the upstream behaviour.
        monkeypatch.setattr(
            endpoints,
            "_probe_vision_support",
            lambda router, *, model: True,  # noqa: ARG005
        )

        r = client.post(
            "/api/config/custom-models/test",
            json={
                "provider": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model": "deepseek-chat",
            },
        )

        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["supports_vision"] is True

    def test_test_endpoint_locks_vision_off_for_rejected_model(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.platform.models.llm import ModelResponse
        from runtime.sensing.gateway import _config_endpoints_custom_models as endpoints
        from runtime.sensing.model_router.openai_router import OpenAIModelRouter

        monkeypatch.setattr(
            OpenAIModelRouter,
            "call",
            lambda self, request: ModelResponse(text="pong"),  # noqa: ARG005
        )
        monkeypatch.setattr(
            endpoints,
            "_probe_vision_support",
            lambda router, *, model: False,  # noqa: ARG005
        )

        r = client.post(
            "/api/config/custom-models/test",
            json={
                "provider": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model": "deepseek-chat",
            },
        )

        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["supports_vision"] is False
