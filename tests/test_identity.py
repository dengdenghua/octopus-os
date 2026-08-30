"""Implementation note."""

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest
from runtime.safety.auth import (
    Identity,
    IdentityStore,
    hash_api_key,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIdentityBasics:
    def test_identity_frozen(self):
        i = Identity(actor_id="alice", roles=("admin",))
        with pytest.raises(dataclasses.FrozenInstanceError):
            i.actor_id = "bob"  # Implementation note.

    def test_identity_defaults(self):
        i = Identity(actor_id="alice")
        assert i.roles == ()
        assert i.metadata == {}

    def test_hash_api_key_uses_unique_salts_and_verifies(self):
        h1 = hash_api_key("sk-test-123")
        h2 = hash_api_key("sk-test-123")
        assert h1 != h2
        assert h1.startswith("pbkdf2_sha256$")
        first = Identity(actor_id="first")
        second = Identity(actor_id="second")
        s = IdentityStore()
        s.add(first, api_key_hash=h1)
        s.add(second, api_key_hash=h2)
        assert s.verify_api_key("sk-test-123") == first

    def test_hash_api_key_different_for_different_input(self):
        assert hash_api_key("a") != hash_api_key("b")

    def test_hash_api_key_empty_rejected(self):
        with pytest.raises(ValueError):
            hash_api_key("")


# ═══════════════════════════════════════════════════════════
# IdentityStore CRUD
# ═══════════════════════════════════════════════════════════


class TestStoreCRUD:
    def test_empty_store(self):
        s = IdentityStore()
        assert len(s) == 0
        assert s.get("alice") is None
        assert s.verify_api_key("anything") is None

    def test_add_and_query_by_actor_id(self):
        s = IdentityStore()
        alice = Identity(actor_id="alice", roles=("admin",))
        s.add(alice, api_key_plaintext="sk-alice-123")
        assert len(s) == 1
        assert s.get("alice") == alice
        assert "alice" in s.actor_ids()

    def test_verify_plaintext_after_add(self):
        s = IdentityStore()
        alice = Identity(actor_id="alice")
        s.add(alice, api_key_plaintext="sk-secret")
        assert s.verify_api_key("sk-secret") == alice
        assert s.verify_api_key("wrong-key") is None

    def test_add_with_precomputed_hash(self):
        s = IdentityStore()
        hashed = hash_api_key("sk-pre")
        s.add(Identity(actor_id="bob"), api_key_hash=hashed)
        assert s.verify_api_key("sk-pre").actor_id == "bob"

    def test_add_with_bare_hex_hash(self):
        """Implementation note."""
        s = IdentityStore()
        raw_hex = hashlib.sha256(b"sk-raw").hexdigest()
        s.add(Identity(actor_id="carol"), api_key_hash=raw_hex)
        assert s.verify_api_key("sk-raw").actor_id == "carol"

    def test_add_without_key_allowed(self):
        """Implementation note."""
        s = IdentityStore()
        s.add(Identity(actor_id="legacy"))
        assert s.get("legacy") is not None
        # Implementation note.
        assert s.verify_api_key("sk-legacy") is None

    def test_duplicate_actor_id_rejected(self):
        s = IdentityStore()
        s.add(Identity(actor_id="alice"))
        with pytest.raises(ValueError, match="duplicate actor_id"):
            s.add(Identity(actor_id="alice"))

    def test_api_key_collision_rejected(self):
        s = IdentityStore()
        s.add(Identity(actor_id="alice"), api_key_plaintext="sk-dup")
        with pytest.raises(ValueError, match="collision"):
            s.add(Identity(actor_id="bob"), api_key_plaintext="sk-dup")

    def test_provide_both_key_forms_rejected(self):
        s = IdentityStore()
        with pytest.raises(ValueError):
            s.add(
                Identity(actor_id="x"),
                api_key_plaintext="sk-a",
                api_key_hash=hash_api_key("sk-a"),
            )

    def test_remove_clears_both_indexes(self):
        s = IdentityStore()
        s.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
        assert s.remove("alice") is True
        assert s.get("alice") is None
        assert s.verify_api_key("sk-alice") is None
        assert s.remove("alice") is False  # Implementation note.

    def test_bad_hash_format_rejected(self):
        s = IdentityStore()
        with pytest.raises(ValueError, match="hash format"):
            s.add(Identity(actor_id="x"), api_key_hash="not_a_hash")


# ═══════════════════════════════════════════════════════════
# YAML load
# ═══════════════════════════════════════════════════════════


class TestYAMLLoad:
    def test_load_basic(self, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "identities.yaml"
        h_alice = hash_api_key("sk-alice-456")
        h_bob = hash_api_key("sk-bob-789")
        path.write_text(
            "identities:\n"
            "  - actor_id: alice\n"
            "    roles: [admin, auditor]\n"
            f"    api_key_hash: {h_alice}\n"
            "    metadata:\n"
            "      email: alice@corp\n"
            "  - actor_id: bob\n"
            "    roles: [user]\n"
            f"    api_key_hash: {h_bob}\n",
            encoding="utf-8",
        )
        store = IdentityStore.load_from_yaml(path)
        assert len(store) == 2

        alice = store.get("alice")
        assert alice is not None
        assert alice.roles == ("admin", "auditor")
        assert alice.metadata["email"] == "alice@corp"

        # Implementation note.
        assert store.verify_api_key("sk-alice-456").actor_id == "alice"
        assert store.verify_api_key("sk-bob-789").actor_id == "bob"
        assert store.verify_api_key("sk-wrong") is None

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            IdentityStore.load_from_yaml(tmp_path / "no.yaml")

    def test_malformed_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "x.yaml"
        path.write_text("identities: not-a-list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a list"):
            IdentityStore.load_from_yaml(path)

    def test_missing_actor_id(self, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "x.yaml"
        path.write_text(
            "identities:\n  - roles: [user]\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing actor_id"):
            IdentityStore.load_from_yaml(path)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config  # noqa: E402
from runtime.sensing.gateway import create_openai_router  # noqa: E402


def _stack():
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/g",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        )
    )
    return build_from_config(cfg)


class TestGatewayBearerActor:
    def test_valid_bearer_sets_actor(self):
        stack = _stack()
        store = IdentityStore()
        store.add(
            Identity(actor_id="alice", roles=("admin",)),
            api_key_plaintext="sk-alice-123",
        )
        app = FastAPI()
        app.include_router(create_openai_router(stack, identity_store=store))
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
            headers={"Authorization": "Bearer sk-alice-123"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["echo"].get("actor") == "alice"

        # Implementation note.
        alice_events = stack.journal.read_by_actor("alice")
        assert len(alice_events) >= 2  # task_started + trajectory etc

    def test_invalid_bearer_no_require_auth_falls_through(self):
        """Implementation note."""
        stack = _stack()
        store = IdentityStore()
        store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=False,
            )
        )
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 200
        # Implementation note.
        assert "actor" not in r.json()["echo"]

    def test_no_auth_header_no_require_auth_anonymous(self):
        stack = _stack()
        store = IdentityStore()
        app = FastAPI()
        app.include_router(create_openai_router(stack, identity_store=store))
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
        )
        assert r.status_code == 200
        # Implementation note.
        assert "actor" not in r.json()["echo"]

    def test_require_auth_missing_header_401(self):
        stack = _stack()
        store = IdentityStore()
        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
            )
        )
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
        )
        assert r.status_code == 401
        assert "Bearer" in r.json()["detail"] or "key" in r.json()["detail"].lower()

    def test_require_auth_bad_key_401(self):
        stack = _stack()
        store = IdentityStore()
        store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
            )
        )
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    def test_x_actor_header_untrusted(self):
        """X-Actor header must not be trusted without identity_store."""
        stack = _stack()
        app = FastAPI()
        app.include_router(create_openai_router(stack))
        client = TestClient(app)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
            headers={"X-Actor": "from-proxy-header"},
        )
        # Without identity_store, X-Actor is ignored. The request may
        # succeed with an anonymous actor or return 401. The key
        # assertion is that the actor is NOT "from-proxy-header".
        assert r.status_code in (200, 401)
        if r.status_code == 200:
            resp = r.json()
            actor = resp.get("echo", {}).get("actor", "")
            assert actor != "from-proxy-header"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMultiUserAudit:
    def test_two_users_events_separated(self):
        stack = _stack()
        store = IdentityStore()
        store.add(Identity(actor_id="alice"), api_key_plaintext="key-alice")
        store.add(Identity(actor_id="bob"), api_key_plaintext="key-bob")

        app = FastAPI()
        app.include_router(create_openai_router(stack, identity_store=store))
        client = TestClient(app)

        # Implementation note.
        for _ in range(2):
            client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "list"}]},
                headers={"Authorization": "Bearer key-alice"},
            )
        # Implementation note.
        client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "list"}]},
            headers={"Authorization": "Bearer key-bob"},
        )

        alice_tasks = {
            e.task_id
            for e in stack.journal.read_by_actor("alice")
            if e.event_type == "task_started"
        }
        bob_tasks = {
            e.task_id for e in stack.journal.read_by_actor("bob") if e.event_type == "task_started"
        }
        assert len(alice_tasks) == 2
        assert len(bob_tasks) == 1
        assert alice_tasks.isdisjoint(bob_tasks)
