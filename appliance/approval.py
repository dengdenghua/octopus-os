"""Password step-up approval for destructive appliance operations.

An authenticated browser must re-enter the device administrator password.
The resulting token is short-lived, single-use, and cryptographically bound
to actor, action and target. It is therefore an actual human step-up rather
than a cosmetic confirmation dialog or a replayable HTTP header.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.identifiers import is_container_id
from appliance.security import ApplianceAuthenticator, resolve_authenticator

APPROVAL_HEADER = "X-Echo-Approval"
INTENT_HEADER = "X-Echo-Intent"
APPROVAL_TTL_SECONDS = 90
APPROVAL_MAX_FAILURES = 5
APPROVAL_FAILURE_WINDOW_SECONDS = 5 * 60
APPROVAL_LOCKOUT_SECONDS = 60
APPROVAL_MAX_ENTRIES = 10_000

_PLAN_ID = re.compile(r"^[0-9a-f]{64}$")
_INTENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ApprovalError(RuntimeError):
    def __init__(self, status_code: int, detail: str, *, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after


@dataclass
class _FailureState:
    failures: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))


def _valid_target(action: str, target: str) -> bool:
    if action in {"app.start", "app.stop"}:
        return is_container_id(target)
    if action == "files.trash.empty":
        return target == "recycle-bin"
    if action == "sessions.revoke":
        return target == "all"
    if action == "audit.key.rotate":
        return target == "audit-chain"
    if action in {
        "device-link.enable",
        "device-link.disable",
        "device-link.pair",
    }:
        return target == "lan"
    if action == "device-link.device.revoke":
        return _INTENT_ID.fullmatch(target) is not None
    if action in {
        "device-sync.photos.enable",
        "device-sync.photos.disable",
        "device-sync.files.enable",
        "device-sync.files.disable",
    }:
        return _INTENT_ID.fullmatch(target) is not None
    if action in {
        "agent.capability.install",
        "agent.capability.authorize",
        "agent.capability.uninstall",
        "agent.capability.rollback",
        "hub.app.install",
        "hub.app.update",
        "hub.app.uninstall",
        "hub.app.start",
        "hub.app.stop",
        "hub.app.restart",
        "photos.index.build",
        "omv.shared-folder.create",
        "omv.share-privilege.apply",
        "omv.smb.apply",
        "omv.nfs.apply",
        "omv.quota.apply",
        "omv.group.create",
        "omv.user.create",
        "omv.user.password.reset",
        "account.member.link",
        "account.member.status.set",
        "account.member.password.reset",
        "account.member.unlink",
    }:
        return _PLAN_ID.fullmatch(target) is not None
    return action == "credentials.rotate" and target == "admin"


class HighRiskApprovalService:
    """Issue and consume one-shot, boot-scoped step-up tokens."""

    def __init__(
        self,
        *,
        password_hash: str,
        jwt_secret: str,
        audit: ApplianceAudit,
        clock: Callable[[], float] = time.time,
        boot_nonce: bytes | None = None,
        ttl_seconds: int = APPROVAL_TTL_SECONDS,
        max_failures: int = APPROVAL_MAX_FAILURES,
    ) -> None:
        if not password_hash or not jwt_secret:
            raise ValueError("password_hash and jwt_secret are required")
        self._password_hash = password_hash
        self._audit = audit
        self._clock = clock
        self._ttl = max(1, int(ttl_seconds))
        self._max_failures = max(1, int(max_failures))
        nonce = boot_nonce if boot_nonce is not None else secrets.token_bytes(32)
        self._key = hmac.new(
            jwt_secret.encode("utf-8"),
            b"echo-os/appliance-approval/v1\0" + nonce,
            hashlib.sha256,
        ).digest()
        self._failures: OrderedDict[tuple[str, str], _FailureState] = OrderedDict()
        self._consumed: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.RLock()

    def _invalidate_tokens_locked(self) -> None:
        self._key = hmac.new(
            self._key,
            b"echo-os/appliance-approval/rotate\0" + secrets.token_bytes(32),
            hashlib.sha256,
        ).digest()
        self._consumed.clear()

    def invalidate_tokens(self) -> None:
        """Invalidate every outstanding step-up token without changing password."""

        with self._lock:
            self._invalidate_tokens_locked()

    def update_password_hash(self, password_hash: str) -> None:
        """Switch live password verification and invalidate all old approvals."""

        if not password_hash:
            raise ValueError("password_hash is required")
        with self._lock:
            self._password_hash = password_hash
            self._failures.clear()
            self._invalidate_tokens_locked()

    def _audit_event(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        reason: str = "",
        intent_id: str | None = None,
    ) -> None:
        metadata = {"requestedAction": action}
        if reason:
            metadata["reason"] = reason
        if intent_id:
            metadata["intentId"] = intent_id
        try:
            self._audit.record(
                actor=actor,
                action="approval",
                target=f"{action}:{target}",
                outcome=outcome,
                metadata=metadata,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise ApprovalError(503, "appliance audit integrity check failed") from exc

    def _failure_key(self, actor: str, client_ip: str) -> tuple[str, str]:
        return actor[:256], (client_ip or "unknown")[:128]

    def _prune_failures(self, state: _FailureState, now: float) -> None:
        cutoff = now - APPROVAL_FAILURE_WINDOW_SECONDS
        while state.failures and state.failures[0] <= cutoff:
            state.failures.popleft()
        if state.locked_until and state.locked_until <= now:
            state.failures.clear()
            state.locked_until = 0.0

    def _retry_after(self, key: tuple[str, str], now: float) -> int:
        state = self._failures.get(key)
        if state is None:
            return 0
        self._prune_failures(state, now)
        if state.locked_until <= now:
            if not state.failures:
                self._failures.pop(key, None)
            return 0
        return max(1, math.ceil(state.locked_until - now))

    def _record_failure(self, key: tuple[str, str], now: float) -> int:
        state = self._failures.get(key)
        if state is None:
            state = _FailureState()
            self._failures[key] = state
        else:
            self._prune_failures(state, now)
        state.failures.append(now)
        if len(state.failures) >= self._max_failures:
            state.locked_until = now + APPROVAL_LOCKOUT_SECONDS
        self._failures.move_to_end(key)
        while len(self._failures) > APPROVAL_MAX_ENTRIES:
            self._failures.popitem(last=False)
        return self._retry_after(key, now)

    def _prune_consumed(self, now: int) -> None:
        expired = [jti for jti, exp in self._consumed.items() if exp <= now]
        for jti in expired:
            self._consumed.pop(jti, None)
        while len(self._consumed) > APPROVAL_MAX_ENTRIES:
            self._consumed.popitem(last=False)

    def issue(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        password: str,
        client_ip: str,
        intent_id: str | None = None,
    ) -> tuple[str, int]:
        if intent_id is not None and _INTENT_ID.fullmatch(intent_id) is None:
            self._audit_event(
                actor=actor,
                action=action,
                target=target,
                outcome="denied",
                reason="invalid intent id",
            )
            raise ApprovalError(422, "invalid intent id")
        if not _valid_target(action, target):
            self._audit_event(
                actor=actor,
                action=action,
                target=target,
                outcome="denied",
                reason="unsupported approval intent",
                intent_id=intent_id,
            )
            raise ApprovalError(422, "unsupported approval intent")

        now_float = self._clock()
        key = self._failure_key(actor, client_ip)
        with self._lock:
            retry_after = self._retry_after(key, now_float)
            if retry_after:
                self._audit_event(
                    actor=actor,
                    action=action,
                    target=target,
                    outcome="rate_limited",
                    intent_id=intent_id,
                )
                raise ApprovalError(429, "approval temporarily locked", retry_after=retry_after)

            from appliance.agent_api.auth import verify_password

            try:
                valid_password = verify_password(password, self._password_hash)
            except (TypeError, ValueError):
                valid_password = False
            if not valid_password:
                retry_after = self._record_failure(key, now_float)
                outcome = "rate_limited" if retry_after else "denied"
                self._audit_event(
                    actor=actor,
                    action=action,
                    target=target,
                    outcome=outcome,
                    reason="password verification failed",
                    intent_id=intent_id,
                )
                if retry_after:
                    raise ApprovalError(
                        429,
                        "approval temporarily locked",
                        retry_after=retry_after,
                    )
                raise ApprovalError(403, "administrator password is incorrect")

            self._failures.pop(key, None)
            now = int(now_float)
            expires_at = now + self._ttl
            payload = {
                "v": 1,
                "jti": secrets.token_urlsafe(18),
                "sub": actor,
                "action": action,
                "target": target,
                "iat": now,
                "exp": expires_at,
            }
            if intent_id is not None:
                payload["intent"] = intent_id
            encoded = _b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            signature = _b64encode(
                hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest()
            )
            self._audit_event(
                actor=actor,
                action=action,
                target=target,
                outcome="issued",
                intent_id=intent_id,
            )
            return f"{encoded}.{signature}", self._ttl

    def consume(
        self,
        *,
        token: str,
        actor: str,
        action: str,
        target: str,
        intent_id: str | None = None,
    ) -> None:
        def deny(reason: str) -> None:
            self._audit_event(
                actor=actor,
                action=action,
                target=target,
                outcome="denied",
                reason=reason,
                intent_id=intent_id,
            )
            raise ApprovalError(403, "valid high-risk approval required")

        if not token or token.count(".") != 1:
            deny("approval token missing or malformed")
        encoded, signature = token.split(".", 1)
        try:
            supplied_signature = _b64decode(signature)
            encoded_ascii = encoded.encode("ascii")
        except (ValueError, UnicodeError, binascii.Error):
            deny("approval signature malformed")
        expected_signature = hmac.new(
            self._key,
            encoded_ascii,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            deny("approval signature mismatch")
        try:
            payload = json.loads(_b64decode(encoded))
            jti = str(payload["jti"])
            subject = str(payload["sub"])
            approved_action = str(payload["action"])
            approved_target = str(payload["target"])
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
            version = int(payload["v"])
            approved_intent_raw = payload.get("intent")
            approved_intent = str(approved_intent_raw) if approved_intent_raw is not None else None
        except (
            ValueError,
            KeyError,
            TypeError,
            UnicodeError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            deny("approval payload malformed")

        now = int(self._clock())
        if version != 1 or not jti or issued_at > now + 5 or expires_at <= now:
            deny("approval token expired or invalid")
        if approved_intent is not None and _INTENT_ID.fullmatch(approved_intent) is None:
            deny("approval intent id invalid")
        if subject != actor or approved_action != action or approved_target != target:
            deny("approval intent binding mismatch")
        if approved_intent is not None and approved_intent != intent_id:
            deny("approval task intent binding mismatch")

        with self._lock:
            self._prune_consumed(now)
            if jti in self._consumed:
                deny("approval token replayed")
            self._consumed[jti] = expires_at
            self._consumed.move_to_end(jti)
            self._prune_consumed(now)
            self._audit_event(
                actor=actor,
                action=action,
                target=target,
                outcome="consumed",
                intent_id=intent_id,
            )


class ApprovalRequestBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=128)
    target: str = Field(..., min_length=1, max_length=512)
    password: str = Field(..., min_length=1, max_length=256)
    intent_id: str | None = Field(default=None, alias="intentId", min_length=1, max_length=128)


def _client_ip(request: Request) -> str:
    client = request.client
    return str(client.host if client else "unknown")


def _as_http_error(exc: ApprovalError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers)


def create_approval_router(
    service: HighRiskApprovalService,
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/appliance/approvals", tags=["appliance", "approval"])
    require_auth = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).dependency()

    @router.post("")
    def issue_approval(
        body: ApprovalRequestBody,
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        try:
            token, expires_in = service.issue(
                actor=actor,
                action=body.action,
                target=body.target,
                password=body.password,
                client_ip=_client_ip(request),
                intent_id=body.intent_id,
            )
        except ApprovalError as exc:
            raise _as_http_error(exc) from exc
        return {
            "approvalToken": token,
            "expiresIn": expires_in,
            "action": body.action,
            "target": body.target,
            "intentId": body.intent_id,
        }

    return router


def consume_request_approval(
    request: Request,
    service: HighRiskApprovalService,
    *,
    actor: str,
    action: str,
    target: str,
) -> None:
    try:
        service.consume(
            token=request.headers.get(APPROVAL_HEADER, ""),
            actor=actor,
            action=action,
            target=target,
            intent_id=request_intent_id(request),
        )
    except ApprovalError as exc:
        raise _as_http_error(exc) from exc


def request_intent_id(request: Request) -> str | None:
    """Return a validated task intent header for audit/projection correlation."""

    value = request.headers.get(INTENT_HEADER, "").strip()
    if not value:
        return None
    if _INTENT_ID.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="invalid intent id")
    return value


__all__ = [
    "APPROVAL_HEADER",
    "INTENT_HEADER",
    "APPROVAL_TTL_SECONDS",
    "ApprovalError",
    "HighRiskApprovalService",
    "consume_request_approval",
    "create_approval_router",
    "request_intent_id",
]
