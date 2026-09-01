"""A2A (Agent-to-Agent) remote agent registry + relay router.

Connects the frontend ``a2a-agents-panel`` (registered remote agents) to the
A2A protocol via the official ``a2a-sdk``. The UI was authored earlier but its
backend endpoints never existed; this router completes that surface:

  GET    /api/a2a/agents                 list registered remote agents
  POST   /api/a2a/agents/register        register by URL (resolve agent card)
  DELETE /api/a2a/agents/{id}            unregister
  POST   /api/a2a/agents/{id}/health     probe the remote agent card
  POST   /api/a2a/agents/{id}/send       send a task message

Registry entries persist to ``~/.echo/a2a/registry.json`` (atomic writes),
mirroring the publisher-trust / cloud-store storage style.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

_log = logging.getLogger(__name__)

_REGISTRY_DIR = Path.home() / ".echo" / "a2a"
_REGISTRY_FILE = _REGISTRY_DIR / "registry.json"
_lock = threading.RLock()


# ── Registry persistence ─────────────────────────────────────────


def _load_registry() -> dict[str, Any]:
    if not _REGISTRY_FILE.exists():
        return {"agents": []}
    try:
        data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        return (
            data
            if isinstance(data, dict) and isinstance(data.get("agents"), list)
            else {"agents": []}
        )
    except (OSError, json.JSONDecodeError):
        return {"agents": []}


def _save_registry(agents: list[dict[str, Any]]) -> None:
    from runtime.platform.io import atomic_write_json

    _REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_REGISTRY_FILE, {"agents": agents})


def _find_agent(agents: list[dict[str, Any]], agent_id: str) -> dict[str, Any] | None:
    for entry in agents:
        if entry.get("agent_id") == agent_id:
            return entry
    return None


# ── A2A SDK helpers (lazy import — SDK is optional at runtime) ───


async def _resolve_agent_card(url: str) -> dict[str, Any]:
    """Fetch a remote agent's A2A card and normalize it for our wire shape."""
    try:
        from a2a.client import ClientFactory
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(500, "a2a-sdk not installed") from exc

    factory = ClientFactory()
    try:
        client = await factory.create_from_url(url)
    except Exception as exc:  # noqa: BLE001 — surface as a clean 502
        _log.warning("A2A card resolution failed for %s: %s", url, exc)
        raise HTTPException(502, f"failed to resolve agent card: {exc}") from exc

    card = getattr(client, "agent_card", None) or getattr(client, "card", None)
    if card is None:
        raise HTTPException(502, f"no agent card resolved from {url}")

    def _field(obj: Any, name: str, default: Any = None) -> Any:
        if obj is None:
            return default
        # protobuf message: getattr works with default None; pydantic: model_dump path.
        try:
            value = getattr(obj, name, None)
        except (AttributeError, ValueError):
            value = None
        if value is None and hasattr(obj, "model_dump"):
            value = obj.model_dump().get(name)
        return value if value is not None else default

    skills: list[dict[str, Any]] = []
    for skill in _field(card, "skills", []) or []:
        skills.append(
            {
                "id": _field(skill, "id", "") or _field(skill, "name", ""),
                "name": _field(skill, "name", "") or _field(skill, "id", ""),
                "description": _field(skill, "description", ""),
                "tags": list(_field(skill, "tags", []) or []),
            }
        )
    capabilities = _field(card, "capabilities", None)
    return {
        "name": _field(card, "name", ""),
        "description": _field(card, "description", ""),
        "version": _field(card, "version", "1.0.0"),
        "skills": skills,
        "capabilities": {
            "streaming": bool(_field(capabilities, "streaming", False)) if capabilities else False,
            # A2A protobuf uses snake_case (push_notifications); the frontend
            # wire shape uses camelCase (pushNotifications).
            "pushNotifications": (
                bool(
                    _field(capabilities, "push_notifications", False)
                    or _field(capabilities, "pushNotifications", False)
                )
                if capabilities
                else False
            ),
            # multiTurn was dropped from the modern AgentCard; keep the field
            # for the frontend contract, defaulting False.
            "multiTurn": (
                bool(_field(capabilities, "multiTurn", False)) if capabilities else False
            ),
        },
    }


async def _probe_agent(url: str) -> dict[str, Any]:
    """Health probe: try resolving the card; success ⇒ healthy."""
    try:
        await _resolve_agent_card(url)
        return {"healthy": True, "status": "active", "error": None}
    except HTTPException as exc:
        return {"healthy": False, "status": "unreachable", "error": str(exc.detail)}


# ── Router ───────────────────────────────────────────────────────


def create_a2a_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    def _auth_dep(request: Request) -> None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "identity store required for a2a auth")
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(
        prefix="/api/a2a",
        tags=["a2a"],
        dependencies=[Depends(_auth_dep)],
    )

    @router.get("/agents")
    def list_agents() -> dict[str, Any]:
        with _lock:
            registry = _load_registry()
        return {"agents": registry["agents"], "count": len(registry["agents"])}

    @router.post("/agents/register")
    async def register_agent(body: dict[str, Any]) -> dict[str, Any]:
        url = str(body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url is required")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "url must be http(s)")

        card = await _resolve_agent_card(url)
        now = datetime.now(UTC).isoformat()
        with _lock:
            registry = _load_registry()
            # Re-registering the same URL refreshes in place.
            existing = next(
                (e for e in registry["agents"] if e.get("base_url") == url),
                None,
            )
            if existing:
                existing.update(
                    {
                        **card,
                        "status": "active",
                        "updated_at": now,
                        "last_health_check": now,
                    }
                )
                entry = existing
            else:
                entry = {
                    "agent_id": f"a2a_{uuid.uuid4().hex[:12]}",
                    "base_url": url,
                    "status": "active",
                    "registered_at": now,
                    "updated_at": now,
                    "last_health_check": now,
                    **card,
                }
                registry["agents"].append(entry)
            _save_registry(registry["agents"])
        return entry

    @router.delete("/agents/{agent_id}")
    def unregister_agent(agent_id: str) -> dict[str, Any]:
        with _lock:
            registry = _load_registry()
            before = len(registry["agents"])
            registry["agents"] = [e for e in registry["agents"] if e.get("agent_id") != agent_id]
            if len(registry["agents"]) == before:
                raise HTTPException(404, f"agent not found: {agent_id}")
            _save_registry(registry["agents"])
        return {"ok": True, "agent_id": agent_id}

    @router.post("/agents/{agent_id}/health")
    async def health_check(agent_id: str) -> dict[str, Any]:
        with _lock:
            registry = _load_registry()
            entry = _find_agent(registry["agents"], agent_id)
        if entry is None:
            raise HTTPException(404, f"agent not found: {agent_id}")
        result = await _probe_agent(str(entry["base_url"]))
        now = datetime.now(UTC).isoformat()
        with _lock:
            registry = _load_registry()
            current = _find_agent(registry["agents"], agent_id)
            if current is not None:
                current["status"] = "active" if result["healthy"] else "unreachable"
                current["last_health_check"] = now
                current["updated_at"] = now
                _save_registry(registry["agents"])
        return {"healthy": result["healthy"], "status": result["status"], "error": result["error"]}

    @router.post("/agents/{agent_id}/send")
    async def send_task(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        with _lock:
            registry = _load_registry()
            entry = _find_agent(registry["agents"], agent_id)
        if entry is None:
            raise HTTPException(404, f"agent not found: {agent_id}")

        url = str(entry["base_url"])
        try:
            from a2a.client import ClientFactory
            from a2a.types import Message, Part, Role, SendMessageRequest
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(500, "a2a-sdk not installed") from exc

        factory = ClientFactory()
        try:
            client = await factory.create_from_url(url)
            message = Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=[Part(text=text)],
            )
            request = SendMessageRequest(message=message)
            responses = []
            async for response in client.send_message(request):
                responses.append(response)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface remote failure cleanly
            _log.warning("A2A send_task to %s failed: %s", url, exc)
            raise HTTPException(502, f"remote agent call failed: {exc}") from exc

        # Flatten the last response into the frontend TaskResult wire shape.
        last = responses[-1] if responses else None
        if last is None:
            raise HTTPException(502, "remote agent returned no response")

        # a2a-sdk's protobuf bindings conflict on several field names
        # (``Task.messages`` → ``history``, ``TaskStatus.message`` vs the
        # ``Message`` base). MessageToDict sidesteps every attribute-name
        # collision and yields plain JSON we can re-shape safely.
        from google.protobuf.json_format import MessageToDict

        if hasattr(last, "DESCRIPTOR"):  # protobuf message
            response_dict: dict[str, Any] = MessageToDict(
                last,
                preserving_proto_field_name=True,
            )
        else:  # plain object / SDK aggregate — fall back to attribute reads
            task_obj = getattr(last, "task", None) or getattr(last, "result", None)
            status_obj = getattr(task_obj, "status", None) if task_obj is not None else None

            def _status_dict(obj: Any) -> dict[str, Any]:
                if obj is None:
                    return {}
                return {
                    "state": getattr(obj, "state", None),
                    "message": getattr(obj, "message", None),
                }

            def _msg_dict(obj: Any) -> dict[str, Any]:
                parts = [
                    {"type": "text", "text": str(getattr(p, "text", ""))}
                    for p in (getattr(obj, "parts", None) or [])
                    if getattr(p, "text", None) is not None
                ]
                return {"role": str(getattr(obj, "role", "")), "parts": parts}

            def _artifact_dict(obj: Any) -> dict[str, Any]:
                parts = [
                    {"type": "text", "text": str(getattr(p, "text", ""))}
                    for p in (getattr(obj, "parts", None) or [])
                    if getattr(p, "text", None) is not None
                ]
                return {"name": str(getattr(obj, "name", "") or ""), "parts": parts}

            response_dict = {
                "task": {
                    "id": getattr(task_obj, "id", None) if task_obj is not None else None,
                    "status": _status_dict(status_obj),
                    "history": [_msg_dict(m) for m in (getattr(task_obj, "history", None) or [])],
                    "artifacts": [
                        _artifact_dict(a) for a in (getattr(task_obj, "artifacts", None) or [])
                    ],
                }
            }
        task_dict: dict[str, Any] = response_dict.get("task") or {}
        # StreamResponse carries updates out-of-band (status_update /
        # artifact_update / message); prefer the aggregated task when present.
        status_dict: dict[str, Any] = task_dict.get("status") or {}
        messages: list[dict[str, Any]] = []
        for msg in task_dict.get("history", []) or []:
            parts = []
            for part in msg.get("parts", []) or []:
                text_val = part.get("text")
                if text_val is not None:
                    parts.append({"type": "text", "text": str(text_val)})
            messages.append({"role": str(msg.get("role", "")), "parts": parts})
        artifacts: list[dict[str, Any]] = []
        for artifact in task_dict.get("artifacts", []) or []:
            parts = [
                {"type": "text", "text": str(p.get("text", ""))}
                for p in artifact.get("parts", []) or []
                if p.get("text") is not None
            ]
            artifacts.append({"name": str(artifact.get("name", "") or ""), "parts": parts})
        return {
            "id": str(task_dict.get("id") or "") or str(uuid.uuid4()),
            "status": {
                "state": str(status_dict.get("state", "") or ""),
                "message": str(status_dict.get("message", "") or ""),
            },
            "messages": messages,
            "artifacts": artifacts,
        }

    return router


__all__ = ["create_a2a_router"]
