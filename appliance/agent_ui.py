"""Verified Agent runtime identity exposed to the Echo OS frontend.

Echo OS owns the only browser frontend and its bounded workbench client.  The
Agent bundle contributes the Python runtime, resources and Codex engine; no
second WebUI, iframe or port-3001 surface is mounted here.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter

_log = logging.getLogger("echo.appliance")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Echo Agent bundle metadata is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Echo Agent bundle metadata must be an object: {path}")
    return value


def _verify_partial(
    root: Path,
    *,
    kind: str,
    source: dict[str, Any],
    artifact: dict[str, Any],
) -> None:
    partial = _json_object(root / "agent-build.json")
    if (
        partial.get("schema_version") != 2
        or partial.get("kind") != kind
        or partial.get("source") != source
        or partial.get("artifact") != artifact
    ):
        raise RuntimeError(f"Echo Agent {kind} provenance does not match the image bundle")


def agent_bundle_status() -> dict[str, Any] | None:
    """Verify the image-baked Agent identity and return a public-safe summary.

    Development without a bundle manifest remains supported. Once an image sets
    ``ECHO_AGENT_BUNDLE_MANIFEST``, every mismatch is fatal rather than silently
    starting an appliance whose runtime, resources and Codex came from
    different Agent revisions.
    """
    value = os.environ.get("ECHO_AGENT_BUNDLE_MANIFEST", "").strip()
    if not value:
        return None

    manifest = _json_object(Path(value))
    source = manifest.get("source")
    wheel = manifest.get("wheel")
    resources = manifest.get("resources")
    codex = manifest.get("codex")
    if (
        manifest.get("schema_version") != 2
        or not isinstance(source, dict)
        or not isinstance(wheel, dict)
        or not isinstance(resources, dict)
        or not isinstance(codex, dict)
        or not source.get("source_id")
    ):
        raise RuntimeError("Echo Agent bundle manifest is incomplete or unsupported")

    distribution = str(wheel.get("distribution") or "")
    expected_version = str(wheel.get("version") or "")
    try:
        installed_version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"Echo Agent distribution is not installed: {distribution}") from exc
    if installed_version != expected_version:
        raise RuntimeError(
            f"Echo Agent runtime version {installed_version} does not match bundle "
            f"{expected_version}"
        )

    resources_value = os.environ.get("ECHO_RESOURCES_DIR", "").strip()
    resources_root = Path(resources_value) if resources_value else None
    codex_value = os.environ.get("ECHO_CODEX_BUNDLE_DIR", "").strip()
    codex_root = Path(codex_value) if codex_value else None
    codex_executable_value = os.environ.get("ECHO_CODEX_EXECUTABLE", "").strip()
    if (
        resources_root is None
        or not resources_root.is_dir()
        or codex_root is None
        or not codex_root.is_dir()
    ):
        raise RuntimeError("Echo Agent bundle resources/Codex are not mounted in the image")
    _verify_partial(resources_root, kind="resources", source=source, artifact=resources)
    _verify_partial(codex_root, kind="codex", source=source, artifact=codex)
    codex_manifest = codex_root / "echo-codex-bundle.json"
    codex_executable = codex_root / "bin/codex"
    if (
        not codex_executable_value
        or Path(codex_executable_value) != codex_executable
        or _sha256(codex_manifest) != codex.get("manifest_sha256")
        or _sha256(codex_executable) != codex.get("executable_sha256")
        or codex_executable.is_symlink()
        or not os.access(codex_executable, os.X_OK)
    ):
        raise RuntimeError("Echo Agent Codex engine does not match the image bundle")

    return {
        "verified": True,
        "source_id": source["source_id"],
        "commit": source.get("commit"),
        "dirty": bool(source.get("dirty")),
        "distribution": distribution,
        "version": installed_version,
        "packaged_codex_version": source.get("packaged_codex_version"),
        "codex_target": codex.get("target"),
    }


def agent_ui_base() -> str | None:
    """Retired compatibility field; Echo OS never exposes a second Agent UI."""

    return None


def agent_workspace_url() -> str | None:
    """Retired compatibility field; the workbench is an OS-local route."""

    return None


def storage_url() -> str | None:
    """返回 echo-storage 服务地址(若已配置或 autostart 启用),否则 None。"""
    explicit = os.environ.get("ECHO_STORAGE_URL")
    if explicit:
        return explicit.rstrip("/")
    if os.environ.get("ECHO_STORAGE_AUTOSTART") == "1":
        host = os.environ.get("ECHO_STORAGE_HOST", "127.0.0.1")
        port = int(os.environ.get("ECHO_STORAGE_PORT") or "8767")
        return f"http://{host}:{port}"
    return None


def mount_agent_ui(app: Any) -> bool:
    """Register the runtime/config projection without mounting a second WebUI."""

    bundle = agent_bundle_status()

    # config 端点始终注册(回 null 表示未投喂),前端无脑读即可。
    router = APIRouter()

    @router.get("/api/appliance/config", include_in_schema=False)
    def _appliance_config() -> dict[str, Any]:
        return {
            "agent_workspace_url": agent_workspace_url(),
            "agent_ui_base": agent_ui_base(),
            "storage_url": storage_url(),
            "agent_bundle": bundle,
            "agent_api": getattr(app.state, "echo_agent_api_contract", None),
        }

    app.include_router(router)

    return False
