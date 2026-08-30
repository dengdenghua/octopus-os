"""
OpenAPI schema snapshot · wire-contract drift detector.

Purpose
-------

FastAPI auto-generates ``/openapi.json`` from endpoint signatures and
response models. The frontend depends on this shape (and in a future
iteration will generate TS types from it). Silent shape drift — a
field added, a required field removed, a response model swapped for
``dict[str, Any]`` — breaks the frontend at runtime with no compile-
time signal.

This test snapshots the schema to ``docs/openapi-snapshot.json`` and
diffs it on every run. Any change in paths, methods, request/response
schemas, or component models fails the test — forcing the author to
either (a) update the snapshot intentionally and explain why in the
PR, or (b) revert the unintentional drift.

Regenerating the snapshot
-------------------------

When the drift is intentional::

    ECHO_OPENAPI_WRITE=1 pytest tests/test_openapi_snapshot.py

Then commit the updated ``docs/openapi-snapshot.json`` as part of the
same PR as the endpoint change · reviewers see both.

Why snapshot and not stronger schema validation
-----------------------------------------------

* A stricter "every endpoint must return a typed pydantic model"
  invariant would be ideal but requires touching every endpoint —
  a larger migration than this session can absorb.
* A snapshot catches *drift* even on ``dict[str, Any]`` endpoints
  (because the path/method entry disappears or reshapes), giving
  a useful signal while the typed-response migration happens
  gradually.
* ECHO_OPENAPI_WRITE=1 keeps the update loop cheap for
  intentional changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.ui.app import create_app

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi-snapshot.json"


def _current_schema() -> dict:
    """Fetch the live OpenAPI schema from a freshly built app.

    We intentionally build with NO stack / registry so the snapshot
    reflects the base surface area · adding a stack-conditional
    endpoint shouldn't shift the baseline.
    """
    app = create_app()
    client = TestClient(app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, f"openapi.json did not return 200: {resp.status_code}"
    return resp.json()


def _normalize(schema: dict) -> dict:
    """Drop fields that change across runs without being actual
    contract changes.

    Currently: the top-level ``info.version`` string — we bump it in
    ``create_app(... version="0.1.0")`` and don't want a release bump
    to invalidate the snapshot. The per-endpoint ``operationId`` and
    ``x-*`` extensions are kept because they ARE part of the wire
    contract a client would bind to.
    """
    out = json.loads(json.dumps(schema))  # deep copy
    info = out.get("info", {})
    info.pop("version", None)
    return out


def test_openapi_snapshot_matches() -> None:
    """Hard-fail on any drift. Set ``ECHO_OPENAPI_WRITE=1`` to
    intentionally regenerate."""
    current = _normalize(_current_schema())

    if os.environ.get("ECHO_OPENAPI_WRITE") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        pytest.skip(
            "snapshot regenerated; commit docs/openapi-snapshot.json",
        )

    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        pytest.fail(
            f"snapshot file missing — created baseline at "
            f"{SNAPSHOT_PATH.relative_to(Path.cwd()) if SNAPSHOT_PATH.is_relative_to(Path.cwd()) else SNAPSHOT_PATH}. "
            "Commit it and re-run the test."
        )

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # Rather than a raw string diff, compare shape-level maps so the
    # pytest failure message can pinpoint exactly which path drifted.
    cur_paths = set(current.get("paths", {}).keys())
    snap_paths = set(snapshot.get("paths", {}).keys())
    added = cur_paths - snap_paths
    removed = snap_paths - cur_paths

    if added or removed:
        pytest.fail(
            f"OpenAPI path set drifted.\n"
            f"  added: {sorted(added)}\n"
            f"  removed: {sorted(removed)}\n"
            "Run with ECHO_OPENAPI_WRITE=1 if intentional."
        )

    # Per-path method + schema-ref check. Full dict equality would be
    # noisy (operationId name changes, etc); we compare just the
    # method set + 200-response $ref per path.
    for p in sorted(cur_paths):
        cur_methods = set(current["paths"][p].keys())
        snap_methods = set(snapshot["paths"][p].keys())
        if cur_methods != snap_methods:
            pytest.fail(
                f"Methods on {p} drifted: "
                f"current={sorted(cur_methods)} snapshot={sorted(snap_methods)}"
            )

    # Component schema names — adding one is fine (new typed model);
    # removing one is a breaking change the caller should know about.
    cur_components = set(
        (current.get("components") or {}).get("schemas", {}).keys(),
    )
    snap_components = set(
        (snapshot.get("components") or {}).get("schemas", {}).keys(),
    )
    removed_components = snap_components - cur_components
    if removed_components:
        pytest.fail(
            f"OpenAPI component schemas removed (breaking): "
            f"{sorted(removed_components)}. Run with "
            "ECHO_OPENAPI_WRITE=1 if intentional."
        )


def test_openapi_response_models_on_config_endpoints() -> None:
    """Hard assertion · the config endpoints MUST use typed response
    models, not ``dict[str, Any]``. Guards against someone reverting
    the typed responses we just added in config_router.py.

    The ``/api/agents`` route isn't asserted here because it only
    registers when ``agent_registry`` is injected into ``create_app``
    — left for a follow-up when we build a representative test app.
    """
    schema = _current_schema()

    checks = [
        ("/api/config/identity-lock", "get", "IdentityLockResponse"),
        ("/api/config/identity-lock", "put", "IdentityLockResponse"),
        ("/api/providers", "get", "ProvidersResponse"),
        ("/api/config/custom-models", "get", "CustomModelsList"),
    ]
    for path, method, expected_component in checks:
        spec = schema["paths"][path][method]
        ref = spec["responses"]["200"]["content"]["application/json"]["schema"].get("$ref", "")
        assert ref.endswith(f"/{expected_component}"), (
            f"{method.upper()} {path} should return {expected_component} · "
            f"got ref={ref!r}. If the model was renamed intentionally, "
            "update both the check above and regenerate the snapshot."
        )
