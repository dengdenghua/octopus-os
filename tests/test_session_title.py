"""Session-title service tests — dsh ctx.sessionTitle port."""

from __future__ import annotations

import pytest

from runtime.memory.threads.session_title import (
    SessionTitleService,
    TitleInvalidError,
    derive_fallback_title,
    normalize_title,
    register_session_title_variable,
)
from runtime.memory.threads.store import ThreadStateStore


def _thread_with_messages(store: ThreadStateStore, messages: list[dict]) -> str:
    thread = store.create(values={"messages": messages})
    return thread["thread_id"]


def test_normalize_title_collapses_and_strips() -> None:
    assert normalize_title("  hello   world \n") == "hello world"
    assert normalize_title("单行 中文   标题") == "单行 中文 标题"


def test_normalize_title_rejects_empty() -> None:
    for bad in ("", "   ", "\n\t "):
        with pytest.raises(TitleInvalidError):
            normalize_title(bad)


def test_derive_fallback_first_human_message() -> None:
    messages = [
        {"type": "ai", "content": "ignored"},
        {"type": "human", "content": "  帮我审计项目  "},
        {"type": "human", "content": "second"},
    ]
    assert derive_fallback_title(messages) == "帮我审计项目"


def test_derive_fallback_list_content() -> None:
    messages = [{"type": "human", "content": [{"type": "text", "text": "  from block "}]}]
    assert derive_fallback_title(messages) == "from block"


def test_derive_fallback_truncates_to_60() -> None:
    long_text = "长" * 80
    derived = derive_fallback_title([{"type": "human", "content": long_text}])
    assert len(derived) == 58
    assert derived.endswith("…")


def test_derive_fallback_none_without_human() -> None:
    assert derive_fallback_title([{"type": "ai", "content": "x"}]) is None
    assert derive_fallback_title([]) is None


def test_get_defaults_to_new_chat() -> None:
    store = ThreadStateStore()
    thread = store.create()
    snapshot = SessionTitleService(store).get(thread["thread_id"])
    assert snapshot.title == "New chat"
    assert snapshot.source == "fallback"
    assert snapshot.pinned is False


def test_get_lazy_fallback_from_messages() -> None:
    store = ThreadStateStore()
    thread_id = _thread_with_messages(store, [{"type": "human", "content": "审计 echo 项目"}])
    snapshot = SessionTitleService(store).get(thread_id)
    assert snapshot.title == "审计 echo 项目"
    assert snapshot.source == "fallback"


def test_rename_pins_and_persists() -> None:
    store = ThreadStateStore()
    thread = store.create(values={"title": "New chat"})
    service = SessionTitleService(store)
    snapshot = service.rename(thread["thread_id"], " 我的  手工标题 ")
    assert snapshot.title == "我的 手工标题"
    assert snapshot.source == "user"
    assert snapshot.pinned is True
    state = store.get_state(thread["thread_id"])
    assert state["values"]["title"] == "我的 手工标题"
    assert state["metadata"]["title_source"] == "user"
    assert state["metadata"]["title_pinned"] is True


def test_rename_rejects_empty() -> None:
    store = ThreadStateStore()
    thread = store.create()
    with pytest.raises(TitleInvalidError):
        SessionTitleService(store).rename(thread["thread_id"], "   ")


def test_rename_unknown_thread_raises() -> None:
    with pytest.raises(KeyError):
        SessionTitleService(ThreadStateStore()).rename("nope", "x")


def test_refresh_without_providers_uses_fallback() -> None:
    store = ThreadStateStore()
    thread_id = _thread_with_messages(store, [{"type": "human", "content": "深挖数据库性能"}])
    service = SessionTitleService(store)
    snapshot = service.refresh(thread_id)
    assert snapshot.title == "深挖数据库性能"
    assert snapshot.source == "fallback"


def test_refresh_without_providers_and_messages_keeps_current() -> None:
    store = ThreadStateStore()
    thread = store.create()
    service = SessionTitleService(store)
    snapshot = service.refresh(thread["thread_id"])
    assert snapshot.title == "New chat"


def test_refresh_runs_registered_provider_with_provenance() -> None:
    store = ThreadStateStore()
    thread = store.create()
    service = SessionTitleService(store)
    service.register_provider("llm", lambda _thread: " 生成  标题 ", model="deepseek-v4")
    snapshot = service.refresh(thread["thread_id"])
    assert snapshot.title == "生成 标题"
    assert snapshot.source == "provider"
    assert snapshot.provider == "llm"
    assert snapshot.model == "deepseek-v4"
    state = store.get_state(thread["thread_id"])
    assert state["metadata"]["title_provider"] == "llm"
    assert state["metadata"]["title_model"] == "deepseek-v4"


def test_refresh_chooses_named_provider() -> None:
    store = ThreadStateStore()
    thread = store.create()
    service = SessionTitleService(store)
    service.register_provider("a", lambda _t: "from-a")
    service.register_provider("b", lambda _t: "from-b")
    snapshot = service.refresh(thread["thread_id"], provider="b")
    assert snapshot.title == "from-b"
    assert snapshot.provider == "b"


def test_refresh_unknown_provider_raises() -> None:
    store = ThreadStateStore()
    thread = store.create()
    with pytest.raises(KeyError):
        SessionTitleService(store).refresh(thread["thread_id"], provider="missing")


def test_refresh_provider_none_keeps_current() -> None:
    store = ThreadStateStore()
    thread = store.create(values={"title": "keep me"})
    service = SessionTitleService(store)
    service.register_provider("empty", lambda _t: None)
    snapshot = service.refresh(thread["thread_id"])
    assert snapshot.title == "keep me"


def test_refresh_provider_exception_keeps_current() -> None:
    store = ThreadStateStore()
    thread = store.create(values={"title": "stable"})
    service = SessionTitleService(store)

    def _boom(_thread: dict) -> str:
        raise RuntimeError("provider down")

    service.register_provider("boom", _boom)
    snapshot = service.refresh(thread["thread_id"])
    assert snapshot.title == "stable"


def test_refresh_respects_pin_without_force() -> None:
    store = ThreadStateStore()
    thread = store.create()
    service = SessionTitleService(store)
    service.rename(thread["thread_id"], "pinned name")
    service.register_provider("llm", lambda _t: "auto name")
    snapshot = service.refresh(thread["thread_id"])
    assert snapshot.title == "pinned name"
    assert snapshot.source == "user"


def test_refresh_force_overrides_pin() -> None:
    store = ThreadStateStore()
    thread = store.create()
    service = SessionTitleService(store)
    service.rename(thread["thread_id"], "pinned name")
    service.register_provider("llm", lambda _t: "auto name")
    snapshot = service.refresh(thread["thread_id"], force=True)
    assert snapshot.title == "auto name"
    assert snapshot.source == "provider"
    assert snapshot.pinned is False


def test_provider_registry_duplicate_and_dispose() -> None:
    store = ThreadStateStore()
    service = SessionTitleService(store)
    service.register_provider("a", lambda _t: "x")
    with pytest.raises(ValueError):
        service.register_provider("a", lambda _t: "y")
    unregister = service.register_provider("b", lambda _t: "z")
    unregister()
    assert service.provider_names() == ["a"]


def test_get_returns_recorded_provider_title() -> None:
    store = ThreadStateStore()
    thread = store.create()
    service = SessionTitleService(store)
    service.register_provider("llm", lambda _t: "persisted title")
    service.refresh(thread["thread_id"])
    fresh = SessionTitleService(store)
    snapshot = fresh.get(thread["thread_id"])
    assert snapshot.title == "persisted title"
    assert snapshot.source == "provider"
    assert snapshot.provider == "llm"


def test_snapshot_to_wire_shape() -> None:
    store = ThreadStateStore()
    thread = store.create()
    snapshot = SessionTitleService(store).rename(thread["thread_id"], "wire me")
    wire = snapshot.to_wire()
    assert wire["title"] == "wire me"
    assert wire["source"] == "user"
    assert wire["pinned"] is True
    assert set(wire) == {"title", "source", "pinned", "provider", "model", "updated_at"}


def test_prompt_variable_renders_session_title() -> None:
    from runtime.platform.prompts.registry import PromptRegistry

    store = ThreadStateStore()
    thread_id = store.create(values={"messages": [{"type": "human", "content": "审计项目性能"}]})[
        "thread_id"
    ]
    registry = PromptRegistry("/tmp/echo-test-prompts-empty")
    registry.register_section(
        "identity", order=-100, text="You are working on: {{ session_title }}"
    )
    register_session_title_variable(registry, store_getter=lambda: store)

    from runtime.platform.process.session import Session, _current_session

    token = _current_session.set(Session(thread_id=thread_id))
    try:
        assert registry.assemble() == "You are working on: 审计项目性能"
    finally:
        _current_session.reset(token)


def test_prompt_variable_falls_back_without_session() -> None:
    from runtime.platform.prompts.registry import PromptRegistry

    registry = PromptRegistry("/tmp/echo-test-prompts-empty-2")
    registry.register_section("identity", order=-100, text="T={{ session_title }}")
    register_session_title_variable(registry, store_getter=lambda: None)
    assert registry.assemble() == "T=New chat"


def test_maybe_auto_refresh_runs_provider_once() -> None:
    store = ThreadStateStore()
    thread_id = _thread_with_messages(store, [{"type": "human", "content": "帮我优化构建"}])
    service = SessionTitleService(store)
    calls: list[str] = []
    service.register_provider(
        "llm",
        lambda thread: calls.append("x") or "优化构建流程",
        model="m1",
    )
    snapshot = service.maybe_auto_refresh(thread_id)
    assert snapshot.title == "优化构建流程"
    assert snapshot.source == "provider"
    assert snapshot.provider == "llm"
    # Second turn snapshot: provider is not consulted again.
    service.maybe_auto_refresh(thread_id)
    assert len(calls) == 1


def test_maybe_auto_refresh_respects_pin() -> None:
    store = ThreadStateStore()
    thread = store.create(values={"messages": [{"type": "human", "content": "h"}]})
    service = SessionTitleService(store)
    service.rename(thread["thread_id"], "手工标题")
    calls: list[str] = []
    service.register_provider("llm", lambda thread: calls.append("x") or "不该出现")
    snapshot = service.maybe_auto_refresh(thread["thread_id"])
    assert snapshot.title == "手工标题"
    assert snapshot.pinned is True
    assert calls == []


def test_maybe_auto_refresh_without_provider_is_noop() -> None:
    store = ThreadStateStore()
    thread_id = _thread_with_messages(store, [{"type": "human", "content": "h"}])
    service = SessionTitleService(store)
    snapshot = service.maybe_auto_refresh(thread_id)
    assert snapshot.source == "fallback"
    state = store.get_state(thread_id)
    assert state["metadata"].get("title_auto_attempted") is None


def test_maybe_auto_refresh_failed_provider_not_retried() -> None:
    store = ThreadStateStore()
    thread_id = _thread_with_messages(store, [{"type": "human", "content": "h"}])
    service = SessionTitleService(store)
    calls: list[str] = []
    service.register_provider(
        "llm",
        lambda thread: calls.append("x") or None,
        model="m1",
    )
    first = service.maybe_auto_refresh(thread_id)
    assert first.source == "fallback"  # failure keeps the current title
    service.maybe_auto_refresh(thread_id)
    assert len(calls) == 1  # marked attempted, no retry on next turn
    state = store.get_state(thread_id)
    assert state["metadata"]["title_auto_attempted"] is True


def test_maybe_auto_refresh_force_via_refresh_still_works() -> None:
    store = ThreadStateStore()
    thread_id = _thread_with_messages(store, [{"type": "human", "content": "h"}])
    service = SessionTitleService(store)
    service.register_provider("llm", lambda thread: "一次标题", model="m1")
    service.maybe_auto_refresh(thread_id)
    # Explicit refresh (e.g. the HTTP endpoint) still regenerates.
    snapshot = service.refresh(thread_id)
    assert snapshot.title == "一次标题"
    assert snapshot.source == "provider"

