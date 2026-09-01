"""FastAPI surface for the cloud control plane and signed edge devices."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .security import TokenError, decode_token, encode_token
from .shares import CreateThreadShareBody, ResolveThreadShareBody, normalise_public_snapshot
from .store import (
    DEFAULT_SHARE_MAX_PER_OWNER,
    DEFAULT_SHARE_MAX_SNAPSHOT_BYTES,
    DEFAULT_SHARE_MAX_TOTAL_BYTES,
    DEFAULT_SHARE_TTL_SECONDS,
    CloudEdgeStore,
    ShareLimitError,
)

TOKEN_ISSUER = "echo-cloud-edge"
TOKEN_AUDIENCE = "echo-edge-device"
_PUBLIC_NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
}
_RELAY_OWNER_SCOPE_RE = re.compile(r"^relay_[a-f0-9]{64}$")


class PairingBody(BaseModel):
    device_name: str = Field(default="Echo Desktop", min_length=1, max_length=80)
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


class EnrollBody(BaseModel):
    pairing_code: str = Field(min_length=24, max_length=256)
    public_key: str = Field(min_length=32, max_length=512)
    device_name: str = Field(default="", max_length=80)


class TokenBody(BaseModel):
    device_id: str = Field(min_length=8, max_length=128)
    challenge: str = Field(min_length=24, max_length=256)
    signature: str = Field(min_length=40, max_length=512)


class EntitlementBody(BaseModel):
    feature: str = Field(min_length=1, max_length=100)
    active: bool = True
    expires_at: int | None = None
    owner_id: str | None = Field(default=None, min_length=1, max_length=128)


class EdgeMessage(BaseModel):
    source: str = Field(min_length=1, max_length=40)
    source_room_id: str = Field(min_length=1, max_length=128)
    source_message_id: str = Field(min_length=1, max_length=160)
    title: str = Field(default="", max_length=240)
    content: str = Field(min_length=1, max_length=50_000)
    published_at: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class MessageBatch(BaseModel):
    messages: list[EdgeMessage] = Field(min_length=1, max_length=100)


def _decode_public_key(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "invalid public key") from exc
    if len(raw) != 32:
        raise HTTPException(400, "public key must be Ed25519 raw bytes")
    return raw


def _verify_device_signature(
    public_key: str, *, device_id: str, challenge: str, signature: str
) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        sig = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        message = f"echo-edge-token-v1:{device_id}:{challenge}".encode()
        Ed25519PublicKey.from_public_bytes(_decode_public_key(public_key)).verify(sig, message)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, "invalid device signature") from exc


def _public_share_base_url(value: str | None) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("public_share_base_url must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("public_share_base_url must use HTTPS outside loopback development")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("public_share_base_url cannot contain credentials, query, or fragment")
    return base


def create_cloud_edge_router(
    *,
    db_path: str | Path,
    token_secret: str | None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    principal_resolver: Callable[[Request], Any] | None = None,
    operator_resolver: Callable[[Request], Any] | None = None,
    public_share_base_url: str | None = None,
    share_ttl_seconds: int = DEFAULT_SHARE_TTL_SECONDS,
    share_max_per_owner: int = DEFAULT_SHARE_MAX_PER_OWNER,
    share_max_snapshot_bytes: int = DEFAULT_SHARE_MAX_SNAPSHOT_BYTES,
    share_max_total_bytes: int = DEFAULT_SHARE_MAX_TOTAL_BYTES,
    share_relay_key: str | None = None,
    share_relay_tenant_id: str = "default",
    share_relay_owner_id: str = "admin",
) -> APIRouter:
    """Create the cloud edge router.

    Management APIs use the host account identity. Device APIs live outside
    the legacy control-plane prefix and authenticate with short-lived tokens.
    """

    router = APIRouter(tags=["cloud-edge"])
    store = CloudEdgeStore(
        db_path,
        share_ttl_seconds=share_ttl_seconds,
        share_max_per_owner=share_max_per_owner,
        share_max_snapshot_bytes=share_max_snapshot_bytes,
        share_max_total_bytes=share_max_total_bytes,
    )
    signing_secret = str(token_secret or "").strip()
    public_base_url = _public_share_base_url(public_share_base_url)
    relay_key = str(share_relay_key or "").strip()
    relay_tenant_id = str(share_relay_tenant_id).strip()
    relay_owner_id = str(share_relay_owner_id).strip()
    if relay_key and len(relay_key) < 32:
        raise ValueError("share_relay_key must contain at least 32 characters")
    if relay_key and signing_secret and secrets.compare_digest(relay_key, signing_secret):
        raise ValueError("share_relay_key must be independent from the device token secret")
    if relay_key and (not relay_tenant_id or not relay_owner_id):
        raise ValueError("share relay tenant and owner must be non-empty")

    def enabled() -> None:
        if len(signing_secret) < 32:
            raise HTTPException(503, "cloud edge is disabled: configure a strong token secret")

    def principal(request: Request) -> Any:
        enabled()
        if principal_resolver is not None:
            return principal_resolver(request)
        from runtime.safety.auth.principal import resolve_principal

        resolved = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if resolved is not None:
            return resolved
        if require_auth:
            raise HTTPException(401, "authentication required")
        return type("LocalPrincipal", (), {"tenant_id": "local", "actor_id": "local"})()

    def operator(request: Request) -> Any:
        enabled()
        if operator_resolver is not None:
            return operator_resolver(request)
        from runtime.safety.auth.principal import require_operator

        resolved = require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if resolved is not None:
            return resolved
        if require_auth:
            raise HTTPException(401, "authentication required")
        return type("LocalPrincipal", (), {"tenant_id": "local", "actor_id": "local"})()

    def share_actor(request: Request) -> Any:
        """Accept the narrow relay key only on share-management routes."""

        enabled()
        candidate = str(request.headers.get("X-API-Key") or "")
        if relay_key and candidate and secrets.compare_digest(candidate, relay_key):
            requested_scope = str(request.headers.get("X-Echo-Share-Owner-Scope") or "").strip()
            if requested_scope and not _RELAY_OWNER_SCOPE_RE.fullmatch(requested_scope):
                raise HTTPException(400, "invalid public share owner scope")
            return type(
                "ShareRelayPrincipal",
                (),
                {
                    "tenant_id": relay_tenant_id,
                    "actor_id": requested_scope or relay_owner_id,
                },
            )()
        return principal(request)

    def device_claims(request: Request) -> dict[str, Any]:
        enabled()
        header = str(request.headers.get("Authorization") or "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(401, "missing device bearer token")
        try:
            claims = decode_token(
                header[7:].strip(),
                secret=signing_secret,
                issuer=TOKEN_ISSUER,
                audience=TOKEN_AUDIENCE,
            )
        except TokenError as exc:
            raise HTTPException(401, "invalid or expired device token") from exc
        if claims.get("token_use") != "edge_device":
            raise HTTPException(401, "invalid token use")
        device = store.device(str(claims.get("device_id") or ""))
        if device is None or device.get("revoked_at") is not None:
            raise HTTPException(401, "device revoked or unknown")
        if device["tenant_id"] != claims.get("tenant_id") or device["owner_id"] != claims.get(
            "sub"
        ):
            raise HTTPException(401, "device token binding mismatch")
        return claims

    def create_share(
        body: CreateThreadShareBody,
        *,
        tenant_id: str,
        owner_id: str,
        creator_type: str,
        creator_id: str,
    ) -> dict[str, Any]:
        try:
            snapshot = normalise_public_snapshot(body.snapshot)
            record = store.create_thread_share(
                tenant_id=tenant_id,
                owner_id=owner_id,
                creator_type=creator_type,
                creator_id=creator_id,
                source_thread_id=body.source_thread_id,
                snapshot=snapshot,
                ttl_seconds=body.ttl_seconds,
            )
        except ShareLimitError as exc:
            status_code = 413 if exc.kind == "snapshot" else 507 if exc.kind == "total" else 409
            raise HTTPException(status_code, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        token = str(record.pop("token"))
        share_path = f"#/share/{token}"
        result = {**record, "token": token, "share_path": share_path}
        if public_base_url:
            result["share_url"] = f"{public_base_url}/{share_path}"
        return result

    def list_shares(
        *,
        tenant_id: str,
        owner_id: str,
        source_thread_id: str | None,
        limit: int,
    ) -> dict[str, Any]:
        return {
            "shares": store.list_thread_shares(
                tenant_id=tenant_id,
                owner_id=owner_id,
                source_thread_id=source_thread_id,
                limit=limit,
            )
        }

    def revoke_share(share_id: str, *, tenant_id: str, owner_id: str) -> Response:
        if not store.revoke_thread_share(
            share_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        ):
            raise HTTPException(404, "shared task not found")
        return Response(status_code=204)

    def resolve_share(token: str, response: Response) -> dict[str, Any]:
        enabled()
        record = store.get_public_thread_share(token)
        if record is None:
            raise HTTPException(
                404,
                "shared task not found or expired",
                headers=_PUBLIC_NO_STORE_HEADERS,
            )
        response.headers.update(_PUBLIC_NO_STORE_HEADERS)
        return record

    @router.get("/api/cloud-edge/status")
    def status() -> dict[str, Any]:
        return {"enabled": len(signing_secret) >= 32, "token_ttl_seconds": 900}

    @router.post("/api/cloud-edge/pairing-codes")
    def create_pairing(body: PairingBody, request: Request) -> dict[str, Any]:
        actor = principal(request)
        return store.create_pairing_code(
            tenant_id=actor.tenant_id,
            owner_id=actor.actor_id,
            device_name=body.device_name,
            ttl_seconds=body.ttl_seconds,
        )

    @router.get("/api/cloud-edge/devices")
    def devices(request: Request) -> dict[str, Any]:
        actor = principal(request)
        return {"devices": store.list_devices(tenant_id=actor.tenant_id, owner_id=actor.actor_id)}

    @router.delete("/api/cloud-edge/devices/{device_id}")
    def revoke(device_id: str, request: Request) -> dict[str, Any]:
        actor = principal(request)
        return {
            "ok": store.revoke_device(
                tenant_id=actor.tenant_id, owner_id=actor.actor_id, device_id=device_id
            )
        }

    @router.put("/api/cloud-edge/entitlements")
    def set_entitlement(body: EntitlementBody, request: Request) -> dict[str, Any]:
        actor = operator(request)
        store.set_entitlement(
            tenant_id=actor.tenant_id,
            owner_id=body.owner_id or actor.actor_id,
            feature=body.feature,
            active=body.active,
            expires_at=body.expires_at,
        )
        return {"ok": True}

    @router.get("/api/cloud-edge/entitlements")
    def account_entitlements(request: Request) -> dict[str, Any]:
        actor = principal(request)
        return {
            "features": store.entitlements(
                tenant_id=actor.tenant_id,
                owner_id=actor.actor_id,
            )
        }

    @router.get("/api/cloud-edge/messages")
    def messages(request: Request, limit: int = 100, after_id: int = 0) -> dict[str, Any]:
        actor = principal(request)
        return {
            "messages": store.list_messages(
                tenant_id=actor.tenant_id,
                owner_id=actor.actor_id,
                limit=limit,
                after_id=after_id,
            )
        }

    @router.post("/api/cloud-edge/thread-shares", status_code=201)
    def create_account_thread_share(
        body: CreateThreadShareBody,
        request: Request,
    ) -> dict[str, Any]:
        actor = share_actor(request)
        return create_share(
            body,
            tenant_id=str(actor.tenant_id),
            owner_id=str(actor.actor_id),
            creator_type="account",
            creator_id=str(actor.actor_id),
        )

    @router.get("/api/cloud-edge/thread-shares")
    def list_account_thread_shares(
        request: Request,
        source_thread_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        actor = share_actor(request)
        return list_shares(
            tenant_id=str(actor.tenant_id),
            owner_id=str(actor.actor_id),
            source_thread_id=source_thread_id,
            limit=limit,
        )

    @router.delete(
        "/api/cloud-edge/thread-shares/{share_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def revoke_account_thread_share(
        share_id: str,
        request: Request,
    ) -> Response:
        actor = share_actor(request)
        return revoke_share(
            share_id,
            tenant_id=str(actor.tenant_id),
            owner_id=str(actor.actor_id),
        )

    @router.get("/api/cloud-edge/messages/stream")
    async def message_stream(
        request: Request,
        after_id: int = 0,
    ) -> StreamingResponse:
        actor = principal(request)

        async def events() -> Any:
            cursor = max(0, int(after_id))
            last_heartbeat = time.monotonic()
            while not await request.is_disconnected():
                batch = store.list_messages(
                    tenant_id=actor.tenant_id,
                    owner_id=actor.actor_id,
                    limit=100,
                    after_id=cursor,
                )
                if batch:
                    for item in batch:
                        cursor = max(cursor, int(item["id"]))
                        payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                        yield f"id: {cursor}\nevent: message\ndata: {payload}\n\n"
                    last_heartbeat = time.monotonic()
                    continue
                if time.monotonic() - last_heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(1)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/edge/v1/enroll")
    def enroll(body: EnrollBody) -> dict[str, Any]:
        enabled()
        _decode_public_key(body.public_key)
        device = store.enroll(
            pairing_code=body.pairing_code,
            public_key=body.public_key,
            device_name=body.device_name,
        )
        if device is None:
            raise HTTPException(401, "invalid, expired, or used pairing code")
        return {"ok": True, **device}

    @router.post("/edge/v1/challenge/{device_id}")
    def challenge(device_id: str) -> dict[str, Any]:
        enabled()
        value = store.create_challenge(device_id)
        if value is None:
            raise HTTPException(404, "device not found")
        return {"challenge": value, "expires_in": 120}

    @router.post("/edge/v1/token")
    def token(body: TokenBody) -> dict[str, Any]:
        enabled()
        device = store.device(body.device_id)
        if device is None or device.get("revoked_at") is not None:
            raise HTTPException(401, "device revoked or unknown")
        _verify_device_signature(
            str(device["public_key"]),
            device_id=body.device_id,
            challenge=body.challenge,
            signature=body.signature,
        )
        if not store.consume_challenge(device_id=body.device_id, challenge=body.challenge):
            raise HTTPException(401, "invalid, expired, or used challenge")
        now = int(time.time())
        features = store.entitlements(tenant_id=device["tenant_id"], owner_id=device["owner_id"])
        access_token = encode_token(
            {
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
                "sub": device["owner_id"],
                "tenant_id": device["tenant_id"],
                "device_id": body.device_id,
                "token_use": "edge_device",
                "features": features,
                "iat": now,
                "exp": now + 900,
            },
            signing_secret,
        )
        store.touch_device(body.device_id)
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 900,
            "features": features,
        }

    @router.get("/edge/v1/entitlements")
    def entitlements(  # noqa: B008 - FastAPI dependency declaration
        claims: dict[str, Any] = Depends(device_claims),  # noqa: B008
    ) -> dict[str, Any]:
        features = store.entitlements(
            tenant_id=str(claims["tenant_id"]), owner_id=str(claims["sub"])
        )
        return {"features": features}

    @router.post("/edge/v1/thread-shares", status_code=201)
    def create_device_thread_share(  # noqa: B008 - FastAPI dependency declaration
        body: CreateThreadShareBody,
        claims: dict[str, Any] = Depends(device_claims),  # noqa: B008
    ) -> dict[str, Any]:
        return create_share(
            body,
            tenant_id=str(claims["tenant_id"]),
            owner_id=str(claims["sub"]),
            creator_type="device",
            creator_id=str(claims["device_id"]),
        )

    @router.get("/edge/v1/thread-shares")
    def list_device_thread_shares(  # noqa: B008 - FastAPI dependency declaration
        source_thread_id: str | None = None,
        limit: int = 100,
        claims: dict[str, Any] = Depends(device_claims),  # noqa: B008
    ) -> dict[str, Any]:
        return list_shares(
            tenant_id=str(claims["tenant_id"]),
            owner_id=str(claims["sub"]),
            source_thread_id=source_thread_id,
            limit=limit,
        )

    @router.delete(
        "/edge/v1/thread-shares/{share_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def revoke_device_thread_share(  # noqa: B008 - FastAPI dependency declaration
        share_id: str,
        claims: dict[str, Any] = Depends(device_claims),  # noqa: B008
    ) -> Response:
        return revoke_share(
            share_id,
            tenant_id=str(claims["tenant_id"]),
            owner_id=str(claims["sub"]),
        )

    @router.post("/edge/v1/messages/batch")
    def ingest(  # noqa: B008 - FastAPI dependency declaration
        body: MessageBatch,
        claims: dict[str, Any] = Depends(device_claims),  # noqa: B008
    ) -> dict[str, Any]:
        features = store.entitlements(
            tenant_id=str(claims["tenant_id"]), owner_id=str(claims["sub"])
        )
        if "mx2025.sync" not in features:
            raise HTTPException(403, "mx2025.sync entitlement required")
        result = store.ingest_messages(
            tenant_id=str(claims["tenant_id"]),
            owner_id=str(claims["sub"]),
            device_id=str(claims["device_id"]),
            messages=[item.model_dump() for item in body.messages],
        )
        return {"ok": True, **result}

    @router.post("/api/public/thread-shares/resolve")
    @router.post("/api/v1/public/thread-shares/resolve")
    def resolve_public_thread_share(
        body: ResolveThreadShareBody,
        response: Response,
    ) -> dict[str, Any]:
        """Resolve a capability from the request body so proxies need not log it."""

        return resolve_share(body.token, response)

    @router.get("/api/public/thread-shares/{token}")
    @router.get("/api/v1/public/thread-shares/{token}")
    def get_public_thread_share_compat(token: str, response: Response) -> dict[str, Any]:
        """Compatibility path; new clients should use the body-based resolver."""

        response.headers["Deprecation"] = "true"
        return resolve_share(token, response)

    return router


def default_cloud_edge_secret(jwt_secret: str | None) -> str | None:
    return os.environ.get("ECHO_CLOUD_EDGE_TOKEN_SECRET") or jwt_secret


__all__ = ["create_cloud_edge_router", "default_cloud_edge_secret"]
