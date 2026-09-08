"""Local image model configuration and non-loading runtime observations."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import os
import threading
from pathlib import Path
from typing import Any

_STATES: dict[str, dict[str, str]] = {}
_STATE_LOCK = threading.Lock()


def model_index_identity(model: Any, *, kind: str = "vision") -> str | None:
    """Identify the loaded encoder, preprocessing and dependency versions.

    Known providers expose the assets they loaded. Hash them once per model
    instance; file mtimes, a model nickname or vector dimensions are not a
    sufficient cache identity. Unknown providers can still index, but must
    recompute. Capture this immediately after loading, while the session and
    its on-disk assets agree; subsequent builds use the loaded session identity.
    """
    if model is None:
        return None
    cache_key = "_echo_index_identity_" + kind
    if hasattr(model, cache_key):
        return getattr(model, cache_key)
    identity = None
    try:
        module = type(model).__module__
        assets: list[tuple[str, Path]] = []
        settings: dict[str, Any] = {"pipeline": "decoded-rgb-index-v2", "kind": kind}
        if kind == "vision" and module == "fastembed.image.image_embedding":
            encoder = model.model
            if type(encoder).__module__ != "fastembed.image.onnx_embedding":
                return None
            directory = Path(encoder._model_dir)
            required = [
                directory / encoder.model_description.model_file,
                directory / "preprocessor_config.json",
            ]
            if not all(path.is_file() for path in required):
                return None
            # Include external ONNX data and preprocessing assets too. HF's
            # transfer metadata is not an encoder input and varies per host.
            assets = sorted(
                (path.relative_to(directory).as_posix(), path)
                for path in directory.rglob("*")
                if path.is_file() and ".cache" not in path.relative_to(directory).parts
            )
            settings["model"] = encoder.model_name
            settings["providers"] = encoder.model.get_providers()
            packages = ("fastembed", "onnxruntime", "numpy", "pillow")
        elif kind == "faces" and module == "insightface.app.face_analysis":
            for task, encoder in sorted(model.models.items()):
                assets.append((str(task), Path(encoder.model_file)))
            settings["detection_size"] = list(model.det_size)
            settings["detection_threshold"] = model.det_thresh
            settings["providers"] = model.det_model.session.get_providers()
            packages = ("insightface", "onnxruntime", "numpy", "pillow")
        else:
            return None
        if not assets or len(assets) > 128:
            return None
        settings["versions"] = {name: importlib.metadata.version(name) for name in packages}
        digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
        for name, path in assets:
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
            digest.update(json.dumps([name, content.hexdigest()]).encode())
        identity = "encoder-v1:" + digest.hexdigest()
    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
        importlib.metadata.PackageNotFoundError,
    ):
        pass
    # An unreadable identity must never prevent an otherwise valid full build.
    with contextlib.suppress(AttributeError, TypeError):
        setattr(model, cache_key, identity)
    return identity


class ImageModelConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def clip_options(*, providers: list[str], quantization: str | None) -> dict[str, Any]:
    """Use a durable cache and only settings supported by the locked CLIP models.

    FastEmbed 0.8's CLIP wrappers accept arbitrary kwargs but do not implement
    a quantization option. Silently forwarding int8/uint8 would still load the
    default full-precision model. An explicit unsupported request must fail.
    """
    if quantization in {"int8", "uint8"}:
        raise ImageModelConfigurationError("unsupported_quantization")
    if quantization not in {None, "float32"}:
        raise ImageModelConfigurationError("invalid_model_configuration")
    from runtime.platform.process.paths import app_paths

    configured_cache = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    cache = (
        Path(configured_cache).expanduser()
        if configured_cache
        else app_paths().data_dir / "models" / "images"
    )
    from runtime.safety.privacy import privacy_enabled

    offline = privacy_enabled() or os.environ.get("HF_HUB_OFFLINE", "").strip().upper() in {"1", "TRUE", "YES", "ON"}
    return {"providers": providers, "cache_dir": str(cache), "local_files_only": offline}


def mark_loading(model: str) -> None:
    with _STATE_LOCK:
        _STATES[model] = {"state": "loading"}


def mark_loaded(model: str) -> None:
    with _STATE_LOCK:
        _STATES[model] = {"state": "loaded"}


def mark_failed(model: str, error: Exception) -> None:
    # Exception strings can contain URLs, access tokens, or private paths.
    # Only publish a bounded code; no raw message or traceback crosses the API.
    code = (
        error.code
        if isinstance(error, ImageModelConfigurationError)
        else "runtime_import_failed"
        if isinstance(error, ImportError)
        else "model_load_failed"
    )
    with _STATE_LOCK:
        _STATES[model] = {"state": "load-failed", "code": code}


def model_states(loaded: dict[str, bool]) -> dict[str, dict[str, str]]:
    """Read in-memory observations without importing packages or touching disk."""
    with _STATE_LOCK:
        return {
            model: {"state": "loaded"}
            if present
            else dict(_STATES.get(model, {"state": "unloaded"}))
            for model, present in loaded.items()
        }
