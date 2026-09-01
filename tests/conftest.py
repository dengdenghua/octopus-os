"""Implementation note."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from runtime.platform.models import (
        Budget,
        BudgetLimits,
        CostEntry,
        ParsedIntent,
        Step,
        TaskGraph,
        Trajectory,
    )

# runtime.platform.models is imported lazily inside fixtures so that
# appliance-only tests can run without the agent dependency installed
# (CI fallback path). With ``from __future__ import annotations`` the
# return-type annotations below are strings and never evaluated at import.


@pytest.fixture
def bypass_serve_port_guard(monkeypatch):
    """Keep mocked-Uvicorn assembly tests independent of host listeners."""
    import runtime.cli_serve as cli_serve

    monkeypatch.setattr(cli_serve, "_port_held", lambda _host, _port: False)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset process-wide mutable state between tests.

    Prevents cross-test leakage through module-level knobs. As more
    such knobs land (prompt cache toggle, rate limiter, etc.), add
    them here so every test starts from a clean slate.

    Currently resets:
      - ``runtime.platform.runtime_policy.identity_filter._RUNTIME_OVERRIDE`` — set by
        ``PUT /api/config/identity-lock``; if a test flips it to False
        and forgets to clean up, later "lock is on by default" tests
        would spuriously see it off.
      - All eight singletons (EventBus, StateStore, PluginLoader,
        UsagePricing, EvolutionAutoTrigger, AmbientScheduler,
        CamouflageScheduler, RegenerationScheduler). Without this,
        tests that triggered a scheduler thread or populated the
        eventbus could leak state into the next test — e.g. the
        invariants_router cache picking up stale third-party module
        state, or anthropic SDK lazy-imports surviving across tests.
    """
    from runtime.platform import identity_filter as _idf

    _idf.set_runtime_lock(None)
    _reset_injection_taint()
    _reset_delegation_budget()
    _reset_subagent_runners()
    _reset_connector_refreshers()
    yield
    _idf.set_runtime_lock(None)
    _reset_injection_taint()
    _reset_delegation_budget()
    _reset_subagent_runners()
    _reset_connector_refreshers()
    _reset_singletons()


def _reset_subagent_runners() -> None:
    """Clear process-wide subagent dispatch hooks between tests."""
    from runtime.execution.subagents import set_sub_agent_runner
    from runtime.execution.suckers.ephemeral_agents import set_ephemeral_role_runner

    set_sub_agent_runner(None)
    set_ephemeral_role_runner(None)


def _reset_connector_refreshers() -> None:
    """Reap process-global connector refresh workers between tests."""
    try:
        from runtime.platform.connectors._token_refresher import (
            reset_refresh_supervisor_for_tests,
        )

        reset_refresh_supervisor_for_tests()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_gene_locks(tmp_path, monkeypatch):
    """Keep durable gene-lock state out of both tests and the live checkout."""
    from runtime.safety.gene_locks import simple_gate as _sg

    monkeypatch.setattr(_sg, "_store_path", lambda: tmp_path / "gene_locks.json")
    monkeypatch.setattr(_sg, "_CACHED", None)
    monkeypatch.setattr(_sg, "_INTEGRITY_FAILED", None)
    monkeypatch.delenv("ECHO_GENE_LOCKS_MODE", raising=False)
    yield
    _sg._CACHED = None
    _sg._INTEGRITY_FAILED = None


@pytest.fixture(autouse=True)
def _isolate_drift_monitor_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DRIFT_STATE_DIR", str(tmp_path / "evolution_drift_state"))


@pytest.fixture(autouse=True)
def _disable_os_keychain(monkeypatch):
    """Never let tests read or write the developer's real OS keychain."""
    from runtime.platform.credentials import secret_store as _ss

    monkeypatch.setenv("ECHO_KEYCHAIN", "off")
    _ss.reset_key_cache_for_tests()
    yield
    _ss.reset_key_cache_for_tests()


@pytest.fixture(autouse=True)
def _deterministic_plugin_policy_coverage(monkeypatch):
    """Keep readiness gates independent of locally installed plugins."""
    from runtime.safety.evolution import permission_sandbox_quality as _psq

    monkeypatch.setattr(
        _psq,
        "_plugin_policy_coverage",
        lambda _base: {
            "schema": "echo.plugin_permission_rule_coverage.v1",
            "ready": True,
            "plugin_count": 1,
            "total": 1,
            "verified": 1,
            "next_actions": [],
        },
    )


@pytest.fixture(autouse=True)
def _protect_live_custom_models():
    """Restore the operator's live model catalog if a test touches it."""
    from runtime.platform.process.paths import app_paths

    try:
        path = app_paths().custom_models_path
        before = path.read_bytes() if path.exists() else None
    except OSError:
        yield
        return
    yield
    try:
        if before is None:
            if path.exists():
                path.unlink()
        elif path.read_bytes() != before:
            path.write_bytes(before)
    except OSError:
        pass


def _reset_injection_taint() -> None:
    """Clear the per-thread prompt-injection taint + gate-handled contextvars.

    These are set during a turn (untrusted tool output) and the react loop
    only resets them at its OWN start — so executor-level tests that call
    ``execute_step`` directly, or any test that runs after a taint-marking
    one, would otherwise inherit a stale taint and spuriously block/allow
    tools. Reset every test for isolation."""
    try:
        from runtime.safety.validation import prompt_injection as _pi

        _pi.reset_injection_taint()
        _pi.set_injection_gate_handled(False)
    except Exception:  # noqa: BLE001 — never let cleanup break a test
        pass


def _reset_delegation_budget() -> None:
    try:
        from runtime.execution.suckers import delegation_budget as _db

        _db._TURN_DELEGATIONS.clear()
        _db._TURN_FAILED_FINGERPRINTS.clear()
    except Exception:
        pass


def _reset_singletons() -> None:
    """Drop every process-wide singleton between tests.

    Each ``reset()`` is wrapped in a try/except: a singleton that
    can't be reset (because its module failed to import on this
    platform) shouldn't take down the rest of the suite. The reset
    methods themselves are documented as test-only.
    """
    import logging

    _log = logging.getLogger(__name__)

    # (module_path, attr) tuples — imported lazily so a missing
    # optional dep on the import chain doesn't break the fixture.
    _singletons = (
        ("runtime.platform.process.eventbus", "EventBus"),
        ("runtime.platform.process.state", "StateStore"),
        ("runtime.platform.plugins.plugin_loader", "PluginLoader"),
        ("runtime.platform.budget.usage_pricing", "UsagePricing"),
        ("runtime.safety.evolution.auto_trigger", "EvolutionAutoTrigger"),
        ("runtime.memory.skills_lib.ambient_suggestions_scheduler", "AmbientScheduler"),
        ("runtime.safety.experiments.scheduler", "CamouflageScheduler"),
        ("runtime.safety.recovery.scheduler", "RegenerationScheduler"),
    )
    for mod_path, cls_name in _singletons:
        try:
            import importlib

            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            reset = getattr(cls, "reset", None)
            if reset is not None:
                reset()
        except Exception as exc:  # noqa: BLE001 — singleton reset must not fail tests
            _log.debug("singleton reset skipped for %s.%s: %s", mod_path, cls_name, exc)

    # ServiceProvider stitches eventbus/statestore/plugin_loader together
    # at first ``get_provider()`` call. If we've blanked those out, the
    # provider is also stale.
    try:
        from runtime.platform import service_provider as _sp

        _sp._PROVIDER = None  # noqa: SLF001 — test reset only
    except Exception as exc:  # noqa: BLE001
        _log.debug("service_provider reset skipped: %s", exc)


@pytest.fixture
def small_limits() -> BudgetLimits:
    from runtime.platform.models import BudgetLimits

    return BudgetLimits(tokens=1_000, usd=0.10)


@pytest.fixture
def small_budget(small_limits: BudgetLimits) -> Budget:
    from runtime.platform.models import Budget, TaskId

    return Budget(task_id=TaskId(uuid4()), limits=small_limits)


@pytest.fixture
def sample_cost() -> CostEntry:
    from runtime.platform.models import CostEntry

    return CostEntry(tokens_in=100, tokens_out=50, usd=0.001, latency_ms=200.0)


@pytest.fixture
def sample_intent() -> ParsedIntent:
    from runtime.platform.models import ParsedIntent

    return ParsedIntent(
        raw="test intent",
        intent_type="task",
        normalized_goal="run a unit test",
    )


@pytest.fixture
def sample_graph() -> TaskGraph:
    from runtime.platform.models import BudgetSpec, TaskGraph, TaskNode

    return TaskGraph(
        nodes=[
            TaskNode(node_id="n1", skill_ref="read_file"),
            TaskNode(node_id="n2", skill_ref="run_test"),
        ],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="code_fix",
    )


@pytest.fixture
def sample_step(sample_cost: CostEntry) -> Step:
    from runtime.platform.models import ExecutionResult, Step, ToolCall

    call = ToolCall(
        caller="arm:code_arm",
        sucker_id="read_file",
        args={"path": "a.py"},
    )
    result = ExecutionResult(
        call_id=call.call_id,
        status="success",
        output="ok",
        cost=sample_cost,
    )
    return Step(step_id=0, node_id="n1", action=call, result=result)


@pytest.fixture
def sample_trajectory(sample_step: Step, sample_graph: TaskGraph) -> Trajectory:
    from runtime.platform.models import Trajectory, TrajectoryOutcome

    return Trajectory(
        task_id=sample_graph.task_id,
        arm_id="code_arm",
        steps=[sample_step],
        outcome=TrajectoryOutcome(success=True),
    )


@functools.lru_cache(maxsize=1)
def _chromium_unavailable_reason() -> str | None:
    """Return why Chromium cannot launch, or None when it is usable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright is not installed"
    last: str | None = None
    for channel in (None, "chromium"):
        kwargs: dict[str, object] = {"headless": True}
        if channel:
            kwargs["channel"] = channel
        try:
            with sync_playwright() as playwright:
                playwright.chromium.launch(**kwargs).close()
            _LAUNCH_KWARGS.clear()
            _LAUNCH_KWARGS.update(kwargs)
            return None
        except Exception as exc:  # noqa: BLE001
            last = str(exc).splitlines()[0][:160]
    return f"chromium cannot launch: {last}"


_LAUNCH_KWARGS: dict[str, object] = {}


def chromium_launch_kwargs() -> dict[str, object]:
    """Launch kwargs known to work here. Call requires_chromium first."""
    return dict(_LAUNCH_KWARGS) or {"headless": True}


def requires_chromium() -> None:
    """Skip the calling test unless a launchable Chromium is present."""
    reason = _chromium_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)


def pytest_addoption(parser):
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="record snapshots instead of comparing them",
    )


@pytest.fixture
def snapshot(request):
    from tests.snapshot_utils import Snapshotter

    return Snapshotter(
        request.node.nodeid,
        update=request.config.getoption("--snapshot-update"),
    )


@pytest.fixture(autouse=True)
def _isolate_subagent_sessions(tmp_path, monkeypatch):
    """Give every test a private durable subagent session store."""
    from runtime.execution.subagents import sessions as _ss

    store = _ss.SubagentSessionStore(base_dir=tmp_path / "subagent_sessions")
    monkeypatch.setattr(_ss, "_default_base_dir", lambda: tmp_path / "subagent_sessions")
    _ss.set_subagent_session_store(store)
    yield
    _ss.set_subagent_session_store(None)
