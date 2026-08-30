from __future__ import annotations

from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter
from runtime.sensing.model_router.models import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelStreamEvent,
)


class _Unavailable(ModelRouter):
    def call(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("http_402: insufficient_balance")

    def call_stream(self, request: ModelRequest):
        raise RuntimeError("http_402: insufficient_balance")
        yield  # pragma: no cover


class _Healthy(ModelRouter):
    def __init__(self) -> None:
        self.models: list[str] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.models.append(request.model)
        return ModelResponse(text="recovered")

    def call_stream(self, request: ModelRequest):
        self.models.append(request.model)
        yield ModelStreamEvent(type="text_delta", delta="recovered")
        yield ModelStreamEvent(type="done", final=ModelResponse(text="recovered"))


def _request() -> ModelRequest:
    return ModelRequest(
        model="unfunded",
        messages=[Message(role="user", content="fix code")],
    )


def _router() -> tuple[ModelDispatchRouter, _Healthy]:
    unavailable = _Unavailable()
    healthy = _Healthy()
    router = ModelDispatchRouter(fallback=unavailable)
    router.register("unfunded-entry", unavailable)
    router.register("unfunded", unavailable)
    router.register("healthy", healthy)
    return router, healthy


def test_named_provider_balance_error_falls_back_for_call() -> None:
    router, healthy = _router()

    response = router.call(_request())

    assert response.text == "recovered"
    assert response.model == "healthy"
    assert healthy.models == ["healthy"]


def test_named_provider_balance_error_falls_back_for_stream() -> None:
    router, healthy = _router()

    events = list(router.call_stream(_request()))

    assert [event.type for event in events] == ["text_delta", "done"]
    assert events[-1].final is not None
    assert events[-1].final.model == "healthy"
    assert healthy.models == ["healthy"]


def test_explicit_picker_selection_does_not_silently_change_provider_for_call() -> None:
    unavailable = _Unavailable()
    healthy = _Healthy()
    selection_id = "echo-custom-model:v1:explicit-selection"
    router = ModelDispatchRouter(fallback=healthy)
    router.register(selection_id, unavailable)
    router.register("healthy", healthy)

    request = ModelRequest(
        model=selection_id,
        messages=[Message(role="user", content="fix code")],
    )
    try:
        router.call(request)
    except RuntimeError as exc:
        assert "http_402" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("explicit picker selection must surface its provider error")
    assert healthy.models == []


def test_explicit_picker_selection_does_not_silently_change_provider_for_stream() -> None:
    unavailable = _Unavailable()
    healthy = _Healthy()
    selection_id = "echo-custom-model:v1:explicit-selection"
    router = ModelDispatchRouter(fallback=healthy)
    router.register(selection_id, unavailable)
    router.register("healthy", healthy)

    request = ModelRequest(
        model=selection_id,
        messages=[Message(role="user", content="fix code")],
    )
    try:
        list(router.call_stream(request))
    except RuntimeError as exc:
        assert "http_402" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("explicit picker selection must surface its provider error")
    assert healthy.models == []


def test_partial_stream_never_replays_on_another_provider() -> None:
    class _Partial(ModelRouter):
        def call(self, request: ModelRequest) -> ModelResponse:
            raise NotImplementedError

        def call_stream(self, request: ModelRequest):
            yield ModelStreamEvent(type="text_delta", delta="partial")
            raise RuntimeError("http_402: insufficient_balance")

    healthy = _Healthy()
    router = ModelDispatchRouter(fallback=_Partial())
    router.register("unfunded", router._fallback)
    router.register("healthy", healthy)

    try:
        list(router.call_stream(_request()))
    except RuntimeError as exc:
        assert "http_402" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("partial stream must surface the original failure")
    assert healthy.models == []


def test_provider_rescue_prefers_pro_over_earlier_chat_route() -> None:
    unavailable = _Unavailable()
    chat = _Healthy()
    pro = _Healthy()
    router = ModelDispatchRouter(fallback=unavailable)
    router.register("unfunded", unavailable)
    router.register("deepseek-chat", chat)
    router.register("deepseek-v4-flash", _Healthy())
    router.register("deepseek-v4-pro", pro)

    response = router.call(_request())

    assert response.text == "recovered"
    assert chat.models == []
    assert pro.models == ["deepseek-v4-pro"]


class _Defaulted(ModelRouter):
    """Fallback that advertises a default_model and records what it saw."""

    def __init__(self, default_model: str) -> None:
        self.default_model = default_model
        self.models: list[str] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.models.append(request.model)
        return ModelResponse(text="ok", model=request.model)

    def call_stream(self, request: ModelRequest):
        self.models.append(request.model)
        yield ModelStreamEvent(type="text_delta", delta="ok")
        yield ModelStreamEvent(
            type="done",
            final=ModelResponse(text="ok", model=request.model),
        )


def test_unrouted_model_degrades_to_fallback_default_for_call() -> None:
    """An unregistered model name must not be forwarded verbatim to the
    fallback provider (which would 400); it degrades to the fallback default."""
    fallback = _Defaulted("deepseek-v4-flash")
    served = _Healthy()
    router = ModelDispatchRouter(fallback=fallback)
    router.register("deepseek-v4-flash", served)

    response = router.call(
        ModelRequest(model="claude-opus", messages=[Message(role="user", content="hi")])
    )

    assert response.text == "ok"
    # The literal unconfigured name never reaches the provider; the served
    # default does instead.
    assert fallback.models == ["deepseek-v4-flash"]


def test_unrouted_model_degrades_to_fallback_default_for_stream() -> None:
    fallback = _Defaulted("deepseek-v4-flash")
    served = _Healthy()
    router = ModelDispatchRouter(fallback=fallback)
    router.register("deepseek-v4-flash", served)

    events = list(
        router.call_stream(
            ModelRequest(model="claude-opus", messages=[Message(role="user", content="hi")])
        )
    )

    assert events[-1].final is not None
    assert fallback.models == ["deepseek-v4-flash"]


def test_routed_model_is_never_rewritten() -> None:
    """A registered model keeps its name; only unrouted models degrade."""
    fallback = _Defaulted("deepseek-v4-flash")
    served = _Healthy()
    router = ModelDispatchRouter(fallback=fallback)
    router.register("deepseek-v4-flash", served)

    router.call(
        ModelRequest(model="deepseek-v4-flash", messages=[Message(role="user", content="hi")])
    )

    assert served.models == ["deepseek-v4-flash"]
    assert fallback.models == []

