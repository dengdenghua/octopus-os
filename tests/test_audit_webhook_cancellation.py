"""Tests for audit log signing, webhook verification, and request cancellation:

1. Audit Log Signing (HMAC chain, tamper-evident)
2. Webhook HMAC verifier (GitHub/Stripe/Shopify/generic)
3. Request Cancellation (CancellationToken + FastAPI integration)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════
# 1. Audit Log Signing
# ═══════════════════════════════════════════════════════════════


class TestAuditChain:
    def test_append_and_verify(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "audit.jsonl",
            keys={"v1": b"top-secret"},
            active_key_id="v1",
        )
        chain.append(kind="step", payload={"step_id": 1})
        chain.append(kind="step", payload={"step_id": 2})
        chain.append(kind="step", payload={"step_id": 3})

        report = chain.verify()
        assert report.ok is True
        assert report.entries_checked == 3

    def test_genesis_prev_mac_is_empty(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "a.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        entry = chain.append(kind="init", payload={})
        assert entry.seq == 0
        assert entry.prev_mac == ""

    def test_chain_links_entries(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "a.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        e0 = chain.append(kind="a", payload={})
        e1 = chain.append(kind="b", payload={})
        assert e1.prev_mac == e0.mac

    def test_tamper_detection_in_payload(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        path = tmp_path / "a.jsonl"
        chain = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        chain.append(kind="a", payload={"value": 100})
        chain.append(kind="b", payload={"value": 200})
        chain.append(kind="c", payload={"value": 300})

        # Tamper with the middle entry's payload.
        lines = path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["payload"]["value"] = 999
        lines[1] = json.dumps(entry)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Need a fresh chain to re-read.
        chain2 = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        report = chain2.verify()
        assert report.ok is False
        assert report.broken_at == 1

    def test_deleted_entry_detected(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        path = tmp_path / "a.jsonl"
        chain = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        chain.append(kind="a", payload={})
        chain.append(kind="b", payload={})
        chain.append(kind="c", payload={})

        # Delete the middle line.
        lines = path.read_text(encoding="utf-8").splitlines()
        del lines[1]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        chain2 = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        report = chain2.verify()
        assert report.ok is False
        # Seq jumps from 0 → 2, verifier should flag the gap.
        assert report.broken_at == 2 or "seq gap" in (report.error or "")

    def test_reordered_entries_detected(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        path = tmp_path / "a.jsonl"
        chain = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        chain.append(kind="a", payload={})
        chain.append(kind="b", payload={})

        # Swap the two lines.
        lines = path.read_text(encoding="utf-8").splitlines()
        lines.reverse()
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        chain2 = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        report = chain2.verify()
        assert report.ok is False

    def test_key_rotation_preserves_old_entries(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "a.jsonl",
            keys={"v1": b"old"},
            active_key_id="v1",
        )
        chain.append(kind="a", payload={"n": 1})
        chain.rotate_key(new_key_id="v2", new_secret=b"new")
        chain.append(kind="b", payload={"n": 2})

        # Both old (v1) and new (v2) entries must still verify.
        report = chain.verify()
        assert report.ok is True
        assert report.entries_checked == 2

    def test_verify_with_missing_key_fails_gracefully(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        path = tmp_path / "a.jsonl"
        chain = AuditChain(
            path=path,
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        chain.append(kind="x", payload={})

        # Verifier without the needed key.
        chain2 = AuditChain(
            path=path,
            keys={"v9": b"other"},
            active_key_id="v9",
        )
        report = chain2.verify()
        assert report.ok is False
        assert "unknown key_id" in (report.error or "")

    def test_tail_returns_latest(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "a.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        for i in range(5):
            chain.append(kind="x", payload={"n": i})
        tail = chain.tail(n=2)
        assert len(tail) == 2
        assert tail[-1].payload["n"] == 4

    def test_verify_limit(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "a.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )
        for i in range(10):
            chain.append(kind="x", payload={"n": i})
        report = chain.verify(limit=3)
        assert report.ok is True
        assert report.entries_checked == 3

    def test_concurrent_append_thread_safe(self, tmp_path: Path):
        from runtime.safety.audit.audit_chain import AuditChain

        chain = AuditChain(
            path=tmp_path / "a.jsonl",
            keys={"v1": b"s"},
            active_key_id="v1",
        )

        def worker():
            for _ in range(20):
                chain.append(kind="x", payload={})

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        report = chain.verify()
        assert report.ok is True
        assert report.entries_checked == 100

    def test_canonical_json_is_deterministic(self):
        from runtime.safety.audit.audit_chain import canonical_bytes

        a = canonical_bytes({"b": 1, "a": 2})
        b = canonical_bytes({"a": 2, "b": 1})
        assert a == b


# ═══════════════════════════════════════════════════════════════
# 2. Webhook HMAC verifier
# ═══════════════════════════════════════════════════════════════


class TestWebhookVerify:
    def test_github_valid_signature(self):
        from runtime.safety.audit.webhook_verify import verify_github_signature

        secret = b"gh-secret"
        body = b'{"action":"opened"}'
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        verify_github_signature(
            secret=secret,
            body=body,
            signature_header=sig,
        )  # no raise

    def test_github_invalid_signature(self):
        from runtime.safety.audit.webhook_verify import (
            WebhookVerificationError,
            verify_github_signature,
        )

        with pytest.raises(WebhookVerificationError):
            verify_github_signature(
                secret=b"gh-secret",
                body=b'{"a":1}',
                signature_header="sha256=deadbeef",
            )

    def test_github_missing_header(self):
        from runtime.safety.audit.webhook_verify import (
            WebhookVerificationError,
            verify_github_signature,
        )

        with pytest.raises(WebhookVerificationError):
            verify_github_signature(
                secret=b"s",
                body=b"{}",
                signature_header=None,
            )

    def test_shopify_valid_signature(self):
        from runtime.safety.audit.webhook_verify import verify_shopify_signature

        secret = b"sp-secret"
        body = b'{"order":1}'
        sig = base64.b64encode(
            hmac.new(secret, body, hashlib.sha256).digest(),
        ).decode("ascii")
        verify_shopify_signature(
            secret=secret,
            body=body,
            signature_header=sig,
        )

    def test_shopify_invalid_signature(self):
        from runtime.safety.audit.webhook_verify import (
            WebhookVerificationError,
            verify_shopify_signature,
        )

        with pytest.raises(WebhookVerificationError):
            verify_shopify_signature(
                secret=b"s",
                body=b"{}",
                signature_header=base64.b64encode(b"wrong").decode(),
            )

    def test_stripe_valid_signature(self):
        from runtime.safety.audit.webhook_verify import verify_stripe_signature

        secret = b"st-secret"
        body = b'{"id":"evt"}'
        ts = int(time.time())
        signed_payload = f"{ts}.".encode() + body
        v1 = hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()
        verify_stripe_signature(
            secret=secret,
            body=body,
            signature_header=f"t={ts},v1={v1}",
        )

    def test_stripe_expired_timestamp_rejected(self):
        from runtime.safety.audit.webhook_verify import (
            WebhookVerificationError,
            verify_stripe_signature,
        )

        secret = b"s"
        body = b"{}"
        ts = int(time.time()) - 10_000  # Way past tolerance
        signed = f"{ts}.".encode() + body
        v1 = hmac.new(secret, signed, hashlib.sha256).hexdigest()
        with pytest.raises(WebhookVerificationError, match="tolerance"):
            verify_stripe_signature(
                secret=secret,
                body=body,
                signature_header=f"t={ts},v1={v1}",
                tolerance_seconds=300,
            )

    def test_stripe_injected_now_for_tests(self):
        from runtime.safety.audit.webhook_verify import verify_stripe_signature

        secret = b"s"
        body = b"{}"
        ts = 1_700_000_000
        signed = f"{ts}.".encode() + body
        v1 = hmac.new(secret, signed, hashlib.sha256).hexdigest()
        # With now=ts, the delta is 0, well within tolerance.
        verify_stripe_signature(
            secret=secret,
            body=body,
            signature_header=f"t={ts},v1={v1}",
            now=float(ts),
        )

    def test_stripe_wrong_signature(self):
        from runtime.safety.audit.webhook_verify import (
            WebhookVerificationError,
            verify_stripe_signature,
        )

        ts = int(time.time())
        with pytest.raises(WebhookVerificationError):
            verify_stripe_signature(
                secret=b"s",
                body=b"{}",
                signature_header=f"t={ts},v1=deadbeef",
            )

    def test_generic_hmac_with_prefix(self):
        from runtime.safety.audit.webhook_verify import verify_generic_hmac

        secret = b"k"
        body = b"payload"
        digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
        verify_generic_hmac(
            secret=secret,
            body=body,
            provided_hex=f"sha256={digest}",
            prefix="sha256=",
        )

    def test_dispatcher_github(self):
        from runtime.safety.audit.webhook_verify import verify_webhook

        secret = b"s"
        body = b"{}"
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        verify_webhook(
            "github",
            secret=secret,
            body=body,
            headers={"x-hub-signature-256": sig},
        )

    def test_dispatcher_unknown_scheme(self):
        from runtime.safety.audit.webhook_verify import verify_webhook

        with pytest.raises(ValueError):
            verify_webhook(
                "unknown",  # type: ignore[arg-type]
                secret=b"s",
                body=b"{}",
                headers={},
            )

    def test_constant_time_compare(self):
        """Sanity: the underlying hmac.compare_digest is what we use."""
        from runtime.safety.audit.webhook_verify import (
            WebhookVerificationError,
            verify_github_signature,
        )

        # Not a timing test (hard to assert in unit tests) — just
        # verify the function exists and rejects wrong values.
        try:
            verify_github_signature(
                secret=b"s",
                body=b"x",
                signature_header="sha256=00",
            )
            raise AssertionError("should have raised")
        except WebhookVerificationError:
            pass

    def test_fastapi_dependency_valid(self):
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient

        from runtime.safety.audit.webhook_verify import create_verify_dependency

        secret = b"hook-secret"
        dep = create_verify_dependency(
            "github",
            secret_getter=lambda: secret,
        )
        app = FastAPI()

        @app.post("/w", dependencies=[Depends(dep)])
        async def handler():
            return {"ok": True}

        body = b'{"x":1}'
        sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        client = TestClient(app)
        r = client.post("/w", content=body, headers={"x-hub-signature-256": sig})
        assert r.status_code == 200

    def test_fastapi_dependency_rejects(self):
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient

        from runtime.safety.audit.webhook_verify import create_verify_dependency

        dep = create_verify_dependency(
            "github",
            secret_getter=lambda: b"s",
        )
        app = FastAPI()

        @app.post("/w", dependencies=[Depends(dep)])
        async def handler():
            return {"ok": True}

        client = TestClient(app)
        r = client.post(
            "/w",
            content=b"{}",
            headers={"x-hub-signature-256": "sha256=00"},
        )
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════
# 3. Request Cancellation
# ═══════════════════════════════════════════════════════════════


class TestCancellation:
    def test_fresh_token_not_cancelled(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        assert src.token.is_cancelled is False

    def test_cancel_trips_token(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        assert src.cancel(reason="test") is True
        assert src.token.is_cancelled is True
        assert src.token.reason == "test"

    def test_cancel_idempotent(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        assert src.cancel(reason="first") is True
        assert src.cancel(reason="second") is False
        # Reason locked in to first.
        assert src.token.reason == "first"

    def test_throw_if_cancelled_raises(self):
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            OperationCancelled,
        )

        src = CancellationSource()
        src.cancel()
        with pytest.raises(OperationCancelled):
            src.token.throw_if_cancelled()

    def test_throw_if_not_cancelled_is_noop(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        src.token.throw_if_cancelled()  # should NOT raise

    def test_on_cancelled_callback_fires(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        calls: list[str] = []
        src.token.on_cancelled(lambda r: calls.append(r))
        src.cancel(reason="bye")
        assert calls == ["bye"]

    def test_on_cancelled_after_already_cancelled(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        src.cancel(reason="late")
        calls: list[str] = []
        src.token.on_cancelled(lambda r: calls.append(r))
        assert calls == ["late"]

    def test_broken_callback_does_not_stop_others(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        calls: list[str] = []
        src.token.on_cancelled(lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
        src.token.on_cancelled(lambda r: calls.append(r))
        src.cancel(reason="x")
        assert calls == ["x"]

    def test_linked_token_inherits_cancellation(self):
        from runtime.safety.approval.cancellation import CancellationSource

        parent = CancellationSource()
        child_src = parent.token.link()
        parent.cancel(reason="parent died")
        assert child_src.token.is_cancelled is True
        assert "parent" in child_src.token.reason

    def test_linked_after_parent_cancel_propagates_immediately(self):
        from runtime.safety.approval.cancellation import CancellationSource

        parent = CancellationSource()
        parent.cancel(reason="early")
        child = parent.token.link()
        assert child.token.is_cancelled is True

    def test_child_cancel_does_not_affect_parent(self):
        from runtime.safety.approval.cancellation import CancellationSource

        parent = CancellationSource()
        child = parent.token.link()
        child.cancel(reason="child only")
        assert parent.token.is_cancelled is False

    def test_none_token_never_cancels(self):
        from runtime.safety.approval.cancellation import CancellationToken

        t = CancellationToken.none()
        assert t.is_cancelled is False
        # Attempts to cancel the underlying source are rejected.
        assert t._source.cancel() is False  # type: ignore[attr-defined]

    def test_wait_returns_true_on_cancel(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()

        def _trigger():
            time.sleep(0.05)
            src.cancel()

        threading.Thread(target=_trigger, daemon=True).start()
        assert src.wait(timeout=2) is True

    def test_wait_times_out(self):
        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()
        assert src.wait(timeout=0.05) is False

    @pytest.mark.asyncio
    async def test_wait_async_returns_true_on_cancel(self):
        import asyncio

        from runtime.safety.approval.cancellation import CancellationSource

        src = CancellationSource()

        async def _trigger():
            await asyncio.sleep(0.05)
            src.cancel()

        asyncio.create_task(_trigger())
        assert await src.wait_async(timeout=2) is True

    def test_end_to_end_pipeline_abort(self):
        """Model a pipeline that polls the token mid-work."""
        from runtime.safety.approval.cancellation import (
            CancellationSource,
            OperationCancelled,
        )

        src = CancellationSource()

        def long_work():
            for i in range(1000):
                src.token.throw_if_cancelled()
                if i == 10:
                    src.cancel(reason="external stop")

        with pytest.raises(OperationCancelled):
            long_work()
