"""Self-contained security helpers for the standalone cloud service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


class TokenError(ValueError):
    pass


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_token(claims: dict[str, Any], secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, *, secret: str, issuer: str, audience: str) -> dict[str, Any]:
    try:
        header, payload, signature = token.split(".")
        expected = hmac.new(
            secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise TokenError("bad signature")
        claims = json.loads(_unb64(payload))
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError("malformed token") from exc
    now = int(time.time())
    if (
        claims.get("iss") != issuer
        or claims.get("aud") != audience
        or int(claims.get("exp", 0)) < now
    ):
        raise TokenError("invalid or expired token")
    return claims


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(actual, _unb64(expected))
    except (TypeError, ValueError):
        return False


def secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
