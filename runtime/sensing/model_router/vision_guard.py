"""Runtime vision capability guard.

Stops image payloads from reaching models that can't see them, and
recovers a turn whose upstream rejected an image, so a picture never
silently kills an agent loop.

Two image channels are covered:

* ``ModelRequest.images_b64`` — the router-agnostic base64 channel used
  by computer-use screenshots (``computer_use_loop``).
* inline ``{"type": "image_url", "image_url": {"url": ...}}`` (and the
  Anthropic ``{"type": "image", "source": ...}`` variant) content blocks
  on user messages, as built by ``_react_context_attachments`` from user
  uploads.

Transcription ("给非视觉模型喂图时用插件转") is best-effort through the
bundled whale_eye 插件(鲸鱼之眼): when agnes is configured, each image is
replaced by a short text description; when it isn't, the image is dropped
with a note so the model at least knows something was removed.

The guard is wired into ``ModelDispatchRouter.call`` / ``call_stream``:

* pre-guard: a model declared ``supports_vision: false`` in
  ``custom_models.json`` never sees raw image blocks — they are
  transcribed (or stripped) before the upstream call.
* pre-guard, undeclared models with inline uploads: user image blocks
  (built by ``_react_context_attachments``) are ALSO transcribed (or
  stripped) when the model's vision capability is undeclared. Text-model
  relays often accept an ``image_url`` block and silently drop it without
  a 4xx, so the crash-recovery path can never fire and the model ends up
  claiming "no image attached" (thread txhjBkLKtmrjdfdJp0FQhN). Treating
  unknown as non-vision trades raw-image fidelity on undeclared vision
  models for a guaranteed, honest signal on text models; operators can
  restore raw pass-through by declaring ``supports_vision: true``.
  The raw-screenshot ``images_b64`` channel (computer-use) keeps its
  pass-through + 4xx-recovery behavior, because screen pixels need to
  survive intact and the channel already recovers on rejection.
* crash recovery: when a declared-vision model (or an undeclared model
  carrying ``images_b64``) rejects an image payload with a 4xx, the
  request is retried once with images transcribed (or stripped) so the
  turn continues instead of dying. The rejection classifier matches the
  same bare ``400``/``422`` markers as the config-test vision probe,
  which covers every router's error shape (``http_400`` prefixes and the
  anthropic SDK's ``Error code: 400``).  This router-level retry is the
  ONLY same-turn defense — the react loop's retry path re-sends the
  identical image-bearing request, so a rejection it can't classify
  would kill the turn after its own retries.

A stripped retry that also fails re-raises the ORIGINAL error, so a
picture never silently swaps a real failure for a confusing secondary
one. A stripped retry that succeeds does drop the image with a visible
note — the guard prefers a turn that continues without the image over a
turn that dies on it.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.models.custom_model_flags import model_supports_vision
from runtime.platform.models.llm import Message, ModelRequest

# OpenAI ``image_url`` block and Anthropic ``image`` block.
_IMAGE_BLOCK_TYPES = ("image_url", "image")
# 注记:图片因模型不支持视觉而无法送达。让模型至少知道有东西被拿掉了。
_REMOVED_NOTE = "[图片已移除:当前模型不支持视觉输入]"
# 单次请求最多转述的图片数(全局预算,跨两个通道共享);超出部分直接丢弃加注记,
# 避免一整批 agnes 调用拖垮主回合。
_MAX_TRANSCRIBE = 4
# 单张图转述的最长等待。转述是尽力而为,绝不因 agnes 慢而阻塞模型回合。
_GUARD_TRANSCRIBE_TIMEOUT = 10


def request_has_images(request: ModelRequest) -> bool:
    """True when the request carries images on either channel."""
    if request.images_b64:
        return True
    return any(_has_image_blocks(message.content) for message in request.messages)


def _has_image_blocks(content: Any) -> bool:
    """True when a message's content list carries an image block."""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") in _IMAGE_BLOCK_TYPES for block in content
    )


def model_known_non_vision(model: str) -> bool:
    """Operator declared ``supports_vision: false`` for this model id."""
    return model_supports_vision(model) is False


def apply_vision_guard(request: ModelRequest) -> ModelRequest:
    """Pre-guard: transcribe/strip images for models that can't see them.

    * Declared ``supports_vision: true`` models pass through untouched.
    * Declared ``supports_vision: false`` models get every image channel
      transcribed (or stripped) up front.
    * Undeclared models get inline user-uploaded image blocks transcribed
      (or stripped) as well — a text relay silently drops ``image_url``
      blocks without a 4xx, so pass-through + crash recovery can't detect
      the loss (thread txhjBkLKtmrjdfdJp0FQhN). The ``images_b64``
      computer-use channel stays pass-through + 4xx-recovery.

    Image-less requests short-circuit before the custom-models lookup,
    keeping the guard off the hot path of plain text turns.
    """
    if not request_has_images(request):
        return request
    declared = model_supports_vision(request.model)
    if declared is True:
        return request
    if declared is False:
        return transcribe_or_strip_images(request)
    # Undeclared: transcribe inline uploads, keep images_b64 untouched
    # (computer-use screenshots need raw pixels + their own 4xx recovery).
    if not any(_has_image_blocks(message.content) for message in request.messages):
        return request
    return transcribe_or_strip_images(request, include_b64=False)


def build_without_images(request: ModelRequest) -> ModelRequest:
    """Crash-recovery variant: always transcribe/strip images.

    Used when an upstream rejected an image it can't see. Transcribing
    gives a (mistakenly undeclared) non-vision model the image content
    it needs; if agnes is unavailable the image is dropped with a note.
    """
    return transcribe_or_strip_images(request)


def classify_image_rejection(exc: BaseException) -> bool:
    """True when the upstream likely rejected the image payload.

    Matches the 4xx markers every router uses when refusing a payload:
    the ``http_<code>`` prefix of ``OpenAIRouterError`` /
    ``GeminiRouterError`` and the anthropic SDK's ``Error code: 400``
    format — the same bare-code classifier the config-test vision probe
    (``_probe_vision_support``) uses. A false positive costs one wasted
    stripped retry; a false negative lets a rejectable image crash the
    turn, which is exactly what the guard exists to prevent.
    """
    message = str(exc)
    return "400" in message or "422" in message


def transcribe_or_strip_images(request: ModelRequest, *, include_b64: bool = True) -> ModelRequest:
    """Replace every image with a transcription (best effort) or a note.

    Returns a new frozen request with ``images_b64`` cleared (when
    ``include_b64``) and inline image blocks replaced by text blocks.
    ``images_b64`` transcriptions are appended to the last user message's
    text — the same message the routers attach them to. ``include_b64=False``
    leaves the raw-screenshot channel alone so undeclared computer-use
    screenshots keep pass-through + 4xx-recovery.
    """
    if not request_has_images(request):
        return request

    # 全局转述预算:跨 images_b64 通道和所有内联块共享,而不是每通道各 N 张
    # (3 条带图用户消息 + 截图 = 12 次 agnes 调用会把 realtime 回合拖到 deadline)。
    budget: list[int] = [_MAX_TRANSCRIBE]

    b64_texts: list[str] = []
    if include_b64:
        for b64 in request.images_b64:
            b64_texts.append(_transcribe_or_note(b64, budget))

    messages = [_rebuild_message(message, budget) for message in request.messages]
    if b64_texts:
        messages = _append_to_last_user(messages, b64_texts)

    return request.model_copy(
        update={
            "images_b64": [] if include_b64 else request.images_b64,
            "messages": messages,
        },
    )


def _rebuild_message(message: Message, budget: list[int]) -> Message:
    """Strip inline image blocks from a message, transcribing best-effort."""
    if not isinstance(message.content, list):
        return message
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, dict) and block.get("type") in _IMAGE_BLOCK_TYPES:
            blocks.append(
                {
                    "type": "text",
                    "text": _transcribe_or_note(_inline_image_value(block), budget),
                }
            )
        else:
            blocks.append(block)
    return message.model_copy(update={"content": blocks})


def _transcribe_or_note(value: str, budget: list[int]) -> str:
    """Transcribe one image to text, or return the removal note.

    ``budget`` is a shared single-element list counting how many agnes
    calls this request may still make; images past the budget are noted
    without a transcription call.
    """
    if not value or budget[0] <= 0:
        return _REMOVED_NOTE
    budget[0] -= 1
    text = _transcribe(value)
    return text or _REMOVED_NOTE


def _transcribe(value: str) -> str | None:
    """Best-effort agnes transcription of one image (base64 or data URL).

    The agnes plugin is imported lazily so the guard never hard-depends
    on it being installed or configured; any failure — or a slow agnes —
    means "no transcription" and the caller drops the image with a note.
    The per-image timeout is short because transcription is best-effort
    and must never stall the model turn waiting on agnes.
    """
    try:
        from runtime.platform.plugins.bundled.whale_eye import service as agnes

        if value.startswith(("data:", "http://", "https://")):
            return agnes.describe_image(image_url=value, timeout=_GUARD_TRANSCRIBE_TIMEOUT)
        return agnes.describe_image(image_b64=value, timeout=_GUARD_TRANSCRIBE_TIMEOUT)
    except Exception:  # noqa: BLE001 — 转述尽力而为
        return None


def _inline_image_value(block: dict[str, Any]) -> str:
    """Extract a usable image reference from an inline block."""
    if block.get("type") == "image_url":
        url = block.get("image_url")
        if isinstance(url, dict):
            return str(url.get("url") or "")
        if isinstance(url, str):
            return url
    source = block.get("source")
    if isinstance(source, dict):
        data = source.get("data")
        if isinstance(data, str):
            media_type = str(source.get("media_type") or "image/png")
            return f"data:{media_type};base64,{data}"
    return ""


def _append_to_last_user(
    messages: list[Message],
    texts: list[str],
) -> list[Message]:
    """Append image transcriptions to the last user message's text."""
    result = list(messages)
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if message.role != "user":
            continue
        suffix = "\n\n" + "\n\n".join(texts)
        if isinstance(message.content, str):
            content = message.content + suffix
        else:
            content = list(message.content) + [{"type": "text", "text": text} for text in texts]
        result[index] = message.model_copy(update={"content": content})
        break
    return result


__all__ = [
    "apply_vision_guard",
    "build_without_images",
    "classify_image_rejection",
    "model_known_non_vision",
    "request_has_images",
    "transcribe_or_strip_images",
]
