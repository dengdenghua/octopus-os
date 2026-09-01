"""Vision guard: stop feeding images to models that can't see them.

Covers the pure helpers in ``runtime.sensing.model_router.vision_guard``
(detection, pre-guard, strip/transcribe, rejection classifier) and their
wiring into ``ModelDispatchRouter`` (pre-guard before the upstream call,
crash recovery on an image 4xx, and rescue-path guarding).

``model_supports_vision`` is monkeypatched directly — the flag's own
resolution from ``custom_models.json`` is covered by the config-endpoint
and capability tests.
"""

from __future__ import annotations

import pytest

from runtime.sensing.model_router import vision_guard as vg
from runtime.sensing.model_router.anthropic_router import _split_system
from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter
from runtime.sensing.model_router.models import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelStreamEvent,
)


def _req(
    *,
    model: str = "m",
    images_b64: list[str] | None = None,
    messages: list[Message] | None = None,
) -> ModelRequest:
    msgs = messages if messages is not None else [Message(role="user", content="hi")]
    return ModelRequest(model=model, messages=msgs, images_b64=images_b64 or [])


def _inline(blocks: list[dict]) -> Message:
    return Message(role="user", content=blocks)


def _vision_mode(monkeypatch: pytest.MonkeyPatch, mode: bool | None) -> None:
    """Pin ``model_supports_vision``: True / False / None (undeclared)."""

    monkeypatch.setattr(vg, "model_supports_vision", lambda model: mode)


def _mock_agnes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reply: str | None = None,
) -> list[tuple]:
    """Replace the bundled agnes ``describe_image`` with a recorder.

    Defaults to returning None (agnes unavailable → strip-with-note) so
    dispatcher tests never touch the network. Returns the recorded calls.
    """

    calls: list[tuple] = []

    def fake(
        image_b64: str = "", image_url: str = "", timeout: int = 10, **_kw: object
    ) -> str | None:
        calls.append((image_b64 or image_url, timeout))
        return reply

    monkeypatch.setattr(
        "runtime.platform.plugins.bundled.whale_eye.service.describe_image",
        fake,
    )
    return calls


# ═══════════════════════════════════════════════════════════
# pure helpers · detection + classifier
# ═══════════════════════════════════════════════════════════


def test_request_has_images_b64_channel() -> None:
    assert vg.request_has_images(_req(images_b64=["AAAA"])) is True


def test_request_has_images_inline_openai_block() -> None:
    req = _req(
        messages=[
            _inline([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
        ]
    )
    assert vg.request_has_images(req) is True


def test_request_has_images_inline_anthropic_block() -> None:
    req = _req(
        messages=[
            _inline(
                [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
                    }
                ]
            )
        ]
    )
    assert vg.request_has_images(req) is True


def test_request_has_images_false_for_plain_text() -> None:
    assert vg.request_has_images(_req()) is False
    assert (
        vg.request_has_images(
            _req(messages=[Message(role="user", content=[{"type": "text", "text": "x"}])])
        )
        is False
    )


def test_classify_image_rejection_covers_every_router_format() -> None:
    assert vg.classify_image_rejection(RuntimeError("http_400: bad")) is True
    assert vg.classify_image_rejection(RuntimeError("Error code: 400 - image rejected")) is True
    assert vg.classify_image_rejection(RuntimeError("oct gateway HTTP 400: no")) is True
    assert vg.classify_image_rejection(RuntimeError("http_422: unprocessable")) is True
    # not an image rejection → must NOT trigger a strip-and-retry
    assert vg.classify_image_rejection(RuntimeError("http_429: rate limited")) is False
    assert vg.classify_image_rejection(RuntimeError("timeout reading response")) is False
    assert vg.classify_image_rejection(RuntimeError("")) is False


# ═══════════════════════════════════════════════════════════
# pure helpers · pre-guard + strip/transcribe
# ═══════════════════════════════════════════════════════════


def test_apply_vision_guard_strips_known_non_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    _vision_mode(monkeypatch, False)
    _mock_agnes(monkeypatch)
    req = _req(
        images_b64=["AAAA"],
        messages=[
            _inline(
                [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
                    {"type": "text", "text": "look"},
                ]
            )
        ],
    )
    guarded = vg.apply_vision_guard(req)
    assert vg.request_has_images(guarded) is False
    assert guarded.images_b64 == []
    blocks = guarded.messages[-1].content
    assert isinstance(blocks, list)
    # inline image → text note, text preserved, b64 screenshot → trailing note
    assert [b.get("type") for b in blocks] == ["text", "text", "text"]
    assert "图片已移除" in blocks[0]["text"]
    assert blocks[1]["text"] == "look"
    assert "图片已移除" in blocks[2]["text"]


def test_apply_vision_guard_passthrough_for_vision_and_undeclared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for mode in (True, None):
        _vision_mode(monkeypatch, mode)
        req = _req(images_b64=["AAAA"])
        guarded = vg.apply_vision_guard(req)
        assert guarded is req  # untouched → rely on crash recovery


def test_apply_vision_guard_skips_config_lookup_for_imageless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(model: str) -> None:
        raise AssertionError("config lookup must not run for an imageless request")

    monkeypatch.setattr(vg, "model_supports_vision", boom)
    req = _req()
    assert vg.apply_vision_guard(req) is req


def test_transcribe_appends_b64_description_to_last_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, False)
    calls = _mock_agnes(monkeypatch, reply="这是一张截图")
    req = _req(
        images_b64=["AAAA"],
        messages=[Message(role="system", content="sys"), Message(role="user", content="hello")],
    )
    guarded = vg.apply_vision_guard(req)
    assert guarded.images_b64 == []
    assert "这是一张截图" in guarded.messages[-1].content
    assert calls == [("AAAA", 10)]  # short guard timeout, base64 channel


def test_transcribe_inline_url_uses_url_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _vision_mode(monkeypatch, False)
    calls = _mock_agnes(monkeypatch, reply="一张图")
    req = _req(
        messages=[
            _inline([{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}])
        ]
    )
    guarded = vg.apply_vision_guard(req)
    assert not vg.request_has_images(guarded)
    assert calls == [("https://example.com/a.png", 10)]


def test_transcription_budget_is_global_across_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six images (1 on b64 + 5 inline) → at most four agnes calls."""
    _vision_mode(monkeypatch, False)
    calls = _mock_agnes(monkeypatch, reply="x")
    blocks = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{i}"}} for i in range(5)
    ]
    req = _req(images_b64=["Z0"], messages=[_inline(blocks)])
    guarded = vg.build_without_images(req)
    assert len(calls) == 4
    assert vg.request_has_images(guarded) is False


def test_image_only_message_keeps_a_text_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stripping an image-only user message must not leave empty content —
    an empty list 400s on the anthropic path and silently drops on openai."""
    _vision_mode(monkeypatch, False)
    _mock_agnes(monkeypatch, reply=None)  # agnes unavailable → strip with note
    req = _req(
        messages=[
            _inline([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
        ]
    )
    guarded = vg.build_without_images(req)
    content = guarded.messages[0].content
    assert isinstance(content, list) and content
    assert content[0]["type"] == "text"
    assert "图片已移除" in content[0]["text"]


# ═══════════════════════════════════════════════════════════
# dispatch integration · pre-guard + crash recovery
# ═══════════════════════════════════════════════════════════


class _Health(ModelRouter):
    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(text="ok")

    def call_stream(self, request: ModelRequest):
        self.calls.append(request)
        yield ModelStreamEvent(type="text_delta", delta="ok")
        yield ModelStreamEvent(type="done", final=ModelResponse(text="ok"))


class _VisionRejecting(ModelRouter):
    """Rejects image-bearing requests with a 400; serves text-only ones."""

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if vg.request_has_images(request):
            raise RuntimeError("Error code: 400 - image content not accepted")
        return ModelResponse(text="text-only reply")

    def call_stream(self, request: ModelRequest):
        self.calls.append(request)
        if vg.request_has_images(request):
            raise RuntimeError("http_400: image content not accepted")
        yield ModelStreamEvent(type="text_delta", delta="text-only reply")
        yield ModelStreamEvent(type="done", final=ModelResponse(text="text-only reply"))


def _bind(router: ModelDispatchRouter, upstream: ModelRouter) -> ModelDispatchRouter:
    router.register("m", upstream)
    return router


def test_pre_guard_known_non_vision_never_sends_an_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, False)
    _mock_agnes(monkeypatch)
    upstream = _Health()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    resp = router.call(_req(images_b64=["AAAA"]))

    assert resp.text == "ok"
    assert all(not vg.request_has_images(c) for c in upstream.calls)


def test_crash_recovery_unknown_model_strips_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)
    _mock_agnes(monkeypatch)
    upstream = _VisionRejecting()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    resp = router.call(_req(images_b64=["AAAA"]))

    assert resp.text == "text-only reply"
    assert len(upstream.calls) == 2
    assert vg.request_has_images(upstream.calls[0]) is True  # first call carried the image
    assert vg.request_has_images(upstream.calls[1]) is False  # retry was stripped


def test_crash_recovery_never_masks_when_strip_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)
    _mock_agnes(monkeypatch)

    class _Always4xx(_VisionRejecting):
        def call(self, request: ModelRequest) -> ModelResponse:
            self.calls.append(request)
            raise RuntimeError("Error code: 400 - nope")

    upstream = _Always4xx()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    with pytest.raises(RuntimeError) as ei:
        router.call(_req(images_b64=["AAAA"]))
    # the ORIGINAL image rejection surfaces, not a second, confusing error
    assert "400" in str(ei.value)
    assert len(upstream.calls) == 2


def test_imageless_400_is_not_mistaken_for_an_image_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)
    calls: list[ModelRequest] = []

    class _ThinkingReject(ModelRouter):
        def call(self, request: ModelRequest) -> ModelResponse:
            calls.append(request)
            raise RuntimeError("http_400: thinking not supported")

        def call_stream(self, request: ModelRequest):
            calls.append(request)
            raise RuntimeError("http_400: thinking not supported")
            yield  # pragma: no cover

    upstream = _ThinkingReject()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    with pytest.raises(RuntimeError):
        router.call(_req())
    assert len(calls) == 1  # no wasted strip-and-retry on an imageless 400


def test_vision_model_images_are_delivered_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, True)
    upstream = _Health()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    router.call(_req(images_b64=["AAAA"]))

    assert len(upstream.calls) == 1
    assert upstream.calls[0].images_b64 == ["AAAA"]


def test_stream_recovery_retries_only_when_nothing_yielded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)
    _mock_agnes(monkeypatch)
    upstream = _VisionRejecting()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    events = list(router.call_stream(_req(images_b64=["AAAA"])))

    assert [e.type for e in events] == ["text_delta", "done"]
    assert len(upstream.calls) == 2


def test_stream_partial_output_never_replays_on_stripped_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)

    class _Partial(ModelRouter):
        def call(self, request: ModelRequest) -> ModelResponse:
            raise NotImplementedError

        def call_stream(self, request: ModelRequest):
            yield ModelStreamEvent(type="text_delta", delta="partial")
            raise RuntimeError("http_400: image rejected")

    upstream = _Partial()
    router = _bind(ModelDispatchRouter(fallback=upstream), upstream)

    with pytest.raises(RuntimeError) as ei:
        list(router.call_stream(_req(images_b64=["AAAA"])))
    assert "400" in str(ei.value)


def test_rescue_path_pre_guards_known_non_vision_rescue_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, False)  # both primary and rescue known non-vision
    _mock_agnes(monkeypatch)

    class _Busy(ModelRouter):
        def call(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("http_429: rate limited")

        def call_stream(self, request: ModelRequest):
            raise RuntimeError("http_429: rate limited")
            yield  # pragma: no cover

    busy = _Busy()
    healthy = _Health()
    router = ModelDispatchRouter(fallback=busy)
    router.register("busy", busy)
    router.register("rescue-model", healthy)

    resp = router.call(_req(model="busy", images_b64=["AAAA"]))

    assert resp.text == "ok"
    # the rescue model (known non-vision) never saw a raw image
    assert all(not vg.request_has_images(c) for c in healthy.calls)


def test_rescue_path_recovers_when_unknown_rescue_model_rejects_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)  # rescue model undeclared → still rejects
    _mock_agnes(monkeypatch)

    class _Busy(ModelRouter):
        def call(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("http_429: rate limited")

        def call_stream(self, request: ModelRequest):
            raise RuntimeError("http_429: rate limited")
            yield  # pragma: no cover

    rescue = _VisionRejecting()
    router = ModelDispatchRouter(fallback=_Busy())
    router.register("busy", router._fallback)
    router.register("rescue-model", rescue)

    resp = router.call(_req(model="busy", images_b64=["AAAA"]))

    assert resp.text == "text-only reply"
    assert len(rescue.calls) == 2  # image attempt → stripped retry


# ═══════════════════════════════════════════════════════════
# anthropic inline-image delivery
# ═══════════════════════════════════════════════════════════


def test_anthropic_split_system_normalizes_inline_image_url() -> None:
    system, rest = _split_system(
        [
            Message(role="system", content="sys"),
            Message(
                role="user",
                content=[
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                    {"type": "text", "text": "what is this?"},
                ],
            ),
        ]
    )
    assert system == "sys"
    content = rest[0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
    }
    assert content[1] == {"type": "text", "text": "what is this?"}


def test_anthropic_split_system_keeps_remote_url_as_url_source() -> None:
    _system, rest = _split_system(
        [
            Message(
                role="user",
                content=[{"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}],
            )
        ]
    )
    assert rest[0]["content"][0] == {
        "type": "image",
        "source": {"type": "url", "url": "https://x.test/a.png"},
    }


def test_anthropic_attaches_screenshots_onto_block_content() -> None:
    """images_b64 + inline blocks in one user message must append, not
    wrap the list as text (wrapping builds an invalid Anthropic block)."""
    from runtime.sensing.model_router.anthropic_router import _attach_images_to_last_user

    _system, rest = _split_system(
        [
            Message(
                role="user",
                content=[
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                    {"type": "text", "text": "what is this?"},
                ],
            )
        ]
    )
    _attach_images_to_last_user(rest, ["SEFL"])
    content = rest[0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
    }
    assert content[1] == {"type": "text", "text": "what is this?"}
    assert content[2] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "SEFL"},
    }


def test_anthropic_attaches_screenshots_onto_plain_text_user() -> None:
    from runtime.sensing.model_router.anthropic_router import _attach_images_to_last_user

    _system, rest = _split_system([Message(role="user", content="hello")])
    _attach_images_to_last_user(rest, ["SEFL"])
    content = rest[0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "SEFL"},
    }
    assert content[1] == {"type": "text", "text": "hello"}


# ═══════════════════════════════════════════════════════════
# undeclared models · inline uploads are transcribed, not silently dropped
# (regression: thread txhjBkLKtmrjdfdJp0FQhN — the model answered
# "No image attached" because a text relay dropped the image_url block
# without a 4xx, so pass-through + crash recovery never fired)


def test_apply_vision_guard_undeclared_transcribes_inline_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)  # undeclared
    calls = _mock_agnes(monkeypatch, reply="一张红色图片")
    req = _req(
        messages=[
            _inline(
                [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    {"type": "text", "text": "describe"},
                ]
            )
        ]
    )
    guarded = vg.apply_vision_guard(req)
    assert vg.request_has_images(guarded) is False
    blocks = guarded.messages[-1].content
    assert isinstance(blocks, list)
    # transcription replaced the image block; text survives
    assert "红色图片" in str(blocks)
    assert any(b.get("type") == "text" for b in blocks)
    assert calls  # whale_eye was actually consulted


def test_apply_vision_guard_undeclared_keeps_b64_screenshot_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)  # undeclared
    calls = _mock_agnes(monkeypatch)
    req = _req(images_b64=["AAAA"])
    guarded = vg.apply_vision_guard(req)
    assert guarded is req  # raw-screenshot channel still pass-through
    assert calls == []  # no transcription for computer-use screenshots


def test_apply_vision_guard_undeclared_no_image_note_without_agnes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, None)  # undeclared
    _mock_agnes(monkeypatch, reply=None)  # agnes unavailable
    req = _req(
        messages=[
            _inline([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
        ]
    )
    guarded = vg.apply_vision_guard(req)
    assert vg.request_has_images(guarded) is False
    content = guarded.messages[0].content
    assert isinstance(content, list) and content
    assert content[0]["type"] == "text"
    assert "图片已移除" in content[0]["text"]


def test_apply_vision_guard_declared_vision_passes_inline_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vision_mode(monkeypatch, True)  # declared vision
    _mock_agnes(monkeypatch)
    req = _req(
        messages=[
            _inline([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
        ]
    )
    guarded = vg.apply_vision_guard(req)
    assert guarded is req  # raw image delivered to a vision model
    assert vg.request_has_images(guarded) is True

