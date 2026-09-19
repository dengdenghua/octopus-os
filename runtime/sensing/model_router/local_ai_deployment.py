"""Reviewable deployment plans and a durable, single-job local AI installer."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid

from runtime.platform.io.atomic import atomic_write_json

from . import hwfit, managed_rknn
from . import managed_ollama as runtime
from .local_model_setup import clear_verification, verify_model

_lock = threading.Lock()
_plans: dict[str, dict] = {}
_active: dict | None = None
_stopping = threading.Event()


def status() -> dict:
    # NPU capability is orthogonal to the Ollama job lifecycle — surface it
    # in both branches so clients see the RK3576 tier regardless of stage.
    npu = managed_rknn.probe()
    with _lock:
        if _active is not None:
            return {**dict(_active), "npu": npu}
    try:
        previous = json.loads((runtime.root() / "deployment.json").read_text("utf-8"))
        if previous.get("stage") not in {"ready", "error"}:
            previous.update(stage="error", error="服务曾中断，请重新检查并部署；已下载权重可复用")
        return {**previous, "npu": npu}
    except (OSError, ValueError):
        return {"stage": "idle", "npu": npu}


def plan(tag: str) -> dict:
    if os.environ.get("OLLAMA_BASE_URL", runtime.BASE).rstrip("/") != runtime.BASE:
        raise ValueError("已显式配置其他 Ollama 地址，请先由管理员清除该覆盖配置")
    info = runtime.asset()
    from dataclasses import asdict

    # Automatic installation uses the curated local catalog, never an unreviewed
    # trending repository's inferred weight size or executable/model URL.
    choices = hwfit.recommend(hwfit.detect_hardware(), hwfit.default_catalog())
    choice = next(
        (
            rec
            for rec in choices
            if (tag == "auto" or rec.tag == tag) and rec.fits and rec.est_mem_gb > 0
        ),
        None,
    )
    rec = asdict(choice) if choice else None
    if rec is None:
        raise ValueError("该模型不在当前可用资源推荐中，请刷新后选择")
    tag = rec["tag"]
    runtime_bytes = 0 if runtime.executable().is_file() else info[1]
    # Weight size is an estimate; reserve a second copy for partial download/update.
    weight_bytes = int(rec["est_mem_gb"] * runtime.GIB)
    required = (
        runtime_bytes
        + (runtime.MAX_UNPACKED if runtime_bytes else 0)
        + weight_bytes * 2
        + 5 * runtime.GIB
    )
    free = runtime.available_disk()
    if free < required:
        raise ValueError(
            f"存储空间不足：建议至少 {required / runtime.GIB:.1f} GiB，当前 {free / runtime.GIB:.1f} GiB"
        )
    result = {
        "plan_id": uuid.uuid4().hex,
        "tag": tag,
        "label": rec["label"],
        "runtime_version": runtime.VERSION,
        "runtime_download_bytes": runtime_bytes,
        "model_estimate_bytes": weight_bytes,
        "required_disk_bytes": required,
        "free_disk_bytes": free,
        "storage_path": str(runtime.root().resolve()),
        "model_memory_gb": rec["est_mem_gb"],
        "endpoint": runtime.BASE,
        "expires_at": time.time() + 600,
    }
    with _lock:
        _plans.clear()  # one reviewable plan per runtime, valid for ten minutes
        _plans[result["plan_id"]] = result
    return dict(result)


def _update(**fields) -> None:
    with _lock:
        if _active is None:
            return
        _active.update(fields)
        atomic_write_json(runtime.root() / "deployment.json", _active, keep_backup=False)


def start(plan_id: str) -> dict:
    global _active
    with _lock:
        selected = _plans.get(plan_id)
        if not selected or selected["expires_at"] < time.time():
            raise ValueError("部署方案已过期，请重新检查")
        if _active and _active["stage"] not in {"ready", "error"}:
            raise ValueError("已有部署进行中")
        if runtime.available_disk() < selected["required_disk_bytes"]:
            raise ValueError("可用磁盘空间已减少，请重新检查")
        with hwfit._pull_lock:
            if any(
                value in {"pulling", "verifying", "deploying"}
                for value in hwfit._pull_state.values()
            ):
                raise ValueError("已有模型正在准备，请等待完成")
            runtime.claim_runtime()
            hwfit._pull_state[selected["tag"]] = "deploying"
        _active = {
            "job_id": uuid.uuid4().hex,
            "tag": selected["tag"],
            "stage": "preparing",
            "started_at": time.time(),
            "downloaded_bytes": 0,
            "total_bytes": 0,
        }
        _stopping.clear()
        try:
            atomic_write_json(runtime.root() / "deployment.json", _active, keep_backup=False)
            threading.Thread(
                target=_worker, args=(dict(selected),), daemon=True, name="local-ai-deployment"
            ).start()
        except Exception as exc:
            _active.update(stage="error", error="无法启动部署任务")
            with hwfit._pull_lock:
                hwfit._pull_state[selected["tag"]] = "error: 无法启动部署任务"
            raise ValueError("无法启动部署任务") from exc
        _plans.pop(plan_id, None)
        return dict(_active)


def _worker(selected: dict) -> None:
    tag = selected["tag"]
    last_update = 0.0

    def progress(received: int, total: int) -> None:
        nonlocal last_update
        if _stopping.is_set():
            raise ValueError("服务正在关闭，请稍后重新部署")
        now = time.monotonic()
        if now - last_update > 1 or received == total:
            _update(downloaded_bytes=received, total_bytes=total)
            last_update = now

    try:
        clear_verification(tag)
        _update(stage="installing")
        runtime.install(progress)
        if _stopping.is_set():
            raise ValueError("服务正在关闭，请稍后重新部署")
        _update(stage="starting", downloaded_bytes=0, total_bytes=0)
        runtime.start()
        _update(stage="pulling")
        result = hwfit.pull_model(tag, base=runtime.BASE)
        if result.get("status") != "ok":
            raise ValueError(str(result.get("error") or "模型下载失败"))
        _update(stage="verifying")
        verify_model(tag, base=runtime.BASE)
        if _stopping.is_set():
            raise ValueError("服务正在关闭，请稍后重新部署")
        runtime.enable()
        with hwfit._pull_lock:
            hwfit._pull_state[tag] = "ready"
        _update(stage="ready")
    except Exception as exc:
        message = (
            str(exc) if isinstance(exc, ValueError) else f"部署失败（{type(exc).__name__}），可重试"
        )
        with hwfit._pull_lock:
            hwfit._pull_state[tag] = f"error: {message}"
        with contextlib.suppress(OSError):
            _update(stage="error", error=message)


def resume_runtime() -> None:
    """Start only an explicitly enabled installation; never resume downloads."""
    if runtime.enabled():
        try:
            runtime.start()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Managed local AI failed to start")


def shutdown() -> None:
    _stopping.set()
    runtime.shutdown()
