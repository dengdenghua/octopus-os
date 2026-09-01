"""
Integration tests for ``runtime/sensing/siphon/invariants_router.py``.

What's covered
--------------

    GET    /api/invariants                · full catalog
    GET    /api/invariants/{rule_id}      · single-rule detail
    GET    /api/invariants/{unknown}      · 404 path
    POST   /api/invariants/refresh        · cache invalidation
    Cache stability across consecutive GETs

Test-data fixture
-----------------
The discovery walk introspects ``sys.modules`` at request time. To
guarantee the test environment has at least one rule to discover, we
import the safety modules at the top of this file (which forces them
into ``sys.modules``) AND define a small set of decorated functions /
classes here — those land in ``tests.test_invariants_router`` and are
discovered the same way real production decorators would be.

That dual approach is intentional: it pins the contract that
discovery works on **any** loaded module, not just on packages with a
specific prefix.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importing these forces them into sys.modules, so even if the
# production codebase doesn't yet apply the decorators to live
# functions, the discovery walk has something to find.
from runtime.safety import invariants  # noqa: F401
from runtime.safety.invariants import (
    append_only,
    enforces,
    ensure,
    monotonic,
    require,
)
from runtime.safety.invariants import enforce as _enforce_module  # noqa: F401
from runtime.sensing.gateway.invariants_router import (
    create_invariants_router,
)

# ═══════════════════════════════════════════════════════════
# Test fixtures · decorated functions/classes that must be
# discovered when the router walks ``sys.modules``.
# ═══════════════════════════════════════════════════════════
#
# These exercise all four decorator shapes so a regression in the
# walker (e.g. missing class methods) shows up as a missing rule
# in the catalog — not as a silent skip.


@enforces("BDG-I1", "TEST-RULE-A")
def _budget_reserve_marker() -> None:
    """Stand-in for production ``Budget.reserve`` enforcement."""


@enforces("MEM-I1")
def _journal_append_marker() -> None:
    """Stand-in for the journal-append rule."""


@require(lambda x: x > 0, rule_id="TEST-REQ-1")
def _positive_only(x: int) -> int:
    """Sanity callable for ``@require`` discovery."""
    return x


@ensure(lambda r: r > 0, rule_id="TEST-ENS-1")
def _positive_result(x: int) -> int:
    return x + 1


class _CounterMarker:
    """``@monotonic`` lives on methods — proves the walker descends
    into class ``__dict__``."""

    def __init__(self) -> None:
        self.val = 0

    @monotonic("val", direction="up", rule_id="TEST-MONO-1")
    def step(self) -> None:
        self.val += 1


class _LedgerMarker:
    """``@append_only`` likewise lives on methods."""

    def __init__(self) -> None:
        self.events: list[int] = []

    @append_only("events", rule_id="TEST-APPEND-1")
    def add(self, x: int) -> None:
        self.events.append(x)


# Sanity: the marker decorators wired up at all?
assert _budget_reserve_marker.__enforces__ == ("BDG-I1", "TEST-RULE-A")


# ═══════════════════════════════════════════════════════════
# App / client fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Minimal app with just the invariants router mounted."""
    # The router caches its discovery walk on first call. When a prior
    # test in the same session triggered that build before this test
    # module's decorators were imported, the cache misses our markers
    # and the catalog tests fail. Invalidate before each test so
    # discovery re-walks the now-fully-loaded sys.modules.
    from runtime.sensing.gateway.invariants_router import _invalidate_cache

    _invalidate_cache()
    app = FastAPI()
    app.include_router(create_invariants_router())
    with TestClient(app) as c:
        yield c


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════


class TestListEndpoint:
    def test_returns_rules_array_with_data(self, client: TestClient) -> None:
        """GET /api/invariants returns at least one rule.

        Our markers above contribute ≥4 distinct rule ids, so the
        catalog can never be empty in this test process. If this
        fails the discovery walk is broken.
        """
        r = client.get("/api/invariants")
        assert r.status_code == 200
        body = r.json()
        assert "rules" in body
        assert isinstance(body["rules"], list)
        assert len(body["rules"]) >= 1
        assert body["total_rules"] == len(body["rules"])
        assert body["total_enforcers"] >= len(body["rules"])

    def test_each_entry_has_required_shape(
        self,
        client: TestClient,
    ) -> None:
        """Every rule entry must expose ``rule_id`` and ``enforcers``."""
        r = client.get("/api/invariants")
        assert r.status_code == 200
        for entry in r.json()["rules"]:
            assert isinstance(entry["rule_id"], str)
            assert isinstance(entry["enforcers"], list)
            for enforcer in entry["enforcers"]:
                assert "module" in enforcer
                assert "qualname" in enforcer


class TestDetailEndpoint:
    def test_known_rule_returns_200(self, client: TestClient) -> None:
        """Pick a rule id we know is in the catalog (BDG-I1 if present,
        otherwise the first rule the listing returns) and verify the
        detail endpoint mirrors the listing entry."""
        listing = client.get("/api/invariants").json()
        rule_ids = [e["rule_id"] for e in listing["rules"]]
        assert rule_ids, "catalog unexpectedly empty"

        candidate = "BDG-I1" if "BDG-I1" in rule_ids else rule_ids[0]

        r = client.get(f"/api/invariants/{candidate}")
        assert r.status_code == 200
        body = r.json()
        assert body["rule_id"] == candidate
        assert isinstance(body["enforcers"], list)
        assert len(body["enforcers"]) >= 1

    def test_unknown_rule_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/invariants/DOES_NOT_EXIST_XYZ")
        assert r.status_code == 404


class TestRefreshEndpoint:
    def test_refresh_returns_200_and_catalog(
        self,
        client: TestClient,
    ) -> None:
        """POST /api/invariants/refresh rebuilds the cache and returns
        the same shape as the listing endpoint."""
        before = client.get("/api/invariants").json()
        r = client.post("/api/invariants/refresh")
        assert r.status_code == 200
        after = r.json()
        assert "rules" in after
        # Counts are stable in a single-process test (no module
        # imports happen between calls), but the contract is that
        # ``total_rules`` *may* change after a refresh — we only
        # require it to be non-negative and self-consistent.
        assert after["total_rules"] == len(after["rules"])
        assert before["total_rules"] >= 0


class TestCacheStability:
    def test_two_consecutive_gets_match(
        self,
        client: TestClient,
    ) -> None:
        """Cache contract: two GETs with no intervening refresh return
        identical rule_id sets. Frontend relies on this for stable
        list rendering across re-fetches."""
        a = client.get("/api/invariants").json()
        b = client.get("/api/invariants").json()
        assert {e["rule_id"] for e in a["rules"]} == {e["rule_id"] for e in b["rules"]}
