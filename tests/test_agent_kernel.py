from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from runtime.kernel import AgentKernel
from runtime.platform.config import AgentConfig


def _stack() -> SimpleNamespace:
    return SimpleNamespace(
        config=AgentConfig(),
        registry=object(),
        journal=object(),
        planner=object(),
        executor=object(),
        runtime=object(),
    )


def test_from_config_builds_and_exposes_the_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack()
    config = AgentConfig()
    monkeypatch.setattr("runtime.kernel.kernel.build_from_config", lambda value: stack)

    kernel = AgentKernel.from_config(config)

    assert kernel.config is config
    assert kernel.stack is stack
    assert kernel.registry is stack.registry
    assert kernel.journal is stack.journal
    assert kernel.planner is stack.planner
    assert kernel.executor is stack.executor
    assert kernel.graph_runtime is stack.runtime


def test_build_is_the_host_facing_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = _stack()
    config = AgentConfig()
    monkeypatch.setattr("runtime.kernel.kernel.build_from_config", lambda value: stack)

    assert AgentKernel.build(config).stack is stack


def test_kernel_is_reachable_from_runtime_namespace() -> None:
    from runtime import kernel as runtime_kernel

    assert runtime_kernel.AgentKernel is AgentKernel


def test_from_config_rejects_untyped_host_config() -> None:
    with pytest.raises(TypeError, match="AgentConfig"):
        AgentKernel.from_config(object())  # type: ignore[arg-type]


def test_create_realtime_runtime_uses_kernel_stack_and_local_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, **kwargs: object) -> None:
            calls.update(kwargs)

        async def handle_request(self, method: str, params: dict, emitter: object) -> dict:
            return {"method": method, "params": params, "emitter": emitter}

    import runtime.sensing.gateway.realtime_cerebrum as realtime_module

    monkeypatch.setattr(realtime_module, "CerebrumRuntime", FakeRuntime)
    stack = _stack()
    kernel = AgentKernel.from_stack(stack)

    runtime = kernel.create_realtime_runtime(agent_registry="agents")

    assert isinstance(runtime, FakeRuntime)
    assert calls["stack"] is stack
    assert calls["agent_registry"] == "agents"
    assert str(calls["logs_root"]).endswith("/threads")
    assert str(calls["workspace_root"]).endswith("/workspaces")
    assert kernel.realtime_runtime is runtime


def test_create_realtime_runtime_rejects_a_different_stack() -> None:
    kernel = AgentKernel.from_stack(_stack())

    with pytest.raises(ValueError, match="this kernel's stack"):
        kernel.create_realtime_runtime(stack=object())


def test_handle_request_lazily_creates_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRuntime:
        async def handle_request(self, method: str, params: dict, emitter: object) -> dict:
            return {"method": method, "params": params, "emitter": emitter}

    kernel = AgentKernel.from_stack(_stack())
    monkeypatch.setattr(kernel, "create_realtime_runtime", lambda: FakeRuntime())

    result = asyncio.run(kernel.handle_request("ping", {"x": 1}, "emit"))

    assert result == {"method": "ping", "params": {"x": 1}, "emitter": "emit"}


def test_close_is_idempotent_and_rejects_new_runtime() -> None:
    calls: list[str] = []
    stack = _stack()
    stack.close_mcp_clients = lambda: calls.append("closed")
    kernel = AgentKernel.from_stack(stack)

    kernel.close()
    kernel.close()

    assert calls == ["closed"]
    with pytest.raises(RuntimeError, match="closed"):
        kernel.create_realtime_runtime()


def test_aclose_drains_before_closing_and_closes_after_drain_failure() -> None:
    calls: list[object] = []

    class Runtime:
        async def drain_active_turns_for_shutdown(self, *, timeout_seconds: float) -> None:
            calls.append(("drain", timeout_seconds))
            raise RuntimeError("drain failed")

    stack = _stack()
    stack.close_mcp_clients = lambda: calls.append("closed")
    kernel = AgentKernel.from_stack(stack)
    kernel._realtime_runtime = Runtime()

    with pytest.raises(RuntimeError, match="drain failed"):
        asyncio.run(kernel.aclose(timeout_seconds=1.25))

    assert calls == [("drain", 1.25), "closed"]
    assert kernel.closed is True

