from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ANONYMOUS_ACTOR = "anonymous"


@dataclass(frozen=True)
class Identity:
    actor_id: str
    roles: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class IdentityStore:
    def __init__(self) -> None:
        self._by_hash: dict[str, Identity] = {}
        self._by_actor: dict[str, Identity] = {}
        self._lock = threading.RLock()

    def add(
        self,
        identity: Identity,
        *,
        api_key_hash: str | None = None,
        api_key_plaintext: str | None = None,
    ) -> None:
        if api_key_plaintext is not None and api_key_hash is not None:
            raise ValueError("provide either api_key_hash or api_key_plaintext, not both")

        with self._lock:
            if identity.actor_id in self._by_actor:
                raise ValueError(f"duplicate actor_id: {identity.actor_id!r}")

            h: str | None = None
            if api_key_plaintext is not None:
                h = hash_api_key(api_key_plaintext)
            elif api_key_hash is not None:
                h = _normalize_hash(api_key_hash)

            if h is not None:
                if api_key_plaintext is not None and any(
                    _verify_plaintext_against_hash(api_key_plaintext, existing)
                    for existing in self._by_hash
                ):
                    raise ValueError("api key hash collision with an existing identity")
                self._by_hash[h] = identity

            self._by_actor[identity.actor_id] = identity

    def remove(self, actor_id: str) -> bool:
        with self._lock:
            identity = self._by_actor.pop(actor_id, None)
            if identity is None:
                return False
            self._by_hash = {h: idn for h, idn in self._by_hash.items() if idn.actor_id != actor_id}
            return True

    def verify_api_key(self, plaintext: str) -> Identity | None:
        if not plaintext:
            return None
        with self._lock:
            for stored, identity in self._by_hash.items():
                if _verify_plaintext_against_hash(plaintext, stored):
                    return identity
            return None

    def verify_jwt(
        self,
        token: str,
        *,
        secret: str,
        leeway_seconds: int = 0,
        required_issuer: str | None = None,
        required_audience: str | None = None,
        trust_jwt_sub: bool = False,
    ) -> Identity | None:
        try:
            claims = verify_jwt_hs256(
                token,
                secret=secret,
                leeway_seconds=leeway_seconds,
                required_issuer=required_issuer,
                required_audience=required_audience,
            )
        except JWTError:
            return None
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            return None
        with self._lock:
            existing = self._by_actor.get(sub)
        if existing is not None:
            return existing
        if not trust_jwt_sub:
            return None
        roles_raw = claims.get("roles") or claims.get("role") or []
        roles: tuple[str, ...]
        if isinstance(roles_raw, str):
            roles = (roles_raw,)
        elif isinstance(roles_raw, (list, tuple)):
            roles = tuple(str(r) for r in roles_raw)
        else:
            roles = ()
        meta: dict[str, Any] = {
            k: v
            for k, v in claims.items()
            if k not in ("sub", "exp", "iat", "roles", "role", "iss", "aud")
        }
        meta.setdefault("synthesized_from_jwt", True)
        return Identity(actor_id=sub, roles=roles, metadata=meta)

    def get(self, actor_id: str) -> Identity | None:
        with self._lock:
            return self._by_actor.get(actor_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_actor)

    def actor_ids(self) -> list[str]:
        with self._lock:
            return list(self._by_actor.keys())

    @classmethod
    def load_from_yaml(cls, path: str | Path) -> IdentityStore:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError("PyYAML required for IdentityStore.load_from_yaml") from e

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"identities file not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        raw = data.get("identities") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise ValueError(f"{p}: 'identities' must be a list")

        store = cls()
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"{p}: identities[{i}] must be a mapping")
            if "actor_id" not in item:
                raise ValueError(f"{p}: identities[{i}] missing actor_id")
            identity = Identity(
                actor_id=str(item["actor_id"]),
                roles=tuple(item.get("roles") or ()),
                metadata=dict(item.get("metadata") or {}),
            )
            store.add(
                identity,
                api_key_hash=item.get("api_key_hash"),
            )
        return store


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


# Audit H2: API keys are hashed with PBKDF2-HMAC-SHA256 and a per-key random
# salt so a leaked database cannot be brute-forced with rainbow tables / GPU
# batches. Format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>``.
# The legacy deterministic ``sha256:<hex>`` format is still accepted on verify
# so pre-existing ``api_key_hash`` configs keep working.
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_SALT_BYTES = 16


def hash_api_key(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("cannot hash empty key")
    salt = os.urandom(_PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        plaintext.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _normalize_hash(h: str) -> str:
    h = h.strip()
    if h.startswith("pbkdf2_sha256$"):
        return h
    if h.startswith("sha256:"):
        return h
    if len(h) == 64 and all(c in "0123456789abcdef" for c in h.lower()):
        return f"sha256:{h.lower()}"
    raise ValueError(
        f"unsupported hash format: {h[:20]}... (expect pbkdf2_sha256$... or sha256:<hex>)"
    )


def _verify_plaintext_against_hash(plaintext: str, stored_hash: str) -> bool:
    """Constant-time-ish verify of ``plaintext`` against a stored hash spec."""
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _prefix, iters_s, salt_hex, digest_hex = stored_hash.split("$")
            iterations = int(iters_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    if stored_hash.startswith("sha256:"):
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        return hmac.compare_digest(stored_hash[len("sha256:") :].lower(), digest)
    return False


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class JWTError(Exception):
    pass


def _b64url_decode(s: str) -> bytes:
    s_bytes = s.encode("ascii")
    padding = b"=" * (-len(s_bytes) % 4)
    return base64.urlsafe_b64decode(s_bytes + padding)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_jwt_hs256(
    claims: dict[str, Any],
    *,
    secret: str,
    header_extra: dict[str, Any] | None = None,
) -> str:
    if not secret:
        raise ValueError("secret must be non-empty")
    header = {"alg": "HS256", "typ": "JWT", **(header_extra or {})}
    header_b = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    claims_b = _b64url_encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b}.{claims_b}".encode("ascii")
    sig = hmac.new(
        secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{header_b}.{claims_b}.{_b64url_encode(sig)}"


def verify_jwt_hs256(
    token: str,
    *,
    secret: str,
    leeway_seconds: int = 0,
    required_issuer: str | None = None,
    required_audience: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    if not isinstance(token, str) or not secret:
        raise JWTError("invalid inputs")

    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError(f"malformed token (got {len(parts)} parts, expected 3)")
    header_b, claims_b, sig_b = parts

    try:
        header_raw = _b64url_decode(header_b)
        claims_raw = _b64url_decode(claims_b)
        sig = _b64url_decode(sig_b)
    except (ValueError, binascii.Error) as e:
        raise JWTError(f"base64 decode failed: {e}") from e

    try:
        header = json.loads(header_raw)
        claims = json.loads(claims_raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise JWTError(f"json decode failed: {e}") from e

    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise JWTError("header/claims must be JSON objects")

    alg = header.get("alg")
    if alg != "HS256":
        raise JWTError(f"unsupported alg: {alg!r} (only HS256 allowed)")

    signing_input = f"{header_b}.{claims_b}".encode("ascii")
    expected = hmac.new(
        secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, sig):
        raise JWTError("signature mismatch")

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise JWTError("missing or invalid 'exp' claim")
    current = now if now is not None else int(time.time())
    if current > exp + leeway_seconds:
        raise JWTError(f"token expired (exp={int(exp)}, now={current}, leeway={leeway_seconds})")

    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and current + leeway_seconds < nbf:
        raise JWTError(f"token not yet valid (nbf={int(nbf)}, now={current})")

    if required_issuer is not None and claims.get("iss") != required_issuer:
        raise JWTError(f"issuer mismatch (got {claims.get('iss')!r}, expected {required_issuer!r})")

    if required_audience is not None:
        aud = claims.get("aud")
        aud_list = aud if isinstance(aud, list) else ([aud] if aud else [])
        if required_audience not in aud_list:
            raise JWTError(f"audience mismatch (got {aud!r}, expected {required_audience!r})")

    return claims
