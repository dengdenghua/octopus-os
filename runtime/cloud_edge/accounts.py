"""Accounts, sessions and auditable points ledger for the cloud service."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .security import (
    TokenError,
    decode_token,
    encode_token,
    hash_password,
    secret_digest,
    verify_password,
)

ACCOUNT_ISSUER = "echo-cloud-account"
ACCOUNT_AUDIENCE = "echo-cloud-api"


@dataclass(frozen=True)
class AccountPrincipal:
    tenant_id: str
    actor_id: str
    roles: tuple[str, ...] = ()

    @property
    def is_operator(self) -> bool:
        return bool({"admin", "operator"}.intersection(self.roles))


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=200)
    registration_code: str = Field(min_length=8, max_length=256)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class SpendBody(BaseModel):
    amount: int = Field(ge=1, le=1_000_000)
    purpose: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120)


class AdjustBody(BaseModel):
    account_id: str = Field(min_length=8, max_length=80)
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120)


class ProductBody(BaseModel):
    sku: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=100)
    feature: str = Field(min_length=1, max_length=100)
    price_points: int = Field(ge=0, le=1_000_000)
    duration_days: int = Field(ge=1, le=3650)
    active: bool = True


class SubscribeBody(BaseModel):
    sku: str = Field(min_length=2, max_length=60)
    idempotency_key: str = Field(min_length=8, max_length=120)


class AccountStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    UNIQUE(tenant_id, username_key)
                );
                CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_account_sessions_account ON account_sessions(account_id);
                CREATE TABLE IF NOT EXISTS point_balances (
                    tenant_id TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(tenant_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS point_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    amount INTEGER NOT NULL CHECK(amount != 0),
                    balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
                    kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(tenant_id, account_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_point_ledger_account ON point_ledger(tenant_id, account_id, id DESC);
                CREATE TABLE IF NOT EXISTS subscription_products (
                    tenant_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    name TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    price_points INTEGER NOT NULL CHECK(price_points >= 0),
                    duration_days INTEGER NOT NULL CHECK(duration_days > 0),
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(tenant_id, sku)
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    sku TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    price_points INTEGER NOT NULL,
                    starts_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(tenant_id, account_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_subscriptions_account
                    ON subscriptions(tenant_id, account_id, expires_at DESC);
                """
            )

    def register(self, *, tenant_id: str, username: str, password: str) -> dict[str, Any]:
        account_id = "usr_" + uuid.uuid4().hex
        now = int(time.time())
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, '[]', 'active', ?)",
                    (
                        account_id,
                        tenant_id,
                        username.strip(),
                        username.strip().casefold(),
                        hash_password(password),
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO point_balances VALUES (?, ?, 0, ?)", (tenant_id, account_id, now)
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("username already exists") from exc
        return self.account(account_id) or {}

    def account(self, account_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT account_id, tenant_id, username, roles_json, status, created_at FROM accounts WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["roles"] = json.loads(result.pop("roles_json"))
        return result

    def authenticate(
        self, *, tenant_id: str, username: str, password: str
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE tenant_id=? AND username_key=? AND status='active'",
                (tenant_id, username.strip().casefold()),
            ).fetchone()
        if row is None or not verify_password(password, str(row["password_hash"])):
            return None
        return self.account(str(row["account_id"]))

    def create_session(self, account_id: str, *, ttl_seconds: int = 30 * 86400) -> str:
        token = "ref_" + secrets.token_urlsafe(40)
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO account_sessions VALUES (?, ?, ?, ?, NULL)",
                (secret_digest(token), account_id, now + ttl_seconds, now),
            )
        return token

    def consume_session(self, token: str, *, rotate: bool = True) -> dict[str, Any] | None:
        now = int(time.time())
        digest = secret_digest(token)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_sessions WHERE token_hash=?", (digest,)
            ).fetchone()
            if row is None or row["revoked_at"] is not None or int(row["expires_at"]) < now:
                return None
            account_id = str(row["account_id"])
            if rotate:
                conn.execute(
                    "UPDATE account_sessions SET revoked_at=? WHERE token_hash=?", (now, digest)
                )
        return self.account(account_id)

    def revoke_session(self, token: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE account_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (int(time.time()), secret_digest(token)),
            )

    def balance(self, *, tenant_id: str, account_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT balance FROM point_balances WHERE tenant_id=? AND account_id=?",
                (tenant_id, account_id),
            ).fetchone()
        return int(row["balance"]) if row else 0

    def apply_points(
        self,
        *,
        tenant_id: str,
        account_id: str,
        amount: int,
        kind: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if amount == 0:
            raise ValueError("amount must not be zero")
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM point_ledger WHERE tenant_id=? AND account_id=? AND idempotency_key=?",
                (tenant_id, account_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "duplicate": True}
            row = conn.execute(
                "SELECT balance FROM point_balances WHERE tenant_id=? AND account_id=?",
                (tenant_id, account_id),
            ).fetchone()
            if row is None:
                raise ValueError("account balance not found")
            after = int(row["balance"]) + amount
            if after < 0:
                raise ValueError("insufficient points")
            entry_id = "pts_" + uuid.uuid4().hex
            conn.execute(
                "UPDATE point_balances SET balance=?, updated_at=? WHERE tenant_id=? AND account_id=?",
                (after, now, tenant_id, account_id),
            )
            conn.execute(
                """INSERT INTO point_ledger
                (entry_id, tenant_id, account_id, amount, balance_after, kind, reason, idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    tenant_id,
                    account_id,
                    amount,
                    after,
                    kind,
                    reason,
                    idempotency_key,
                    now,
                ),
            )
        return {"entry_id": entry_id, "amount": amount, "balance_after": after, "duplicate": False}

    def ledger(self, *, tenant_id: str, account_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT entry_id, amount, balance_after, kind, reason, created_at FROM point_ledger
                WHERE tenant_id=? AND account_id=? ORDER BY id DESC LIMIT ?""",
                (tenant_id, account_id, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_product(
        self,
        *,
        tenant_id: str,
        sku: str,
        name: str,
        feature: str,
        price_points: int,
        duration_days: int,
        active: bool,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO subscription_products VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, sku) DO UPDATE SET name=excluded.name, feature=excluded.feature,
                price_points=excluded.price_points, duration_days=excluded.duration_days,
                active=excluded.active, updated_at=excluded.updated_at""",
                (
                    tenant_id,
                    sku,
                    name,
                    feature,
                    price_points,
                    duration_days,
                    int(active),
                    int(time.time()),
                ),
            )
        return {
            "sku": sku,
            "name": name,
            "feature": feature,
            "price_points": price_points,
            "duration_days": duration_days,
            "active": active,
        }

    def products(self, *, tenant_id: str, include_inactive: bool = False) -> list[dict[str, Any]]:
        query = """SELECT sku, name, feature, price_points, duration_days, active
            FROM subscription_products WHERE tenant_id=?"""
        params: list[Any] = [tenant_id]
        if not include_inactive:
            query += " AND active=1"
        query += " ORDER BY price_points, sku"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [{**dict(row), "active": bool(row["active"])} for row in rows]

    def subscriptions(self, *, tenant_id: str, account_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT subscription_id, sku, feature, price_points, starts_at, expires_at, created_at
                FROM subscriptions WHERE tenant_id=? AND account_id=? ORDER BY expires_at DESC""",
                (tenant_id, account_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def subscribe(
        self, *, tenant_id: str, account_id: str, sku: str, idempotency_key: str
    ) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM subscriptions WHERE tenant_id=? AND account_id=? AND idempotency_key=?",
                (tenant_id, account_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "duplicate": True}
            product = conn.execute(
                "SELECT * FROM subscription_products WHERE tenant_id=? AND sku=? AND active=1",
                (tenant_id, sku),
            ).fetchone()
            if product is None:
                raise ValueError("subscription product not found")
            balance_row = conn.execute(
                "SELECT balance FROM point_balances WHERE tenant_id=? AND account_id=?",
                (tenant_id, account_id),
            ).fetchone()
            price = int(product["price_points"])
            after = int(balance_row["balance"] if balance_row else 0) - price
            if after < 0:
                raise ValueError("insufficient points")
            current = conn.execute(
                "SELECT expires_at FROM entitlements WHERE tenant_id=? AND owner_id=? AND feature=?",
                (tenant_id, account_id, product["feature"]),
            ).fetchone()
            starts_at = max(now, int(current["expires_at"] or 0) if current else 0)
            expires_at = starts_at + int(product["duration_days"]) * 86400
            subscription_id = "sub_" + uuid.uuid4().hex
            if price:
                conn.execute(
                    "UPDATE point_balances SET balance=?, updated_at=? WHERE tenant_id=? AND account_id=?",
                    (after, now, tenant_id, account_id),
                )
                conn.execute(
                    """INSERT INTO point_ledger
                    (entry_id, tenant_id, account_id, amount, balance_after, kind, reason, idempotency_key, created_at)
                    VALUES (?, ?, ?, ?, ?, 'subscription', ?, ?, ?)""",
                    (
                        "pts_" + uuid.uuid4().hex,
                        tenant_id,
                        account_id,
                        -price,
                        after,
                        f"订阅 {product['name']}",
                        f"subscription:{idempotency_key}",
                        now,
                    ),
                )
            conn.execute(
                """INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    subscription_id,
                    tenant_id,
                    account_id,
                    sku,
                    product["feature"],
                    price,
                    now,
                    expires_at,
                    idempotency_key,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO entitlements VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(tenant_id, owner_id, feature)
                DO UPDATE SET active=1, expires_at=excluded.expires_at""",
                (tenant_id, account_id, product["feature"], expires_at),
            )
        return {
            "subscription_id": subscription_id,
            "sku": sku,
            "feature": str(product["feature"]),
            "price_points": price,
            "starts_at": now,
            "expires_at": expires_at,
            "balance_after": after,
            "duplicate": False,
        }


class AccountAuth:
    def __init__(
        self,
        *,
        store: AccountStore,
        token_secret: str,
        admin_key: str,
        tenant_id: str,
        admin_id: str,
    ):
        self.store = store
        self.token_secret = token_secret
        self.admin_key = admin_key
        self.tenant_id = tenant_id
        self.admin_id = admin_id

    def access_token(self, account: dict[str, Any]) -> str:
        now = int(time.time())
        return encode_token(
            {
                "iss": ACCOUNT_ISSUER,
                "aud": ACCOUNT_AUDIENCE,
                "sub": account["account_id"],
                "tenant_id": account["tenant_id"],
                "roles": account["roles"],
                "token_use": "account",
                "iat": now,
                "exp": now + 900,
            },
            self.token_secret,
        )

    def principal(self, request: Request) -> AccountPrincipal:
        raw = str(request.headers.get("Authorization") or "")
        candidate = (
            raw[7:].strip()
            if raw.lower().startswith("bearer ")
            else str(request.headers.get("X-API-Key") or "")
        )
        if candidate and secrets.compare_digest(candidate, self.admin_key):
            return AccountPrincipal(self.tenant_id, self.admin_id, ("admin", "operator"))
        try:
            claims = decode_token(
                candidate,
                secret=self.token_secret,
                issuer=ACCOUNT_ISSUER,
                audience=ACCOUNT_AUDIENCE,
            )
        except TokenError as exc:
            raise HTTPException(401, "authentication required") from exc
        if claims.get("token_use") != "account":
            raise HTTPException(401, "invalid token use")
        account = self.store.account(str(claims.get("sub") or ""))
        if (
            account is None
            or account["status"] != "active"
            or account["tenant_id"] != claims.get("tenant_id")
        ):
            raise HTTPException(401, "account disabled or unknown")
        return AccountPrincipal(
            account["tenant_id"], account["account_id"], tuple(account["roles"])
        )

    def operator(self, request: Request) -> AccountPrincipal:
        actor = self.principal(request)
        if not actor.is_operator:
            raise HTTPException(403, "operator role required")
        return actor


def create_account_router(
    *,
    store: AccountStore,
    auth: AccountAuth,
    registration_code: str,
    checkin_points: int = 10,
    timezone_name: str = "Asia/Shanghai",
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["accounts"])
    timezone = ZoneInfo(timezone_name)

    def tokens(account: dict[str, Any]) -> dict[str, Any]:
        return {
            "access_token": auth.access_token(account),
            "refresh_token": store.create_session(account["account_id"]),
            "token_type": "Bearer",
            "expires_in": 900,
            "account": account,
        }

    @router.post("/accounts/register", status_code=201)
    def register(body: RegisterBody) -> dict[str, Any]:
        if not secrets.compare_digest(body.registration_code, registration_code):
            raise HTTPException(403, "invalid registration code")
        try:
            return tokens(
                store.register(
                    tenant_id=auth.tenant_id, username=body.username, password=body.password
                )
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/accounts/login")
    def login(body: LoginBody) -> dict[str, Any]:
        account = store.authenticate(
            tenant_id=auth.tenant_id, username=body.username, password=body.password
        )
        if account is None:
            raise HTTPException(401, "invalid username or password")
        return tokens(account)

    @router.post("/accounts/refresh")
    def refresh(body: RefreshBody) -> dict[str, Any]:
        account = store.consume_session(body.refresh_token)
        if account is None:
            raise HTTPException(401, "invalid or expired refresh token")
        return tokens(account)

    @router.post("/accounts/logout")
    def logout(body: RefreshBody) -> dict[str, bool]:
        store.revoke_session(body.refresh_token)
        return {"ok": True}

    @router.get("/account")
    def account(request: Request) -> dict[str, Any]:
        actor = auth.principal(request)
        details = store.account(actor.actor_id)
        return {"account": details or {"account_id": actor.actor_id, "roles": list(actor.roles)}}

    @router.get("/points")
    def points(request: Request) -> dict[str, int]:
        actor = auth.principal(request)
        return {"balance": store.balance(tenant_id=actor.tenant_id, account_id=actor.actor_id)}

    @router.get("/points/ledger")
    def ledger(request: Request, limit: int = 100) -> dict[str, Any]:
        actor = auth.principal(request)
        return {
            "entries": store.ledger(
                tenant_id=actor.tenant_id, account_id=actor.actor_id, limit=limit
            )
        }

    @router.post("/points/check-in")
    def check_in(request: Request) -> dict[str, Any]:
        actor = auth.principal(request)
        day = datetime.now(timezone).date().isoformat()
        return store.apply_points(
            tenant_id=actor.tenant_id,
            account_id=actor.actor_id,
            amount=checkin_points,
            kind="daily_checkin",
            reason=f"每日签到 {day}",
            idempotency_key=f"checkin:{day}",
        )

    @router.post("/points/spend")
    def spend(body: SpendBody, request: Request) -> dict[str, Any]:
        actor = auth.principal(request)
        try:
            return store.apply_points(
                tenant_id=actor.tenant_id,
                account_id=actor.actor_id,
                amount=-body.amount,
                kind="spend",
                reason=body.purpose,
                idempotency_key=f"spend:{body.idempotency_key}",
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/admin/points/adjust")
    def adjust(body: AdjustBody, request: Request) -> dict[str, Any]:
        actor = auth.operator(request)
        target = store.account(body.account_id)
        if target is None or target["tenant_id"] != actor.tenant_id:
            raise HTTPException(404, "account not found")
        try:
            return store.apply_points(
                tenant_id=actor.tenant_id,
                account_id=body.account_id,
                amount=body.amount,
                kind="admin_adjustment",
                reason=body.reason,
                idempotency_key=f"admin:{body.idempotency_key}",
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/subscription-products")
    def products(request: Request) -> dict[str, Any]:
        actor = auth.principal(request)
        return {"products": store.products(tenant_id=actor.tenant_id)}

    @router.put("/admin/subscription-products/{sku}")
    def put_product(sku: str, body: ProductBody, request: Request) -> dict[str, Any]:
        actor = auth.operator(request)
        if sku != body.sku:
            raise HTTPException(400, "path sku and body sku must match")
        return store.upsert_product(tenant_id=actor.tenant_id, **body.model_dump())

    @router.post("/subscriptions/activate")
    def activate(body: SubscribeBody, request: Request) -> dict[str, Any]:
        actor = auth.principal(request)
        try:
            return store.subscribe(
                tenant_id=actor.tenant_id,
                account_id=actor.actor_id,
                sku=body.sku,
                idempotency_key=body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/subscriptions")
    def list_subscriptions(request: Request) -> dict[str, Any]:
        actor = auth.principal(request)
        return {
            "subscriptions": store.subscriptions(
                tenant_id=actor.tenant_id, account_id=actor.actor_id
            )
        }

    return router


__all__ = ["AccountAuth", "AccountPrincipal", "AccountStore", "create_account_router"]
