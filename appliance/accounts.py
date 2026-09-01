"""Echo family login directory backed by public OMV account projections.

OMV remains the storage identity authority and Agent remains the authentication
implementation.  Echo owns only a small, explicit mapping between them.  A
member can be linked only after the corresponding normal OMV user is visible
through ``sharing_overview``; no OMV or Agent private database is opened.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from appliance.account_security import AccountSecurityError
from appliance.agent_api.auth import hash_password, verify_password
from appliance.approval import (
    HighRiskApprovalService,
    consume_request_approval,
    request_intent_id,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.auth import (
    ACCOUNTS_KEY,
    ADMIN_USERNAME,
    MAX_LOCAL_ACCOUNTS,
    auth_store_path,
    normalized_accounts,
    read_auth_store,
    write_auth_store,
)
from appliance.omv_client import OmvControlRejected, OmvUnavailable
from appliance.security import ApplianceAuthenticator, resolve_authenticator

ACCOUNT_DIRECTORY_SCHEMA = "echo.account-directory.v1"
ACCOUNT_LINK_PLAN_SCHEMA = "echo.account-link-plan.v1"
ACCOUNT_LINK_ACTION = "account.member.link"
ACCOUNT_STATUS_ACTION = "account.member.status.set"
ACCOUNT_PASSWORD_ACTION = "account.member.password.reset"
ACCOUNT_UNLINK_ACTION = "account.member.unlink"
MIN_MEMBER_PASSWORD_CHARACTERS = 12
MAX_PASSWORD_BYTES = 72
_USERNAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PASSWORD_CATEGORIES = tuple(
    re.compile(pattern) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
)


class AccountDirectoryError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AccountLinkDesired(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    omv_username: str = Field(..., alias="omvUsername", min_length=1, max_length=32)
    display_name: str = Field(..., alias="displayName", min_length=1, max_length=64)
    password: str = Field(..., min_length=MIN_MEMBER_PASSWORD_CHARACTERS, max_length=256)


class AccountLinkApply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_id: str = Field(..., alias="planId", pattern=r"^[0-9a-f]{64}$")
    desired: AccountLinkDesired


class AccountStatusDesired(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=32)
    active: bool


class AccountPasswordDesired(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    username: str = Field(min_length=1, max_length=32)
    new_password: str = Field(
        ...,
        alias="newPassword",
        min_length=MIN_MEMBER_PASSWORD_CHARACTERS,
        max_length=256,
    )


class AccountUnlinkDesired(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=32)


class AccountStatusApply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_id: str = Field(..., alias="planId", pattern=r"^[0-9a-f]{64}$")
    desired: AccountStatusDesired


class AccountPasswordApply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_id: str = Field(..., alias="planId", pattern=r"^[0-9a-f]{64}$")
    desired: AccountPasswordDesired


class AccountUnlinkApply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_id: str = Field(..., alias="planId", pattern=r"^[0-9a-f]{64}$")
    desired: AccountUnlinkDesired


class _AccountSecurity(Protocol):
    def persist_member_account(
        self,
        *,
        username: str,
        account: dict[str, Any] | None,
        expect_exists: bool,
    ) -> int: ...

    def wait_for_account_login_window(self, username: str) -> None: ...


class ApplianceAccountDirectory:
    """Maintain the live Agent user map and its durable Echo/OMV mapping."""

    def __init__(
        self,
        *,
        auth_config: Any,
        omv: Any,
        jwt_secret: str,
        account_security: _AccountSecurity | None = None,
    ) -> None:
        if not jwt_secret:
            raise ValueError("account directory requires a JWT secret")
        self._config = auth_config
        self._omv = omv
        self._path = auth_store_path()
        self._plan_key = hmac.new(
            jwt_secret.encode("utf-8"),
            b"echo-os/account-link-plan/v1",
            hashlib.sha256,
        ).digest()
        self._lock = threading.RLock()
        self._account_security = account_security

    @staticmethod
    def _validated_password(username: str, password: str) -> str:
        if len(password) < MIN_MEMBER_PASSWORD_CHARACTERS:
            raise AccountDirectoryError(422, "member password must contain at least 12 characters")
        if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise AccountDirectoryError(
                422,
                "member password must be at most 72 UTF-8 bytes",
            )
        password_categories = sum(
            bool(pattern.search(password)) for pattern in _PASSWORD_CATEGORIES
        )
        if (
            password.casefold() == username.casefold()
            or any(ord(character) < 32 for character in password)
            or (len(password) < 20 and password_categories < 3)
        ):
            raise AccountDirectoryError(422, "member password does not meet the strength policy")
        return password

    @staticmethod
    def _validated_desired(desired: AccountLinkDesired) -> tuple[str, str, str]:
        username = desired.omv_username.strip()
        display_name = desired.display_name.strip()
        password = desired.password
        if _USERNAME.fullmatch(username) is None or username == ADMIN_USERNAME:
            raise AccountDirectoryError(422, "invalid family member username")
        if (
            not display_name
            or len(display_name) > 64
            or any(ord(character) < 32 for character in display_name)
        ):
            raise AccountDirectoryError(422, "invalid family member display name")
        ApplianceAccountDirectory._validated_password(username, password)
        return username, display_name, password

    def _public_accounts(self, accounts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "username": username,
                "displayName": account["display_name"],
                "role": account["role"],
                "omvUsername": account["omv_username"],
                "active": account["active"],
            }
            for username, account in sorted(accounts.items())
        ]

    def list_accounts(self, *, actor: str) -> dict[str, Any]:
        accounts = normalized_accounts(read_auth_store(self._path))
        actor_username = actor.removeprefix("local:") if actor.startswith("local:") else ""
        visible = (
            accounts
            if actor == "local:admin"
            else {
                username: account
                for username, account in accounts.items()
                if username == actor_username
            }
        )
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "accounts": self._public_accounts(visible),
            "canManage": actor == "local:admin",
        }

    def omv_username_for_actor(self, actor: str) -> str | None:
        if not actor.startswith("local:") or actor == "local:admin":
            return None
        username = actor.removeprefix("local:")
        accounts = normalized_accounts(read_auth_store(self._path))
        account = accounts.get(username)
        if (
            account is None
            or account["role"] != "member"
            or account["active"] is not True
            or account["omv_username"] != username
        ):
            return None
        return username

    def _require_omv_user(self, username: str) -> None:
        try:
            overview = self._omv.sharing_overview()
        except OmvControlRejected as exc:
            raise AccountDirectoryError(502, "OMV account inventory was rejected") from exc
        except OmvUnavailable as exc:
            raise AccountDirectoryError(503, "OMV account inventory is unavailable") from exc
        users = overview.get("users") if isinstance(overview, dict) else None
        if not isinstance(users, list) or not any(
            isinstance(user, dict) and user.get("name") == username for user in users
        ):
            raise AccountDirectoryError(
                409,
                "the OMV family member no longer exists; refresh storage accounts",
            )

    def _plan_id(self, *, username: str, display_name: str, password: str) -> str:
        password_binding = hmac.new(
            self._plan_key,
            b"password\0" + password.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        canonical = json.dumps(
            {
                "schema": ACCOUNT_LINK_PLAN_SCHEMA,
                "username": username,
                "displayName": display_name,
                "passwordBinding": password_binding,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._plan_key, canonical, hashlib.sha256).hexdigest()

    def _lifecycle_plan_id(self, operation: str, username: str, value: str) -> str:
        canonical = json.dumps(
            {
                "schema": ACCOUNT_DIRECTORY_SCHEMA,
                "operation": operation,
                "username": username,
                "valueBinding": hmac.new(
                    self._plan_key,
                    value.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._plan_key, canonical, hashlib.sha256).hexdigest()

    def _member_account(self, username: str) -> dict[str, Any]:
        if _USERNAME.fullmatch(username) is None or username == ADMIN_USERNAME:
            raise AccountDirectoryError(422, "invalid family member username")
        account = normalized_accounts(read_auth_store(self._path)).get(username)
        if account is None or account["role"] != "member":
            raise AccountDirectoryError(404, "family member account was not found")
        return account

    def _persist_existing_member(self, username: str, account: dict[str, Any]) -> int:
        if self._account_security is not None:
            try:
                floor = self._account_security.persist_member_account(
                    username=username,
                    account=account,
                    expect_exists=True,
                )
                self._account_security.wait_for_account_login_window(username)
            except AccountSecurityError as exc:
                raise AccountDirectoryError(exc.status_code, exc.detail) from exc
            return floor
        payload = read_auth_store(self._path)
        accounts = normalized_accounts(payload)
        accounts[username] = account
        payload[ACCOUNTS_KEY] = accounts
        write_auth_store(payload, self._path)
        if account["active"]:
            self._config.users[username] = account["password_hash"]
        else:
            self._config.users.pop(username, None)
        return 0

    def _persist_new_member(self, username: str, account: dict[str, Any]) -> int:
        if self._account_security is not None:
            try:
                floor = self._account_security.persist_member_account(
                    username=username,
                    account=account,
                    expect_exists=False,
                )
                self._account_security.wait_for_account_login_window(username)
            except AccountSecurityError as exc:
                raise AccountDirectoryError(exc.status_code, exc.detail) from exc
            return floor
        payload = read_auth_store(self._path)
        accounts = normalized_accounts(payload)
        if username in accounts:
            raise AccountDirectoryError(409, "this OMV member already has an Echo login")
        accounts[username] = account
        payload[ACCOUNTS_KEY] = accounts
        write_auth_store(payload, self._path)
        self._config.users[username] = account["password_hash"]
        return 0

    def _remove_member(self, username: str) -> int:
        if self._account_security is not None:
            try:
                floor = self._account_security.persist_member_account(
                    username=username,
                    account=None,
                    expect_exists=True,
                )
                self._account_security.wait_for_account_login_window(username)
            except AccountSecurityError as exc:
                raise AccountDirectoryError(exc.status_code, exc.detail) from exc
            return floor
        payload = read_auth_store(self._path)
        accounts = normalized_accounts(payload)
        accounts.pop(username, None)
        payload[ACCOUNTS_KEY] = accounts
        write_auth_store(payload, self._path)
        self._config.users.pop(username, None)
        return 0

    def plan_status(self, desired: AccountStatusDesired) -> dict[str, Any]:
        username = desired.username.strip()
        account = self._member_account(username)
        if account["active"] is desired.active:
            raise AccountDirectoryError(409, "family member already has the requested status")
        if desired.active:
            self._require_omv_user(username)
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "planId": self._lifecycle_plan_id(
                "setMemberStatus",
                username,
                "active" if desired.active else "inactive",
            ),
            "operation": "setMemberStatus",
            "requiresApproval": True,
            "account": {"username": username, "active": desired.active},
            "changes": ["echoLogin", "memberSessions"],
        }

    def apply_status(self, desired: AccountStatusDesired, *, plan_id: str) -> dict[str, Any]:
        current = self.plan_status(desired)
        if not hmac.compare_digest(str(current["planId"]), plan_id):
            raise AccountDirectoryError(409, "account status plan is stale; preview again")
        username = desired.username.strip()
        with self._lock:
            account = self._member_account(username)
            account["active"] = desired.active
            floor = self._persist_existing_member(username, account)
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "updated": True,
            "account": current["account"],
            "sessionsRevoked": True,
            "sessionNotBefore": floor,
        }

    def plan_password(self, desired: AccountPasswordDesired) -> dict[str, Any]:
        username = desired.username.strip()
        password = self._validated_password(username, desired.new_password)
        account = self._member_account(username)
        if verify_password(password, account["password_hash"]):
            raise AccountDirectoryError(422, "new password must differ from current password")
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "planId": self._lifecycle_plan_id(
                "resetMemberPassword",
                username,
                f"{password}\0{account['password_hash']}",
            ),
            "operation": "resetMemberPassword",
            "requiresApproval": True,
            "account": {"username": username, "active": account["active"]},
            "changes": ["echoPassword", "memberSessions"],
            "safety": {"omvPasswordChanged": False, "passwordReturned": False},
        }

    def apply_password(
        self,
        desired: AccountPasswordDesired,
        *,
        plan_id: str,
    ) -> dict[str, Any]:
        current = self.plan_password(desired)
        if not hmac.compare_digest(str(current["planId"]), plan_id):
            raise AccountDirectoryError(409, "account password plan is stale; preview again")
        username = desired.username.strip()
        with self._lock:
            account = self._member_account(username)
            account["password_hash"] = hash_password(desired.new_password)
            floor = self._persist_existing_member(username, account)
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "updated": True,
            "account": current["account"],
            "sessionsRevoked": True,
            "sessionNotBefore": floor,
        }

    def plan_unlink(self, desired: AccountUnlinkDesired) -> dict[str, Any]:
        username = desired.username.strip()
        account = self._member_account(username)
        if account["active"]:
            raise AccountDirectoryError(409, "disable the Echo member before unlinking it")
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "planId": self._lifecycle_plan_id(
                "unlinkMember",
                username,
                account["password_hash"],
            ),
            "operation": "unlinkMember",
            "requiresApproval": True,
            "account": {"username": username, "active": False},
            "changes": ["echoLogin", "agentPrincipal", "memberSessions"],
            "safety": {"omvUserDeleted": False, "nasDataDeleted": False},
        }

    def apply_unlink(self, desired: AccountUnlinkDesired, *, plan_id: str) -> dict[str, Any]:
        current = self.plan_unlink(desired)
        if not hmac.compare_digest(str(current["planId"]), plan_id):
            raise AccountDirectoryError(409, "account unlink plan is stale; preview again")
        username = desired.username.strip()
        with self._lock:
            floor = self._remove_member(username)
        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "unlinked": True,
            "account": current["account"],
            "sessionsRevoked": True,
            "sessionNotBefore": floor,
        }

    def plan_link(self, desired: AccountLinkDesired) -> dict[str, Any]:
        username, display_name, password = self._validated_desired(desired)
        self._require_omv_user(username)
        accounts = normalized_accounts(read_auth_store(self._path))
        if username in accounts:
            raise AccountDirectoryError(409, "this OMV member already has an Echo login")
        if len(accounts) >= MAX_LOCAL_ACCOUNTS:
            raise AccountDirectoryError(409, "the local family account limit has been reached")
        return {
            "schema": ACCOUNT_LINK_PLAN_SCHEMA,
            "planId": self._plan_id(
                username=username,
                display_name=display_name,
                password=password,
            ),
            "operation": "linkExistingOmvMember",
            "requiresApproval": True,
            "account": {
                "username": username,
                "displayName": display_name,
                "role": "member",
                "omvUsername": username,
            },
            "changes": ["echoLogin", "agentPrincipal", "omvIdentityMapping"],
            "safety": {
                "omvPasswordReused": False,
                "privateDatabaseRead": False,
                "passwordReturned": False,
            },
        }

    def apply_link(self, desired: AccountLinkDesired, *, plan_id: str) -> dict[str, Any]:
        current_plan = self.plan_link(desired)
        if not hmac.compare_digest(str(current_plan["planId"]), plan_id):
            raise AccountDirectoryError(409, "account link plan is stale; preview again")
        username, display_name, password = self._validated_desired(desired)
        password_hash = hash_password(password)

        with self._lock:
            accounts = normalized_accounts(read_auth_store(self._path))
            if username in accounts:
                raise AccountDirectoryError(409, "this OMV member already has an Echo login")
            if len(accounts) >= MAX_LOCAL_ACCOUNTS:
                raise AccountDirectoryError(
                    409,
                    "the local family account limit has been reached",
                )
            account = {
                "display_name": display_name,
                "role": "member",
                "password_hash": password_hash,
                "omv_username": username,
                "active": True,
            }
            self._persist_new_member(username, account)

        return {
            "schema": ACCOUNT_DIRECTORY_SCHEMA,
            "linked": True,
            "account": current_plan["account"],
        }


def create_account_directory_router(
    directory: ApplianceAccountDirectory,
    *,
    authenticator: ApplianceAuthenticator,
    approval: HighRiskApprovalService,
    audit: ApplianceAudit,
) -> APIRouter:
    router = APIRouter(prefix="/api/appliance/accounts", tags=["appliance-accounts"])
    auth = resolve_authenticator(authenticator=authenticator)
    require_auth = auth.dependency()
    require_operator = auth.operator_dependency()

    async def validated_body(request: Request, model: type[BaseModel]) -> BaseModel:
        """Parse credential-bearing JSON without FastAPI echoing invalid input."""

        try:
            raw = await request.json()
            if not isinstance(raw, dict):
                raise ValueError
            return model.model_validate(raw)
        except (ValueError, TypeError, ValidationError):
            raise HTTPException(status_code=422, detail="invalid account request") from None

    def record(
        request: Request,
        *,
        actor: str,
        target: str,
        outcome: str,
        metadata: dict[str, Any],
        action: str = ACCOUNT_LINK_ACTION,
    ) -> None:
        event_metadata = dict(metadata)
        intent_id = request_intent_id(request)
        if intent_id:
            event_metadata["intentId"] = intent_id
        try:
            audit.record(
                actor=actor,
                action=action,
                target=target,
                outcome=outcome,
                metadata=event_metadata,
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    @router.get("")
    async def list_accounts(actor: str = Depends(require_auth)) -> dict[str, Any]:
        try:
            return await run_in_threadpool(directory.list_accounts, actor=actor)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="account directory unavailable") from exc

    @router.post("/link/plan")
    async def plan_link(
        request: Request,
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = await validated_body(request, AccountLinkDesired)
        if not isinstance(desired, AccountLinkDesired):  # pragma: no cover - typing guard
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            return await run_in_threadpool(directory.plan_link, desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/link/apply")
    async def apply_link(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        parsed = await validated_body(request, AccountLinkApply)
        if not isinstance(parsed, AccountLinkApply):  # pragma: no cover - typing guard
            raise HTTPException(status_code=422, detail="invalid account request")
        body = parsed
        try:
            current_plan = await run_in_threadpool(directory.plan_link, body.desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not hmac.compare_digest(str(current_plan["planId"]), body.plan_id):
            raise HTTPException(status_code=409, detail="account link plan is stale; preview again")

        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=ACCOUNT_LINK_ACTION,
            target=body.plan_id,
        )
        account = current_plan["account"]
        metadata = {
            "operation": "linkExistingOmvMember",
            "username": account["username"],
            "omvUsername": account["omvUsername"],
            "role": "member",
            "secretFields": ["password"],
        }
        record(
            request,
            actor=actor,
            target=body.plan_id,
            outcome="attempted",
            metadata=metadata,
        )
        try:
            result = await run_in_threadpool(
                directory.apply_link,
                body.desired,
                plan_id=body.plan_id,
            )
        except AccountDirectoryError as exc:
            record(
                request,
                actor=actor,
                target=body.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        record(
            request,
            actor=actor,
            target=body.plan_id,
            outcome="succeeded",
            metadata=metadata,
        )
        return result

    @router.post("/status/plan")
    async def plan_status(
        request: Request,
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = await validated_body(request, AccountStatusDesired)
        if not isinstance(desired, AccountStatusDesired):  # pragma: no cover
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            return await run_in_threadpool(directory.plan_status, desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/status/apply")
    async def apply_status(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        parsed = await validated_body(request, AccountStatusApply)
        if not isinstance(parsed, AccountStatusApply):  # pragma: no cover
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            current = await run_in_threadpool(directory.plan_status, parsed.desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not hmac.compare_digest(str(current["planId"]), parsed.plan_id):
            raise HTTPException(
                status_code=409, detail="account status plan is stale; preview again"
            )
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=ACCOUNT_STATUS_ACTION,
            target=parsed.plan_id,
        )
        metadata = {
            "operation": "setMemberStatus",
            "username": parsed.desired.username,
            "active": parsed.desired.active,
        }
        record(
            request,
            actor=actor,
            target=parsed.plan_id,
            outcome="attempted",
            metadata=metadata,
            action=ACCOUNT_STATUS_ACTION,
        )
        try:
            result = await run_in_threadpool(
                directory.apply_status,
                parsed.desired,
                plan_id=parsed.plan_id,
            )
        except AccountDirectoryError as exc:
            record(
                request,
                actor=actor,
                target=parsed.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
                action=ACCOUNT_STATUS_ACTION,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        record(
            request,
            actor=actor,
            target=parsed.plan_id,
            outcome="succeeded",
            metadata=metadata,
            action=ACCOUNT_STATUS_ACTION,
        )
        return result

    @router.post("/password/plan")
    async def plan_password(
        request: Request,
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = await validated_body(request, AccountPasswordDesired)
        if not isinstance(desired, AccountPasswordDesired):  # pragma: no cover
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            return await run_in_threadpool(directory.plan_password, desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/password/apply")
    async def apply_password(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        parsed = await validated_body(request, AccountPasswordApply)
        if not isinstance(parsed, AccountPasswordApply):  # pragma: no cover
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            current = await run_in_threadpool(directory.plan_password, parsed.desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not hmac.compare_digest(str(current["planId"]), parsed.plan_id):
            raise HTTPException(
                status_code=409,
                detail="account password plan is stale; preview again",
            )
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=ACCOUNT_PASSWORD_ACTION,
            target=parsed.plan_id,
        )
        metadata = {
            "operation": "resetMemberPassword",
            "username": parsed.desired.username,
            "secretFields": ["newPassword"],
            "omvPasswordChanged": False,
        }
        record(
            request,
            actor=actor,
            target=parsed.plan_id,
            outcome="attempted",
            metadata=metadata,
            action=ACCOUNT_PASSWORD_ACTION,
        )
        try:
            result = await run_in_threadpool(
                directory.apply_password,
                parsed.desired,
                plan_id=parsed.plan_id,
            )
        except AccountDirectoryError as exc:
            record(
                request,
                actor=actor,
                target=parsed.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
                action=ACCOUNT_PASSWORD_ACTION,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        record(
            request,
            actor=actor,
            target=parsed.plan_id,
            outcome="succeeded",
            metadata=metadata,
            action=ACCOUNT_PASSWORD_ACTION,
        )
        return result

    @router.post("/unlink/plan")
    async def plan_unlink(
        request: Request,
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        desired = await validated_body(request, AccountUnlinkDesired)
        if not isinstance(desired, AccountUnlinkDesired):  # pragma: no cover
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            return await run_in_threadpool(directory.plan_unlink, desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/unlink/apply")
    async def apply_unlink(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        parsed = await validated_body(request, AccountUnlinkApply)
        if not isinstance(parsed, AccountUnlinkApply):  # pragma: no cover
            raise HTTPException(status_code=422, detail="invalid account request")
        try:
            current = await run_in_threadpool(directory.plan_unlink, parsed.desired)
        except AccountDirectoryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not hmac.compare_digest(str(current["planId"]), parsed.plan_id):
            raise HTTPException(
                status_code=409, detail="account unlink plan is stale; preview again"
            )
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=ACCOUNT_UNLINK_ACTION,
            target=parsed.plan_id,
        )
        metadata = {
            "operation": "unlinkMember",
            "username": parsed.desired.username,
            "omvUserDeleted": False,
            "nasDataDeleted": False,
        }
        record(
            request,
            actor=actor,
            target=parsed.plan_id,
            outcome="attempted",
            metadata=metadata,
            action=ACCOUNT_UNLINK_ACTION,
        )
        try:
            result = await run_in_threadpool(
                directory.apply_unlink,
                parsed.desired,
                plan_id=parsed.plan_id,
            )
        except AccountDirectoryError as exc:
            record(
                request,
                actor=actor,
                target=parsed.plan_id,
                outcome="failed",
                metadata={**metadata, "errorType": type(exc).__name__},
                action=ACCOUNT_UNLINK_ACTION,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        record(
            request,
            actor=actor,
            target=parsed.plan_id,
            outcome="succeeded",
            metadata=metadata,
            action=ACCOUNT_UNLINK_ACTION,
        )
        return result

    return router


__all__ = [
    "ACCOUNT_DIRECTORY_SCHEMA",
    "ACCOUNT_LINK_ACTION",
    "ACCOUNT_PASSWORD_ACTION",
    "ACCOUNT_STATUS_ACTION",
    "ACCOUNT_UNLINK_ACTION",
    "ACCOUNT_LINK_PLAN_SCHEMA",
    "AccountDirectoryError",
    "AccountLinkDesired",
    "ApplianceAccountDirectory",
    "create_account_directory_router",
]
