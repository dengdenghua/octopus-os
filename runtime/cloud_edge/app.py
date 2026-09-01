"""Lightweight standalone ASGI app for small cloud servers."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI

from .accounts import AccountAuth, AccountStore, create_account_router
from .router import create_cloud_edge_router
from .store import (
    DEFAULT_SHARE_MAX_PER_OWNER,
    DEFAULT_SHARE_MAX_SNAPSHOT_BYTES,
    DEFAULT_SHARE_MAX_TOTAL_BYTES,
    DEFAULT_SHARE_TTL_SECONDS,
)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def create_cloud_edge_app(
    *,
    data_dir: str | Path,
    token_secret: str,
    admin_key: str,
    registration_code: str,
    owner_id: str = "admin",
    tenant_id: str = "default",
    public_share_base_url: str | None = None,
    share_ttl_seconds: int = DEFAULT_SHARE_TTL_SECONDS,
    share_max_per_owner: int = DEFAULT_SHARE_MAX_PER_OWNER,
    share_max_snapshot_bytes: int = DEFAULT_SHARE_MAX_SNAPSHOT_BYTES,
    share_max_total_bytes: int = DEFAULT_SHARE_MAX_TOTAL_BYTES,
    share_relay_key: str | None = None,
) -> FastAPI:
    if len(token_secret) < 32:
        raise RuntimeError("ECHO_CLOUD_EDGE_TOKEN_SECRET must contain at least 32 characters")
    if len(admin_key) < 32:
        raise RuntimeError("ECHO_CLOUD_EDGE_ADMIN_KEY must contain at least 32 characters")
    if token_secret == admin_key:
        raise RuntimeError("device token secret and admin key must be independent")
    clean_relay_key = str(share_relay_key or "").strip()
    if clean_relay_key and len(clean_relay_key) < 32:
        raise RuntimeError("ECHO_CLOUD_SHARE_RELAY_KEY must contain at least 32 characters")
    if clean_relay_key and (
        secrets.compare_digest(clean_relay_key, token_secret)
        or secrets.compare_digest(clean_relay_key, admin_key)
    ):
        raise RuntimeError("share relay key must be independent from other service secrets")
    if len(registration_code) < 12:
        raise RuntimeError("ECHO_CLOUD_REGISTRATION_CODE must contain at least 12 characters")
    clean_owner = owner_id.strip()
    clean_tenant = tenant_id.strip()
    if not clean_owner or not clean_tenant:
        raise RuntimeError("cloud edge owner and tenant must be non-empty")

    root = Path(data_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "cloud_service.sqlite3"
    accounts = AccountStore(db_path)
    auth = AccountAuth(
        store=accounts,
        token_secret=token_secret,
        admin_key=admin_key,
        tenant_id=clean_tenant,
        admin_id=clean_owner,
    )
    app = FastAPI(title="Cloud Account and Message Service", version="1.0")
    app.include_router(
        create_account_router(store=accounts, auth=auth, registration_code=registration_code)
    )
    app.include_router(
        create_cloud_edge_router(
            db_path=db_path,
            token_secret=token_secret,
            require_auth=True,
            principal_resolver=auth.principal,
            operator_resolver=auth.operator,
            public_share_base_url=public_share_base_url,
            share_ttl_seconds=share_ttl_seconds,
            share_max_per_owner=share_max_per_owner,
            share_max_snapshot_bytes=share_max_snapshot_bytes,
            share_max_total_bytes=share_max_total_bytes,
            share_relay_key=clean_relay_key,
            share_relay_tenant_id=clean_tenant,
            share_relay_owner_id=clean_owner,
        )
    )

    @app.get("/livez", include_in_schema=False)
    def livez() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> dict[str, bool]:
        return {"ready": True}

    return app


def create_cloud_edge_app_from_env() -> FastAPI:
    return create_cloud_edge_app(
        data_dir=os.environ.get("ECHO_CLOUD_EDGE_DATA_DIR", "/data"),
        token_secret=os.environ.get("ECHO_CLOUD_EDGE_TOKEN_SECRET", ""),
        admin_key=os.environ.get("ECHO_CLOUD_EDGE_ADMIN_KEY", ""),
        registration_code=os.environ.get("ECHO_CLOUD_REGISTRATION_CODE", ""),
        owner_id=os.environ.get("ECHO_CLOUD_EDGE_OWNER_ID", "admin"),
        tenant_id=os.environ.get("ECHO_CLOUD_EDGE_TENANT_ID", "default"),
        public_share_base_url=os.environ.get("ECHO_PUBLIC_SHARE_BASE_URL"),
        share_ttl_seconds=_env_positive_int(
            "ECHO_CLOUD_EDGE_SHARE_TTL_SECONDS", DEFAULT_SHARE_TTL_SECONDS
        ),
        share_max_per_owner=_env_positive_int(
            "ECHO_CLOUD_EDGE_SHARE_MAX_PER_OWNER", DEFAULT_SHARE_MAX_PER_OWNER
        ),
        share_max_snapshot_bytes=_env_positive_int(
            "ECHO_CLOUD_EDGE_SHARE_MAX_SNAPSHOT_BYTES",
            DEFAULT_SHARE_MAX_SNAPSHOT_BYTES,
        ),
        share_max_total_bytes=_env_positive_int(
            "ECHO_CLOUD_EDGE_SHARE_MAX_TOTAL_BYTES", DEFAULT_SHARE_MAX_TOTAL_BYTES
        ),
        share_relay_key=os.environ.get("ECHO_CLOUD_SHARE_RELAY_KEY"),
    )


__all__ = ["create_cloud_edge_app", "create_cloud_edge_app_from_env"]
