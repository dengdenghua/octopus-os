"""Live appliance credentials, global session revocation and account controls.

The Agent still owns password hashing, JWT issuance and identity storage.  Echo
OS owns the mutable device credential record and a persisted ``iat`` floor so
"sign out every session" also invalidates tokens on Agent routes that were
mounted before the appliance extension.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from appliance.agent_api.auth import (
    LEGACY_SESSION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    JWTError,
    clear_session_cookie,
    create_local_auth_router,
    hash_password,
    verify_jwt_hs256,
    verify_password,
)
from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.auth import (
    ACCOUNT_SESSION_NOT_BEFORE_KEY,
    ACCOUNTS_KEY,
    ADMIN_USERNAME,
    SESSION_NOT_BEFORE_KEY,
    auth_store_path,
    normalized_accounts,
    read_auth_store,
    write_auth_store,
)
from appliance.security import ApplianceAuthenticator, resolve_authenticator

AUTH_HASH_ENV = "ECHO_APPLIANCE_ADMIN_PASSWORD_HASH"
MIN_PASSWORD_CHARACTERS = 12
MAX_PASSWORD_BYTES = 72  # bcrypt's portable input limit


class AccountSecurityError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PasswordRotationBody(BaseModel):
    new_password: str = Field(
        ...,
        alias="newPassword",
        min_length=MIN_PASSWORD_CHARACTERS,
        max_length=256,
    )


class ApplianceAccountSecurity:
    """Coordinate disk state, live auth config, approval tokens and JWT floor."""

    def __init__(
        self,
        *,
        auth_config: Any,
        approval: HighRiskApprovalService,
        audit: ApplianceAudit,
        clock: Any = time.time,
        sleeper: Any = time.sleep,
    ) -> None:
        if not getattr(auth_config, "jwt_secret", None):
            raise ValueError("appliance account security requires a JWT secret")
        self._config = auth_config
        self._approval = approval
        self._audit = audit
        self._clock = clock
        self._sleeper = sleeper
        self._path = auth_store_path()
        self._lock = threading.RLock()

        payload = read_auth_store(self._path)
        raw_floor = payload.get(SESSION_NOT_BEFORE_KEY, 0)
        if isinstance(raw_floor, bool):
            raise ValueError("invalid appliance session floor")
        try:
            floor = int(raw_floor)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid appliance session floor") from exc
        if floor < 0 or floor > int(self._clock()) + 60:
            raise ValueError("invalid appliance session floor")
        self._session_not_before = floor
        self._account_session_not_before = self._validated_account_floors(payload)
        self._active_accounts = {
            username
            for username, account in normalized_accounts(payload).items()
            if account["active"]
        }

    def _validated_account_floors(self, payload: dict[str, Any]) -> dict[str, int]:
        raw = payload.get(ACCOUNT_SESSION_NOT_BEFORE_KEY, {})
        if not isinstance(raw, dict):
            raise ValueError("invalid appliance account session floors")
        floors: dict[str, int] = {}
        for username, value in raw.items():
            if not isinstance(username, str) or not username or isinstance(value, bool):
                raise ValueError("invalid appliance account session floor")
            try:
                floor = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid appliance account session floor") from exc
            if floor < 0 or floor > int(self._clock()) + 60:
                raise ValueError("invalid appliance account session floor")
            floors[username] = floor
        return floors

    @property
    def session_not_before(self) -> int:
        with self._lock:
            return self._session_not_before

    def claims_are_current(self, claims: dict[str, Any]) -> bool:
        issued_at = claims.get("iat")
        if isinstance(issued_at, bool):
            return False
        try:
            issued = int(issued_at)
        except (TypeError, ValueError):
            return False
        subject = claims.get("sub")
        with self._lock:
            floor = self._session_not_before
            if isinstance(subject, str) and subject.startswith("local:"):
                username = subject.removeprefix("local:")
                if username not in self._active_accounts:
                    return False
                floor = max(
                    floor,
                    self._account_session_not_before.get(username, 0),
                )
        return issued >= floor

    def token_is_stale(self, token: str) -> bool:
        claims = self._verified_claims(token)
        return claims is not None and not self.claims_are_current(claims)

    def token_issued_at(self, token: str) -> int | None:
        claims = self._verified_claims(token)
        if claims is None:
            return None
        issued_at = claims.get("iat")
        if isinstance(issued_at, bool):
            return None
        try:
            return int(issued_at)
        except (TypeError, ValueError):
            return None

    def _verified_claims(self, token: str) -> dict[str, Any] | None:
        try:
            return verify_jwt_hs256(token, secret=self._config.jwt_secret)
        except JWTError:
            return None

    def wait_for_login_window(self) -> None:
        """Prevent a token being minted in the same second as a revocation."""

        deadline = self._clock() + 2.5
        while int(self._clock()) < self.session_not_before:
            if self._clock() >= deadline:
                raise AccountSecurityError(503, "session revocation clock did not advance")
            self._sleeper(0.025)

    def _record(self, *, actor: str, action: str, outcome: str) -> None:
        try:
            self._audit.record(
                actor=actor,
                action=action,
                target="all" if action == "sessions.revoke" else ADMIN_USERNAME,
                outcome=outcome,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise AccountSecurityError(503, "appliance audit integrity check failed") from exc

    def _next_floor(self) -> int:
        # Repeated/concurrent revocations in the same second share one cutoff;
        # continually incrementing a future floor could lock out fresh logins.
        return max(int(self._clock()) + 1, self._session_not_before)

    def _next_account_floor(self, username: str) -> int:
        return max(
            int(self._clock()) + 1,
            self._session_not_before,
            self._account_session_not_before.get(username, 0),
        )

    def persist_member_account(
        self,
        *,
        username: str,
        account: dict[str, Any] | None,
        expect_exists: bool,
    ) -> int:
        """Atomically update one member login and revoke only its old sessions."""

        if not username or username == ADMIN_USERNAME:
            raise AccountSecurityError(422, "invalid family member account")
        with self._lock:
            payload = read_auth_store(self._path)
            accounts = normalized_accounts(payload)
            if (username in accounts) != expect_exists:
                raise AccountSecurityError(409, "family member account changed; preview again")
            if account is None:
                accounts.pop(username, None)
            else:
                candidate = dict(accounts)
                candidate[username] = dict(account)
                payload[ACCOUNTS_KEY] = candidate
                accounts = normalized_accounts(payload)
            floor = self._next_account_floor(username)
            floors = self._validated_account_floors(payload)
            floors[username] = floor
            payload[ACCOUNTS_KEY] = accounts
            payload[ACCOUNT_SESSION_NOT_BEFORE_KEY] = floors
            write_auth_store(payload, self._path)
            if account is not None and account.get("active") is True:
                self._config.users[username] = str(account["password_hash"])
                self._active_accounts.add(username)
            else:
                self._config.users.pop(username, None)
                self._active_accounts.discard(username)
            self._account_session_not_before = floors
            return floor

    def wait_for_account_login_window(self, username: str) -> None:
        deadline = self._clock() + 2.5
        while int(self._clock()) < self._account_session_not_before.get(username, 0):
            if self._clock() >= deadline:
                raise AccountSecurityError(503, "account session revocation clock did not advance")
            self._sleeper(0.025)

    def _updated_payload(self, *, floor: int, password_hash: str | None = None) -> dict[str, Any]:
        payload = read_auth_store(self._path)
        payload[SESSION_NOT_BEFORE_KEY] = floor
        if password_hash is not None:
            payload["password_hash"] = password_hash
            accounts = payload.get(ACCOUNTS_KEY)
            if isinstance(accounts, dict) and isinstance(accounts.get(ADMIN_USERNAME), dict):
                accounts[ADMIN_USERNAME]["password_hash"] = password_hash
        return payload

    def revoke_all(self, *, actor: str) -> int:
        with self._lock:
            self._record(actor=actor, action="sessions.revoke", outcome="attempted")
            floor = self._next_floor()
            write_auth_store(self._updated_payload(floor=floor), self._path)
            self._session_not_before = floor
            self._approval.invalidate_tokens()
            self._record(actor=actor, action="sessions.revoke", outcome="succeeded")
            return floor

    def rotate_password(self, *, actor: str, new_password: str) -> int:
        encoded = new_password.encode("utf-8")
        if len(new_password) < MIN_PASSWORD_CHARACTERS:
            raise AccountSecurityError(422, "new password must contain at least 12 characters")
        if len(encoded) > MAX_PASSWORD_BYTES:
            raise AccountSecurityError(422, "new password must be at most 72 UTF-8 bytes")

        with self._lock:
            current_hash = self._config.users[ADMIN_USERNAME]
            with contextlib.suppress(TypeError, ValueError):
                if verify_password(new_password, current_hash):
                    raise AccountSecurityError(
                        422, "new password must differ from current password"
                    )
            new_hash = hash_password(new_password)
            self._record(actor=actor, action="credentials.rotate", outcome="attempted")
            floor = self._next_floor()
            write_auth_store(
                self._updated_payload(floor=floor, password_hash=new_hash),
                self._path,
            )
            # The official Agent local-auth router closes over this mutable
            # config. Updating the dict changes the next login immediately.
            self._config.users[ADMIN_USERNAME] = new_hash
            os.environ[AUTH_HASH_ENV] = new_hash
            self._session_not_before = floor
            self._approval.update_password_hash(new_hash)
            self._record(actor=actor, action="credentials.rotate", outcome="succeeded")
            return floor


class ApplianceLocalAuthMiddleware:
    """Give appliance mode one live official local-auth router.

    Agent may already have mounted a router with boot-time credentials.  This
    exact-prefix ASGI dispatch makes the appliance-owned mutable config
    authoritative without removing Agent routes or copying its crypto code.
    """

    def __init__(
        self,
        app: Any,
        *,
        auth_config: Any,
        identity_store: Any,
        account_security: ApplianceAccountSecurity,
    ) -> None:
        self.app = app
        self._security = account_security
        auth_app = FastAPI()
        auth_app.include_router(
            create_local_auth_router(config=auth_config, identity_store=identity_store)
        )
        self._auth_app = auth_app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if path != "/api/auth/local" and not path.startswith("/api/auth/local/"):
            await self.app(scope, receive, send)
            return
        if scope.get("type") == "http" and path == "/api/auth/local/login":
            try:
                await run_in_threadpool(self._security.wait_for_login_window)
            except AccountSecurityError as exc:
                response = Response(exc.detail, status_code=exc.status_code)
                await response(scope, receive, send)
                return
        await self._auth_app(scope, receive, send)


def _cookie_without_stale_session(raw: str, security: ApplianceAccountSecurity) -> str:
    parsed = SimpleCookie()
    with contextlib.suppress(Exception):
        parsed.load(raw)
    session_name = next(
        (
            name
            for name in (SESSION_COOKIE_NAME, LEGACY_SESSION_COOKIE_NAME)
            if parsed.get(name) is not None
        ),
        None,
    )
    session = parsed.get(session_name) if session_name else None
    if session is None or not security.token_is_stale(session.value):
        return raw
    del parsed[session_name]
    return "; ".join(morsel.OutputString() for morsel in parsed.values())


def _scope_token(scope: dict[str, Any]) -> str:
    """Match Agent principal resolution order for an attached WebSocket JWT."""

    header_map = {key.lower(): value.decode("latin-1") for key, value in scope.get("headers") or ()}
    authorization = header_map.get(b"authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    protocol = header_map.get(b"sec-websocket-protocol", "")
    if protocol:
        parts = [part.strip() for part in protocol.split(",") if part.strip()]
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            return parts[1]
    for key, value in parse_qsl(
        (scope.get("query_string") or b"").decode("latin-1"),
        keep_blank_values=True,
    ):
        if key == "token" and value:
            return value
    parsed = SimpleCookie()
    with contextlib.suppress(Exception):
        parsed.load(header_map.get(b"cookie", ""))
    session = parsed.get(SESSION_COOKIE_NAME) or parsed.get(LEGACY_SESSION_COOKIE_NAME)
    return session.value if session is not None else ""


def _strip_stale_credentials(
    scope: dict[str, Any], security: ApplianceAccountSecurity
) -> dict[str, Any]:
    changed = False
    headers: list[tuple[bytes, bytes]] = []
    for key, value in scope.get("headers") or ():
        lowered = key.lower()
        replacement = value
        if lowered == b"authorization":
            decoded = value.decode("latin-1")
            if decoded.lower().startswith("bearer "):
                token = decoded[7:].strip()
                if token and security.token_is_stale(token):
                    changed = True
                    continue
        elif lowered == b"cookie":
            decoded = value.decode("latin-1")
            fresh = _cookie_without_stale_session(decoded, security)
            if fresh != decoded:
                changed = True
                if not fresh:
                    continue
                replacement = fresh.encode("latin-1")
        elif lowered == b"sec-websocket-protocol":
            parts = [part.strip() for part in value.decode("latin-1").split(",")]
            if (
                len(parts) >= 2
                and parts[0].lower() == "bearer"
                and security.token_is_stale(parts[1])
            ):
                changed = True
                continue
        headers.append((key, replacement))

    raw_query = scope.get("query_string") or b""
    query = parse_qsl(raw_query.decode("latin-1"), keep_blank_values=True)
    filtered_query = [
        (key, value)
        for key, value in query
        if not (key == "token" and value and security.token_is_stale(value))
    ]
    if len(filtered_query) != len(query):
        changed = True

    if not changed:
        return scope
    updated = dict(scope)
    updated["headers"] = headers
    updated["query_string"] = urlencode(filtered_query, doseq=True).encode("latin-1")
    return updated


class ApplianceSessionRevocationMiddleware:
    """Remove revoked JWTs from every downstream HTTP and WebSocket request."""

    def __init__(self, app: Any, *, account_security: ApplianceAccountSecurity) -> None:
        self.app = app
        self._security = account_security

    async def _run_live_websocket(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        *,
        token: str,
    ) -> None:
        send_lock = asyncio.Lock()

        async def serialized_send(message: dict[str, Any]) -> None:
            async with send_lock:
                await send(message)

        async def watch_revocation() -> None:
            while not self._security.token_is_stale(token):
                await asyncio.sleep(0.1)
            with contextlib.suppress(RuntimeError):
                await serialized_send(
                    {
                        "type": "websocket.close",
                        "code": 4401,
                        "reason": "appliance session revoked",
                    }
                )

        application = asyncio.create_task(self.app(scope, receive, serialized_send))
        watcher = asyncio.create_task(watch_revocation())
        done, pending = await asyncio.wait(
            {application, watcher},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if application in done:
            await application

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        token = _scope_token(scope) if scope.get("type") == "websocket" else ""
        cleaned_scope = _strip_stale_credentials(scope, self._security)
        if (
            scope.get("type") == "websocket"
            and token
            and self._security.token_issued_at(token) is not None
            and not self._security.token_is_stale(token)
        ):
            await self._run_live_websocket(
                cleaned_scope,
                receive,
                send,
                token=token,
            )
            return
        await self.app(cleaned_scope, receive, send)


def create_account_security_router(
    service: ApplianceAccountSecurity,
    *,
    approval: HighRiskApprovalService,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/appliance", tags=["appliance", "account-security"])
    require_operator = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).operator_dependency()

    def _raise(exc: AccountSecurityError) -> None:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/sessions/revoke")
    def revoke_sessions(
        request: Request,
        response: Response,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action="sessions.revoke",
            target="all",
        )
        try:
            floor = service.revoke_all(actor=actor)
            service.wait_for_login_window()
        except AccountSecurityError as exc:
            _raise(exc)
        clear_session_cookie(response, request)
        return {"success": True, "sessionsRevoked": True, "sessionNotBefore": floor}

    @router.post("/credentials/rotate")
    def rotate_password(
        body: PasswordRotationBody,
        request: Request,
        response: Response,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action="credentials.rotate",
            target=ADMIN_USERNAME,
        )
        try:
            floor = service.rotate_password(actor=actor, new_password=body.new_password)
            service.wait_for_login_window()
        except AccountSecurityError as exc:
            _raise(exc)
        clear_session_cookie(response, request)
        return {"success": True, "sessionsRevoked": True, "sessionNotBefore": floor}

    return router


__all__ = [
    "AUTH_HASH_ENV",
    "AccountSecurityError",
    "ApplianceAccountSecurity",
    "ApplianceLocalAuthMiddleware",
    "ApplianceSessionRevocationMiddleware",
    "create_account_security_router",
]
