"""Implementation note."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from runtime.safety.auth import (
    Identity,
    IdentityStore,
    JWTError,
    encode_jwt_hs256,
    verify_jwt_hs256,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEncodeVerifyRoundtrip:
    def test_basic_roundtrip(self):
        exp = int(time.time()) + 3600
        token = encode_jwt_hs256(
            {"sub": "alice", "exp": exp},
            secret="shh",
        )
        claims = verify_jwt_hs256(token, secret="shh")
        assert claims["sub"] == "alice"
        assert claims["exp"] == exp

    def test_token_has_three_parts(self):
        token = encode_jwt_hs256(
            {"sub": "a", "exp": int(time.time()) + 60},
            secret="x",
        )
        assert token.count(".") == 2

    def test_empty_secret_rejected_on_encode(self):
        with pytest.raises(ValueError):
            encode_jwt_hs256({"sub": "a", "exp": 1}, secret="")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAlgorithmDefense:
    def test_alg_none_rejected(self):
        """Implementation note."""
        # Implementation note.
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        claims = (
            base64.urlsafe_b64encode(json.dumps({"sub": "admin", "exp": 9999999999}).encode())
            .rstrip(b"=")
            .decode()
        )
        token = f"{header}.{claims}."
        with pytest.raises(JWTError, match="unsupported alg"):
            verify_jwt_hs256(token, secret="any")

    def test_alg_rs256_rejected(self):
        """Implementation note."""
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        claims = (
            base64.urlsafe_b64encode(json.dumps({"sub": "a", "exp": 9999999999}).encode())
            .rstrip(b"=")
            .decode()
        )
        token = f"{header}.{claims}.sig"
        with pytest.raises(JWTError, match="unsupported alg"):
            verify_jwt_hs256(token, secret="any")

    def test_signature_tampering_detected(self):
        token = encode_jwt_hs256(
            {"sub": "a", "exp": int(time.time()) + 60},
            secret="s",
        )
        header, claims, sig = token.split(".")
        # Implementation note.
        mid = len(sig) // 2
        flipped = "A" if sig[mid] != "A" else "B"
        bad_sig = sig[:mid] + flipped + sig[mid + 1 :]
        bad_token = f"{header}.{claims}.{bad_sig}"
        with pytest.raises(JWTError, match="signature"):
            verify_jwt_hs256(bad_token, secret="s")

    def test_wrong_secret_rejected(self):
        token = encode_jwt_hs256(
            {"sub": "a", "exp": int(time.time()) + 60},
            secret="right",
        )
        with pytest.raises(JWTError, match="signature"):
            verify_jwt_hs256(token, secret="wrong")

    def test_payload_tamper_detected(self):
        """Implementation note."""
        import base64
        import json

        token = encode_jwt_hs256(
            {"sub": "bob", "exp": int(time.time()) + 60},
            secret="s",
        )
        header, _, sig = token.split(".")
        # Implementation note.
        evil_claims = (
            base64.urlsafe_b64encode(json.dumps({"sub": "admin", "exp": 9999999999}).encode())
            .rstrip(b"=")
            .decode()
        )
        bad = f"{header}.{evil_claims}.{sig}"
        with pytest.raises(JWTError, match="signature"):
            verify_jwt_hs256(bad, secret="s")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTimeClaims:
    def test_expired_rejected(self):
        token = encode_jwt_hs256(
            {"sub": "a", "exp": int(time.time()) - 10},
            secret="s",
        )
        with pytest.raises(JWTError, match="expired"):
            verify_jwt_hs256(token, secret="s")

    def test_expired_with_leeway_passes(self):
        token = encode_jwt_hs256(
            {"sub": "a", "exp": int(time.time()) - 5},
            secret="s",
        )
        # Implementation note.
        claims = verify_jwt_hs256(token, secret="s", leeway_seconds=10)
        assert claims["sub"] == "a"

    def test_missing_exp_rejected(self):
        token = encode_jwt_hs256({"sub": "a", "exp": 1}, secret="s")
        # Implementation note.
        header, claims_b64, sig = token.split(".")
        # Implementation note.
        new_token = encode_jwt_hs256({"sub": "a"}, secret="s")  # Implementation note.
        # Implementation note.
        with pytest.raises(JWTError, match="exp"):
            verify_jwt_hs256(new_token, secret="s")

    def test_nbf_not_yet_valid(self):
        future = int(time.time()) + 3600
        token = encode_jwt_hs256(
            {"sub": "a", "exp": future + 3600, "nbf": future},
            secret="s",
        )
        with pytest.raises(JWTError, match="not yet valid"):
            verify_jwt_hs256(token, secret="s")

    def test_now_override_for_testing(self):
        """Implementation note."""
        t0 = 1_700_000_000
        token = encode_jwt_hs256(
            {"sub": "a", "exp": t0 + 60},
            secret="s",
        )
        # Implementation note.
        claims = verify_jwt_hs256(token, secret="s", now=t0)
        assert claims["sub"] == "a"
        # Implementation note.
        with pytest.raises(JWTError, match="expired"):
            verify_jwt_hs256(token, secret="s", now=t0 + 1000)


# ═══════════════════════════════════════════════════════════
# iss / aud
# ═══════════════════════════════════════════════════════════


class TestClaimConstraints:
    def _token(self, **extra):
        return encode_jwt_hs256(
            {"sub": "a", "exp": int(time.time()) + 60, **extra},
            secret="s",
        )

    def test_issuer_match_required(self):
        token = self._token(iss="my-idp")
        claims = verify_jwt_hs256(
            token,
            secret="s",
            required_issuer="my-idp",
        )
        assert claims["iss"] == "my-idp"

    def test_issuer_mismatch_rejected(self):
        token = self._token(iss="other-idp")
        with pytest.raises(JWTError, match="issuer"):
            verify_jwt_hs256(token, secret="s", required_issuer="my-idp")

    def test_audience_as_list(self):
        token = self._token(aud=["svc-a", "svc-b"])
        claims = verify_jwt_hs256(
            token,
            secret="s",
            required_audience="svc-a",
        )
        assert claims["aud"] == ["svc-a", "svc-b"]

    def test_audience_as_string(self):
        token = self._token(aud="svc-a")
        claims = verify_jwt_hs256(
            token,
            secret="s",
            required_audience="svc-a",
        )
        assert claims["aud"] == "svc-a"

    def test_audience_mismatch_rejected(self):
        token = self._token(aud="other")
        with pytest.raises(JWTError, match="audience"):
            verify_jwt_hs256(token, secret="s", required_audience="me")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMalformed:
    def test_wrong_part_count_rejected(self):
        with pytest.raises(JWTError, match="malformed"):
            verify_jwt_hs256("only.two", secret="s")
        with pytest.raises(JWTError, match="malformed"):
            verify_jwt_hs256("a.b.c.d", secret="s")

    def test_empty_token_rejected(self):
        with pytest.raises(JWTError):
            verify_jwt_hs256("", secret="s")

    def test_empty_secret_rejected(self):
        token = encode_jwt_hs256(
            {"sub": "a", "exp": 9999999999},
            secret="s",
        )
        with pytest.raises(JWTError):
            verify_jwt_hs256(token, secret="")

    def test_garbage_base64_rejected(self):
        with pytest.raises(JWTError):
            verify_jwt_hs256("???.???.???", secret="s")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIdentityStoreJWT:
    def test_verify_jwt_resolves_to_identity(self):
        store = IdentityStore()
        store.add(Identity(actor_id="alice", roles=("user",)))

        token = encode_jwt_hs256(
            {"sub": "alice", "exp": int(time.time()) + 60},
            secret="s",
        )
        identity = store.verify_jwt(token, secret="s")
        assert identity is not None
        assert identity.actor_id == "alice"

    def test_unknown_sub_returns_none(self):
        store = IdentityStore()
        token = encode_jwt_hs256(
            {"sub": "ghost", "exp": int(time.time()) + 60},
            secret="s",
        )
        assert store.verify_jwt(token, secret="s") is None

    def test_bad_signature_returns_none(self):
        store = IdentityStore()
        store.add(Identity(actor_id="alice"))
        token = encode_jwt_hs256(
            {"sub": "alice", "exp": int(time.time()) + 60},
            secret="right",
        )
        assert store.verify_jwt(token, secret="wrong") is None

    def test_missing_sub_returns_none(self):
        store = IdentityStore()
        token = encode_jwt_hs256(
            {"exp": int(time.time()) + 60},
            secret="s",
        )
        assert store.verify_jwt(token, secret="s") is None

    def test_api_key_and_jwt_can_coexist(self):
        """Implementation note."""
        store = IdentityStore()
        store.add(
            Identity(actor_id="alice"),
            api_key_plaintext="sk-alice-key",
        )
        # Implementation note.
        token = encode_jwt_hs256(
            {"sub": "alice", "exp": int(time.time()) + 60},
            secret="s",
        )
        assert store.verify_jwt(token, secret="s").actor_id == "alice"
        # Implementation note.
        assert store.verify_api_key("sk-alice-key").actor_id == "alice"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from runtime.safety.auth import TrustEngine  # noqa: E402
from runtime.sensing.gateway.openai_gateway_router import create_openai_router  # noqa: E402


def _build_stack(tmp_path: Path):
    """Implementation note."""
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.core.graph_runtime import GraphRuntime
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import JSONLJournal
    from runtime.platform.models import BudgetSpec, SkillId

    journal = JSONLJournal(tmp_path / "events.jsonl")
    registry = SkillRegistry()
    register_all(registry)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    planner = StaticPlanner(
        rules=[
            Rule(
                name="default",
                intent_types=["task"],
                skill_sequence=[SkillId("list_cwd")],
            )
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        fallback_skill=SkillId("list_cwd"),
    )

    class _Stack:
        pass

    s = _Stack()
    s.planner = planner
    s.runtime = runtime
    s.registry = registry
    s.journal = journal
    return s


class TestGatewayJWT:
    def test_jwt_bearer_resolves_to_actor(self, tmp_path: Path):
        """Implementation note."""
        from fastapi import FastAPI

        stack = _build_stack(tmp_path)
        store = IdentityStore()
        store.add(Identity(actor_id="alice"))
        token = encode_jwt_hs256(
            {"sub": "alice", "exp": int(time.time()) + 60},
            secret="top-secret",
        )

        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
                jwt_secret="top-secret",
            )
        )
        client = TestClient(app)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "echo-agent",
                "messages": [{"role": "user", "content": "list files"}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Implementation note.
        events = list(stack.journal.read_all())
        actors = {getattr(e, "actor", None) for e in events}
        assert "alice" in actors

    def test_jwt_bad_signature_rejected(self, tmp_path: Path):
        from fastapi import FastAPI

        stack = _build_stack(tmp_path)
        store = IdentityStore()
        store.add(Identity(actor_id="alice"))
        bad = encode_jwt_hs256(
            {"sub": "alice", "exp": int(time.time()) + 60},
            secret="other",  # Implementation note.
        )

        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
                jwt_secret="top-secret",
            )
        )
        client = TestClient(app)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": f"Bearer {bad}"},
        )
        assert resp.status_code == 401

    def test_jwt_expired_rejected(self, tmp_path: Path):
        from fastapi import FastAPI

        stack = _build_stack(tmp_path)
        store = IdentityStore()
        store.add(Identity(actor_id="alice"))
        expired = encode_jwt_hs256(
            {"sub": "alice", "exp": int(time.time()) - 100},
            secret="s",
        )

        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
                jwt_secret="s",
            )
        )
        client = TestClient(app)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401

    def test_jwt_and_api_key_coexist(self, tmp_path: Path):
        """Implementation note."""
        from fastapi import FastAPI

        stack = _build_stack(tmp_path)
        store = IdentityStore()
        store.add(
            Identity(actor_id="bob"),
            api_key_plaintext="sk-bob",
        )

        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
                jwt_secret="s",
            )
        )
        client = TestClient(app)

        # Implementation note.
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": "Bearer sk-bob"},
        )
        assert resp.status_code == 200

        events = list(stack.journal.read_all())
        actors = {getattr(e, "actor", None) for e in events}
        assert "bob" in actors

    def test_jwt_issuer_enforced(self, tmp_path: Path):
        from fastapi import FastAPI

        stack = _build_stack(tmp_path)
        store = IdentityStore()
        store.add(Identity(actor_id="alice"))

        wrong_iss = encode_jwt_hs256(
            {
                "sub": "alice",
                "exp": int(time.time()) + 60,
                "iss": "evil-idp",
            },
            secret="s",
        )

        app = FastAPI()
        app.include_router(
            create_openai_router(
                stack,
                identity_store=store,
                require_auth=True,
                jwt_secret="s",
                jwt_issuer="good-idp",
            )
        )
        client = TestClient(app)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": f"Bearer {wrong_iss}"},
        )
        assert resp.status_code == 401
