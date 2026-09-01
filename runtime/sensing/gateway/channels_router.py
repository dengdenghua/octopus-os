from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from ._channels_constructors import (
    _construct_channel,
    _credentials_on,
    _load_credentials_and_bootstrap,
    _mask_credentials,
    _save_credentials,
    _UnsupportedPlatformError,
    register_channel_constructor,
)
from ._channels_models import (
    _FALLBACK_ASSIGNMENTS,
    _FALLBACK_META,
    _PLATFORM_META,
    _WECHAT_QR_SESSIONS,
    _guess_platform,
    _is_group_message,
    _normalize_agent_id,
    _normalize_channel_id,
    _normalize_pairing_ref,
    _normalize_platform_id,
    _zero_metrics,
)
from ._channels_persist import (
    _MAX_ASSIGNMENTS,  # noqa: F401 — re-exported for backward compat (tests read them)
    _MAX_PAIRINGS_PER_CHANNEL,  # noqa: F401 — re-exported for backward compat (tests read them)
    _MAX_STATE_FILE_BYTES,  # noqa: F401 — re-exported for backward compat (tests read them)
    _load_state,
    _pairings,
    _sanitize_credentials_body,
    _save_state,
)

logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi  # noqa: E402, I001 — after FASTAPI_AVAILABLE flag

__all__ = [
    "LocalChannelManager",
    "create_channels_router",
    "register_channel_constructor",
]


class LocalChannelManager:
    """Small channel manager for dashboard-only sessions.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ channels_router.py · navigation map (kept below 1000 lines).        ║
    ║                                                                    ║
    ║   §1 LocalChannelManager (in-memory store)        this file         ║
    ║   §2 create_channels_router (factory)            this file          ║
    ║       §2.1 /api/channels (list)                   ~L60              ║
    ║       §2.2 /api/channels/{id}/assistant           ~L120             ║
    ║       §2.3 /api/channels/credentials/{platform}   ~L150             ║
    ║       §2.4 /api/channels/wechat/qr/{start,poll}   ~L230             ║
    ║       §2.5 /api/channels/{id}/pairings + approve  ~L300             ║
    ║       §2.6 /api/channels/{id}/inbound             ~L390             ║
    ║   §3 _channels_models.py   platform meta + validation/utils        ║
    ║   §4 _channels_persist.py  PairingStore + state load/save          ║
    ║   §5 _channels_constructors.py  registry + credentials + crypto    ║
    ║                                                                    ║
    ║ Each platform's _make_* helper is < 15 lines and shares signature; ║
    ║ they live together in _channels_constructors.py because the        ║
    ║ registry resolves them by name.                                    ║
    ╚════════════════════════════════════════════════════════════════════╝
    """

    def __init__(self) -> None:
        self._channels: dict[str, Any] = {}

    def register(self, channel: Any) -> None:
        channel_id = getattr(channel, "channel_id", None)
        if not channel_id:
            raise ValueError(f"channel {type(channel).__name__} missing channel_id")
        if hasattr(channel, "bind_dispatcher"):
            channel.bind_dispatcher(self.process_inbound)
        self._channels[str(channel_id)] = channel

    def has(self, channel_id: str) -> bool:
        return channel_id in self._channels

    def get(self, channel_id: str) -> Any:
        return self._channels[channel_id]

    def channel_ids(self) -> list[str]:
        return sorted(self._channels)

    def process_inbound(self, _msg: Any) -> Any:
        raise RuntimeError("channel runtime is not available in this UI session")


def create_channels_router(
    *,
    manager: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    state_path: str | Path | None = None,
) -> Any:
    require_fastapi(__name__)

    if manager is None:
        manager = LocalChannelManager()

    if state_path is None:
        state_path = os.environ.get("ECHO_CHANNEL_STATE") or "data/channel_state.json"
    _state_file: Path | None = Path(state_path) if state_path else None
    _creds_file: Path | None = (
        _state_file.with_name(
            _state_file.stem + ".credentials" + _state_file.suffix,
        )
        if _state_file is not None
        else None
    )
    _load_state(manager, _state_file)
    _load_credentials_and_bootstrap(manager, _creds_file)

    router = APIRouter(tags=["channels"])

    def _auth(request: Any) -> str | None:
        from .openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _resolve_identity(request: Any) -> Any:
        """Resolve full Identity for role checks. None when auth disabled."""
        if not require_auth or identity_store is None:
            return None
        auth_header = request.headers.get("Authorization") or ""
        if not auth_header.lower().startswith("bearer "):
            return None
        token = auth_header[7:].strip()
        if jwt_secret and token.count(".") == 2:
            with contextlib.suppress(Exception):
                identity = identity_store.verify_jwt(
                    token,
                    secret=jwt_secret,
                    required_issuer=jwt_issuer,
                    required_audience=jwt_audience,
                )
                if identity is not None:
                    return identity
        with contextlib.suppress(Exception):
            return identity_store.verify_api_key(token)
        return None

    def _require_admin(request: Any) -> None:
        """Gate for global channel configuration mutations.

        Channels manage shared IM credentials, agent assignments, and
        pairing approvals — all global per-server state. A regular user
        should not be able to rotate Discord tokens, reassign which
        agent answers a channel, or approve a stranger's pairing.

        No-op when ``require_auth=False`` (single-user dev mode).
        """
        _auth(request)  # AUTH-OK: actor-agnostic — credential check; role check follows
        if not require_auth:
            return
        identity = _resolve_identity(request)
        roles = getattr(identity, "roles", ()) or ()
        if "admin" not in {str(r).lower() for r in roles}:
            raise HTTPException(403, "admin role required for channel configuration")

    #
    #

    @router.get("/api/channels")
    def list_channels(request: Request) -> list[dict[str, Any]]:
        _auth(request)  # AUTH-OK: actor-agnostic — channel registry is server-global
        registered_ids = set(manager.channel_ids())
        seen: set[str] = set()
        assignments = _assignments()
        pairings = _pairings(manager)
        out: list[dict[str, Any]] = []

        for cid in manager.channel_ids():
            platform = _guess_platform(
                cid,
                type(manager.get(cid)).__name__,
            )
            meta = _PLATFORM_META.get(platform, _FALLBACK_META)
            out.append(
                {
                    "channel_id": cid,
                    "type": type(manager.get(cid)).__name__,
                    "platform": platform,
                    "connected": True,
                    "display_name": meta["display_name"],
                    "description": meta["description"],
                    "help_url": meta["help_url"],
                    "metrics": pairings.metrics(cid),
                    "assigned_agent_id": assignments.get(cid),
                }
            )
            seen.add(platform)

        for platform, meta in _PLATFORM_META.items():
            if platform in seen:
                continue
            out.append(
                {
                    "channel_id": platform,  # Implementation note.
                    "type": meta["cls_name"],
                    "platform": platform,
                    "connected": False,
                    "display_name": meta["display_name"],
                    "description": meta["description"],
                    "help_url": meta["help_url"],
                    "metrics": _zero_metrics(),
                    "assigned_agent_id": assignments.get(platform),
                }
            )
            seen.add(platform)

        _ = registered_ids  # Implementation note.
        return out

    def _assignments() -> dict[str, str]:
        a = getattr(manager, "_channel_assignments", None)
        if a is None:
            a = {}
            try:
                manager._channel_assignments = a
            except (AttributeError, TypeError):
                _FALLBACK_ASSIGNMENTS.clear()
                return _FALLBACK_ASSIGNMENTS
        return a

    @router.get("/api/channels/{channel_id}/assistant")
    def get_channel_assignment(
        channel_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — assignments are server-global
        safe_channel_id = _normalize_channel_id(channel_id)
        if safe_channel_id is None:
            raise HTTPException(400, "invalid channel_id")
        return {"channel_id": safe_channel_id, "agent_id": _assignments().get(safe_channel_id)}

    @router.post("/api/channels/{channel_id}/assistant")
    async def set_channel_assignment(
        channel_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_admin(request)  # Mutation: assigns which agent handles this channel (global state)
        try:
            body = await request.json()
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            raise HTTPException(400, f"body: {e}") from e
        agent_id = (body or {}).get("agent_id")
        safe_channel_id = _normalize_channel_id(channel_id)
        safe_agent_id = _normalize_agent_id(agent_id)
        if safe_channel_id is None:
            raise HTTPException(400, "invalid channel_id")
        if safe_agent_id is None:
            raise HTTPException(400, "invalid agent_id")
        _assignments()[safe_channel_id] = safe_agent_id
        _save_state(manager, _state_file)
        return {
            "channel_id": safe_channel_id,
            "agent_id": safe_agent_id,
            "ok": True,
        }

    @router.delete("/api/channels/{channel_id}/assistant")
    def delete_channel_assignment(
        channel_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_admin(request)  # Mutation: removes agent assignment
        safe_channel_id = _normalize_channel_id(channel_id)
        if safe_channel_id is None:
            raise HTTPException(400, "invalid channel_id")
        dropped = _assignments().pop(safe_channel_id, None)
        _save_state(manager, _state_file)
        return {"channel_id": safe_channel_id, "dropped": dropped, "ok": True}

    #
    #
    #

    @router.get("/api/channels/credentials")
    def list_credentials(request: Request) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — credentials list is server-global (masked)
        creds = _credentials_on(manager)
        return {
            "credentials": {platform: _mask_credentials(body) for platform, body in creds.items()},
        }

    @router.post("/api/channels/credentials/{platform}")
    async def set_credentials(
        platform: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_admin(request)  # Mutation: sets sensitive IM API credentials
        try:
            body = await request.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"body: {e}") from e
        if not isinstance(body, dict):
            raise HTTPException(400, "credentials must be a JSON object")

        safe_platform = _normalize_platform_id(platform)
        if safe_platform is None:
            raise HTTPException(
                404,
                f"unknown platform: {platform!r}",
            )

        try:
            clean_body = _sanitize_credentials_body(body)
            channel = _construct_channel(safe_platform, clean_body)
        except _UnsupportedPlatformError as e:
            raise HTTPException(
                400,
                f"platform {safe_platform!r} not yet supported for "
                f"interactive credential setup: {e}",
            ) from e
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(
                400,
                f"invalid credentials: {e}",
            ) from e
        safe_channel_id = _normalize_channel_id(
            getattr(channel, "channel_id", None),
        )
        if safe_channel_id is None:
            raise HTTPException(400, "invalid channel_id")

        if manager.has(safe_channel_id):
            with contextlib.suppress(AttributeError):
                manager._channels.pop(safe_channel_id, None)  # noqa: SLF001
        manager.register(channel)

        _credentials_on(manager)[safe_platform] = clean_body
        _save_credentials(manager, _creds_file)
        return {
            "platform": safe_platform,
            "channel_id": safe_channel_id,
            "connected": True,
            "ok": True,
        }

    @router.delete("/api/channels/credentials/{platform}")
    def delete_credentials(
        platform: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_admin(request)  # Mutation: deletes IM credentials, disconnects channels
        safe_platform = _normalize_platform_id(platform)
        if safe_platform is None:
            raise HTTPException(
                404,
                f"unknown platform: {platform!r}",
            )
        creds = _credentials_on(manager)
        dropped = creds.pop(safe_platform, None)
        try:
            for cid in list(manager.channel_ids()):
                cls_name = type(manager.get(cid)).__name__
                if _guess_platform(cid, cls_name) == safe_platform:
                    manager._channels.pop(cid, None)  # noqa: SLF001
        except (AttributeError, TypeError):
            pass
        _save_credentials(manager, _creds_file)
        return {
            "platform": safe_platform,
            "dropped": dropped is not None,
            "ok": True,
        }

    #
    #

    @router.post("/api/channels/wechat/qr/start")
    def wechat_qr_start(request: Request) -> dict[str, Any]:
        _require_admin(request)  # Mutation: starts WeChat OAuth pairing flow
        try:
            from runtime.adapters.channels.weixin_bot import (
                WeixinBotChannel,
            )
        except ImportError as e:
            raise HTTPException(500, f"wechat adapter unavailable: {e}") from e
        try:
            tmp = WeixinBotChannel()
            out = tmp.request_qr_code()
        except (ConnectionError, TimeoutError, OSError) as e:
            raise HTTPException(
                502,
                f"iLink get_bot_qrcode failed: {e}",
            ) from e
        qrcode = out["qrcode"]
        img_content = out["qrcode_img_content"]
        if img_content.startswith("data:"):
            pass
        elif not img_content.startswith("http"):
            img_content = f"data:image/png;base64,{img_content}"
        _WECHAT_QR_SESSIONS[qrcode] = tmp
        return {
            "qrcode": qrcode,
            "qrcode_img_content": img_content,
        }

    @router.post("/api/channels/wechat/qr/poll")
    async def wechat_qr_poll(request: Request) -> dict[str, Any]:
        _require_admin(request)  # Mutation-adjacent: polls + completes the pairing
        try:
            body = await request.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"body: {e}") from e
        qrcode = (body or {}).get("qrcode")
        if not isinstance(qrcode, str) or not qrcode:
            raise HTTPException(400, "qrcode required")
        tmp = _WECHAT_QR_SESSIONS.get(qrcode)
        if tmp is None:
            raise HTTPException(404, "unknown or expired qrcode session")

        try:
            resp = tmp.poll_qr_status(qrcode)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise HTTPException(
                502,
                f"iLink poll_qr_status failed: {e}",
            ) from e

        status = resp.get("status", "pending")
        confirmed = status == "confirmed"

        if confirmed:
            if not manager.has(tmp.channel_id):
                try:
                    manager.register(tmp)
                except (ConnectionError, TimeoutError, OSError) as e:
                    _WECHAT_QR_SESSIONS.pop(qrcode, None)
                    raise HTTPException(
                        500,
                        f"register wechat channel failed: {e}",
                    ) from e
            tok = resp.get("bot_token")
            if isinstance(tok, str) and tok:
                _credentials_on(manager)["wechat"] = {
                    "bot_token": tok,
                    "baseurl": str(resp.get("baseurl", "")),
                }
                _save_credentials(manager, _creds_file)
            _WECHAT_QR_SESSIONS.pop(qrcode, None)

        return {
            "status": status,
            "confirmed": confirmed,
        }

    @router.get("/api/channels/{channel_id}/pairings")
    def list_pairings(
        channel_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — pairing lists are server-global
        safe_channel_id = _normalize_channel_id(channel_id)
        if safe_channel_id is None:
            raise HTTPException(400, "invalid channel_id")
        p = _pairings(manager)
        return {
            "channel_id": safe_channel_id,
            "users": p.users_list(safe_channel_id),
            "groups": p.groups_list(safe_channel_id),
            "pending": list(p.pending.get(safe_channel_id, [])),
            "metrics": p.metrics(safe_channel_id),
        }

    @router.get("/api/channels/detail")
    def channels_detail(request: Request) -> dict[str, Any]:
        _auth(request)  # AUTH-OK: actor-agnostic — channel details are server-global
        assigns = _assignments()
        p = _pairings(manager)
        seen: set[str] = set()
        channels: list[dict[str, Any]] = []

        for cid in manager.channel_ids():
            platform = _guess_platform(cid, type(manager.get(cid)).__name__)
            m = p.metrics(cid)
            channels.append(
                {
                    "name": platform,
                    "enabled": True,
                    "running": True,
                    "linked": True,
                    "assigned_agent": assigns.get(cid),
                    "stats": {
                        "paired_users": m["pairings_count"],
                        "paired_groups": m["group_count"],
                        "pending_requests": m["pending_count"],
                    },
                }
            )
            seen.add(platform)

        for platform in _PLATFORM_META:
            if platform in seen:
                continue
            channels.append(
                {
                    "name": platform,
                    "enabled": False,
                    "running": False,
                    "linked": False,
                    "assigned_agent": assigns.get(platform),
                    "stats": {
                        "paired_users": 0,
                        "paired_groups": 0,
                        "pending_requests": 0,
                    },
                }
            )
        return {"channels": channels}

    @router.post("/api/channels/pairing/{pairing_id}/approve")
    def approve_pairing(
        pairing_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_admin(request)  # Mutation: authorizes external user to access the bot
        safe_pairing_id = _normalize_pairing_ref(pairing_id)
        if safe_pairing_id is None:
            raise HTTPException(400, "invalid pairing_id")
        for cid, pending_list in _pairings(manager).pending.items():
            for entry in pending_list:
                eid = entry.get("sender_id", "") or str(id(entry))
                if eid == safe_pairing_id:
                    pending_list.remove(entry)
                    _pairings(manager).record(
                        cid,
                        sender_id=entry.get("sender_id"),
                        thread_id=entry.get("thread_id"),
                    )
                    _save_state(manager, _state_file)
                    return {"ok": True, "pairing_id": safe_pairing_id}
        raise HTTPException(404, f"pairing {safe_pairing_id!r} not found")

    @router.post("/api/channels/pairing/{pairing_id}/reject")
    def reject_pairing(
        pairing_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_admin(request)  # Mutation: denies external user access
        safe_pairing_id = _normalize_pairing_ref(pairing_id)
        if safe_pairing_id is None:
            raise HTTPException(400, "invalid pairing_id")
        for _cid, pending_list in _pairings(manager).pending.items():
            for entry in pending_list:
                eid = entry.get("sender_id", "") or str(id(entry))
                if eid == safe_pairing_id:
                    pending_list.remove(entry)
                    _save_state(manager, _state_file)
                    return {"ok": True, "pairing_id": safe_pairing_id}
        raise HTTPException(404, f"pairing {safe_pairing_id!r} not found")

    @router.post("/api/channels/{channel_id}/inbound")
    async def inbound_webhook(
        channel_id: str,
        request: Request,
    ) -> Any:
        # No _auth() gate here: IM platforms (Discord/Slack/WeChat) don't
        # send bearer tokens. Authenticity is verified by the channel's
        # handle_webhook() via platform-specific signature checks
        # (Discord's X-Signature-Ed25519, Slack's X-Slack-Signature, etc).

        safe_channel_id = _normalize_channel_id(channel_id)
        if safe_channel_id is None:
            raise HTTPException(400, "invalid channel_id")
        if not manager.has(safe_channel_id):
            raise HTTPException(404, f"unknown channel: {channel_id}")
        channel = manager.get(safe_channel_id)

        try:
            body = await request.body()
        except (OSError, ValueError) as e:
            raise HTTPException(400, f"body read failed: {e}") from e

        headers = {k.lower(): v for k, v in request.headers.items()}

        try:
            result = channel.handle_webhook(body=body, headers=headers)
        except NotImplementedError as e:
            raise HTTPException(400, str(e)) from e
        except ValueError as e:
            msg = str(e).lower()
            if any(
                k in msg
                for k in (
                    "signature",
                    "timestamp",
                    "too old",
                    "not yet valid",
                )
            ):
                raise HTTPException(401, str(e)) from e
            raise HTTPException(400, str(e)) from e
        except (ConnectionError, TimeoutError, OSError) as e:
            raise HTTPException(500, f"handle_webhook: {e}") from e

        if isinstance(result, dict):
            return result

        if result is None:
            return {"ok": True, "dispatched": False}

        from runtime.adapters.channels import ChannelRoutingError, InboundMessage

        if not isinstance(result, InboundMessage):
            raise HTTPException(
                500,
                f"handle_webhook returned unexpected type: {type(result).__name__}",
            )

        try:
            out = manager.process_inbound(result)
        except ChannelRoutingError as e:
            raise HTTPException(400, str(e)) from e
        except (ConnectionError, TimeoutError, OSError) as e:
            raise HTTPException(500, f"dispatch: {e}") from e

        try:
            _pairings(manager).record(
                safe_channel_id,
                sender_id=getattr(result, "sender_id", None),
                thread_id=getattr(result, "thread_id", None),
                is_group=_is_group_message(result),
            )
            _save_state(manager, _state_file)
        except (AttributeError, TypeError, OSError):  # noqa: BLE001 — state save best-effort; main op already succeeded
            pass

        return {
            "ok": True,
            "dispatched": True,
            "conversation_id": out.metadata.get("conversation_id"),
            "agent_id": out.metadata.get("agent_id"),
        }

    return router
