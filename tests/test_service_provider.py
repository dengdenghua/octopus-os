"""Tests for ServiceProvider dependency injection container."""

import pytest

from runtime.platform.process.service_provider import (
    ServiceProvider,
    get_provider,
    reset_provider_for_tests,
)


class TestServiceProvider:
    def test_register_and_get_instance(self):
        provider = ServiceProvider()
        provider.register_instance("test_service", "hello")
        assert provider.get("test_service") == "hello"

    def test_register_and_get_factory(self):
        provider = ServiceProvider()
        provider.register_factory("test_factory", lambda: {"key": "value"})
        result = provider.get("test_factory")
        assert result == {"key": "value"}

    def test_factory_only_called_once(self):
        provider = ServiceProvider()
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return f"instance_{call_count}"

        provider.register_factory("singleton", factory)
        first = provider.get("singleton")
        second = provider.get("singleton")
        assert first == "instance_1"
        assert second == "instance_1"
        assert call_count == 1

    def test_instance_overrides_factory(self):
        provider = ServiceProvider()
        provider.register_factory("key", lambda: "factory_value")
        provider.register_instance("key", "instance_value")
        assert provider.get("key") == "instance_value"

    def test_factory_overrides_instance(self):
        provider = ServiceProvider()
        provider.register_instance("key", "instance_value")
        provider.register_factory("key", lambda: "factory_value")
        assert provider.get("key") == "factory_value"

    def test_get_with_default(self):
        provider = ServiceProvider()
        assert provider.get("missing") is None
        assert provider.get("missing", default="fallback") == "fallback"

    def test_require_raises_when_missing(self):
        provider = ServiceProvider()
        with pytest.raises(KeyError, match="Service 'missing' not registered"):
            provider.require("missing")

    def test_require_returns_when_present(self):
        provider = ServiceProvider()
        provider.register_instance("present", 42)
        assert provider.require("present") == 42

    def test_has(self):
        provider = ServiceProvider()
        assert not provider.has("key")
        provider.register_instance("key", "value")
        assert provider.has("key")

    def test_has_factory(self):
        provider = ServiceProvider()
        provider.register_factory("key", lambda: "value")
        assert provider.has("key")
        assert provider.get("key") == "value"

    def test_unregister(self):
        provider = ServiceProvider()
        provider.register_instance("key", "value")
        assert provider.has("key")
        provider.unregister("key")
        assert not provider.has("key")
        assert provider.get("key") is None

    def test_clear(self):
        provider = ServiceProvider()
        provider.register_instance("a", 1)
        provider.register_instance("b", 2)
        provider.clear()
        assert not provider.has("a")
        assert not provider.has("b")

    def test_thread_safety(self):
        import threading

        provider = ServiceProvider()
        results = []

        def worker():
            for i in range(100):
                provider.register_instance(f"key_{i}", f"value_{i}")
                results.append(provider.get(f"key_{i}"))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1000
        assert all(r is not None for r in results)


class TestGlobalProvider:
    def test_get_provider_singleton(self):
        p1 = get_provider()
        p2 = get_provider()
        assert p1 is p2

    def test_reset_provider(self):
        p1 = get_provider()
        p1.register_instance("test", "value")
        reset_provider_for_tests()
        p2 = get_provider()
        assert p1 is not p2
        assert not p2.has("test")
