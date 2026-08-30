"""Local creative-workbench integrations used by the Design canvas."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from runtime.platform.plugins.bundled.comfyui_bridge.workflow_diagnostics import (
    diagnose_workflow,
)
from runtime.platform.process.paths import app_paths
from runtime.platform.process.state import SQLiteBackend, StateStore
from runtime.sensing.gateway.comfyui_manager import (
    cancel_manager_job,
    list_managed_models,
    list_model_backups,
    list_node_backups,
    managed_home,
    manager_status,
    remove_managed_model,
    restore_managed_model,
    rollback_managed_node,
    start_manager_job,
    uninstall_managed_node,
)
from runtime.sensing.gateway.comfyui_supervisor import (
    process_status as comfyui_process_status,
)
from runtime.sensing.gateway.comfyui_supervisor import (
    resolve_comfyui_home,
    start_comfyui,
    stop_comfyui,
)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+")
_SAFE_PROJECT_ID = re.compile(r"^[a-zA-Z0-9._-]{1,160}$")
_CANVAS_LOCK = threading.RLock()
_CANVAS_PRESENCE_LOCK = threading.RLock()
_CANVAS_PRESENCE: dict[str, dict[str, dict[str, Any]]] = {}
_CANVAS_PRESENCE_TTL_SECONDS = 8.0
_MAX_CANVAS_BYTES = 2 * 1024 * 1024
_MAX_PROJECT_ASSET_BYTES = 64 * 1024 * 1024
_ASSET_CATEGORIES = frozenset({"角色", "场景", "风格包", "道具", "自定义"})
_SKILL_PREVIEW_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml", ".txt"})
_MAX_PLUGIN_NODE_VALUE_BYTES = 256 * 1024
_MAX_PLUGIN_NODE_BYTES = 2 * 1024 * 1024
_MAX_PLUGIN_NODE_KEYS = 64
_PLUGIN_NODE_STATE_LOCK = threading.RLock()
_SAFE_STATE_COMPONENT = re.compile(r"^[a-zA-Z0-9._:-]{1,160}$")
_COMFY_MODEL_GROUPS = (
    "checkpoints",
    "diffusion_models",
    "loras",
    "vae",
    "controlnet",
    "text_encoders",
    "clip_vision",
    "upscale_models",
)
_COMFY_MODEL_SUFFIXES = frozenset({".safetensors", ".ckpt", ".pt", ".pth", ".bin"})
_COMFY_REGISTRY_URL = "https://api.comfy.org"
_CURATED_COMFY_NODES = (
    "comfyui-impact-pack",
    "comfyui-kjnodes",
    "comfyui_essentials",
    "comfyui-videohelpersuite",
    "comfyui_ipadapter_plus",
    "rgthree-comfy",
    "comfyui_controlnet_aux",
    "comfyui-advanced-controlnet",
    "comfyui-easy-use",
)


class WorkflowImport(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workflow: dict[str, Any]
    ui: dict[str, Any] = Field(default_factory=dict)


class WorkflowSave(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workflow: dict[str, Any]
    ui: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int = Field(default=0, ge=0)


class QueueRequest(BaseModel):
    prompt: dict[str, Any] | None = None
    workflow_id: str | None = Field(default=None, max_length=80)
    client_id: str | None = Field(default=None, max_length=120)


class CanvasSave(BaseModel):
    document: dict[str, Any]
    expected_revision: int = Field(default=0, ge=0)


class CanvasPresenceHeartbeat(BaseModel):
    client_id: str = Field(pattern=r"^[a-zA-Z0-9._:-]{8,128}$")
    display_name: str = Field(default="协作者", min_length=1, max_length=48)
    x: float | None = Field(default=None, ge=-100000, le=100000)
    y: float | None = Field(default=None, ge=-100000, le=100000)
    section: str = Field(default="canvas", pattern=r"^(home|canvas|assets|skills|comfyui)$")


class PluginNodeStatePut(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=160)
    value: Any
    expected_revision: int = Field(default=0, ge=0)


class ComfyCustomNodeAction(BaseModel):
    node_id: str = Field(min_length=2, max_length=120)


class ComfyCustomNodeRollback(BaseModel):
    backup_id: str | None = Field(default=None, max_length=180)


class ComfyModelDownload(BaseModel):
    url: str = Field(min_length=12, max_length=2048)
    group: str = Field(min_length=2, max_length=80)


class ComfyModelAction(BaseModel):
    group: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=500)


class ComfyModelRestore(BaseModel):
    backup_id: str = Field(min_length=3, max_length=600)


def _comfyui_url() -> str:
    raw = os.environ.get("ECHO_COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("ECHO_COMFYUI_URL must point to a local HTTP service")
    return raw


def _comfyui_home() -> Path | None:
    return resolve_comfyui_home()


def _comfyui_dependencies() -> dict[str, Any]:
    """Summarize user-managed ComfyUI assets without modifying the installation."""
    home = _comfyui_home()
    configured = bool(os.environ.get("ECHO_COMFYUI_HOME", "").strip())
    model_counts = {group: 0 for group in _COMFY_MODEL_GROUPS}
    custom_nodes: list[str] = []
    if home is not None:
        models_dir = home / "models"
        for group in _COMFY_MODEL_GROUPS:
            directory = models_dir / group
            if not directory.is_dir():
                continue
            model_counts[group] = sum(
                1
                for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() in _COMFY_MODEL_SUFFIXES
            )
        nodes_dir = home / "custom_nodes"
        if nodes_dir.is_dir():
            custom_nodes = sorted(
                path.name
                for path in nodes_dir.iterdir()
                if path.is_dir() and not path.name.startswith((".", "_"))
            )[:200]
    managed = manager_status()
    return {
        "detected": home is not None,
        "configured": configured,
        "path": str(home) if home is not None else None,
        "model_counts": model_counts,
        "total_models": sum(model_counts.values()),
        "custom_nodes": custom_nodes,
        "total_custom_nodes": len(custom_nodes),
        "managed": bool(managed.get("installed")) and home == managed_home(),
        "manager": managed,
    }


def _workflow_dir() -> Path:
    target = app_paths().data_dir / "design" / "comfyui-workflows"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _bundled_workflow_dir() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "platform"
        / "plugins"
        / "bundled"
        / "comfyui_bridge"
        / "workflows"
    )


def _canvas_dir() -> Path:
    target = app_paths().data_dir / "design" / "project-canvases"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _default_plugin_node_state_store() -> StateStore:
    target = app_paths().data_dir / "design" / "plugin-node-state.sqlite3"
    return StateStore(backend=SQLiteBackend(str(target)))


def _plugin_node_namespace(project_id: str, plugin_id: str, node_id: str) -> str:
    if not _SAFE_PROJECT_ID.fullmatch(project_id):
        raise HTTPException(404, "project not found")
    if not _SAFE_STATE_COMPONENT.fullmatch(plugin_id):
        raise HTTPException(422, "invalid plugin id")
    if not _SAFE_STATE_COMPONENT.fullmatch(node_id):
        raise HTTPException(422, "invalid plugin node id")
    return f"design.plugin-node.{project_id}.{plugin_id}.{node_id}"


def _plugin_node_key(key: str) -> str:
    if not _SAFE_STATE_COMPONENT.fullmatch(key):
        raise HTTPException(422, "invalid plugin state key")
    return key


def _canvas_path(project_id: str) -> Path:
    if not _SAFE_PROJECT_ID.fullmatch(project_id):
        raise HTTPException(404, "project not found")
    return _canvas_dir() / f"{project_id}.json"


def _project_asset_dir(project_id: str) -> Path:
    if not _SAFE_PROJECT_ID.fullmatch(project_id):
        raise HTTPException(404, "project not found")
    target = app_paths().data_dir / "design" / "project-assets" / project_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def _asset_library_dir() -> Path:
    target = app_paths().data_dir / "design" / "asset-library"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _creative_skill_dir(skill_id: str) -> Path:
    if not re.fullmatch(r"creative-[a-z0-9-]{2,120}", skill_id):
        raise HTTPException(404, "skill not found")
    root = Path(__file__).resolve().parents[2] / "execution" / "all_skills"
    target = root / skill_id
    if not target.is_dir() or target.is_symlink() or not (target / "SKILL.md").is_file():
        raise HTTPException(404, "skill not found")
    return target


def _asset_owner_key(request: Request) -> str:
    principal = getattr(request.state, "design_principal", None)
    if principal is None:
        return "local"
    identity = f"{getattr(principal, 'tenant_id', '')}:{getattr(principal, 'actor_id', '')}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _asset_owner_dir(request: Request, persona_id: str | None = None) -> Path:
    target = _asset_library_dir() / _asset_owner_key(request)
    if persona_id:
        normalized = persona_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", normalized):
            raise HTTPException(422, "invalid persona id")
        target = target / "personas" / normalized
    target.mkdir(parents=True, exist_ok=True)
    return target


def _asset_metadata(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_asset_metadata(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _asset_kind(filename: str, content_type: str | None) -> str:
    marker = f"{filename.lower()} {(content_type or '').lower()}"
    if re.search(r"\.(png|jpe?g|webp|gif|svg)$|image/", marker):
        return "image"
    if re.search(r"\.(mp4|mov|webm|mkv)$|video/", marker):
        return "video"
    if re.search(r"\.(mp3|wav|m4a|aac|flac)$|audio/", marker):
        return "audio"
    if re.search(r"\.(csv|xlsx?|ods)$", marker):
        return "table"
    return "file"


def _read_canvas(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "project canvas is unreadable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("document"), dict):
        raise HTTPException(500, "project canvas is invalid")
    return payload


def _presence_now() -> float:
    return time.monotonic()


def _presence_color(identity: str) -> str:
    palette = ("#7c3aed", "#2563eb", "#0891b2", "#059669", "#db2777", "#ea580c")
    return palette[sum(identity.encode("utf-8")) % len(palette)]


def _active_presence(project_id: str, *, now: float) -> list[dict[str, Any]]:
    project = _CANVAS_PRESENCE.get(project_id, {})
    expired = [
        key
        for key, item in project.items()
        if now - float(item.get("seen_at_monotonic") or 0) > _CANVAS_PRESENCE_TTL_SECONDS
    ]
    for key in expired:
        project.pop(key, None)
    if not project:
        _CANVAS_PRESENCE.pop(project_id, None)
        return []
    return [
        {key: value for key, value in item.items() if key != "seen_at_monotonic"}
        for item in sorted(project.values(), key=lambda value: str(value.get("id")))
    ]


def _workflow_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow"), dict):
        return None
    return payload


def create_design_studio_router(
    *,
    project_store: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    plugin_node_state_store: StateStore | None = None,
) -> APIRouter:
    node_state_store = plugin_node_state_store

    def _node_state_store() -> StateStore:
        nonlocal node_state_store
        if node_state_store is None:
            node_state_store = _default_plugin_node_state_store()
        return node_state_store

    def _auth_dep(request: Request) -> None:
        from runtime.safety.auth.principal import resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        request.state.design_principal = principal

    def _scoped_project_store(request: Request) -> Any:
        if project_store is None:
            return None
        principal = getattr(request.state, "design_principal", None)
        if principal is None:
            return project_store
        from runtime.safety.auth.scope import scope_from_principal

        allow_cross_tenant = bool(principal.roles.intersection({"admin", "operator"}))
        with_scope = getattr(project_store, "with_scope", None)
        return (
            with_scope(
                scope_from_principal(
                    principal,
                    allow_cross_tenant=allow_cross_tenant,
                )
            )
            if callable(with_scope)
            else project_store
        )

    def _require_project(request: Request, project_id: str) -> None:
        scoped = _scoped_project_store(request)
        if scoped is None:
            return
        getter = getattr(scoped, "get_project", None)
        if not callable(getter) or getter(project_id) is None:
            raise HTTPException(404, "project not found")

    router = APIRouter(
        prefix="/api/design",
        tags=["design-studio"],
        dependencies=[Depends(_auth_dep)],
    )

    @router.get("/projects/{project_id}/canvas")
    def get_project_canvas(request: Request, project_id: str) -> dict[str, Any]:
        _require_project(request, project_id)
        path = _canvas_path(project_id)
        with _CANVAS_LOCK:
            payload = _read_canvas(path)
        if payload is None:
            return {
                "project_id": project_id,
                "revision": 0,
                "document": None,
                "updated_at": None,
            }
        return {"project_id": project_id, **payload}

    @router.put("/projects/{project_id}/canvas")
    def save_project_canvas(
        request: Request,
        project_id: str,
        body: CanvasSave,
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        encoded_document = json.dumps(body.document, ensure_ascii=False).encode("utf-8")
        if len(encoded_document) > _MAX_CANVAS_BYTES:
            raise HTTPException(413, "project canvas is too large")
        path = _canvas_path(project_id)
        with _CANVAS_LOCK:
            current = _read_canvas(path)
            current_revision = int((current or {}).get("revision") or 0)
            if body.expected_revision != current_revision:
                raise HTTPException(
                    409,
                    {
                        "code": "CANVAS_REVISION_CONFLICT",
                        "revision": current_revision,
                    },
                )
            payload = {
                "revision": current_revision + 1,
                "document": body.document,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        return {"project_id": project_id, **payload}

    @router.get("/projects/{project_id}/presence")
    def get_project_presence(request: Request, project_id: str) -> dict[str, Any]:
        _require_project(request, project_id)
        _canvas_path(project_id)
        with _CANVAS_PRESENCE_LOCK:
            items = _active_presence(project_id, now=_presence_now())
        return {"project_id": project_id, "items": items, "ttl_seconds": 8}

    @router.post("/projects/{project_id}/presence")
    def heartbeat_project_presence(
        request: Request,
        project_id: str,
        body: CanvasPresenceHeartbeat,
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        _canvas_path(project_id)
        principal = getattr(request.state, "design_principal", None)
        actor_id = str(getattr(principal, "actor_id", "") or f"local:{body.client_id}")
        identity = f"{actor_id}:{body.client_id}"
        participant_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        display_name = re.sub(r"[\x00-\x1f\x7f]+", " ", body.display_name).strip()
        now = _presence_now()
        with _CANVAS_PRESENCE_LOCK:
            active = _active_presence(project_id, now=now)
            project = _CANVAS_PRESENCE.setdefault(project_id, {})
            if identity not in project and len(active) >= 64:
                raise HTTPException(429, "project presence is full")
            project[identity] = {
                "id": participant_id,
                "client_id": body.client_id,
                "display_name": display_name or "协作者",
                "x": body.x,
                "y": body.y,
                "section": body.section,
                "color": _presence_color(identity),
                "updated_at": datetime.now(UTC).isoformat(),
                "seen_at_monotonic": now,
            }
            items = _active_presence(project_id, now=now)
        return {
            "project_id": project_id,
            "self_id": participant_id,
            "items": items,
            "ttl_seconds": 8,
        }

    @router.delete("/projects/{project_id}/presence/{client_id}")
    def leave_project_presence(
        request: Request,
        project_id: str,
        client_id: str,
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        _canvas_path(project_id)
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{8,128}", client_id):
            raise HTTPException(422, "invalid presence client id")
        principal = getattr(request.state, "design_principal", None)
        actor_id = str(getattr(principal, "actor_id", "") or f"local:{client_id}")
        identity = f"{actor_id}:{client_id}"
        with _CANVAS_PRESENCE_LOCK:
            project = _CANVAS_PRESENCE.get(project_id, {})
            removed = project.pop(identity, None) is not None
            if not project:
                _CANVAS_PRESENCE.pop(project_id, None)
        return {"project_id": project_id, "left": removed}

    @router.get("/projects/{project_id}/plugin-nodes/{node_id}/state")
    def get_plugin_node_state(
        request: Request,
        project_id: str,
        node_id: str,
        plugin_id: str = Query(min_length=1, max_length=160),
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        namespace = _plugin_node_namespace(project_id, plugin_id, node_id)
        store = _node_state_store()
        with _PLUGIN_NODE_STATE_LOCK:
            keys = store.backend.list_keys(namespace)
            items: dict[str, Any] = {}
            revisions: dict[str, int] = {}
            for key in keys:
                entry = store.get_entry(key, namespace)
                if entry is None:
                    continue
                items[key] = entry.value
                revisions[key] = entry.version
        return {
            "project_id": project_id,
            "plugin_id": plugin_id,
            "node_id": node_id,
            "items": items,
            "revisions": revisions,
            "quota": {
                "max_keys": _MAX_PLUGIN_NODE_KEYS,
                "max_value_bytes": _MAX_PLUGIN_NODE_VALUE_BYTES,
                "max_total_bytes": _MAX_PLUGIN_NODE_BYTES,
            },
        }

    @router.put("/projects/{project_id}/plugin-nodes/{node_id}/state/{key}")
    def put_plugin_node_state(
        request: Request,
        project_id: str,
        node_id: str,
        key: str,
        body: PluginNodeStatePut,
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        namespace = _plugin_node_namespace(project_id, body.plugin_id, node_id)
        safe_key = _plugin_node_key(key)
        store = _node_state_store()
        encoded = json.dumps(body.value, ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > _MAX_PLUGIN_NODE_VALUE_BYTES:
            raise HTTPException(413, "plugin node value is too large")
        with _PLUGIN_NODE_STATE_LOCK:
            keys = store.backend.list_keys(namespace)
            current = store.get_entry(safe_key, namespace)
            current_revision = current.version if current is not None else 0
            if body.expected_revision != current_revision:
                raise HTTPException(
                    409,
                    {
                        "code": "PLUGIN_NODE_STATE_REVISION_CONFLICT",
                        "revision": current_revision,
                    },
                )
            if current is None and len(keys) >= _MAX_PLUGIN_NODE_KEYS:
                raise HTTPException(413, "plugin node key quota exceeded")
            total_bytes = len(encoded)
            for existing_key in keys:
                if existing_key == safe_key:
                    continue
                value = store.get(existing_key, namespace)
                total_bytes += len(
                    json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
                )
            if total_bytes > _MAX_PLUGIN_NODE_BYTES:
                raise HTTPException(413, "plugin node state quota exceeded")
            entry = store.set(
                safe_key,
                body.value,
                namespace,
                metadata={
                    "project_id": project_id,
                    "plugin_id": body.plugin_id,
                    "node_id": node_id,
                },
            )
        return {
            "project_id": project_id,
            "plugin_id": body.plugin_id,
            "node_id": node_id,
            "key": safe_key,
            "value": entry.value,
            "revision": entry.version,
        }

    @router.delete("/projects/{project_id}/plugin-nodes/{node_id}/state/{key}")
    def delete_plugin_node_state(
        request: Request,
        project_id: str,
        node_id: str,
        key: str,
        plugin_id: str = Query(min_length=1, max_length=160),
        expected_revision: int = Query(ge=1),
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        namespace = _plugin_node_namespace(project_id, plugin_id, node_id)
        safe_key = _plugin_node_key(key)
        store = _node_state_store()
        with _PLUGIN_NODE_STATE_LOCK:
            current = store.get_entry(safe_key, namespace)
            current_revision = current.version if current is not None else 0
            if expected_revision != current_revision:
                raise HTTPException(
                    409,
                    {
                        "code": "PLUGIN_NODE_STATE_REVISION_CONFLICT",
                        "revision": current_revision,
                    },
                )
            deleted = store.delete(safe_key, namespace)
        return {"deleted": deleted, "key": safe_key, "revision": current_revision}

    @router.get("/skills/{skill_id}/files")
    def preview_creative_skill_files(skill_id: str) -> dict[str, Any]:
        root = _creative_skill_dir(skill_id)
        items: list[dict[str, str]] = []
        total_bytes = 0
        for path in sorted(root.rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in _SKILL_PREVIEW_SUFFIXES
            ):
                continue
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            size = path.stat().st_size
            if size > 512 * 1024 or total_bytes + size > 2 * 1024 * 1024:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            items.append({"path": relative.as_posix(), "content": content})
            total_bytes += size
            if len(items) >= 80:
                break
        return {"skill_id": skill_id, "items": items, "total": len(items)}

    @router.get("/assets")
    def list_design_assets(
        request: Request,
        persona_id: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for directory in _asset_owner_dir(request, persona_id).iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            metadata = _asset_metadata(directory / "metadata.json")
            if metadata is not None:
                items.append(metadata)
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"items": items, "total": len(items)}

    @router.post("/assets")
    async def create_design_asset(
        request: Request,
        file: UploadFile = File(...),  # noqa: B008
        name: str = Form(..., min_length=1, max_length=120),  # noqa: B008
        category: str = Form("自定义", max_length=20),  # noqa: B008
        description: str = Form("", max_length=1200),  # noqa: B008
        tags: str = Form("", max_length=600),  # noqa: B008
        persona_id: str | None = Form(default=None, max_length=128),  # noqa: B008
    ) -> dict[str, Any]:
        if category not in _ASSET_CATEGORIES:
            raise HTTPException(422, "invalid asset category")
        original = Path(file.filename or "asset").name
        safe_name = _SAFE_NAME.sub("-", original).strip(".-")[:180] or "asset"
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_PROJECT_ASSET_BYTES:
                raise HTTPException(413, "asset is too large")
            chunks.append(chunk)
        asset_id = f"asset-{uuid4().hex[:16]}"
        directory = _asset_owner_dir(request, persona_id) / asset_id
        directory.mkdir(parents=True, exist_ok=False)
        target = directory / safe_name
        try:
            await asyncio.to_thread(target.write_bytes, b"".join(chunks))
            normalized_tags = list(
                dict.fromkeys(tag.strip()[:32] for tag in re.split(r"[,，]", tags) if tag.strip())
            )[:12]
            asset = {
                "id": asset_id,
                "name": name.strip(),
                "category": category,
                "description": description.strip(),
                "tags": normalized_tags,
                "kind": _asset_kind(safe_name, file.content_type),
                "filename": original[:240] or safe_name,
                "size": size,
                "created_at": datetime.now(UTC).isoformat(),
                "url": (
                    f"/api/design/assets/{asset_id}/content"
                    + (f"?persona_id={quote(persona_id)}" if persona_id else "")
                ),
            }
            await asyncio.to_thread(
                _write_asset_metadata,
                directory / "metadata.json",
                asset,
            )
        except Exception:
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return {"ok": True, "item": asset}

    @router.post("/assets/import-pack")
    async def import_design_asset_pack(
        request: Request,
        file: UploadFile = File(...),  # noqa: B008
        persona_id: str | None = Form(default=None, max_length=128),  # noqa: B008
    ) -> dict[str, Any]:
        chunks: list[bytes] = []
        archive_size = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            archive_size += len(chunk)
            if archive_size > _MAX_PROJECT_ASSET_BYTES * 2:
                raise HTTPException(413, "asset pack is too large")
            chunks.append(chunk)
        try:
            archive = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
        except zipfile.BadZipFile as exc:
            raise HTTPException(422, "asset pack must be a valid zip archive") from exc
        with archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= 220:
                raise HTTPException(422, "asset pack entry count must be 1-220")
            total_uncompressed = 0
            safe_files: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                normalized = info.filename.replace("\\", "/").strip("/")
                parts = [part for part in normalized.split("/") if part]
                mode = (info.external_attr >> 16) & 0o170000
                if (
                    not normalized
                    or any(part in {".", ".."} for part in parts)
                    or info.filename.startswith(("/", "\\"))
                    or stat.S_ISLNK(mode)
                    or info.flag_bits & 0x1
                ):
                    raise HTTPException(422, "asset pack contains an unsafe entry")
                if not info.is_dir() and normalized in safe_files:
                    raise HTTPException(422, "asset pack contains duplicate paths")
                total_uncompressed += int(info.file_size)
                if total_uncompressed > _MAX_PROJECT_ASSET_BYTES * 2:
                    raise HTTPException(413, "asset pack expands beyond the size limit")
                if not info.is_dir():
                    safe_files[normalized] = info
            manifest_info = safe_files.get("manifest.json")
            if manifest_info is None or manifest_info.file_size > 1024 * 1024:
                raise HTTPException(422, "asset pack requires manifest.json")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(422, "asset pack manifest is invalid") from exc
            entries = manifest.get("assets") if isinstance(manifest, dict) else None
            if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
                raise HTTPException(422, "asset pack must describe 1-100 assets")
            owner = _asset_owner_dir(request, persona_id)
            staging = owner / f".import-{uuid4().hex}"
            staging.mkdir(parents=False, exist_ok=False)
            created: list[dict[str, Any]] = []
            try:
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        raise HTTPException(422, f"asset {index + 1} metadata is invalid")
                    source_path = str(entry.get("path") or "").replace("\\", "/").strip("/")
                    info = safe_files.get(source_path)
                    category = str(entry.get("category") or "自定义")
                    name = str(entry.get("name") or "").strip()
                    if info is None or source_path == "manifest.json":
                        raise HTTPException(422, f"asset file is missing: {source_path}")
                    if info.file_size > _MAX_PROJECT_ASSET_BYTES:
                        raise HTTPException(413, f"asset is too large: {source_path}")
                    if category not in _ASSET_CATEGORIES or not 1 <= len(name) <= 120:
                        raise HTTPException(422, f"asset metadata is invalid: {source_path}")
                    original = Path(source_path).name
                    safe_name = _SAFE_NAME.sub("-", original).strip(".-")[:180] or "asset"
                    asset_id = f"asset-{uuid4().hex[:16]}"
                    directory = staging / asset_id
                    directory.mkdir()
                    content = archive.read(info)
                    (directory / safe_name).write_bytes(content)
                    raw_tags = entry.get("tags") or []
                    tags = (
                        [str(tag).strip()[:32] for tag in raw_tags]
                        if isinstance(raw_tags, list)
                        else []
                    )
                    asset = {
                        "id": asset_id,
                        "name": name,
                        "category": category,
                        "description": str(entry.get("description") or "").strip()[:1200],
                        "tags": list(dict.fromkeys(tag for tag in tags if tag))[:12],
                        "kind": _asset_kind(safe_name, None),
                        "filename": original[:240] or safe_name,
                        "size": len(content),
                        "created_at": datetime.now(UTC).isoformat(),
                        "source_pack": Path(file.filename or "asset-pack.zip").name[:240],
                        "url": (
                            f"/api/design/assets/{asset_id}/content"
                            + (f"?persona_id={quote(persona_id)}" if persona_id else "")
                        ),
                    }
                    _write_asset_metadata(directory / "metadata.json", asset)
                    created.append(asset)
                committed: list[Path] = []
                try:
                    for asset in created:
                        target = owner / asset["id"]
                        (staging / asset["id"]).replace(target)
                        committed.append(target)
                except Exception:
                    for target in committed:
                        shutil.rmtree(target, ignore_errors=True)
                    raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return {"ok": True, "items": created, "total": len(created)}

    @router.get("/assets/{asset_id}/content")
    def read_design_asset(
        request: Request,
        asset_id: str,
        persona_id: str | None = Query(default=None, max_length=128),
    ) -> FileResponse:
        if not _SAFE_PROJECT_ID.fullmatch(asset_id):
            raise HTTPException(404, "asset not found")
        directory = _asset_owner_dir(request, persona_id) / asset_id
        if not directory.is_dir() or directory.is_symlink():
            raise HTTPException(404, "asset not found")
        metadata = _asset_metadata(directory / "metadata.json")
        if metadata is None:
            raise HTTPException(404, "asset not found")
        safe_name = _SAFE_NAME.sub("-", str(metadata.get("filename") or "asset")).strip(".-")[:180]
        target = directory / safe_name
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404, "asset not found")
        return FileResponse(target)

    @router.post("/projects/{project_id}/assets")
    async def upload_project_assets(
        request: Request,
        project_id: str,
        files: list[UploadFile] = File(...),  # noqa: B008
    ) -> dict[str, Any]:
        _require_project(request, project_id)
        scoped = _scoped_project_store(request)
        if scoped is None or not callable(getattr(scoped, "append_event", None)):
            raise HTTPException(503, "project asset store is unavailable")
        if not files or len(files) > 12:
            raise HTTPException(413, "asset count must be 1-12")
        created: list[dict[str, Any]] = []
        for upload in files:
            original = Path(upload.filename or "asset").name
            safe_name = _SAFE_NAME.sub("-", original).strip(".-")[:180] or "asset"
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_PROJECT_ASSET_BYTES:
                    raise HTTPException(413, f"asset is too large: {safe_name}")
                chunks.append(chunk)
            artifact_id = f"asset-{uuid4().hex[:16]}"
            target_dir = _project_asset_dir(project_id) / artifact_id
            target_dir.mkdir(parents=True, exist_ok=False)
            target = target_dir / safe_name
            await asyncio.to_thread(target.write_bytes, b"".join(chunks))
            kind = _asset_kind(safe_name, upload.content_type)
            artifact = {
                "id": artifact_id,
                "name": original[:240] or safe_name,
                "kind": kind,
                "path": f"design-assets/{project_id}/{artifact_id}/{safe_name}",
                "url": (f"/api/design/projects/{project_id}/assets/{artifact_id}/{safe_name}"),
                "summary": f"本地上传 · {size} bytes",
            }
            try:
                scoped.append_event(
                    project_id,
                    kind="project.artifact_published",
                    payload={"actor": "design", "artifact": artifact},
                )
            except (PermissionError, ValueError) as exc:
                raise HTTPException(404, "project not found") from exc
            created.append(artifact)
        return {"ok": True, "items": created, "total": len(created)}

    @router.get("/projects/{project_id}/assets/{artifact_id}/{filename}")
    def read_project_asset(
        request: Request,
        project_id: str,
        artifact_id: str,
        filename: str,
    ) -> FileResponse:
        _require_project(request, project_id)
        if not _SAFE_PROJECT_ID.fullmatch(artifact_id) or Path(filename).name != filename:
            raise HTTPException(404, "asset not found")
        target = _project_asset_dir(project_id) / artifact_id / filename
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404, "asset not found")
        return FileResponse(target)

    @router.get("/comfyui/status")
    async def comfyui_status() -> dict[str, Any]:
        try:
            base_url = _comfyui_url()
        except RuntimeError as exc:
            return {"online": False, "state": "invalid_config", "detail": str(exc)}
        try:
            async with httpx.AsyncClient(timeout=1.8) as client:
                response = await client.get(f"{base_url}/system_stats")
                response.raise_for_status()
                payload = response.json()
            return {
                "online": True,
                "state": "online",
                "base_url": base_url,
                "system": payload,
                "process": comfyui_process_status(),
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "online": False,
                "state": "offline",
                "base_url": base_url,
                "detail": str(exc),
                "process": comfyui_process_status(),
            }

    @router.get("/comfyui/dependencies")
    def comfyui_dependencies() -> dict[str, Any]:
        return _comfyui_dependencies()

    @router.get("/comfyui/manager")
    def comfyui_manager_state() -> dict[str, Any]:
        return manager_status()

    @router.post("/comfyui/install")
    def comfyui_install() -> dict[str, Any]:
        status = manager_status()
        if status.get("installed"):
            return {"ok": True, "state": "already_installed", "manager": status}
        state = start_manager_job("install")
        return {
            "ok": state in {"started", "already_running"},
            "state": state,
            "manager": manager_status(),
        }

    @router.post("/comfyui/update")
    def comfyui_update() -> dict[str, Any]:
        process = comfyui_process_status()
        if process.get("running"):
            raise HTTPException(409, "stop managed ComfyUI before updating")
        state = start_manager_job("update")
        return {
            "ok": state in {"started", "already_running"},
            "state": state,
            "manager": manager_status(),
        }

    @router.post("/comfyui/manager/cancel")
    def comfyui_manager_cancel() -> dict[str, Any]:
        state = cancel_manager_job()
        return {
            "ok": state in {"cancelled", "not_running"},
            "state": state,
            "manager": manager_status(),
        }

    def _require_stopped_comfyui() -> None:
        if comfyui_process_status().get("running"):
            raise HTTPException(409, "stop managed ComfyUI before changing custom nodes")

    @router.get("/comfyui/custom-nodes/registry")
    async def comfyui_custom_node_registry(
        query: str = Query(default="", max_length=120),
    ) -> dict[str, Any]:
        needle = query.strip().lower()
        ids = [needle] if re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,119}", needle) else []
        if not ids:
            ids = list(_CURATED_COMFY_NODES)
        async with httpx.AsyncClient(timeout=6) as client:
            responses = await asyncio.gather(
                *(client.get(f"{_COMFY_REGISTRY_URL}/nodes/{node_id}") for node_id in ids),
                return_exceptions=True,
            )
        items: list[dict[str, Any]] = []
        for response in responses:
            if isinstance(response, Exception) or response.status_code != 200:
                continue
            try:
                raw = response.json()
            except ValueError:
                continue
            if not isinstance(raw, dict):
                continue
            latest_version = raw.get("latest_version")
            latest: dict[str, Any] = latest_version if isinstance(latest_version, dict) else {}
            publisher_value = raw.get("publisher")
            publisher: dict[str, Any] = publisher_value if isinstance(publisher_value, dict) else {}
            searchable = (
                f"{raw.get('id', '')} {raw.get('name', '')} {raw.get('description', '')}".lower()
            )
            if needle and ids != [needle] and needle not in searchable:
                continue
            items.append(
                {
                    "id": str(raw.get("id") or ""),
                    "name": str(raw.get("name") or raw.get("id") or ""),
                    "description": str(raw.get("description") or ""),
                    "publisher": str(publisher.get("name") or publisher.get("id") or ""),
                    "repository": str(raw.get("repository") or ""),
                    "downloads": int(raw.get("downloads") or 0),
                    "stars": int(raw.get("github_stars") or 0),
                    "version": str(latest.get("version") or ""),
                    "dependencies": list(latest.get("dependencies") or [])[:100],
                    "deprecated": bool(latest.get("deprecated")),
                }
            )
        installed = set(_comfyui_dependencies().get("custom_nodes") or [])
        for item in items:
            item["installed"] = item["id"] in installed
            item["backups"] = list_node_backups(item["id"])
        return {"items": items, "query": needle, "source": "Comfy Registry"}

    @router.post("/comfyui/custom-nodes/install")
    def comfyui_custom_node_install(body: ComfyCustomNodeAction) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = start_manager_job("node_install", body.node_id)
        return {
            "ok": state in {"started", "already_running"},
            "state": state,
            "manager": manager_status(),
        }

    @router.post("/comfyui/custom-nodes/update")
    def comfyui_custom_node_update(body: ComfyCustomNodeAction) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = start_manager_job("node_update", body.node_id)
        return {
            "ok": state in {"started", "already_running"},
            "state": state,
            "manager": manager_status(),
        }

    @router.delete("/comfyui/custom-nodes/{node_id}")
    def comfyui_custom_node_uninstall(node_id: str) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = uninstall_managed_node(node_id)
        return {
            "ok": state == "uninstalled",
            "state": state,
            "backups": list_node_backups(node_id),
        }

    @router.post("/comfyui/custom-nodes/{node_id}/rollback")
    def comfyui_custom_node_rollback(
        node_id: str,
        body: ComfyCustomNodeRollback,
    ) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = rollback_managed_node(node_id, body.backup_id)
        return {
            "ok": state == "restored",
            "state": state,
            "backups": list_node_backups(node_id),
        }

    @router.get("/comfyui/models")
    def comfyui_models() -> dict[str, Any]:
        return {
            "items": list_managed_models(),
            "backups": list_model_backups(),
            "groups": [
                "checkpoints",
                "diffusion_models",
                "loras",
                "vae",
                "controlnet",
                "text_encoders",
                "clip_vision",
                "upscale_models",
            ],
        }

    @router.post("/comfyui/models/download")
    def comfyui_model_download(body: ComfyModelDownload) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = start_manager_job(
            "model_download",
            model_url=body.url,
            model_group=body.group,
        )
        return {
            "ok": state in {"started", "already_running"},
            "state": state,
            "manager": manager_status(),
        }

    @router.post("/comfyui/models/remove")
    def comfyui_model_remove(body: ComfyModelAction) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = remove_managed_model(body.group, body.name)
        return {
            "ok": state == "removed",
            "state": state,
            "backups": list_model_backups(),
        }

    @router.post("/comfyui/models/restore")
    def comfyui_model_restore(body: ComfyModelRestore) -> dict[str, Any]:
        _require_stopped_comfyui()
        state = restore_managed_model(body.backup_id)
        return {
            "ok": state == "restored",
            "state": state,
            "items": list_managed_models(),
        }

    @router.post("/comfyui/start")
    async def comfyui_start() -> dict[str, Any]:
        try:
            base_url = _comfyui_url()
            async with httpx.AsyncClient(timeout=0.7) as client:
                response = await client.get(f"{base_url}/system_stats")
            if response.status_code < 500:
                return {
                    "ok": True,
                    "state": "already_running",
                    "process": comfyui_process_status(),
                }
        except (RuntimeError, httpx.HTTPError):
            pass
        state = start_comfyui()
        return {
            "ok": state in {"started", "already_started"},
            "state": state,
            "process": comfyui_process_status(),
        }

    @router.post("/comfyui/stop")
    def comfyui_stop() -> dict[str, Any]:
        state = stop_comfyui()
        return {
            "ok": state in {"stopped", "already_stopped"},
            "state": state,
            "process": comfyui_process_status(),
        }

    @router.get("/comfyui/object-info")
    async def comfyui_object_info() -> dict[str, Any]:
        """Return a compact, UI-safe view of locally installed ComfyUI nodes."""
        try:
            base_url = _comfyui_url()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(f"{base_url}/object_info")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "local ComfyUI node catalog is unavailable") from exc
        if not isinstance(payload, dict):
            raise HTTPException(502, "local ComfyUI returned an invalid node catalog")
        nodes: list[dict[str, Any]] = []
        for class_type, raw in list(payload.items())[:2000]:
            if not isinstance(class_type, str) or not isinstance(raw, dict):
                continue
            inputs: list[dict[str, Any]] = []
            input_value = raw.get("input")
            input_groups: dict[str, Any] = input_value if isinstance(input_value, dict) else {}
            for group in ("required", "optional"):
                definitions = input_groups.get(group)
                if not isinstance(definitions, dict):
                    continue
                for input_name, spec in list(definitions.items())[:100]:
                    if not isinstance(input_name, str):
                        continue
                    input_type: Any = spec
                    options: dict[str, Any] = {}
                    if isinstance(spec, list) and spec:
                        input_type = spec[0]
                        if len(spec) > 1 and isinstance(spec[1], dict):
                            options = spec[1]
                    inputs.append(
                        {
                            "name": input_name,
                            "type": input_type,
                            "optional": group == "optional",
                            "default": options.get("default"),
                        }
                    )
            nodes.append(
                {
                    "class_type": class_type,
                    "title": raw.get("display_name") or raw.get("name") or class_type,
                    "category": raw.get("category") or "其他",
                    "inputs": inputs,
                }
            )
        nodes.sort(key=lambda item: (str(item["category"]), str(item["title"])))
        return {"online": True, "items": nodes, "total": len(nodes)}

    @router.get("/comfyui/workflows")
    def list_workflows() -> dict[str, Any]:
        indexed: dict[str, dict[str, Any]] = {}
        for source, directory in (
            ("bundled", _bundled_workflow_dir()),
            ("user", _workflow_dir()),
        ):
            for path in sorted(directory.glob("*.json")):
                payload = _workflow_payload(path)
                if payload is None:
                    continue
                indexed[path.stem] = {
                    "id": path.stem,
                    "name": payload.get("name", path.stem),
                    "description": payload.get("description", ""),
                    "tags": payload.get("tags", []),
                    "source": source,
                    "revision": int(payload.get("revision") or 0),
                }
        items = list(indexed.values())
        return {"items": items, "total": len(items)}

    @router.get("/comfyui/workflows/{workflow_id}")
    def get_workflow(workflow_id: str) -> dict[str, Any]:
        safe_id = _SAFE_NAME.sub("-", workflow_id.strip()).strip("-.")[:80]
        if not safe_id or safe_id != workflow_id:
            raise HTTPException(404, "workflow not found")
        for source, directory in (
            ("user", _workflow_dir()),
            ("bundled", _bundled_workflow_dir()),
        ):
            path = directory / f"{safe_id}.json"
            payload = _workflow_payload(path)
            if payload is not None:
                return {"id": safe_id, "source": source, **payload}
        raise HTTPException(404, "workflow not found")

    @router.get("/comfyui/workflows/{workflow_id}/diagnostics")
    async def diagnose_comfyui_workflow(workflow_id: str) -> dict[str, Any]:
        safe_id = _SAFE_NAME.sub("-", workflow_id.strip()).strip("-.")[:80]
        if not safe_id or safe_id != workflow_id:
            raise HTTPException(404, "workflow not found")
        source = ""
        payload: dict[str, Any] | None = None
        for candidate_source, directory in (
            ("user", _workflow_dir()),
            ("bundled", _bundled_workflow_dir()),
        ):
            payload = _workflow_payload(directory / f"{safe_id}.json")
            if payload is not None:
                source = candidate_source
                break
        if payload is None:
            raise HTTPException(404, "workflow not found")
        object_info: dict[str, Any] | None = None
        catalog_error: str | None = None
        try:
            base_url = _comfyui_url()
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(f"{base_url}/object_info")
                response.raise_for_status()
                candidate = response.json()
            if isinstance(candidate, dict):
                object_info = candidate
            else:
                catalog_error = "local ComfyUI returned an invalid node catalog"
        except (RuntimeError, httpx.HTTPError, ValueError) as exc:
            catalog_error = str(exc)
        result = diagnose_workflow(
            payload["workflow"],
            object_info=object_info,
            comfy_home=_comfyui_home(),
        )
        return {
            "workflowId": safe_id,
            "source": source,
            "nodeCatalogError": catalog_error,
            **result,
        }

    @router.post("/comfyui/workflows/import")
    def import_workflow(body: WorkflowImport) -> dict[str, Any]:
        slug = _SAFE_NAME.sub("-", body.name.strip()).strip("-.")[:80]
        if not slug:
            raise HTTPException(400, "workflow name has no usable characters")
        target = _workflow_dir() / f"{slug}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "name": body.name.strip(),
                    "workflow": body.workflow,
                    "ui": body.ui,
                    "revision": 1,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(target)
        return {"ok": True, "id": slug, "name": body.name.strip(), "revision": 1}

    @router.put("/comfyui/workflows/{workflow_id}")
    def save_workflow(workflow_id: str, body: WorkflowSave) -> dict[str, Any]:
        safe_id = _SAFE_NAME.sub("-", workflow_id.strip()).strip("-.")[:80]
        if not safe_id or safe_id != workflow_id:
            raise HTTPException(404, "workflow not found")
        target = _workflow_dir() / f"{safe_id}.json"
        current = _workflow_payload(target)
        current_revision = int((current or {}).get("revision") or 0)
        if body.expected_revision != current_revision:
            raise HTTPException(
                409,
                {
                    "code": "WORKFLOW_REVISION_CONFLICT",
                    "revision": current_revision,
                },
            )
        payload = {
            "name": body.name.strip(),
            "workflow": body.workflow,
            "ui": body.ui,
            "revision": current_revision + 1,
        }
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)
        return {"ok": True, "id": safe_id, **payload}

    @router.post("/comfyui/queue")
    async def queue_workflow(body: QueueRequest) -> dict[str, Any]:
        try:
            base_url = _comfyui_url()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        prompt = body.prompt
        if prompt is None and body.workflow_id:
            safe_id = _SAFE_NAME.sub("-", body.workflow_id.strip()).strip("-.")[:80]
            if not safe_id or safe_id != body.workflow_id:
                raise HTTPException(404, "workflow not found")
            for directory in (_workflow_dir(), _bundled_workflow_dir()):
                workflow_payload = _workflow_payload(directory / f"{safe_id}.json")
                if workflow_payload is not None:
                    prompt = workflow_payload["workflow"]
                    break
        if not prompt:
            raise HTTPException(400, "prompt or workflow_id is required")
        payload: dict[str, Any] = {"prompt": prompt}
        if body.client_id:
            payload["client_id"] = body.client_id
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(f"{base_url}/prompt", json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"ComfyUI is unavailable: {exc}") from exc

    @router.get("/comfyui/history/{prompt_id}")
    async def comfyui_history(prompt_id: str) -> dict[str, Any]:
        if not prompt_id or len(prompt_id) > 160 or "/" in prompt_id:
            raise HTTPException(404, "prompt not found")
        try:
            base_url = _comfyui_url()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{base_url}/history/{prompt_id}")
                response.raise_for_status()
                history = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, f"ComfyUI is unavailable: {exc}") from exc
        record = history.get(prompt_id) if isinstance(history, dict) else None
        if not isinstance(record, dict):
            return {"ok": True, "state": "pending", "prompt_id": prompt_id, "outputs": []}
        outputs: list[dict[str, Any]] = []
        for node_id, node_output in (record.get("outputs") or {}).items():
            if not isinstance(node_output, dict):
                continue
            for media_type, values in node_output.items():
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    query = httpx.QueryParams(
                        {
                            "filename": str(item["filename"]),
                            "subfolder": str(item.get("subfolder") or ""),
                            "type": str(item.get("type") or "output"),
                        }
                    )
                    outputs.append(
                        {
                            "node_id": str(node_id),
                            "kind": media_type,
                            **item,
                            "url": f"{base_url}/view?{query}",
                        }
                    )
        status_value = record.get("status")
        status: dict[str, Any] = status_value if isinstance(status_value, dict) else {}
        completed = bool(status.get("completed", outputs))
        return {
            "ok": True,
            "state": "completed" if completed else "running",
            "prompt_id": prompt_id,
            "outputs": outputs,
            "status": status,
        }

    return router


__all__ = ["create_design_studio_router"]
