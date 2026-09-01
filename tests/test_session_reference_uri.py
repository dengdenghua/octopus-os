"""Canonical ``echo-session:`` URI plus legacy-reference compatibility.

The host-neutral session reference lane: lossless canonical URIs, strict
decode/canonicalization, Markdown mention rendering, and text parsing that
feeds ``SessionReferenceResolver.resolve_mentions`` alongside the legacy
``@session:`` tokens.
"""

from __future__ import annotations

import pytest

from runtime.execution.tool_engine.session_reference import (
    SessionReferenceError,
    SessionReferenceRecord,
    SessionReferenceResolver,
)
from runtime.execution.tool_engine.session_reference_uri import (
    LEGACY_SESSION_REFERENCE_SCHEMES,
    SESSION_REFERENCE_SCHEME,
    decode_session_reference_uri,
    encode_session_reference_uri,
    format_session_reference_mention,
    parse_session_reference_text,
)


def test_roundtrip_canonical_uri() -> None:
    for sid in ("abc123", "0123456789abcdef0123456789abcdef", "with space", "中文id"):
        uri = encode_session_reference_uri(sid)
        assert uri.startswith(SESSION_REFERENCE_SCHEME)
        assert decode_session_reference_uri(uri) == sid


def test_legacy_dsh_uri_remains_readable_but_new_encoding_is_echo() -> None:
    legacy = f"{LEGACY_SESSION_REFERENCE_SCHEMES[0]}ImFiYzEyMyI"
    assert decode_session_reference_uri(legacy) == "abc123"
    assert encode_session_reference_uri("abc123") == "echo-session:ImFiYzEyMyI"
    parsed = parse_session_reference_text(f"历史 @[研究员]({legacy})")
    assert parsed.text == "历史 @研究员"
    assert parsed.references == [{"session_id": "abc123", "label": "研究员"}]


def test_decode_rejects_non_canonical() -> None:
    # Valid base64url but WITH padding — the canonical form is unpadded, so
    # re-encoding yields a different URI and the canonical check must fail.
    with pytest.raises(SessionReferenceError) as err:
        decode_session_reference_uri(f"{SESSION_REFERENCE_SCHEME}Inh5eiI=")
    assert err.value.code == "SESSION_REFERENCE_INVALID_REFERENCE"


def test_decode_rejects_bad_shapes() -> None:
    for uri in (
        "https://example.com/x",  # wrong scheme
        "echo-session:",  # empty payload
        "echo-session:!!!",  # invalid payload chars
        "echo-session:NQ",  # decodes to JSON number 5 → not a string
        "echo-session:IiI",  # decodes to "" → empty session id rejected
    ):
        with pytest.raises(SessionReferenceError) as err:
            decode_session_reference_uri(uri)
        assert err.value.code == "SESSION_REFERENCE_INVALID_REFERENCE"


def test_format_mention_escapes_label() -> None:
    mention = format_session_reference_mention("abc123", label="a]b\\c")
    assert mention == "@[a\\]b\\\\c](echo-session:ImFiYzEyMyI)"
    # Default label is the session id itself.
    assert format_session_reference_mention("abc123") == ("@[abc123](echo-session:ImFiYzEyMyI)")


def test_parse_mentions_and_bare_uris_in_order() -> None:
    uri_a = encode_session_reference_uri("abc123")
    uri_b = encode_session_reference_uri("def456")
    text = f"看 @[研究 session]({uri_a}) 然后 {uri_b}"
    parsed = parse_session_reference_text(text)
    assert parsed.text == "看 @研究 session 然后 @def456"
    assert parsed.references == [
        {"session_id": "abc123", "label": "研究 session"},
        {"session_id": "def456", "label": "def456"},
    ]


def test_parse_escaped_label_unescaped() -> None:
    uri = encode_session_reference_uri("abc123")
    parsed = parse_session_reference_text(f"@[a\\]b]({uri})")
    assert parsed.text == "@a]b"
    assert parsed.references[0]["label"] == "a]b"


def test_parse_malformed_uri_fails_loud() -> None:
    with pytest.raises(SessionReferenceError) as err:
        parse_session_reference_text("@[x](echo-session:!!!)")
    assert err.value.code == "SESSION_REFERENCE_INVALID_REFERENCE"


def _surface(session_id: str) -> list[dict]:
    return [
        {
            "type": "user/message",
            "data": {
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": f"prompt-{session_id}"}],
            },
        }
    ]


def test_resolve_mentions_accepts_canonical_uri() -> None:
    resolver = SessionReferenceResolver()
    sid = "00112233445566778899aabbccddeeff"
    uri = encode_session_reference_uri(sid)
    out = resolver.resolve_mentions(
        f"研究 @[研究员]({uri}) 的成果",
        target_id="target",
        read_surface=_surface,
    )
    # The canonical mention is replaced with its readable @label, not stripped.
    assert out.content == "研究 @研究员 的成果"
    assert out.additional_context is not None
    rendered = out.additional_context["content"][0]["text"]
    assert "<referenced-sessions>" in rendered
    assert "prompt-00112233445566778899aabbccddeeff" in rendered


def test_resolve_mentions_mixes_canonical_and_legacy() -> None:
    resolver = SessionReferenceResolver()
    sid_a = "00112233445566778899aabbccddeeff"
    sid_b = "11111111111111111111111111111111"
    out = resolver.resolve_mentions(
        f"对比 @[{sid_a}]({encode_session_reference_uri(sid_a)}) 与 @session:{sid_b}",
        target_id="target",
        read_surface=_surface,
    )
    rendered = out.additional_context["content"][0]["text"]
    assert "prompt-00112233445566778899aabbccddeeff" in rendered
    assert "prompt-11111111111111111111111111111111" in rendered


def test_resolve_mentions_skips_stale_canonical_and_self() -> None:
    resolver = SessionReferenceResolver()
    known = "00112233445566778899aabbccddeeff"
    stale = "ffffffffffffffffffffffffffffffff"
    record = SessionReferenceRecord(session_id=known, label="researcher")
    out = resolver.resolve_mentions(
        f"@[{known}]({encode_session_reference_uri(known)}) "
        f"@[{stale}]({encode_session_reference_uri(stale)}) "
        f"@[self]({encode_session_reference_uri('target')})",
        target_id="target",
        read_surface=_surface,
        sessions=[record],
    )
    rendered = out.additional_context["content"][0]["text"]
    assert "prompt-00112233445566778899aabbccddeeff" in rendered
    assert stale not in rendered


def test_resolve_mentions_tolerates_malformed_canonical() -> None:
    resolver = SessionReferenceResolver()
    sid = "00112233445566778899aabbccddeeff"
    out = resolver.resolve_mentions(
        f"@[坏](echo-session:!!!) 继续 @session:{sid}",
        target_id="target",
        read_surface=_surface,
    )
    # Malformed canonical lane is skipped; the legacy seam still resolves.
    assert out.additional_context is not None
    rendered = out.additional_context["content"][0]["text"]
    assert "prompt-00112233445566778899aabbccddeeff" in rendered


def test_resolve_mentions_canonical_only_without_known_sessions() -> None:
    resolver = SessionReferenceResolver()
    sid = "00112233445566778899aabbccddeeff"
    out = resolver.resolve_mentions(
        f"@[{sid}]({encode_session_reference_uri(sid)})",
        target_id="target",
        read_surface=_surface,
    )
    assert out.additional_context is not None
    assert "prompt-00112233445566778899aabbccddeeff" in out.additional_context["content"][0]["text"]

