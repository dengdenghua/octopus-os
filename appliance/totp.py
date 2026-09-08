"""Administrator TOTP and one-time recovery-code state for Echo OS."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from appliance.auth import ADMIN_USERNAME, auth_store_path, read_auth_store, write_auth_store

TOTP_FILENAME = "appliance-totp.json"
TOTP_FORMAT = "echo-appliance-totp-v1"
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_RECOVERY_CODES = 8
TOTP_ENROLLMENT_SECONDS = 5 * 60
MAX_TOTP_STATE_BYTES = 16 * 1024

_BASE32_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


@dataclass(frozen=True)
class TotpEnrollment:
    enrollment_id: str
    secret: str
    recovery_codes: tuple[str, ...]
    expires_at: float


def _decode_secret(secret: str) -> bytes:
    if (
        not isinstance(secret, str)
        or not 16 <= len(secret) <= 128
        or any(character not in _BASE32_ALPHABET for character in secret)
    ):
        raise ValueError("invalid TOTP secret")
    try:
        return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid TOTP secret") from exc


def totp_code(secret: str, *, timestamp: float | None = None, counter: int | None = None) -> str:
    """Return an RFC 6238 SHA-1 code using only stdlib cryptographic primitives."""

    if counter is None:
        instant = time.time() if timestamp is None else float(timestamp)
        counter = int(instant // TOTP_PERIOD_SECONDS)
    if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
        raise ValueError("invalid TOTP counter")
    digest = hmac.new(
        _decode_secret(secret),
        counter.to_bytes(8, "big"),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{binary % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def _normalized_recovery_code(value: str) -> str:
    normalized = "".join(character for character in value.upper() if character not in " -")
    if len(normalized) != 16 or any(
        character not in _RECOVERY_ALPHABET for character in normalized
    ):
        return ""
    return normalized


def validate_totp_state(payload: Any) -> dict[str, Any]:
    expected = {
        "format",
        "username",
        "secret",
        "recovery_code_hashes",
        "last_accepted_counter",
        "enabled_at",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("invalid appliance TOTP state")
    if payload.get("format") != TOTP_FORMAT or payload.get("username") != ADMIN_USERNAME:
        raise ValueError("invalid appliance TOTP identity")
    _decode_secret(payload.get("secret"))
    hashes = payload.get("recovery_code_hashes")
    if (
        not isinstance(hashes, list)
        or len(hashes) > TOTP_RECOVERY_CODES
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in hashes
        )
        or len(set(hashes)) != len(hashes)
    ):
        raise ValueError("invalid appliance TOTP recovery codes")
    counter = payload.get("last_accepted_counter")
    enabled_at = payload.get("enabled_at")
    if (
        isinstance(counter, bool)
        or not isinstance(counter, int)
        or counter < -1
        or isinstance(enabled_at, bool)
        or not isinstance(enabled_at, int)
        or enabled_at < 0
    ):
        raise ValueError("invalid appliance TOTP counters")
    return {
        "format": TOTP_FORMAT,
        "username": ADMIN_USERNAME,
        "secret": payload["secret"],
        "recovery_code_hashes": list(hashes),
        "last_accepted_counter": counter,
        "enabled_at": enabled_at,
    }


class AdministratorTotp:
    """Fail-closed TOTP gate backed by one root-private atomic JSON file."""

    def __init__(
        self,
        *,
        jwt_secret: str,
        path: Path | None = None,
        clock: Any = time.time,
    ) -> None:
        if not jwt_secret:
            raise ValueError("administrator TOTP requires a device secret")
        self._path = path or auth_store_path().with_name(TOTP_FILENAME)
        self._clock = clock
        self._recovery_key = hmac.new(
            jwt_secret.encode("utf-8"),
            b"echo-appliance-totp-recovery-v1",
            hashlib.sha256,
        ).digest()
        self._lock = threading.RLock()
        self._pending: TotpEnrollment | None = None
        if self._path.exists() or self._path.is_symlink():
            self._read_state()

    @property
    def path(self) -> Path:
        return self._path

    def _read_state(self) -> dict[str, Any]:
        try:
            if self._path.stat().st_size > MAX_TOTP_STATE_BYTES:
                raise ValueError("appliance TOTP state is too large")
            return validate_totp_state(read_auth_store(self._path))
        except FileNotFoundError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("appliance TOTP state is unreadable") from exc

    def _write_state(self, state: dict[str, Any]) -> None:
        write_auth_store(validate_totp_state(state), self._path)

    def _recovery_hash(self, value: str) -> str:
        return hmac.new(
            self._recovery_key,
            value.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def enabled(self) -> bool:
        with self._lock:
            return self._path.exists()

    def status(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return {"enabled": False, "recoveryCodesRemaining": 0}
            state = self._read_state()
            return {
                "enabled": True,
                "recoveryCodesRemaining": len(state["recovery_code_hashes"]),
            }

    def begin_enrollment(self) -> dict[str, Any]:
        with self._lock:
            if self._path.exists():
                raise HTTPException(409, "管理员动态验证码已经启用")
            secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
            codes = tuple(
                "-".join(
                    "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4)) for _ in range(4)
                )
                for _ in range(TOTP_RECOVERY_CODES)
            )
            enrollment = TotpEnrollment(
                enrollment_id=secrets.token_urlsafe(24),
                secret=secret,
                recovery_codes=codes,
                expires_at=float(self._clock()) + TOTP_ENROLLMENT_SECONDS,
            )
            self._pending = enrollment
            label = quote("Echo OS:admin", safe="")
            issuer = quote("Echo OS", safe="")
            return {
                "enrollmentId": enrollment.enrollment_id,
                "secret": secret,
                "otpauthUri": (
                    f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"
                    f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
                ),
                "recoveryCodes": list(codes),
                "expiresIn": TOTP_ENROLLMENT_SECONDS,
            }

    def confirm_enrollment(self, *, enrollment_id: str, code: str) -> None:
        with self._lock:
            pending = self._pending
            if (
                pending is None
                or not secrets.compare_digest(pending.enrollment_id, enrollment_id)
                or float(self._clock()) > pending.expires_at
            ):
                self._pending = None
                raise HTTPException(409, "动态验证码设置已过期，请重新开始")
            counter = int(float(self._clock()) // TOTP_PERIOD_SECONDS)
            matched = self._matching_counter(pending.secret, code, counter=counter, floor=-1)
            if matched is None:
                raise HTTPException(422, "动态验证码不正确")
            state = {
                "format": TOTP_FORMAT,
                "username": ADMIN_USERNAME,
                "secret": pending.secret,
                "recovery_code_hashes": [
                    self._recovery_hash(_normalized_recovery_code(value))
                    for value in pending.recovery_codes
                ],
                # Enrollment proves possession but does not mint a session.
                # Permit this code once at the immediately following login;
                # the login path persists the counter before issuing its JWT.
                "last_accepted_counter": -1,
                "enabled_at": int(self._clock()),
            }
            self._write_state(state)
            self._pending = None

    @staticmethod
    def _matching_counter(secret: str, code: str, *, counter: int, floor: int) -> int | None:
        if (
            not isinstance(code, str)
            or len(code) != TOTP_DIGITS
            or not code.isascii()
            or not code.isdigit()
        ):
            return None
        for candidate in (counter - 1, counter, counter + 1):
            if (
                candidate > floor
                and candidate >= 0
                and hmac.compare_digest(totp_code(secret, counter=candidate), code)
            ):
                return candidate
        return None

    def verify_login_factor(self, username: str, factor: str | None, _request: Any) -> None:
        if username != ADMIN_USERNAME:
            return
        with self._lock:
            if not self._path.exists():
                return
            if not factor:
                raise HTTPException(428, "second_factor_required")
            state = self._read_state()
            current = int(float(self._clock()) // TOTP_PERIOD_SECONDS)
            matched = self._matching_counter(
                state["secret"],
                factor,
                counter=current,
                floor=state["last_accepted_counter"],
            )
            if matched is not None:
                state["last_accepted_counter"] = matched
                self._write_state(state)
                return
            normalized = _normalized_recovery_code(factor)
            candidate_hash = self._recovery_hash(normalized) if normalized else "0" * 64
            match_index = next(
                (
                    index
                    for index, stored in enumerate(state["recovery_code_hashes"])
                    if hmac.compare_digest(stored, candidate_hash)
                ),
                None,
            )
            if match_index is None:
                raise HTTPException(401, "动态验证码或恢复码错误")
            del state["recovery_code_hashes"][match_index]
            self._write_state(state)

    def verify_management_factor(self, factor: str) -> None:
        self.verify_login_factor(ADMIN_USERNAME, factor, None)

    def disable(self) -> None:
        with self._lock:
            if not self._path.exists():
                raise HTTPException(409, "管理员动态验证码尚未启用")
            self._path.unlink()
            self._pending = None


__all__ = [
    "AdministratorTotp",
    "MAX_TOTP_STATE_BYTES",
    "TOTP_FILENAME",
    "TOTP_FORMAT",
    "totp_code",
    "validate_totp_state",
]
