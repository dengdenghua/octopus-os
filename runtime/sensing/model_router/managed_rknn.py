"""Rockchip NPU local-AI runtime: probe, catalog, and gating.

RK3576 (and RK35xx siblings) expose two *distinct* Rockchip userspaces
that are routinely conflated:

- **RKLLM** (``librkllmrt.so``, models ``*.rkllm``): LLM inference on the
  NPU. This is the chat-model path — on RK3576 the NPU is 6 TOPS INT8
  and LLM decode is LPDDR-bandwidth-bound, not compute-bound.
- **RKNN** (``librknnrt.so``, models ``*.rknn``): classic/vision models
  (embedders, VLM encoders, classifiers).

Model *conversion* (rknn-toolkit2 / rkllm-toolkit) always happens on an
x86 build host; firmware bakes only the runtimes. This module owns
probing, cataloguing, enablement and the loud-failure contract for the
not-yet-wired inference bridge. It deliberately does **not** fall back
to CPU silently — a missing NPU runtime must surface as an error, never
as a slow surprise.

Design parity with ``managed_ollama``: same lease/root/enable patterns
so ``local_ai_deployment`` can treat both runtimes uniformly later.
"""

from __future__ import annotations

import glob
import json
import os
import platform
import threading
from pathlib import Path

from runtime.platform.io.atomic import atomic_write_json
from runtime.platform.process.paths import app_paths

RKLLM_LIB = "librkllmrt.so"
RKNN_LIB = "librknnrt.so"

# LPDDR4x-3733 dual-channel ≈ 14.9 GB/s theoretical; decode TPS roofline
# for a 3B model ≈ bandwidth / active-weight-bytes. Keep one honest number
# instead of marketing TOPS.
LPDDR_BANDWIDTH_GBPS = 14.9
NPU_NAME = "Rockchip RK3576 NPU (6 TOPS)"

_LEASE: object | None = None
_LEASE_LOCK = threading.Lock()


def root() -> Path:
    """State root for the NPU runtime (models, enable marker, lease)."""
    return app_paths().data_dir / "local-ai" / "rknn-npu"


def _device_nodes() -> list[str]:
    """NPU device nodes as the BSP kernel driver exposes them."""
    return sorted(glob.glob("/dev/rknpu*"))


def _runtime_paths(library: str) -> list[str]:
    """Where the baked runtime library may live (firmware layout)."""
    import ctypes

    found = ctypes.util.find_library(library.removesuffix(".so"))
    candidates = [f"/usr/lib/{library}", f"/usr/lib/aarch64-linux-gnu/{library}"]
    if found and found not in candidates:
        candidates.append(found)
    return [p for p in candidates if os.path.exists(p)]


def _is_rk_board() -> bool:
    if platform.system() != "Linux":
        return False
    if platform.machine().lower() not in {"aarch64", "armv8l"}:
        return False
    # DTB-compatible match beats arch guessing.
    for marker in ("/proc/device-tree/compatible", "/proc/device-tree/model"):
        try:
            if "rk3576" in Path(marker).read_text("utf-8", errors="ignore"):
                return True
            if "rk35" in Path(marker).read_text("utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def probe() -> dict:
    """Best-effort snapshot. Never raises; every value is independently true/false."""
    board = _is_rk_board()
    nodes = _device_nodes() if board else []
    llm = _runtime_paths(RKLLM_LIB) if board else []
    classic = _runtime_paths(RKNN_LIB) if board else []
    return {
        "board": board,
        "npu_device": bool(nodes),
        "npu_nodes": nodes,
        "llm_runtime": bool(llm),
        "classic_runtime": bool(classic),
        "backend": "rknn-npu" if (nodes and llm) else None,
    }


def available() -> bool:
    """True only when a full NPU LLM path exists (device + RKLLM runtime)."""
    info = probe()
    return bool(info["npu_device"] and info["llm_runtime"])


def classic_available() -> bool:
    """RKNN classic-model path (vision/embedding), independent of LLM."""
    info = probe()
    return bool(info["npu_device"] and info["classic_runtime"])


# ── model catalog ────────────────────────────────────────────────────────────
def model_root() -> Path:
    return root() / "models"


def model_catalog() -> list[dict]:
    """Enumerate baked NPU models with their manifests.

    Layout: ``models/<name>/model.<rkllm|rknn>`` plus an optional
    ``manifest.json`` (params_b, quant, kind). Entries without a model
    file are ignored; manifests are advisory metadata, never executed.
    """
    catalog: list[dict] = []
    models = model_root()
    if not models.is_dir():
        return catalog
    for entry in sorted(models.iterdir()):
        if not entry.is_dir():
            continue
        model_file = next((p for p in entry.iterdir() if p.suffix in {".rkllm", ".rknn"}), None)
        if model_file is None:
            continue
        meta: dict = {}
        manifest = entry / "manifest.json"
        if manifest.is_file():
            try:
                loaded = json.loads(manifest.read_text("utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            except (OSError, ValueError):
                meta = {}
        catalog.append(
            {
                "name": entry.name,
                "kind": meta.get("kind", "llm" if model_file.suffix == ".rkllm" else "vision"),
                "format": model_file.suffix.lstrip("."),
                "params_b": meta.get("params_b"),
                "quant": meta.get("quant"),
                "path": str(model_file),
            }
        )
    return catalog


# ── enablement + lease (parity with managed_ollama) ─────────────────────────
def enable() -> None:
    root().mkdir(parents=True, exist_ok=True)
    atomic_write_json(root() / "enabled.json", {"version": 1})


def enabled() -> bool:
    try:
        return json.loads((root() / "enabled.json").read_text("utf-8")) == {"version": 1}
    except (OSError, ValueError):
        return False


def claim_runtime() -> None:
    """Single-owner lease so two Echo processes never drive the NPU at once."""
    global _LEASE
    with _LEASE_LOCK:
        if _LEASE is not None:
            return
        root().mkdir(parents=True, exist_ok=True)
        handle = (root() / "runtime.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            handle.close()
            raise ValueError("其他 Echo 进程正在管理 NPU 推理") from None
        _LEASE = handle


def shutdown() -> None:
    global _LEASE
    with _LEASE_LOCK:
        if _LEASE is not None:
            _LEASE.close()
            _LEASE = None


def start() -> None:
    """Loud-failure contract: the ctypes bridge is wired during firmware
    bring-up. Fail explicitly instead of silently degrading to CPU."""
    if not available():
        raise ValueError("NPU 推理不可用：缺少 RK3576 设备节点或 RKLLM 运行库")
    raise ValueError("RKLLM 推理桥尚未接线：等待固件 bring-up 后接入 librkllmrt C API")
