"""Native image gates for persisted Agent task recovery."""

from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _runtime_verifier():
    return _load_script(
        "echo_test_verify_native_agent_runtime",
        ROOT / "packaging/image/verify-native-agent-runtime.py",
    )


def _health_verifier():
    return _load_script(
        "echo_test_verify_native_agent_health",
        ROOT / "deploy/agent/verify-native-agent-health",
    )


def _write_recovery_sources(root: Path, *, include_resume: bool = True) -> None:
    gateway = root / "site-packages/runtime/sensing/gateway/task_runs_router.py"
    analysis = root / "site-packages/runtime/platform/process/_task_supervisor_analysis.py"
    gateway.parent.mkdir(parents=True)
    analysis.parent.mkdir(parents=True)
    resume = (
        """
@router.post('/api/task-runs/{task_id}/resume-execution')
async def resume_execution(task_id):
    return {'schema': 'echo.task_run_resume_execution.v1'}
"""
        if include_resume
        else ""
    )
    gateway.write_text(
        """
@router.get('/api/task-runs/recovery-queue')
def recovery_queue():
    return store.recovery_queue()
"""
        + resume,
        encoding="utf-8",
    )
    analysis.write_text(
        "RECOVERY_SCHEMA = 'echo.task_recovery_queue.v1'\n",
        encoding="utf-8",
    )


def _write_identity_source(root: Path, *, include_source_id: bool = True) -> None:
    health = root / "site-packages/runtime/platform/ui/health_router.py"
    health.parent.mkdir(parents=True, exist_ok=True)
    source_id = 'identity["sourceId"] = source_id' if include_source_id else ""
    health.write_text(
        f"""
from runtime import __version__

def _runtime_identity():
    identity = {{
        "name": "echo-agent-runtime",
        "version": __version__,
        "verifiedBundle": False,
    }}
    source_id = os.environ.get("ECHO_RUNTIME_SOURCE_ID", "")
    verified = os.environ.get("ECHO_RUNTIME_BUNDLE_VERIFIED") == "1"
    if verified:
        {source_id}
        identity["verifiedBundle"] = True
    return identity

@router.get("/api/health")
def api_health():
    return {{"runtime": _runtime_identity()}}
""",
        encoding="utf-8",
    )


def test_native_runtime_manifest_binds_both_recovery_routes(tmp_path) -> None:
    verifier = _runtime_verifier()
    _write_recovery_sources(tmp_path)

    contract = verifier._verify_agent_recovery_surface(tmp_path)

    assert contract == {
        "schema": "echo.agent_recovery_surface.v1",
        "recovery_queue": {
            "method": "GET",
            "path": "/api/task-runs/recovery-queue",
            "response_schema": "echo.task_recovery_queue.v1",
        },
        "resume_execution": {
            "method": "POST",
            "path": "/api/task-runs/{task_id}/resume-execution",
            "response_schema": "echo.task_run_resume_execution.v1",
        },
    }


def test_native_runtime_manifest_accepts_the_echo_agent_protocol_family(tmp_path) -> None:
    verifier = _runtime_verifier()
    _write_recovery_sources(tmp_path)
    gateway = tmp_path / "site-packages/runtime/sensing/gateway/task_runs_router.py"
    analysis = tmp_path / "site-packages/runtime/platform/process/_task_supervisor_analysis.py"
    gateway.write_text(gateway.read_text().replace("echo.task_", "echo.task_"), encoding="utf-8")
    analysis.write_text(analysis.read_text().replace("echo.task_", "echo.task_"), encoding="utf-8")

    contract = verifier._verify_agent_recovery_surface(tmp_path)

    assert contract["recovery_queue"]["response_schema"] == ("echo.task_recovery_queue.v1")
    assert contract["resume_execution"]["response_schema"] == ("echo.task_run_resume_execution.v1")


def test_native_runtime_manifest_rejects_old_agent_without_resume_route(tmp_path) -> None:
    verifier = _runtime_verifier()
    _write_recovery_sources(tmp_path, include_resume=False)

    with pytest.raises(verifier.RuntimeError, match="POST .*resume-execution"):
        verifier._verify_agent_recovery_surface(tmp_path)


def test_native_runtime_manifest_binds_verified_health_identity(tmp_path) -> None:
    verifier = _runtime_verifier()
    _write_identity_source(tmp_path)

    assert verifier._verify_agent_identity_surface(tmp_path) == {
        "schema": "echo.agent_runtime_identity.v1",
        "health": {"method": "GET", "path": "/api/health"},
        "fields": ["name", "version", "sourceId", "verifiedBundle"],
    }


def test_native_runtime_manifest_accepts_the_echo_agent_identity(tmp_path) -> None:
    verifier = _runtime_verifier()
    _write_identity_source(tmp_path)
    health = tmp_path / "site-packages/runtime/platform/ui/health_router.py"
    source = health.read_text()
    source = source.replace("echo-agent-runtime", "echo-agent-runtime")
    source = source.replace("ECHO_RUNTIME_", "ECHO_RUNTIME_")
    health.write_text(source, encoding="utf-8")

    assert verifier._verify_agent_identity_surface(tmp_path)["schema"] == (
        "echo.agent_runtime_identity.v1"
    )


def test_native_runtime_manifest_rejects_agent_without_health_source_identity(
    tmp_path,
) -> None:
    verifier = _runtime_verifier()
    _write_identity_source(tmp_path, include_source_id=False)

    with pytest.raises(verifier.RuntimeError, match="verified runtime identity contract"):
        verifier._verify_agent_identity_surface(tmp_path)


def _health_payloads(health) -> tuple[dict, dict[str, bytes]]:
    expected = {
        "source": {"source_id": "a" * 40},
    }
    payloads = {
        f"{health.BASE_URL}/api/appliance/config": json.dumps(
            {
                "agent_bundle": {
                    "verified": True,
                    "source_id": "a" * 40,
                    "version": "0.2.0",
                },
                "agent_ui_base": None,
                "agent_workspace_url": None,
            }
        ).encode(),
        f"{health.BASE_URL}/api/health": json.dumps(
            {
                "status": "ok",
                "runtime": {
                    "name": "echo-agent-runtime",
                    "version": "0.2.0",
                    "sourceId": "a" * 40,
                    "verifiedBundle": True,
                },
            }
        ).encode(),
        f"{health.BASE_URL}/api/task-runs/recovery-queue?limit=200": json.dumps(
            {
                "schema": "echo.task_recovery_queue.v1",
                "total": 1,
                "count": 1,
                "limit": 200,
                "items": [
                    {
                        "task_id": "task-after-power-loss",
                        "recommended_action": "takeover_and_resume",
                    }
                ],
                "generated_at": "2026-08-26T00:00:00+00:00",
            }
        ).encode(),
    }
    return expected, payloads


def test_native_health_reads_recovery_queue_without_mutation(monkeypatch) -> None:
    health = _health_verifier()
    expected, payloads = _health_payloads(health)
    observed: list[str] = []

    def _read(url: str, *, maximum: int) -> bytes:
        observed.append(url)
        value = payloads[url]
        assert len(value) <= maximum
        return value

    monkeypatch.setattr(health, "_read", _read)

    assert health._verify_once(expected) == ("a" * 40, 1)
    assert observed[-1].endswith("/api/task-runs/recovery-queue?limit=200")
    assert all("resume-execution" not in url and "takeover" not in url for url in observed)


def test_native_health_rejects_a_runtime_from_another_agent_revision(monkeypatch) -> None:
    health = _health_verifier()
    expected, payloads = _health_payloads(health)
    health_url = f"{health.BASE_URL}/api/health"
    payload = json.loads(payloads[health_url])
    payload["runtime"]["sourceId"] = "b" * 40
    payloads[health_url] = json.dumps(payload).encode()
    monkeypatch.setattr(health, "_read", lambda url, *, maximum: payloads[url])

    with pytest.raises(RuntimeError, match="health identity differs"):
        health._verify_once(expected)


def test_native_health_rejects_incompatible_recovery_queue(monkeypatch) -> None:
    health = _health_verifier()
    expected, payloads = _health_payloads(health)
    queue_url = f"{health.BASE_URL}/api/task-runs/recovery-queue?limit=200"
    payloads[queue_url] = json.dumps(
        {"schema": "legacy.queue", "total": 0, "count": 0, "limit": 200, "items": []}
    ).encode()
    monkeypatch.setattr(health, "_read", lambda url, *, maximum: payloads[url])

    with pytest.raises(RuntimeError, match="recovery queue response is incompatible"):
        health._verify_once(expected)
