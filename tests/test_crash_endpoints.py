"""Integration tests for the crash-report endpoints.

Guards the read side of the crash reporter: ``/api/crashes`` and friends.
The writer side (``sys.excepthook`` / ``threading`` / ``asyncio`` hooks and
the bounded store) is covered in ``tests/test_crash_reporter.py``; these
tests only assert that a record on disk becomes a readable, diagnosed
response — and that the endpoints never turn an unhealthy system into a
500-ing one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.observability.crash_reporter import default_store, record_exception
from runtime.platform.ui.app import create_app
from runtime.sensing.gateway.observability_router import create_observability_router


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect CWD so ``app_paths().data_dir`` lands in a scratch dir."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def stack(isolated_cwd: Path):
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/ob",
            mock_response='{"reasoning":"r","nodes":[]}',
        ),
    )
    return build_from_config(cfg)


@pytest.fixture
def client(stack, isolated_cwd: Path) -> TestClient:
    return TestClient(
        create_app(journal=stack.journal, registry=stack.registry, stack=stack)
    )


def _seed(exc: BaseException, **context: str) -> str:
    """Write one crash into the default store and return its fingerprint."""
    record = record_exception(
        default_store(),
        exc,
        source="manual",
        context=dict(context),
    )
    return record.fingerprint


# ═══════════════════════════════════════════════════════════
# GET /api/crashes
# ═══════════════════════════════════════════════════════════


def test_empty_store_returns_zero(client: TestClient) -> None:
    r = client.get("/api/crashes")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["crashes"] == []
    assert body["schema"] == "echo.crash_report.v1"


def test_recorded_crash_is_listed_with_local_diagnosis(client: TestClient) -> None:
    fingerprint = _seed(OSError(28, "No space left on device"), component="storage")

    r = client.get("/api/crashes")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    crash = body["crashes"][0]
    assert crash["fingerprint"] == fingerprint
    assert crash["occurrence_count"] == 1
    assert crash["context"] == {"component": "storage"}
    # Local rule matched, no model needed.
    assert crash["diagnosis"]["cause"] == "disk_full"
    assert crash["diagnosis"]["confidence"] == "high"
    # The list view must not drag stack frames into the panel.
    assert "traceback" not in crash


def test_repeat_crashes_collapse_into_one_entry(client: TestClient) -> None:
    for _ in range(3):
        _seed(PermissionError(13, "Permission denied"))

    body = client.get("/api/crashes").json()
    assert body["count"] == 1
    assert body["crashes"][0]["occurrence_count"] == 3


def test_unknown_failure_still_gets_a_low_confidence_verdict(client: TestClient) -> None:
    _seed(ValueError("something nobody wrote a rule for"))

    crash = client.get("/api/crashes").json()["crashes"][0]
    assert crash["diagnosis"]["cause"] == "unknown"
    assert crash["diagnosis"]["confidence"] == "low"
    assert crash["diagnosis"]["suggested_fix"]


# ═══════════════════════════════════════════════════════════
# GET /api/crashes/{fingerprint}
# ═══════════════════════════════════════════════════════════


def test_detail_carries_the_traceback(client: TestClient) -> None:
    fingerprint = _seed(RuntimeError("boom"))

    r = client.get(f"/api/crashes/{fingerprint}")
    assert r.status_code == 200
    body = r.json()
    assert body["traceback"]
    assert "RuntimeError" in body["traceback"]
    assert "environment" in body


def test_detail_404s_on_unknown_fingerprint(client: TestClient) -> None:
    r = client.get("/api/crashes/deadbeefdeadbeef")
    assert r.status_code == 404


def test_report_endpoint_returns_pasteable_text(client: TestClient) -> None:
    fingerprint = _seed(OSError(28, "No space left on device"))

    body = client.get(f"/api/crashes/{fingerprint}/report").json()
    report = body["report"]
    assert "disk_full" in report
    assert "Traceback" in report or "OSError" in report


# ═══════════════════════════════════════════════════════════
# DELETE /api/crashes
# ═══════════════════════════════════════════════════════════


def test_delete_acknowledges_every_dump(client: TestClient) -> None:
    # Two distinct types: two RuntimeErrors from adjacent lines share a
    # fingerprint (frame keys deliberately drop line numbers) and would
    # collapse into one record — which is the dedup working, not a bug.
    _seed(RuntimeError("one"))
    _seed(ValueError("two"))
    assert client.get("/api/crashes").json()["count"] == 2

    r = client.delete("/api/crashes")
    assert r.status_code == 200
    assert r.json()["removed"] == 2
    assert r.json()["failed"] == 0
    assert client.get("/api/crashes").json()["count"] == 0


# ═══════════════════════════════════════════════════════════
# Optional LLM explainer
# ═══════════════════════════════════════════════════════════


def test_explainer_refines_only_when_asked(
    stack,
    isolated_cwd: Path,
) -> None:
    """``explain=1`` routes through ``crash_explainer``; the default does not.

    An appliance with no key (or no model) must still get the local verdict,
    so the explainer is opt-in at request time and absent by default.
    """
    calls: list[str] = []

    def fake_explainer(prompt: str) -> str:
        calls.append(prompt)
        return "The volume filled up because the trace writer never rotates."

    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=stack.journal,
            registry=stack.registry,
            crash_explainer=fake_explainer,
        )
    )
    client = TestClient(app)
    fingerprint = _seed(OSError(28, "No space left on device"))

    plain = client.get(f"/api/crashes/{fingerprint}").json()
    assert plain["diagnosis"]["cause"] == "disk_full"
    assert "trace writer never rotates" not in plain["diagnosis"]["explanation"]
    assert calls == []

    explained = client.get(f"/api/crashes/{fingerprint}?explain=true").json()
    assert len(calls) == 1
    assert "volume filled up" in explained["diagnosis"]["explanation"]
    # The rule's cause still stands; only the prose is enriched.
    assert explained["diagnosis"]["cause"] == "disk_full"


def test_a_broken_explainer_degrades_to_the_local_verdict(
    stack,
    isolated_cwd: Path,
) -> None:
    def exploding_explainer(_prompt: str) -> str:
        raise RuntimeError("model unavailable")

    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=stack.journal,
            registry=stack.registry,
            crash_explainer=exploding_explainer,
        )
    )
    client = TestClient(app)
    fingerprint = _seed(OSError(28, "No space left on device"))

    body = client.get(f"/api/crashes/{fingerprint}?explain=true").json()
    assert body["diagnosis"]["cause"] == "disk_full"
    assert body["diagnosis"]["explanation"]


def test_list_marks_whether_an_explainer_is_available(
    stack,
    isolated_cwd: Path,
) -> None:
    _seed(RuntimeError("boom"))

    with_explainer = FastAPI()
    with_explainer.include_router(
        create_observability_router(
            journal=stack.journal,
            registry=stack.registry,
            crash_explainer=lambda _p: "explained",
        )
    )
    body = TestClient(with_explainer).get("/api/crashes?explain=true").json()
    assert body["explained"] is True
    assert "explained" in body["crashes"][0]["diagnosis"]["explanation"]
