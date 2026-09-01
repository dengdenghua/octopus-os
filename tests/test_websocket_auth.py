from __future__ import annotations

import base64

from runtime.safety.auth.websocket import (
    accepted_auth_subprotocol,
    offered_websocket_subprotocols,
    websocket_bearer_token,
)


class _Connection:
    def __init__(
        self,
        *,
        subprotocols: list[str] | None = None,
        header: str = "",
    ) -> None:
        self.scope = {"subprotocols": subprotocols} if subprotocols is not None else {}
        self.headers = {"sec-websocket-protocol": header} if header else {}


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def test_websocket_bearer_token_decodes_arbitrary_utf8_without_url_transport() -> None:
    connection = _Connection(
        subprotocols=["bearer.b64", _encoded("令牌 with spaces/(test)")],
    )

    assert websocket_bearer_token(connection) == "令牌 with spaces/(test)"
    assert accepted_auth_subprotocol(connection) == "bearer.b64"


def test_websocket_bearer_token_handles_proxy_collapsed_protocol_header() -> None:
    token = "sk-alice"
    encoded = _encoded(token)
    connection = _Connection(subprotocols=[f"bearer.b64, {encoded}"])

    assert websocket_bearer_token(connection) == token
    assert accepted_auth_subprotocol(connection) == "bearer.b64"


def test_websocket_bearer_token_keeps_legacy_protocol_compatibility() -> None:
    connection = _Connection(header="bearer, sk-legacy")

    assert offered_websocket_subprotocols(connection) == ["bearer", "sk-legacy"]
    assert websocket_bearer_token(connection) == "sk-legacy"
    assert accepted_auth_subprotocol(connection) == "bearer"


def test_websocket_bearer_token_rejects_malformed_base64url() -> None:
    connection = _Connection(subprotocols=["bearer.b64", "not(valid)"])

    assert websocket_bearer_token(connection) is None
    assert accepted_auth_subprotocol(connection) == "bearer.b64"


def test_accepted_auth_subprotocol_never_echoes_the_credential() -> None:
    encoded = _encoded("secret")
    connection = _Connection(subprotocols=["Bearer.B64", encoded])

    assert accepted_auth_subprotocol(connection) == "Bearer.B64"
    assert accepted_auth_subprotocol(connection) != encoded

