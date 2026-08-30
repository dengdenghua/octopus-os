from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from runtime.sensing.gateway.thread_share_relay import (
    ThreadShareRelayClient,
    ThreadShareRelayError,
)


class _Response:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return b"" if self.payload is None else json.dumps(self.payload).encode()


def test_relay_requires_https_and_authentication() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ThreadShareRelayClient("http://share.example.com", api_key="secret")
    with pytest.raises(ValueError, match="authentication"):
        ThreadShareRelayClient("https://share.example.com")


def test_relay_create_list_and_revoke_use_narrow_account_routes(monkeypatch) -> None:
    calls: list[tuple[object, bytes | None]] = []
    responses = iter(
        [
            _Response(
                {
                    "token": "capability",
                    "share_id": "shr_123",
                    "share_path": "#/share/capability",
                    "share_url": "https://share.example.com/#/share/capability",
                    "created_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2026-02-01T00:00:00Z",
                }
            ),
            _Response({"shares": [{"share_id": "shr_123"}]}),
            _Response(),
        ]
    )

    def fake_urlopen(request, timeout):
        assert timeout == 10
        calls.append((request, request.data))
        return next(responses)

    monkeypatch.setattr(
        "runtime.sensing.gateway.thread_share_relay.urlopen",
        fake_urlopen,
    )
    client = ThreadShareRelayClient(
        "https://share.example.com",
        api_key="relay-account-key-that-is-at-least-thirty-two-characters",
    )
    created = client.create(
        source_thread_id="thread-one",
        snapshot={"title": "Public", "messages": [{"role": "user", "content": "Hi"}]},
        actor_id="alice",
        tenant_id="tenant-a",
    )
    listed = client.list_for_thread(
        source_thread_id="thread-one",
        actor_id="alice",
        tenant_id="tenant-a",
    )
    client.revoke("shr_123", actor_id="alice", tenant_id="tenant-a")

    assert created["share_id"] == "shr_123"
    assert listed == [{"share_id": "shr_123"}]
    first = calls[0][0]
    assert first.full_url == "https://share.example.com/api/cloud-edge/thread-shares"
    assert first.get_header("X-api-key") == (
        "relay-account-key-that-is-at-least-thirty-two-characters"
    )
    owner_scope = first.get_header("X-echo-share-owner-scope")
    assert owner_scope.startswith("relay_")
    assert len(owner_scope) == 70
    assert calls[1][0].get_header("X-echo-share-owner-scope") == owner_scope
    assert calls[2][0].get_header("X-echo-share-owner-scope") == owner_scope
    assert "relay-account-key" not in first.full_url
    assert json.loads(calls[0][1] or b"{}") == {
        "source_thread_id": "thread-one",
        "snapshot": {
            "title": "Public",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    }
    assert "source_thread_id=thread-one" in calls[1][0].full_url
    assert calls[2][0].get_method() == "DELETE"
    assert calls[2][0].full_url.endswith("/api/cloud-edge/thread-shares/shr_123")


def test_relay_maps_auth_and_quota_failures_without_leaking_response(monkeypatch) -> None:
    def fail(_request, timeout):
        _ = timeout
        raise HTTPError("https://share.example.com", 429, "secret upstream detail", {}, None)

    monkeypatch.setattr("runtime.sensing.gateway.thread_share_relay.urlopen", fail)
    client = ThreadShareRelayClient("https://share.example.com", bearer_token="short-lived")
    with pytest.raises(ThreadShareRelayError, match="quota exceeded"):
        client.create(
            source_thread_id="thread",
            snapshot={"messages": []},
            actor_id="alice",
            tenant_id="tenant-a",
        )


def test_relay_bearer_uses_device_owner_scoped_routes(monkeypatch) -> None:
    captured = []

    def fail(request, timeout):
        _ = timeout
        captured.append(request)
        raise HTTPError(request.full_url, 429, "quota", {}, None)

    monkeypatch.setattr("runtime.sensing.gateway.thread_share_relay.urlopen", fail)
    client = ThreadShareRelayClient("https://share.example.com", bearer_token="device-token")
    with pytest.raises(ThreadShareRelayError, match="quota exceeded"):
        client.create(
            source_thread_id="thread",
            snapshot={"messages": []},
            actor_id="ignored-local-actor",
            tenant_id="ignored-local-tenant",
        )
    request = captured[0]
    assert request.full_url == "https://share.example.com/edge/v1/thread-shares"
    assert request.get_header("Authorization") == "Bearer device-token"
    assert request.get_header("X-echo-share-owner-scope") is None

