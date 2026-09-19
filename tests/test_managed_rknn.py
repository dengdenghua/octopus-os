"""managed_rknn: NPU probe / catalog / gating contracts.

All hardware-dependent edges are monkeypatchable seams: `_device_nodes`,
`_runtime_paths`, `_is_rk_board`. Tests never touch /dev or real libs.
"""

from __future__ import annotations

import json

import pytest

from runtime.sensing.model_router import hwfit, managed_rknn


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    class _Paths:
        data_dir = tmp_path

    # Patch the *module-level* accessor only; never mutate the real
    # app_paths object — that would leak into every other test in the
    # session (same global-state class of bug as the threading snow-gate).
    monkeypatch.setattr(managed_rknn, "app_paths", lambda: _Paths())
    monkeypatch.setattr(managed_rknn, "_LEASE", None)
    monkeypatch.setattr(
        managed_rknn,
        "_is_rk_board",
        lambda: True,
    )
    yield


def test_probe_off_board_reports_nothing(monkeypatch):
    monkeypatch.setattr(managed_rknn, "_is_rk_board", lambda: False)
    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: ["/dev/rknpu0"])
    monkeypatch.setattr(managed_rknn, "_runtime_paths", lambda lib: ["/usr/lib/" + lib])
    info = managed_rknn.probe()
    assert info["board"] is False
    assert info["npu_device"] is False
    assert info["backend"] is None
    assert managed_rknn.available() is False


def test_available_requires_device_and_runtime(monkeypatch):
    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: ["/dev/rknpu0"])
    monkeypatch.setattr(managed_rknn, "_runtime_paths", lambda lib: [])
    assert managed_rknn.available() is False
    assert managed_rknn.probe()["backend"] is None

    monkeypatch.setattr(
        managed_rknn,
        "_runtime_paths",
        lambda lib: ["/usr/lib/" + lib] if lib == managed_rknn.RKLLM_LIB else [],
    )
    assert managed_rknn.available() is True
    assert managed_rknn.probe()["backend"] == "rknn-npu"


def test_classic_path_independent_of_llm(monkeypatch):
    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: ["/dev/rknpu0"])
    monkeypatch.setattr(
        managed_rknn,
        "_runtime_paths",
        lambda lib: ["/usr/lib/" + lib] if lib == managed_rknn.RKNN_LIB else [],
    )
    assert managed_rknn.classic_available() is True
    assert managed_rknn.available() is False  # LLM path still incomplete


def test_model_catalog_reads_manifest_and_skips_incomplete(tmp_path):
    models = managed_rknn.model_root()
    good = models / "qwen3-1.7b-w8a8"
    good.mkdir(parents=True)
    (good / "model.rkllm").write_bytes(b"weights")
    (good / "manifest.json").write_text(
        json.dumps({"kind": "llm", "params_b": 1.7, "quant": "w8a8"}), "utf-8"
    )
    empty = models / "broken-entry"
    empty.mkdir()
    (empty / "README.txt").write_text("no model file", "utf-8")
    vision = models / "clip-vit"
    vision.mkdir()
    (vision / "model.rknn").write_bytes(b"vision")

    catalog = managed_rknn.model_catalog()
    by_name = {item["name"]: item for item in catalog}
    assert set(by_name) == {"qwen3-1.7b-w8a8", "clip-vit"}
    assert by_name["qwen3-1.7b-w8a8"]["kind"] == "llm"
    assert by_name["qwen3-1.7b-w8a8"]["params_b"] == 1.7
    assert by_name["clip-vit"]["kind"] == "vision"


def test_model_catalog_tolerates_corrupt_manifest(tmp_path):
    entry = managed_rknn.model_root() / "m1"
    entry.mkdir(parents=True)
    (entry / "model.rkllm").write_bytes(b"x")
    (entry / "manifest.json").write_text("{not json", "utf-8")
    catalog = managed_rknn.model_catalog()
    assert len(catalog) == 1
    assert catalog[0]["kind"] == "llm"  # suffix-derived default


def test_enable_roundtrip(tmp_path):
    assert managed_rknn.enabled() is False
    managed_rknn.enable()
    assert managed_rknn.enabled() is True


def test_start_fails_loudly_without_device_or_runtime(monkeypatch):
    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: [])
    monkeypatch.setattr(managed_rknn, "_runtime_paths", lambda lib: [])
    with pytest.raises(ValueError, match="NPU 推理不可用"):
        managed_rknn.start()


def test_start_fails_loudly_when_bridge_unwired(monkeypatch):
    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: ["/dev/rknpu0"])
    monkeypatch.setattr(managed_rknn, "_runtime_paths", lambda lib: ["/usr/lib/" + lib])
    with pytest.raises(ValueError, match="尚未接线"):
        managed_rknn.start()


def test_lease_idempotent_and_releasable():
    # Single-owner is a *cross-process* contract (lock on runtime.lock);
    # within one process the second claim is an idempotent no-op, exactly
    # like managed_ollama.
    managed_rknn.claim_runtime()
    managed_rknn.claim_runtime()  # idempotent, no error
    managed_rknn.shutdown()
    managed_rknn.claim_runtime()  # releasable after shutdown
    managed_rknn.shutdown()


def test_hwfit_reports_rknn_npu_backend(monkeypatch):
    monkeypatch.setattr(managed_rknn, "available", lambda: True)
    monkeypatch.setattr(hwfit, "_detect_nvidia", lambda: (0.0, None))
    monkeypatch.setattr(hwfit, "_detect_apple", lambda: (None, None))
    monkeypatch.setattr(hwfit, "_ram_gb", lambda: 8.0)
    import runtime.platform.system_memory as memory

    monkeypatch.setattr(memory, "total_memory_gb", lambda: 8.0)
    monkeypatch.setattr(memory, "available_memory_gb", lambda: 4.0)
    hardware = hwfit.detect_hardware()
    assert hardware.backend == "rknn-npu"
    assert hardware.unified_memory is True
    assert hardware.bandwidth_gbps == managed_rknn.LPDDR_BANDWIDTH_GBPS


def test_hwfit_stays_cpu_off_board(monkeypatch):
    monkeypatch.setattr(managed_rknn, "available", lambda: False)
    monkeypatch.setattr(hwfit, "_detect_nvidia", lambda: (0.0, None))
    monkeypatch.setattr(hwfit, "_detect_apple", lambda: (None, None))
    import runtime.platform.system_memory as memory

    monkeypatch.setattr(memory, "total_memory_gb", lambda: 8.0)
    monkeypatch.setattr(memory, "available_memory_gb", lambda: 4.0)
    hardware = hwfit.detect_hardware()
    assert hardware.backend == "cpu"


# ── deployment-status integration ────────────────────────────────────────────
def test_local_ai_deployment_status_exposes_npu(monkeypatch, tmp_path):
    """The deployment status API carries the NPU probe in every lifecycle stage."""
    from runtime.sensing.model_router import local_ai_deployment as deploy

    class _Paths:
        data_dir = tmp_path

    monkeypatch.setattr(deploy.runtime, "root", lambda: tmp_path / "local-ai")
    monkeypatch.setattr(deploy.runtime, "_lease", None)
    monkeypatch.setattr(deploy.runtime, "_process", None)
    monkeypatch.setattr(deploy, "_active", None)
    monkeypatch.setattr(deploy, "managed_rknn", managed_rknn)

    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: ["/dev/rknpu0"])
    monkeypatch.setattr(
        managed_rknn,
        "_runtime_paths",
        lambda lib: ["/usr/lib/" + lib] if lib == managed_rknn.RKLLM_LIB else [],
    )

    info = deploy.status()
    assert info["stage"] == "idle"
    assert info["npu"]["backend"] == "rknn-npu"
    assert info["npu"]["npu_device"] is True
    assert info["npu"]["llm_runtime"] is True


def test_local_ai_deployment_status_npu_off_board(monkeypatch, tmp_path):
    from runtime.sensing.model_router import local_ai_deployment as deploy

    class _Paths:
        data_dir = tmp_path

    monkeypatch.setattr(deploy.runtime, "root", lambda: tmp_path / "local-ai")
    monkeypatch.setattr(deploy.runtime, "_lease", None)
    monkeypatch.setattr(deploy.runtime, "_process", None)
    monkeypatch.setattr(deploy, "_active", None)
    monkeypatch.setattr(deploy, "managed_rknn", managed_rknn)
    monkeypatch.setattr(managed_rknn, "_is_rk_board", lambda: False)
    monkeypatch.setattr(managed_rknn, "_device_nodes", lambda: [])

    info = deploy.status()
    assert info["npu"]["backend"] is None
    assert info["npu"]["npu_device"] is False
