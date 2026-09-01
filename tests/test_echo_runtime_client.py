"""Echo runtime registry client contract tests."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

import httpx
import pytest

from echo_runtime.client import RegistryClient, RegistryResponseTooLarge
from echo_runtime.materialize import sync_skills


class _ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], on_chunk=None) -> None:
        self._chunks = chunks
        self._on_chunk = on_chunk

    def __iter__(self):
        for chunk in self._chunks:
            if self._on_chunk is not None:
                self._on_chunk()
            yield chunk


def _client(handler, **kwargs: Any) -> RegistryClient:
    return RegistryClient(
        "https://registry.test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _json_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))


def test_fetch_verifies_checksum_even_when_body_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/skill/empty/download")
        return _json_response(
            {
                "data": {
                    "id": "skill/empty",
                    "type": "skill",
                    "kind": "data",
                    "content": {"checksum": "sha256:" + ("0" * 64)},
                    "body": "",
                }
            },
        )

    with pytest.raises(ValueError, match="checksum mismatch"):
        _client(handler).fetch("skill/empty")


def test_fetch_rejects_unsafe_asset_id_before_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _json_response({})

    with pytest.raises(ValueError, match="unsafe registry asset id"):
        _client(handler).fetch("skill/../escape")

    assert called is False


def test_fetch_rejects_malformed_content_checksum() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "data": {
                    "id": "skill/bad",
                    "type": "skill",
                    "kind": "data",
                    "content": {"checksum": "md5:not-accepted"},
                    "body": "hello",
                }
            },
        )

    with pytest.raises(ValueError, match="invalid sha256 checksum"):
        _client(handler).fetch("skill/bad")


def test_fetch_accepts_uppercase_sha256_checksum() -> None:
    body = "hello"
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest().upper()

    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "data": {
                    "id": "skill/ok",
                    "type": "skill",
                    "kind": "data",
                    "content": {"checksum": "sha256:" + expected},
                    "body": body,
                }
            },
        )

    payload = _client(handler).fetch("skill/ok")

    assert payload.id == "skill/ok"
    assert payload.body == body


def test_fetch_accepts_safe_asset_id_variants() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return _json_response(
            {
                "data": {
                    "id": "twin-role/operator_1.2",
                    "type": "twin-role",
                    "kind": "data",
                    "body": "hello",
                }
            },
        )

    payload = _client(handler).fetch("twin-role/operator_1.2")

    assert payload.id == "twin-role/operator_1.2"
    assert captured == [
        "https://registry.test/api/v1/registry/assets/twin-role/operator_1.2/download"
    ]


def test_fetch_bundle_rejects_malformed_header_checksum() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"bundle", headers={"X-Checksum-Sha256": "bad"})

    with pytest.raises(ValueError, match="invalid sha256 checksum"):
        _client(handler).fetch_bundle("skill/bad")


def test_fetch_bundle_rejects_unsafe_asset_id_before_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"")

    with pytest.raises(ValueError, match="unsafe registry asset id"):
        _client(handler).fetch_bundle("skill/name?bad=1")

    assert called is False


def test_fetch_bundle_accepts_sha256_header() -> None:
    content = b"bundle"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=content,
            headers={"X-Checksum-Sha256": hashlib.sha256(content).hexdigest()},
        )

    assert _client(handler).fetch_bundle("skill/ok", expected_size=len(content)) == content


def test_list_assets_streaming_normal_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("type") == "skill"
        return _json_response(
            {
                "data": [
                    {
                        "id": "skill/research-pack",
                        "type": "skill",
                        "kind": "data",
                        "name": "Research Pack",
                    }
                ]
            }
        )

    assets = _client(handler).list_skills()

    assert [asset.id for asset in assets] == ["skill/research-pack"]


@pytest.mark.parametrize(
    ("operation", "limit_name"),
    [
        ("list", "max_json_bytes"),
        ("fetch", "max_skill_bytes"),
        ("bundle", "max_bundle_bytes"),
    ],
)
def test_chunked_response_without_content_length_stops_at_limit(
    operation: str,
    limit_name: str,
) -> None:
    chunks_seen = 0

    def saw_chunk() -> None:
        nonlocal chunks_seen
        chunks_seen += 1

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_ChunkStream([b"12345678", b"abcdefgh", b"never-read"], saw_chunk),
        )

    client = _client(handler, **{limit_name: 10})
    with pytest.raises(RegistryResponseTooLarge, match="exceeds 10 byte limit"):
        if operation == "list":
            client.list_assets()
        elif operation == "fetch":
            client.fetch("skill/oversized")
        else:
            client.fetch_bundle("skill/oversized")

    # The second chunk crosses cap+1; the third chunk is never consumed.
    assert chunks_seen == 2


def test_bundle_declared_size_mismatch_is_fail_closed() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"bundle")

    client = _client(handler, max_bundle_bytes=16)

    with pytest.raises(ValueError, match="declared 8, got 6"):
        client.fetch_bundle("skill/bad-size", expected_size=8)
    assert called is True

    called = False
    with pytest.raises(RegistryResponseTooLarge, match="declared bundle size"):
        client.fetch_bundle("skill/too-large", expected_size=17)
    assert called is False


def test_concurrent_skill_refreshes_stop_each_stream_at_its_limit(tmp_path) -> None:
    chunks_seen: dict[str, int] = {}
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        asset_id = request.url.path.split("/assets/", 1)[1].removesuffix("/download")

        def saw_chunk() -> None:
            with lock:
                chunks_seen[asset_id] = chunks_seen.get(asset_id, 0) + 1

        return httpx.Response(
            200,
            stream=_ChunkStream([b"12345678", b"abcdefgh", b"never-read"], saw_chunk),
        )

    client = _client(handler, max_skill_bytes=10)
    slugs = [f"pack-{index}" for index in range(8)]

    ok, skipped, errors = sync_skills(
        slugs,
        tmp_path / "skills",
        max_workers=4,
        client=client,
    )

    assert ok == []
    assert skipped == []
    assert {slug for slug, _reason in errors} == set(slugs)
    assert all("exceeds 10 byte limit" in reason for _slug, reason in errors)
    assert set(chunks_seen) == {f"skill/{slug}" for slug in slugs}
    assert all(count == 2 for count in chunks_seen.values())
    assert not (tmp_path / "skills").exists()
