"""User-triggered managed ComfyUI installation and update jobs.

The manager deliberately installs no checkpoints or other model weights.  It
uses the official ``comfy-cli`` inside an isolated virtual environment and
keeps all process arguments fixed by Echo rather than accepting shell text
from the browser.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from runtime.platform.process.paths import app_paths
from runtime.platform.process.tree import process_group_kwargs, terminate_process_tree

_COMFY_CLI_VERSION = "1.17.0"
_PROCESS: subprocess.Popen[bytes] | None = None
_LOCK = threading.RLock()
_SAFE_NODE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,119}$")
_MODEL_HOSTS = frozenset(
    {"huggingface.co", "www.huggingface.co", "hf.co", "civitai.com", "www.civitai.com"}
)
_MODEL_GROUPS = frozenset(
    {
        "checkpoints",
        "diffusion_models",
        "loras",
        "vae",
        "controlnet",
        "text_encoders",
        "clip_vision",
        "upscale_models",
    }
)
_MODEL_SUFFIXES = frozenset({".safetensors", ".ckpt", ".pt", ".pth", ".bin"})


def managed_root() -> Path:
    return app_paths().data_dir / "design" / "comfyui-managed"


def managed_home() -> Path:
    return managed_root() / "workspace" / "ComfyUI"


def managed_python() -> Path:
    if os.name == "nt":
        return managed_root() / "venv" / "Scripts" / "python.exe"
    return managed_root() / "venv" / "bin" / "python"


def _comfy_executable() -> Path:
    if os.name == "nt":
        return managed_root() / "venv" / "Scripts" / "comfy.exe"
    return managed_root() / "venv" / "bin" / "comfy"


def _state_path() -> Path:
    return managed_root() / "state.json"


def _log_path() -> Path:
    return managed_root() / "install.log"


def _write_state(**values: Any) -> None:
    root = managed_root()
    root.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            current = payload
    current.update(values)
    current["updated_at"] = datetime.now(UTC).isoformat()
    temporary = _state_path().with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(_state_path())


def _read_state() -> dict[str, Any]:
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail_log(limit: int = 60) -> list[str]:
    try:
        lines = _log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def _git_version(home: Path) -> dict[str, str | None]:
    if not (home / ".git").is_dir():
        return {"commit": None, "version": None}
    try:
        commit = subprocess.run(  # noqa: S603 — fixed executable and arguments
            ["git", "-C", str(home), "rev-parse", "--short=12", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        version = subprocess.run(  # noqa: S603 — fixed executable and arguments
            ["git", "-C", str(home), "describe", "--tags", "--always"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return {"commit": commit or None, "version": version or None}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "version": None}


def manager_status() -> dict[str, Any]:
    with _LOCK:
        process = _PROCESS
        running = process is not None and process.poll() is None
        pid = process.pid if running else None
        return_code = None if process is None or running else process.returncode
    state = _read_state()
    if not running and state.get("state") == "running":
        state.update({"state": "failed", "phase": "interrupted"})
    home = managed_home()
    return {
        "managed": True,
        "installed": (home / "main.py").is_file(),
        "home": str(home),
        "job": {
            **state,
            "running": running,
            "pid": pid,
            "return_code": return_code,
        },
        "runtime": {
            "comfy_cli_version": _COMFY_CLI_VERSION,
            **_git_version(home),
        },
        "log_tail": _tail_log(),
    }


def _safe_node_id(node_id: str | None) -> str | None:
    if node_id is None:
        return None
    normalized = node_id.strip().lower()
    return normalized if _SAFE_NODE_ID.fullmatch(normalized) else None


def _node_dir(node_id: str) -> Path:
    return managed_home() / "custom_nodes" / node_id


def _node_backups_dir(node_id: str) -> Path:
    return managed_root() / "node-backups" / node_id


def _safe_model_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _MODEL_HOSTS
        or parsed.username
        or parsed.password
    ):
        return None
    secret_markers = {"token", "api_key", "apikey", "authorization", "auth"}
    if any(key.lower() in secret_markers for key, _value in parse_qsl(parsed.query)):
        return None
    return value.strip()


def _safe_model_group(value: str | None) -> str | None:
    return value if value in _MODEL_GROUPS else None


def start_manager_job(
    action: str,
    node_id: str | None = None,
    *,
    model_url: str | None = None,
    model_group: str | None = None,
) -> str:
    """Start an install/update job without waiting for large dependencies."""
    global _PROCESS
    if action not in {
        "install",
        "update",
        "node_install",
        "node_update",
        "model_download",
    }:
        return "invalid_action"
    safe_node = _safe_node_id(node_id)
    if action.startswith("node_") and safe_node is None:
        return "invalid_node_id"
    safe_model_url = _safe_model_url(model_url)
    safe_model_group = _safe_model_group(model_group)
    if action == "model_download" and (safe_model_url is None or safe_model_group is None):
        return "invalid_model_source"
    if (
        action in {"update", "node_install", "node_update", "model_download"}
        and not (managed_home() / "main.py").is_file()
    ):
        return "not_installed"
    if action == "node_install" and safe_node is not None and _node_dir(safe_node).is_dir():
        return "node_already_installed"
    if action == "node_update" and safe_node is not None and not _node_dir(safe_node).is_dir():
        return "node_not_installed"
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None:
            return "already_running"
        root = managed_root()
        root.mkdir(parents=True, exist_ok=True)
        _write_state(
            state="running",
            phase="queued",
            action=action,
            node_id=safe_node,
            model_source=urlparse(safe_model_url).hostname if safe_model_url else None,
            model_group=safe_model_group,
            error=None,
        )
        try:
            with _log_path().open("ab") as log:
                _PROCESS = subprocess.Popen(  # noqa: S603 — fixed module and action
                    [
                        sys.executable,
                        "-m",
                        "runtime.sensing.gateway.comfyui_manager",
                        "_worker",
                        action,
                        *([safe_node] if safe_node else []),
                        *(
                            [safe_model_url, safe_model_group]
                            if safe_model_url and safe_model_group
                            else []
                        ),
                    ],
                    cwd=str(Path(__file__).resolve().parents[3]),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **process_group_kwargs(),
                )
        except OSError as exc:
            _PROCESS = None
            _write_state(state="failed", phase="launch", error=str(exc))
            return "error"
    atexit.register(cancel_manager_job)
    return "started"


def cancel_manager_job() -> str:
    """Cancel only the install/update process tree started by Echo."""
    global _PROCESS
    with _LOCK:
        process, _PROCESS = _PROCESS, None
    if process is None:
        return "not_running"
    if process.poll() is None:
        with contextlib.suppress(Exception):
            terminate_process_tree(process, grace_s=3, kill_wait_s=2)
    _write_state(state="cancelled", phase="cancelled", error=None)
    return "cancelled"


def _worker_command(
    action: str,
    node_id: str | None = None,
    *,
    model_url: str | None = None,
    model_group: str | None = None,
) -> list[str]:
    workspace = managed_root() / "workspace"
    prefix = [
        str(_comfy_executable()),
        f"--workspace={workspace}",
        "--skip-prompt",
    ]
    if action == "install":
        return [
            *prefix,
            "install",
            "--version",
            "latest",
            "--skip-manager",
            "--cpu",
        ]
    if action == "update":
        return [*prefix, "update", "comfy", "--version", "latest"]
    if action == "model_download":
        safe_url = _safe_model_url(model_url)
        safe_group = _safe_model_group(model_group)
        if safe_url is None or safe_group is None:
            raise ValueError("invalid model source")
        return [
            *prefix,
            "model",
            "download",
            "--url",
            safe_url,
            "--relative-path",
            safe_group,
        ]
    safe_node = _safe_node_id(node_id)
    if safe_node is None:
        raise ValueError("invalid custom node id")
    return [*prefix, "node", "registry-install", safe_node]


def _park_node(node_id: str, reason: str) -> Path:
    source = _node_dir(node_id)
    if not source.is_dir() or source.is_symlink():
        raise FileNotFoundError(f"custom node is not installed: {node_id}")
    target_root = _node_backups_dir(node_id)
    target_root.mkdir(parents=True, exist_ok=True)
    marker = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_root / f"{marker}-{reason}"
    shutil.move(str(source), str(target))
    return target


def list_node_backups(node_id: str) -> list[dict[str, Any]]:
    safe_node = _safe_node_id(node_id)
    if safe_node is None:
        return []
    root = _node_backups_dir(safe_node)
    if not root.is_dir():
        return []
    return [
        {
            "id": path.name,
            "node_id": safe_node,
            "path": str(path),
            "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        }
        for path in sorted(root.iterdir(), reverse=True)
        if path.is_dir() and not path.is_symlink()
    ][:20]


def uninstall_managed_node(node_id: str) -> str:
    safe_node = _safe_node_id(node_id)
    if safe_node is None:
        return "invalid_node_id"
    try:
        _park_node(safe_node, "uninstalled")
    except FileNotFoundError:
        return "node_not_installed"
    return "uninstalled"


def rollback_managed_node(node_id: str, backup_id: str | None = None) -> str:
    safe_node = _safe_node_id(node_id)
    if safe_node is None:
        return "invalid_node_id"
    backups = list_node_backups(safe_node)
    chosen = next(
        (item for item in backups if backup_id is None or item.get("id") == backup_id),
        None,
    )
    if chosen is None:
        return "backup_not_found"
    current = _node_dir(safe_node)
    if current.exists():
        try:
            _park_node(safe_node, "pre-rollback")
        except FileNotFoundError:
            return "unsafe_node_path"
    current.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(chosen["path"]), str(current))
    return "restored"


def list_managed_models() -> list[dict[str, Any]]:
    models_root = managed_home() / "models"
    items: list[dict[str, Any]] = []
    for group in sorted(_MODEL_GROUPS):
        directory = models_root / group
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in _MODEL_SUFFIXES
            ):
                continue
            relative = path.relative_to(directory).as_posix()
            stat = path.stat()
            items.append(
                {
                    "id": f"{group}:{relative}",
                    "group": group,
                    "name": relative,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                }
            )
            if len(items) >= 1000:
                return items
    return items


def _safe_model_file(group: str, name: str) -> Path | None:
    safe_group = _safe_model_group(group)
    if safe_group is None or not name or "\x00" in name:
        return None
    root = (managed_home() / "models" / safe_group).resolve(strict=False)
    target = (root / name).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if target.suffix.lower() not in _MODEL_SUFFIXES:
        return None
    return target


def remove_managed_model(group: str, name: str) -> str:
    source = _safe_model_file(group, name)
    if source is None:
        return "invalid_model_path"
    if not source.is_file() or source.is_symlink():
        return "model_not_found"
    backup_root = managed_root() / "model-backups" / group
    backup_root.mkdir(parents=True, exist_ok=True)
    marker = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_root / f"{marker}-{source.name}"
    shutil.move(str(source), str(target))
    return "removed"


def list_model_backups() -> list[dict[str, Any]]:
    root = managed_root() / "model-backups"
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*"), reverse=True):
        if not path.is_file() or path.is_symlink():
            continue
        items.append(
            {
                "id": f"{path.parent.name}:{path.name}",
                "group": path.parent.name,
                "name": path.name,
                "size_bytes": path.stat().st_size,
            }
        )
    return items[:100]


def restore_managed_model(backup_id: str) -> str:
    if ":" not in backup_id:
        return "invalid_backup_id"
    group, filename = backup_id.split(":", 1)
    if _safe_model_group(group) is None or Path(filename).name != filename:
        return "invalid_backup_id"
    source = managed_root() / "model-backups" / group / filename
    if not source.is_file() or source.is_symlink():
        return "backup_not_found"
    # Backup names are <timestamp>-<original filename>.
    original_name = filename.split("-", 1)[1] if "-" in filename else filename
    target = _safe_model_file(group, original_name)
    if target is None or target.exists():
        return "model_exists"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return "restored"


def _run_worker(
    action: str,
    node_id: str | None = None,
    model_url: str | None = None,
    model_group: str | None = None,
) -> int:
    root = managed_root()
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(root / "venv")
    env["PATH"] = f"{managed_python().parent}{os.pathsep}{env.get('PATH', '')}"
    parked: Path | None = None
    try:
        if not managed_python().is_file():
            _write_state(state="running", phase="creating_runtime", action=action)
            subprocess.run(  # noqa: S603 — fixed Python module invocation
                [sys.executable, "-m", "venv", str(root / "venv")],
                check=True,
                env=env,
            )
        _write_state(state="running", phase="installing_cli", action=action)
        subprocess.run(  # noqa: S603 — fixed package and isolated interpreter
            [
                str(managed_python()),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"comfy-cli=={_COMFY_CLI_VERSION}",
            ],
            check=True,
            env=env,
        )
        if action == "node_update" and node_id is not None:
            _write_state(
                state="running",
                phase="backing_up_node",
                action=action,
                node_id=node_id,
            )
            parked = _park_node(node_id, "pre-update")
        _write_state(state="running", phase=action, action=action, node_id=node_id)
        subprocess.run(  # noqa: S603
            _worker_command(
                action,
                node_id,
                model_url=model_url,
                model_group=model_group,
            ),
            check=True,
            env=env,
        )
        if not (managed_home() / "main.py").is_file():
            raise OSError("comfy-cli finished without creating a ComfyUI installation")
        if action.startswith("node_") and node_id is not None and not _node_dir(node_id).is_dir():
            raise OSError("comfy-cli finished without creating the custom node")
        version = _git_version(managed_home())
        _write_state(
            state="completed",
            phase="ready",
            action=action,
            node_id=node_id,
            error=None,
            **version,
        )
        return 0
    except (OSError, subprocess.SubprocessError) as exc:
        if parked is not None and node_id is not None and not _node_dir(node_id).exists():
            _node_dir(node_id).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(parked), str(_node_dir(node_id)))
        _write_state(
            state="failed",
            phase=action,
            action=action,
            node_id=node_id,
            error=str(exc),
        )
        return 1


def _main() -> int:
    if len(sys.argv) in {3, 4, 5} and sys.argv[1] == "_worker":
        action = sys.argv[2]
        if action == "model_download" and len(sys.argv) == 5:
            return _run_worker(action, model_url=sys.argv[3], model_group=sys.argv[4])
        return _run_worker(action, sys.argv[3] if len(sys.argv) == 4 else None)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "cancel_manager_job",
    "managed_home",
    "managed_python",
    "managed_root",
    "manager_status",
    "list_managed_models",
    "list_model_backups",
    "list_node_backups",
    "remove_managed_model",
    "restore_managed_model",
    "rollback_managed_node",
    "start_manager_job",
    "uninstall_managed_node",
]
