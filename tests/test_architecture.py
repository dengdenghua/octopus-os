from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import pytest  # noqa: E402

from runtime.platform.plugins.plugin_loader import (  # noqa: E402
    EchoPlugin,
    PluginContext,
    PluginLoader,
    PluginManifest,
    PluginState,
)
from runtime.platform.process.eventbus import (  # noqa: E402
    ALL_DOMAIN_EVENTS,
    EVOLUTION_EVENTS,
    PLATFORM_EVENTS,
    SAFETY_EVENTS,
    BudgetPressureEvent,
    CanaryStageChanged,
    DomainEvent,
    DriftDetected,
    EventBus,
    EventBusConfig,
    EvolutionCompleted,
    EvolutionTriggered,
    FileWriteBlocked,
    FitnessComputed,
    GenomeRolledBack,
    IterationExtended,
    PluginLoadedEvent,
    ProposalCreated,
    SkillUsed,
    StateChanged,
    ToolCallBlocked,
)
from runtime.platform.process.state import (  # noqa: E402
    FileBackend,
    MemoryBackend,
    SQLiteBackend,
    StateEntry,
    StateStore,
)


class TestDomainEvent:
    def test_domain_event_creation(self):
        e = DomainEvent(event_type="test.event", agent_id="agent1")
        assert e.event_type == "test.event"
        assert e.agent_id == "agent1"
        assert e.payload == {}

    def test_domain_event_frozen(self):
        from pydantic import ValidationError

        e = DomainEvent(event_type="test.event")
        # pydantic v2 frozen models raise ValidationError on attribute mutation.
        with pytest.raises((AttributeError, TypeError, ValidationError)):
            e.event_type = "changed"

    def test_typed_events_have_correct_type(self):
        assert FitnessComputed().event_type == "fitness.computed"
        assert DriftDetected().event_type == "drift.detected"
        assert SkillUsed().event_type == "skill.used"
        assert EvolutionTriggered().event_type == "evolution.triggered"
        assert EvolutionCompleted().event_type == "evolution.completed"
        assert GenomeRolledBack().event_type == "genome.rolled_back"
        assert CanaryStageChanged().event_type == "canary.stage_changed"
        assert ProposalCreated().event_type == "proposal.created"
        assert BudgetPressureEvent().event_type == "budget.pressure"
        assert IterationExtended().event_type == "iteration.extended"
        assert ToolCallBlocked().event_type == "tool_call.blocked"
        assert FileWriteBlocked().event_type == "file_write.blocked"
        assert PluginLoadedEvent().event_type == "plugin.loaded"
        assert StateChanged().event_type == "state.changed"

    def test_event_collections(self):
        assert FitnessComputed in EVOLUTION_EVENTS
        assert BudgetPressureEvent in SAFETY_EVENTS
        assert PluginLoadedEvent in PLATFORM_EVENTS
        assert len(ALL_DOMAIN_EVENTS) == len(EVOLUTION_EVENTS) + len(SAFETY_EVENTS) + len(
            PLATFORM_EVENTS
        )


class TestEventBus:
    def setup_method(self):
        EventBus.reset()
        self.bus = EventBus()

    def teardown_method(self):
        EventBus.reset()

    def test_subscribe_and_publish(self):
        received = []
        self.bus.subscribe("test.event", lambda e: received.append(e))
        event = DomainEvent(event_type="test.event", payload={"key": "val"})
        called = self.bus.publish(event)
        assert called == 1
        assert len(received) == 1
        assert received[0].payload["key"] == "val"

    def test_subscribe_by_type(self):
        received = []
        self.bus.subscribe(FitnessComputed, lambda e: received.append(e))
        event = FitnessComputed(combined_score=0.85, verdict="healthy")
        self.bus.publish(event)
        assert len(received) == 1
        assert received[0].combined_score == 0.85

    def test_wildcard_subscription(self):
        received = []
        self.bus.subscribe("*", lambda e: received.append(e))
        self.bus.publish(DomainEvent(event_type="a.b"))
        self.bus.publish(DomainEvent(event_type="c.d"))
        assert len(received) == 2

    def test_unsubscribe(self):
        received = []
        sub_id = self.bus.subscribe("test.event", lambda e: received.append(e))
        self.bus.unsubscribe(sub_id)
        self.bus.publish(DomainEvent(event_type="test.event"))
        assert len(received) == 0

    def test_unsubscribe_wildcard(self):
        received = []
        sub_id = self.bus.subscribe("*", lambda e: received.append(e))
        self.bus.unsubscribe(sub_id)
        self.bus.publish(DomainEvent(event_type="test.event"))
        assert len(received) == 0

    def test_unsubscribe_nonexistent(self):
        assert self.bus.unsubscribe(99999) is False

    def test_priority_ordering(self):
        order = []
        self.bus.subscribe("test", lambda e: order.append("low"), priority=0)
        self.bus.subscribe("test", lambda e: order.append("high"), priority=10)
        self.bus.publish(DomainEvent(event_type="test"))
        assert order == ["high", "low"]

    def test_once_subscription(self):
        received = []
        self.bus.once("test", lambda e: received.append(e))
        self.bus.publish(DomainEvent(event_type="test"))
        self.bus.publish(DomainEvent(event_type="test"))
        assert len(received) == 1

    def test_filter_fn(self):
        received = []
        self.bus.subscribe(
            "test",
            lambda e: received.append(e),
            filter_fn=lambda e: e.payload.get("pass") is True,
        )
        self.bus.publish(DomainEvent(event_type="test", payload={"pass": True}))
        self.bus.publish(DomainEvent(event_type="test", payload={"pass": False}))
        assert len(received) == 1

    def test_crash_resilient(self):
        self.bus.subscribe("test", lambda e: 1 / 0)
        called = self.bus.publish(DomainEvent(event_type="test"))
        assert called == 0
        assert len(self.bus.errors) == 1

    def test_crash_not_resilient(self):
        bus = EventBus(EventBusConfig(crash_resilient=False))
        bus.subscribe("test", lambda e: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            bus.publish(DomainEvent(event_type="test"))

    def test_emit_shorthand(self):
        received = []
        self.bus.subscribe("test", lambda e: received.append(e))
        self.bus.emit("test", agent_id="a1", key="val")
        assert len(received) == 1
        assert received[0].agent_id == "a1"
        assert received[0].payload["key"] == "val"

    def test_history(self):
        self.bus.publish(DomainEvent(event_type="a"))
        self.bus.publish(DomainEvent(event_type="b"))
        self.bus.publish(DomainEvent(event_type="a"))
        assert len(self.bus.history()) == 3
        assert len(self.bus.history("a")) == 2
        assert len(self.bus.history("b")) == 1

    def test_history_limit(self):
        bus = EventBus(EventBusConfig(max_history=5))
        for i in range(10):
            bus.publish(DomainEvent(event_type="test", payload={"i": i}))
        assert len(bus.history()) == 5

    def test_subscriber_count(self):
        self.bus.subscribe("a", lambda e: None)
        self.bus.subscribe("a", lambda e: None)
        self.bus.subscribe("b", lambda e: None)
        self.bus.subscribe("*", lambda e: None)
        assert self.bus.subscriber_count("a") == 2
        assert self.bus.subscriber_count("b") == 1
        assert self.bus.subscriber_count() == 4

    def test_clear(self):
        self.bus.subscribe("test", lambda e: None)
        self.bus.publish(DomainEvent(event_type="test"))
        self.bus.clear()
        assert self.bus.subscriber_count() == 0
        assert len(self.bus.history()) == 0

    def test_wait_for(self):
        def _publish_later():
            time.sleep(0.1)
            self.bus.publish(DomainEvent(event_type="wait.test", payload={"ok": True}))

        t = threading.Thread(target=_publish_later, daemon=True)
        t.start()
        event = self.bus.wait_for("wait.test", timeout=2.0)
        assert event is not None
        assert event.payload["ok"] is True

    def test_wait_for_timeout(self):
        event = self.bus.wait_for("never", timeout=0.1)
        assert event is None

    def test_invalid_handler(self):
        with pytest.raises(TypeError):
            self.bus.subscribe("test", "not_callable")

    def test_invalid_event(self):
        with pytest.raises(TypeError):
            self.bus.publish("not_an_event")

    def test_singleton(self):
        bus1 = EventBus.get()
        bus2 = EventBus.get()
        assert bus1 is bus2
        EventBus.reset()

    def test_thread_safety(self):
        received = []
        lock = threading.Lock()

        def handler(e):
            with lock:
                received.append(e)

        self.bus.subscribe("test", handler)
        threads = []
        for i in range(10):
            t = threading.Thread(
                target=lambda idx=i: self.bus.publish(
                    DomainEvent(event_type="test", payload={"i": idx})
                )
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(received) == 10


class TestMemoryBackend:
    def test_set_and_get(self):
        backend = MemoryBackend()
        entry = StateEntry(key="k1", value="v1", namespace="ns1")
        backend.set(entry)
        result = backend.get("k1", "ns1")
        assert result is not None
        assert result.value == "v1"

    def test_version_increments(self):
        backend = MemoryBackend()
        backend.set(StateEntry(key="k1", value="v1"))
        backend.set(StateEntry(key="k1", value="v2"))
        result = backend.get("k1")
        assert result.version == 2

    def test_delete(self):
        backend = MemoryBackend()
        backend.set(StateEntry(key="k1", value="v1"))
        assert backend.delete("k1") is True
        assert backend.get("k1") is None
        assert backend.delete("k1") is False

    def test_list_keys(self):
        backend = MemoryBackend()
        backend.set(StateEntry(key="app.config", value=1))
        backend.set(StateEntry(key="app.state", value=2))
        backend.set(StateEntry(key="other.key", value=3))
        assert backend.list_keys(prefix="app") == ["app.config", "app.state"]
        assert len(backend.list_keys()) == 3

    def test_list_namespaces(self):
        backend = MemoryBackend()
        backend.set(StateEntry(key="k", value="v", namespace="ns1"))
        backend.set(StateEntry(key="k", value="v", namespace="ns2"))
        assert set(backend.list_namespaces()) == {"ns1", "ns2"}


class TestFileBackend:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.backend = FileBackend(base_dir=self.tmpdir)

    def test_set_and_get(self):
        self.backend.set(StateEntry(key="k1", value={"a": 1}))
        result = self.backend.get("k1")
        assert result is not None
        assert result.value == {"a": 1}

    def test_version_increments(self):
        self.backend.set(StateEntry(key="k1", value="v1"))
        self.backend.set(StateEntry(key="k1", value="v2"))
        result = self.backend.get("k1")
        assert result.version == 2

    def test_delete(self):
        self.backend.set(StateEntry(key="k1", value="v1"))
        assert self.backend.delete("k1") is True
        assert self.backend.get("k1") is None

    def test_list_keys_and_namespaces(self):
        self.backend.set(StateEntry(key="k1", value=1, namespace="ns1"))
        self.backend.set(StateEntry(key="k2", value=2, namespace="ns1"))
        self.backend.set(StateEntry(key="k3", value=3, namespace="ns2"))
        assert len(self.backend.list_keys("ns1")) == 2
        assert set(self.backend.list_namespaces()) == {"ns1", "ns2"}

    def test_rejects_namespace_path_traversal(self):
        base = Path(self.tmpdir)
        outside = base.parent / f"{base.name}-escape"

        with pytest.raises(ValueError, match="invalid file state namespace"):
            self.backend.set(StateEntry(key="k", value="v", namespace="../escape"))

        assert not outside.exists()

    def test_rejects_symlinked_namespace_escape(self):
        base = Path(self.tmpdir)
        outside = base.parent / f"{base.name}-outside"
        outside.mkdir()
        link = base / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink unavailable: {exc}")

        with pytest.raises(ValueError, match="escapes base directory"):
            self.backend.set(StateEntry(key="k", value="v", namespace="linked"))

        assert not (outside / "k.json").exists()


class TestSQLiteBackend:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.backend = SQLiteBackend(db_path=self.db_path)

    def test_set_and_get(self):
        self.backend.set(StateEntry(key="k1", value=[1, 2, 3]))
        result = self.backend.get("k1")
        assert result is not None
        assert result.value == [1, 2, 3]

    def test_version_increments(self):
        self.backend.set(StateEntry(key="k1", value="v1"))
        self.backend.set(StateEntry(key="k1", value="v2"))
        result = self.backend.get("k1")
        assert result.version == 2

    def test_delete(self):
        self.backend.set(StateEntry(key="k1", value="v1"))
        assert self.backend.delete("k1") is True
        assert self.backend.get("k1") is None

    def test_list_keys_prefix(self):
        self.backend.set(StateEntry(key="app.config", value=1))
        self.backend.set(StateEntry(key="app.state", value=2))
        self.backend.set(StateEntry(key="sys.info", value=3))
        assert len(self.backend.list_keys(prefix="app")) == 2

    def test_list_namespaces(self):
        self.backend.set(StateEntry(key="k", value="v", namespace="ns1"))
        self.backend.set(StateEntry(key="k", value="v", namespace="ns2"))
        assert set(self.backend.list_namespaces()) == {"ns1", "ns2"}


class TestStateStore:
    def setup_method(self):
        StateStore.reset()
        EventBus.reset()
        self.store = StateStore(backend=MemoryBackend())

    def teardown_method(self):
        StateStore.reset()
        EventBus.reset()

    def test_get_set(self):
        self.store.set("key1", "value1")
        assert self.store.get("key1") == "value1"

    def test_get_nonexistent(self):
        assert self.store.get("nope") is None

    def test_get_entry(self):
        self.store.set("key1", "value1")
        entry = self.store.get_entry("key1")
        assert entry is not None
        assert entry.key == "key1"
        assert entry.version == 1

    def test_version_increments(self):
        self.store.set("key1", "v1")
        self.store.set("key1", "v2")
        self.store.set("key1", "v3")
        entry = self.store.get_entry("key1")
        assert entry.version == 3

    def test_delete(self):
        self.store.set("key1", "v1")
        assert self.store.delete("key1") is True
        assert self.store.get("key1") is None

    def test_namespaces(self):
        self.store.set("key1", "ns1_val", namespace="ns1")
        self.store.set("key1", "ns2_val", namespace="ns2")
        assert self.store.get("key1", "ns1") == "ns1_val"
        assert self.store.get("key1", "ns2") == "ns2_val"

    def test_list_keys(self):
        self.store.set("app.config", 1)
        self.store.set("app.state", 2)
        self.store.set("sys.info", 3)
        keys = self.store.list_keys(prefix="app")
        assert len(keys) == 2

    def test_inc(self):
        self.store.set("counter", 5)
        result = self.store.inc("counter")
        assert result == 6
        assert self.store.get("counter") == 6

    def test_inc_from_zero(self):
        result = self.store.inc("new_counter")
        assert result == 1

    def test_get_or_set(self):
        result = self.store.get_or_set("key1", "default_val")
        assert result == "default_val"
        result2 = self.store.get_or_set("key1", "other_val")
        assert result2 == "default_val"

    def test_watch(self):
        changes = []
        self.store.watch("app.*", lambda new, old: changes.append((new, old)))
        self.store.set("app.config", "v1")
        self.store.set("sys.info", "v2")
        assert len(changes) == 1
        assert changes[0][0].value == "v1"
        assert changes[0][1] is None

    def test_watch_with_old_value(self):
        changes = []
        self.store.set("key1", "v1")
        self.store.watch("key1", lambda new, old: changes.append((new, old)))
        self.store.set("key1", "v2")
        assert len(changes) == 1
        assert changes[0][0].value == "v2"
        assert changes[0][1].value == "v1"

    def test_unwatch(self):
        changes = []
        wid = self.store.watch("key1", lambda new, old: changes.append(1))
        self.store.set("key1", "v1")
        assert len(changes) == 1
        self.store.unwatch(wid)
        self.store.set("key1", "v2")
        assert len(changes) == 1

    def test_metadata(self):
        self.store.set("key1", "v1", metadata={"source": "test"})
        entry = self.store.get_entry("key1")
        assert entry.metadata["source"] == "test"

    def test_singleton(self):
        s1 = StateStore.get_instance()
        s2 = StateStore.get_instance()
        assert s1 is s2
        StateStore.reset()


class TestEchoPlugin:
    def test_default_lifecycle(self):
        p = EchoPlugin()
        ctx = PluginContext(plugin_name="test")
        p.on_load(ctx)
        p.on_start(ctx)
        p.on_stop(ctx)
        p.on_unload(ctx)

    def test_context_state(self):
        store = StateStore(backend=MemoryBackend())
        ctx = PluginContext(plugin_name="test", state_store=store)
        ctx.set_state("key1", "val1")
        assert ctx.get_state("key1") == "val1"
        assert ctx.get_state("missing", "default") == "default"

    def test_context_event_bus(self):
        bus = EventBus()
        received = []
        bus.subscribe("test.event", lambda e: received.append(e))
        ctx = PluginContext(plugin_name="test", event_bus=bus)
        ctx.emit("test.event", data="hello")
        assert len(received) == 1

    def test_context_subscribe(self):
        bus = EventBus()
        received = []
        ctx = PluginContext(plugin_name="test", event_bus=bus)
        ctx.subscribe("test.event", lambda e: received.append(e))
        bus.emit("test.event")
        assert len(received) == 1


class TestPluginManifest:
    def test_basic_manifest(self):
        m = PluginManifest(name="my-plugin", version="1.0.0")
        assert m.name == "my-plugin"
        assert m.version == "1.0.0"
        assert m.subscribes == []

    def test_manifest_with_subscribes(self):
        m = PluginManifest(
            name="my-plugin",
            subscribes=["fitness.computed", "drift.detected"],
        )
        assert len(m.subscribes) == 2


class TestPluginLoader:
    def setup_method(self):
        PluginLoader.reset()
        EventBus.reset()
        StateStore.reset()
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        PluginLoader.reset()
        EventBus.reset()
        StateStore.reset()

    def _write_plugin(self, name, code):
        plugin_dir = os.path.join(self.tmpdir, name)
        os.makedirs(plugin_dir, exist_ok=True)
        with open(os.path.join(plugin_dir, "__init__.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(code))

    def _write_single_plugin(self, name, code):
        with open(os.path.join(self.tmpdir, f"{name}.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(code))

    def test_discover_dir_plugin(self):
        self._write_plugin(
            "hello",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin

            class HelloPlugin(EchoPlugin):
                name = "hello"
                version = "1.0.0"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        discovered = loader.discover()
        assert "hello" in discovered

    def test_discover_single_file_plugin(self):
        self._write_single_plugin(
            "simple",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin

            class SimplePlugin(EchoPlugin):
                name = "simple"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        discovered = loader.discover()
        assert "simple" in discovered

    def test_load_dir_plugin(self):
        self._write_plugin(
            "hello",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin

            class HelloPlugin(EchoPlugin):
                name = "hello"
                version = "1.0.0"
                description = "A test plugin"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        pi = loader.load("hello")
        assert pi is not None
        assert pi.state == PluginState.LOADED
        assert pi.manifest.name == "hello"

    def test_load_single_file_plugin(self):
        self._write_single_plugin(
            "simple",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin

            class SimplePlugin(EchoPlugin):
                name = "simple"
                version = "0.1.0"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        pi = loader.load("simple")
        assert pi is not None
        assert pi.state == PluginState.LOADED

    def test_load_all(self):
        self._write_plugin(
            "p1",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class P1(EchoPlugin):
                name = "p1"
        """,
        )
        self._write_plugin(
            "p2",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class P2(EchoPlugin):
                name = "p2"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        loaded = loader.load_all()
        assert len(loaded) == 2

    def test_start_plugin(self):
        self._write_plugin(
            "hello",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class HelloPlugin(EchoPlugin):
                name = "hello"
                def on_start(self, ctx):
                    pass
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        loader.load("hello")
        result = loader.start("hello")
        assert result is True
        assert loader.plugins["hello"].state == PluginState.STARTED

    def test_stop_plugin(self):
        self._write_plugin(
            "hello",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class HelloPlugin(EchoPlugin):
                name = "hello"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        loader.load("hello")
        loader.start("hello")
        result = loader.stop("hello")
        assert result is True
        assert loader.plugins["hello"].state == PluginState.STOPPED

    def test_unload_plugin(self):
        self._write_plugin(
            "hello",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class HelloPlugin(EchoPlugin):
                name = "hello"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        loader.load("hello")
        result = loader.unload("hello")
        assert result is True
        assert "hello" not in loader.plugins

    def test_start_all_stop_all(self):
        self._write_plugin(
            "p1",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class P1(EchoPlugin):
                name = "p1"
        """,
        )
        self._write_plugin(
            "p2",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class P2(EchoPlugin):
                name = "p2"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        loader.load_all()
        started = loader.start_all()
        assert len(started) == 2
        stopped = loader.stop_all()
        assert len(stopped) == 2

    def test_status(self):
        self._write_plugin(
            "hello",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin
            class HelloPlugin(EchoPlugin):
                name = "hello"
                version = "1.0.0"
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        loader.load("hello")
        status = loader.status()
        assert "hello" in status
        assert status["hello"]["state"] == "loaded"

    def test_load_nonexistent(self):
        loader = PluginLoader(plugin_dir=self.tmpdir)
        result = loader.load("nonexistent")
        assert result is None

    def test_load_broken_plugin(self):
        self._write_plugin(
            "broken",
            """
            raise ImportError("broken!")
        """,
        )
        loader = PluginLoader(plugin_dir=self.tmpdir)
        pi = loader.load("broken")
        assert pi is not None
        assert pi.state == PluginState.ERROR

    def test_plugin_context_integration(self):
        store = StateStore(backend=MemoryBackend())
        bus = EventBus()
        self._write_plugin(
            "stateful",
            """
            from runtime.platform.plugins.plugin_loader import EchoPlugin

            class StatefulPlugin(EchoPlugin):
                name = "stateful"
                version = "1.0.0"

                def on_load(self, ctx):
                    ctx.set_state("loaded", True)

                def on_start(self, ctx):
                    ctx.set_state("started", True)
        """,
        )
        loader = PluginLoader(
            plugin_dir=self.tmpdir,
            state_store=store,
            event_bus=bus,
        )
        loader.load("stateful")
        loader.start("stateful")
        assert store.get("loaded", namespace="plugin.stateful") is True
        assert store.get("started", namespace="plugin.stateful") is True

    def test_singleton(self):
        l1 = PluginLoader.get()
        l2 = PluginLoader.get()
        assert l1 is l2
        PluginLoader.reset()


class TestEventBusStateStoreIntegration:
    def setup_method(self):
        EventBus.reset()
        StateStore.reset()

    def teardown_method(self):
        EventBus.reset()
        StateStore.reset()

    def test_state_change_publishes_event(self):
        bus = EventBus.get()
        store = StateStore(backend=MemoryBackend())
        received = []
        bus.subscribe("state.changed", lambda e: received.append(e))
        store.set("key1", "v1")
        assert len(received) >= 1

    def test_event_bus_and_store_cross_reference(self):
        bus = EventBus()
        store = StateStore(backend=MemoryBackend())
        bus.subscribe(
            "fitness.computed",
            lambda e: store.set("last_fitness", e.combined_score, namespace="metrics"),
        )
        bus.publish(FitnessComputed(combined_score=0.85, verdict="healthy"))
        assert store.get("last_fitness", namespace="metrics") == 0.85

    def test_full_pipeline(self):
        bus = EventBus()
        store = StateStore(backend=MemoryBackend())

        fitness_events = []
        bus.subscribe("fitness.computed", lambda e: fitness_events.append(e))

        bus.publish(
            FitnessComputed(
                combined_score=0.3,
                verdict="unhealthy",
                trend="regressing",
                agent_id="agent1",
            )
        )

        store.set("agent1_fitness", 0.3, namespace="evolution")

        assert len(fitness_events) == 1
        assert fitness_events[0].combined_score == 0.3
        assert store.get("agent1_fitness", namespace="evolution") == 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])


