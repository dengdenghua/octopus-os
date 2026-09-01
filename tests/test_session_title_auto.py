"""Auto-title regeneration wiring — dsh auto-title after the first turn."""

from __future__ import annotations

from typing import Any

from runtime.memory.threads.session_title import SessionTitleService
from runtime.memory.threads.store import ThreadStateStore
from runtime.protocol import Turn, TurnStatus
from runtime.sensing.gateway._realtime_cerebrum_thread import _snapshot_to_thread_store


class _Log:
    def __init__(
        self,
        thread_id: str | None = None,
        status: TurnStatus = TurnStatus.COMPLETED,
    ) -> None:
        self._turns = [Turn(threadId=thread_id, status=status)] if thread_id else []

    def replay(self) -> list[Any]:
        return self._turns


class _Runtime:
    def __init__(self, store: Any) -> None:
        self._thread_store = store


def test_snapshot_triggers_auto_title_once() -> None:
    store = ThreadStateStore()
    service = SessionTitleService(store)
    calls: list[str] = []
    service.register_provider(
        "llm",
        lambda thread: calls.append(str(thread["thread_id"])) or "自动标题",
        model="m1",
    )

    thread_id = "th-auto-title"
    completed_log = _Log(thread_id)
    _snapshot_to_thread_store(
        _Runtime(store),
        thread_id,
        completed_log,
        None,
        session_titles=service,
    )
    assert calls == [thread_id]
    state = store.get_state(thread_id)
    assert state["values"]["title"] == "自动标题"
    assert state["metadata"]["title_source"] == "provider"
    assert state["metadata"]["title_auto_attempted"] is True

    # A second turn snapshot (e.g. failed/interrupted) never re-invokes it.
    _snapshot_to_thread_store(
        _Runtime(store),
        thread_id,
        completed_log,
        None,
        session_titles=service,
    )
    assert calls == [thread_id]


def test_cancelled_snapshot_does_not_start_or_consume_auto_title() -> None:
    store = ThreadStateStore()
    service = SessionTitleService(store)
    calls: list[str] = []
    service.register_provider(
        "llm",
        lambda thread: calls.append(str(thread["thread_id"])) or "不应生成",
        model="m1",
    )

    thread_id = "th-cancelled-before-title"
    _snapshot_to_thread_store(
        _Runtime(store),
        thread_id,
        _Log(thread_id, TurnStatus.CANCELLED),
        None,
        session_titles=service,
    )

    assert calls == []
    state = store.get_state(thread_id)
    assert store.get(thread_id)["status"] == "cancelled"
    assert state["metadata"].get("title_auto_attempted") is None


def test_snapshot_without_service_keeps_legacy_behavior() -> None:
    store = ThreadStateStore()
    thread_id = "th-legacy"
    _snapshot_to_thread_store(_Runtime(store), thread_id, _Log(), None)
    state = store.get_state(thread_id)
    assert state["values"]["title"] == ""
    assert "title_source" not in state["metadata"]


def test_runtime_wrapper_passes_service_through() -> None:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    store = ThreadStateStore()
    service = SessionTitleService(store)
    calls: list[str] = []
    service.register_provider(
        "llm",
        lambda thread: calls.append(str(thread["thread_id"])) or "包装标题",
        model="m1",
    )
    runtime = CerebrumRuntime(stack=None, thread_store=store, session_titles=service)

    thread_id = "th-wrapped"
    runtime._snapshot_to_thread_store(thread_id, _Log(thread_id), None)
    assert calls == [thread_id]
    state = store.get_state(thread_id)
    assert state["values"]["title"] == "包装标题"


class _FakeRouter:
    def __init__(self, text: str | None = "生成的标题") -> None:
        self.text = text
        self.calls: list[Any] = []

    def call(self, request: Any) -> Any:
        self.calls.append(request)
        return type("Resp", (), {"text": self.text})()


def test_build_auto_title_service_without_store() -> None:
    from runtime.sensing.gateway.thread_state_router import build_auto_title_service

    assert build_auto_title_service(None, model_router=_FakeRouter()) is None


def test_build_auto_title_service_without_router_has_no_provider() -> None:
    from runtime.sensing.gateway.thread_state_router import build_auto_title_service

    store = ThreadStateStore()
    service = build_auto_title_service(store)
    assert service is not None
    assert service.provider_names() == []


def test_build_auto_title_service_registers_llm_provider() -> None:
    from runtime.sensing.gateway.thread_state_router import build_auto_title_service

    store = ThreadStateStore()
    router = _FakeRouter(text="构建流程优化")
    service = build_auto_title_service(store, model_router=router)
    assert service is not None
    assert service.provider_names() == ["llm"]

    thread = store.create(values={"messages": [{"type": "human", "content": "帮我优化构建流程"}]})
    snapshot = service.maybe_auto_refresh(thread["thread_id"])
    assert snapshot.title == "构建流程优化"
    assert snapshot.source == "provider"
    assert router.calls and "帮我优化构建流程" in router.calls[0].messages[0].content


def test_build_auto_title_provider_without_human_message() -> None:
    from runtime.sensing.gateway.thread_state_router import build_auto_title_service

    store = ThreadStateStore()
    router = _FakeRouter(text="不该出现")
    service = build_auto_title_service(store, model_router=router)
    assert service is not None

    thread = store.create(values={"messages": [{"type": "ai", "content": "hi"}]})
    snapshot = service.maybe_auto_refresh(thread["thread_id"])
    assert snapshot.title == "New chat"
    assert router.calls == []

