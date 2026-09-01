"""Unit tests for the multimodal user-message path in react_loop.

Validates that an image attachment with a data URL is folded into the
user message as an OpenAI-style `content` array, while non-image
attachments and bare text fall back to plain string content.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_loop import (
    _build_user_message_content,
    _image_blocks_from_attachments,
    _looks_like_image_attachment,
)

# ── _looks_like_image_attachment ──────────────────────────


def test_looks_like_image_via_media_type() -> None:
    assert _looks_like_image_attachment({"mediaType": "image/png"})
    assert _looks_like_image_attachment({"mime_type": "image/jpeg"})
    assert _looks_like_image_attachment({"media_type": "IMAGE/WEBP"})
    assert not _looks_like_image_attachment({"mediaType": "application/pdf"})


def test_looks_like_image_via_extension() -> None:
    assert _looks_like_image_attachment({"filename": "cat.png"})
    assert _looks_like_image_attachment({"filename": "photo.JPG"})
    assert _looks_like_image_attachment({"name": "diagram.gif"})
    assert not _looks_like_image_attachment({"filename": "doc.pdf"})
    assert not _looks_like_image_attachment({"filename": "noext"})


def test_looks_like_image_falsy_input() -> None:
    assert not _looks_like_image_attachment({})
    assert not _looks_like_image_attachment({"filename": "", "mediaType": ""})


# ── _image_blocks_from_attachments ────────────────────────


def test_image_blocks_data_url_takes_priority() -> None:
    blocks, consumed = _image_blocks_from_attachments(
        [{"data_url": "data:image/png;base64,AAA="}]
    )
    assert len(blocks) == 1
    assert consumed == {0}
    assert blocks[0]["type"] == "image_url"
    assert blocks[0]["image_url"]["url"].startswith("data:image/")


def test_image_blocks_hosted_url_with_media_type() -> None:
    blocks, consumed = _image_blocks_from_attachments(
        [{"url": "https://example.com/cat.png", "mediaType": "image/png"}]
    )
    assert len(blocks) == 1
    assert consumed == {0}
    assert blocks[0]["image_url"]["url"] == "https://example.com/cat.png"


def test_image_blocks_filters_non_image() -> None:
    blocks, consumed = _image_blocks_from_attachments(
        [
            {"url": "https://example.com/doc.pdf", "mediaType": "application/pdf"},
            {"data_url": "data:image/png;base64,AAA="},
        ]
    )
    assert len(blocks) == 1
    assert consumed == {1}


def test_image_blocks_handles_invalid_input() -> None:
    assert _image_blocks_from_attachments(None) == ([], set())
    assert _image_blocks_from_attachments([]) == ([], set())
    assert _image_blocks_from_attachments("not a list") == ([], set())
    assert _image_blocks_from_attachments([None, "string", 42]) == ([], set())


def test_image_blocks_skips_attachment_without_url() -> None:
    blocks, consumed = _image_blocks_from_attachments(
        [
            {"filename": "cat.png", "mediaType": "image/png"}  # no url
        ]
    )
    assert blocks == []
    assert consumed == set()


# ── _build_user_message_content ───────────────────────────


def test_build_content_plain_text_when_no_attachments() -> None:
    assert _build_user_message_content("hello world", []) == "hello world"
    assert _build_user_message_content("hi", None) == "hi"


def test_build_content_strips_text() -> None:
    assert _build_user_message_content("  hi  ", []) == "hi"


def test_build_content_returns_array_with_image() -> None:
    result = _build_user_message_content(
        "describe this",
        [{"data_url": "data:image/png;base64,AAA="}],
    )
    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": "describe this"}
    assert result[1]["type"] == "image_url"


def test_build_content_image_only_no_text() -> None:
    """Empty text + image → content array with just the image block."""
    result = _build_user_message_content(
        "",
        [{"data_url": "data:image/png;base64,AAA="}],
    )
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "image_url"


def test_build_content_describes_non_image_attachments_to_the_model() -> None:
    """Non-image attachments stay out of image blocks but remain discoverable."""
    result = _build_user_message_content(
        "hello",
        [{"url": "https://example.com/doc.pdf", "mediaType": "application/pdf"}],
    )
    assert isinstance(result, str)
    assert result.startswith("hello\n\n<attached_files")
    assert '"media_type": "application/pdf"' in result


def test_build_content_multiple_images() -> None:
    result = _build_user_message_content(
        "compare",
        [
            {"data_url": "data:image/png;base64,AAA="},
            {"data_url": "data:image/jpeg;base64,BBB="},
        ],
    )
    assert isinstance(result, list)
    image_blocks = [b for b in result if b.get("type") == "image_url"]
    assert len(image_blocks) == 2
