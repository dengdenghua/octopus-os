"""Tests for dispatcher, context engine, and capability features:

1. KanbanDispatcher — background lease-expiry daemon
2. SOUL.md hot-reload — already implemented; smoke-test the watcher API
3. ContextEngine ABC — pluggable compression strategy for Hemolymph
4. Provider Capability Auto-Detection — probe_provider + cache
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════
# 1. KanbanDispatcher
# ═══════════════════════════════════════════════════════════════


class TestKanbanDispatcher:
    @pytest.fixture
    def store(self, tmp_path: Path):
        from runtime.memory.cowork.store import CoworkStore, Task

        s = CoworkStore(base_dir=tmp_path)
        # Create a session with one task.
        s.create_plan(
            "sess-1",
            created_by="test",
            tasks=[Task(id="t1", title="Task 1")],
        )
        s.advance_phase("sess-1", "work")
        return s

    def test_dispatcher_starts_and_stops(self, store):
        from runtime.memory.cowork.store import KanbanDispatcher

        d = KanbanDispatcher(store, tick_seconds=60)
        d.start()
        assert d.running
        d.stop(timeout=2.0)
        assert not d.running

    def test_dispatcher_idempotent_start(self, store):
        from runtime.memory.cowork.store import KanbanDispatcher

        d = KanbanDispatcher(store, tick_seconds=60)
        d.start()
        d.start()  # second call is a no-op
        assert d.running
        d.stop(timeout=2.0)

    def test_dispatcher_fires_callback_on_expired_lease(self, store, tmp_path):
        from runtime.memory.cowork.store import KanbanDispatcher

        # Claim the task with a 0-second lease so it expires immediately.
        store.claim_task("sess-1", "t1", "agent-x", lease_seconds=0)

        released_sessions: list[str] = []
        released_tasks: list[str] = []

        def on_available(session_id: str, task_ids: list[str]) -> None:
            released_sessions.append(session_id)
            released_tasks.extend(task_ids)

        d = KanbanDispatcher(
            store,
            tick_seconds=0.05,  # very fast for test
            on_task_available=on_available,
        )
        d.start()
        # Give the dispatcher a couple of ticks to fire.
        time.sleep(0.3)
        d.stop(timeout=2.0)

        assert "sess-1" in released_sessions
        assert "t1" in released_tasks

    def test_dispatcher_does_not_release_completed_tasks(self, store):
        from runtime.memory.cowork.store import KanbanDispatcher

        store.claim_task("sess-1", "t1", "agent-x", lease_seconds=0)
        store.update_assignment_status("sess-1", "t1", "done")

        fired: list[str] = []

        def on_available(session_id: str, task_ids: list[str]) -> None:
            fired.extend(task_ids)

        d = KanbanDispatcher(
            store,
            tick_seconds=0.05,
            on_task_available=on_available,
        )
        d.start()
        time.sleep(0.3)
        d.stop(timeout=2.0)

        assert "t1" not in fired

    def test_renew_lease_prevents_expiry(self, store):
        from runtime.memory.cowork.store import KanbanDispatcher

        # Claim with a 1-second lease.
        store.claim_task("sess-1", "t1", "agent-x", lease_seconds=1)
        # Renew immediately with a 60-second lease.
        ok = store.renew_lease("sess-1", "t1", "agent-x", lease_seconds=60)
        assert ok is True

        fired: list[str] = []

        def on_available(session_id: str, task_ids: list[str]) -> None:
            fired.extend(task_ids)

        d = KanbanDispatcher(
            store,
            tick_seconds=0.05,
            on_task_available=on_available,
        )
        d.start()
        time.sleep(0.3)
        d.stop(timeout=2.0)

        # Lease was renewed — should NOT have been released.
        assert "t1" not in fired


# ═══════════════════════════════════════════════════════════════
# 2. SOUL.md hot-reload (smoke test — watcher already implemented)
# ═══════════════════════════════════════════════════════════════


class TestSoulHotReload:
    def test_start_agent_watcher_importable(self):
        """start_agent_watcher must be importable and callable."""
        from runtime.execution.agents.watcher import start_agent_watcher

        assert callable(start_agent_watcher)

    def test_watcher_accepts_agent_id_and_callback(self, tmp_path: Path):
        """start_agent_watcher must accept agents_root, registry, runtime args."""
        from runtime.execution.agents.watcher import start_agent_watcher

        # Should not raise even with mock args.
        registry = MagicMock()
        runtime = MagicMock()
        watcher = start_agent_watcher(tmp_path, registry, runtime)
        # Returns None when watchdog not installed, or an Observer otherwise.
        # Either is acceptable — just confirm no exception was raised.
        if watcher is not None:
            with contextlib.suppress(Exception):
                watcher.stop()


# ═══════════════════════════════════════════════════════════════
# 3. ContextEngine ABC
# ═══════════════════════════════════════════════════════════════


class TestContextEngine:
    def test_context_engine_is_abstract(self):
        from runtime.memory.hemolymph.composer import ContextEngine

        with pytest.raises(TypeError):
            ContextEngine()  # type: ignore[abstract]

    def test_truncation_engine_is_default(self):
        from runtime.execution.suckers import SkillRegistry
        from runtime.memory.hemolymph.composer import (
            ContextComposer,
            TruncationContextEngine,
        )

        reg = SkillRegistry()
        composer = ContextComposer(reg)
        assert isinstance(composer.engine, TruncationContextEngine)

    def test_custom_engine_replaces_default(self):
        from runtime.execution.suckers import SkillRegistry
        from runtime.memory.hemolymph.composer import ContextComposer, ContextEngine
        from runtime.platform.models import ContextSegment

        class PassthroughEngine(ContextEngine):
            def compress(
                self,
                segments: list[ContextSegment],
                budget_tokens: int,
            ) -> list[ContextSegment]:
                return segments  # no-op

        reg = SkillRegistry()
        engine = PassthroughEngine()
        composer = ContextComposer(reg, engine=engine)
        assert composer.engine is engine

    def test_custom_engine_compress_is_called(self):
        from runtime.execution.suckers import SkillRegistry
        from runtime.memory.hemolymph.composer import ContextComposer, ContextEngine
        from runtime.platform.models import ContextSegment, ParsedIntent

        compress_calls: list[int] = []

        class CountingEngine(ContextEngine):
            def compress(
                self,
                segments: list[ContextSegment],
                budget_tokens: int,
            ) -> list[ContextSegment]:
                compress_calls.append(len(segments))
                return segments

        reg = SkillRegistry()
        composer = ContextComposer(reg, engine=CountingEngine())
        intent = ParsedIntent(
            raw="test",
            normalized_goal="test",
            intent_type="task",
        )
        composer.compose(intent, system_prompt="Hello", budget_tokens=5000)
        assert len(compress_calls) == 1

    def test_truncation_engine_drops_segments_over_budget(self):
        from runtime.memory.hemolymph.composer import TruncationContextEngine
        from runtime.platform.models import ContextSegment

        engine = TruncationContextEngine()
        segments = [
            ContextSegment(
                bucket="history",
                content="x" * 300,
                tokens_estimated=100,
                source_refs=[],
            )
            for _ in range(10)
        ]
        result = engine.compress(segments, budget_tokens=300)
        total = sum(s.tokens_estimated for s in result)
        assert total <= 300

    def test_context_engine_exported_from_hemolymph(self):
        from runtime.memory.hemolymph import ContextEngine, TruncationContextEngine

        assert ContextEngine is not None
        assert TruncationContextEngine is not None


# ═══════════════════════════════════════════════════════════════
# 4. Provider Capability Auto-Detection
# ═══════════════════════════════════════════════════════════════


class TestCapabilityProbe:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        from runtime.sensing.model_router.capability_probe import clear_capability_cache

        clear_capability_cache()
        yield
        clear_capability_cache()

    def test_probe_mock_router_no_stream(self):
        from runtime.sensing.model_router.capability_probe import probe_provider
        from runtime.sensing.model_router.models import MockModelRouter

        router = MockModelRouter(response="ok")
        caps = probe_provider(router, model="mock/ok")
        # MockModelRouter has no stream() method → streaming=False
        assert caps.supports_streaming is False

    def test_probe_result_cached_in_memory(self):
        from runtime.sensing.model_router.capability_probe import (
            get_cached_capabilities,
            probe_provider,
        )
        from runtime.sensing.model_router.models import MockModelRouter

        router = MockModelRouter(response="ok")
        caps1 = probe_provider(router, model="mock/ok")
        caps2 = probe_provider(router, model="mock/ok")
        assert caps1 == caps2

        key = f"{type(router).__name__}:mock/ok"
        cached = get_cached_capabilities(key)
        assert cached is not None
        assert cached == caps1

    def test_force_re_probe(self):
        from runtime.sensing.model_router.capability_probe import probe_provider
        from runtime.sensing.model_router.models import MockModelRouter

        router = MockModelRouter(response="ok")
        caps1 = probe_provider(router, model="mock/ok")
        caps2 = probe_provider(router, model="mock/ok", force=True)
        # Both should be valid ProviderCapabilities instances.
        assert caps1 is not None
        assert caps2 is not None

    def test_clear_cache_removes_entries(self):
        from runtime.sensing.model_router.capability_probe import (
            clear_capability_cache,
            get_cached_capabilities,
            probe_provider,
        )
        from runtime.sensing.model_router.models import MockModelRouter

        router = MockModelRouter(response="ok")
        probe_provider(router, model="mock/ok")
        key = f"{type(router).__name__}:mock/ok"
        assert get_cached_capabilities(key) is not None

        clear_capability_cache()
        assert get_cached_capabilities(key) is None

    def test_probe_disk_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from runtime.sensing.model_router import capability_probe as _mod
        from runtime.sensing.model_router.capability_probe import probe_provider
        from runtime.sensing.model_router.models import MockModelRouter

        monkeypatch.setattr(_mod, "_disk_cache_path", lambda: tmp_path / "provider_caps.json")

        router = MockModelRouter(response="ok")
        probe_provider(router, model="mock/disk")

        # Cache file should exist.
        cache_file = tmp_path / "provider_caps.json"
        assert cache_file.exists()
        data = __import__("json").loads(cache_file.read_text())
        key = f"{type(router).__name__}:mock/disk"
        assert key in data

    def test_probe_returns_static_caps_on_error(self):
        """When all probes fail, static capabilities are returned."""
        from runtime.sensing.model_router.capability_probe import probe_provider
        from runtime.sensing.model_router.provider import Provider, ProviderCapabilities

        class BrokenRouter(Provider):
            provider_name = "broken"
            capabilities = ProviderCapabilities(supports_vision=True)

            def call(self, req: Any) -> Any:
                raise RuntimeError("network error")

        router = BrokenRouter()
        caps = probe_provider(router, model="broken/v1")
        # Vision was declared statically — should be preserved.
        assert caps.supports_vision is True

    def test_probe_exported_from_eyes(self):
        from runtime.sensing.model_router import (
            Provider,
            ProviderCapabilities,
            clear_capability_cache,
            get_cached_capabilities,
            probe_provider,
        )

        assert callable(probe_provider)
        assert callable(get_cached_capabilities)
        assert callable(clear_capability_cache)
        assert Provider is not None
        assert ProviderCapabilities is not None
