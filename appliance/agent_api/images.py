"""Agent semantic-image compatibility surface consumed by Echo OS."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def load_agent_image_index_module() -> Any | None:
    try:
        return importlib.import_module("runtime.memory.hemolymph.image_semantic_index")
    except (ImportError, ModuleNotFoundError):
        return None


def _dependency_present(name: str) -> bool:
    """Inspect top-level module specs without importing optional model packages."""

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def agent_image_index_readiness(module: Any | None) -> dict[str, Any]:
    """Report dependency discovery and already loaded models without loading any.

    A package being discoverable does not prove its model can run. In particular,
    this probe does not inspect model caches, instantiate fastembed/insightface,
    download weights, or call Agent's side-effecting ``face_capable`` helper.
    Actual model initialization remains part of the explicit index/search action.
    """

    disabled = os.environ.get("ECHO_IMAGE_SEMANTIC", "auto").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }
    compatible = module is not None and all(
        callable(getattr(module, name, None))
        for name in ("build_index", "search_by_text", "_iter_images", "_load_image", "_mtime")
    )
    dependencies = {
        "pillow": _dependency_present("PIL"),
        "fastembed": _dependency_present("fastembed"),
        "onnxruntime": _dependency_present("onnxruntime"),
        "insightface": _dependency_present("insightface"),
        "numpy": _dependency_present("numpy"),
    }
    # The current runtime exposes only in-memory observations. This optional ABI
    # seam must never be replaced by _text_model/_image_model/face_capable calls.
    observe = getattr(module, "image_model_status", None)
    observed = observe() if callable(observe) else {}
    if not isinstance(observed, dict):
        observed = {}

    def capability(
        required: tuple[str, ...], loaded: bool, models: tuple[str, ...]
    ) -> dict[str, Any]:
        missing = [name for name in required if not dependencies[name]]
        available = compatible and not disabled and not missing
        states = {name: observed.get(name, {}) for name in models}
        errors = (
            [
                {"model": name, "code": state["code"]}
                for name, state in states.items()
                if isinstance(state, dict)
                and state.get("state") == "load-failed"
                and state.get("code")
                in {
                    "runtime_import_failed",
                    "unsupported_quantization",
                    "invalid_model_configuration",
                    "model_load_failed",
                }
            ]
            if available
            else []
        )
        loading = any(
            isinstance(state, dict) and state.get("state") == "loading" for state in states.values()
        )
        return {
            "available": available,
            "state": (
                "disabled"
                if disabled
                else "unavailable"
                if not available
                else "loaded"
                if loaded
                else "loading"
                if loading
                else "load-failed"
                if errors
                else "dependencies-ready"
            ),
            "missingDependencies": missing,
            "modelsLoaded": available and loaded,
            "modelErrors": errors,
        }

    semantic = capability(
        ("pillow", "fastembed", "onnxruntime", "numpy"),
        getattr(module, "_CLIP_TEXT", None) is not None
        and getattr(module, "_CLIP_IMAGE", None) is not None,
        ("text", "vision"),
    )
    faces = capability(
        ("pillow", "insightface", "onnxruntime", "numpy"),
        getattr(module, "_FACE_APP", None) is not None,
        ("faces",),
    )
    result = {
        "schema": "echo.photos.readiness.v1",
        "browseAvailable": True,
        "previewAvailable": dependencies["pillow"],
        "semantic": semantic,
        "faces": faces,
        "modelDownloadMayBeRequired": semantic["state"] == "dependencies-ready",
    }
    observe_inference = getattr(module, "image_inference_status", None)
    if callable(observe_inference):
        try:
            resource = observe_inference()
        except Exception:  # noqa: BLE001 — readiness must remain side-effect free
            resource = None
        if isinstance(resource, dict):
            # Keep this observation intentionally narrow: no thread identity,
            # model path, or exception text crosses the appliance boundary.
            result["inference"] = {
                name: resource[name]
                for name in ("maxConcurrent", "active", "waiting", "available", "processShared")
                if type(resource.get(name)) in (int, float, bool)
            }
    return result


def search_agent_image_index(
    module: Any,
    query: str,
    *,
    top_k: int,
    db_path: Path,
    allowed_paths: Sequence[str] | None = None,
) -> list[dict[str, Any]] | None:
    """Keep legacy unscoped search compatible; require explicit scoped support."""

    search = getattr(module, "search_by_text", None)
    if not callable(search):
        return None
    if allowed_paths is not None:
        if not allowed_paths:
            return []
        try:
            parameter = inspect.signature(search).parameters.get("allowed_paths")
        except (TypeError, ValueError):
            return None
        # A legacy **kwargs wrapper may silently ignore this restriction.
        if parameter is None or parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return None
        result = search(query, top_k=top_k, db_path=db_path, allowed_paths=allowed_paths)
    else:
        result = search(query, top_k=top_k, db_path=db_path)
    return result if isinstance(result, list) else None


__all__ = [
    "agent_image_index_readiness",
    "load_agent_image_index_module",
    "search_agent_image_index",
]
