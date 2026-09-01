"""Tests for schema-forced structured output from sub-agents.

Covers the dependency-free validator/extractor in
``runtime.execution.subagents.schema_output`` and the end-to-end enforcement
wired into ``call_subagent`` (prompt steering, post-hoc validation, bounded
re-ask on mismatch, and the unchanged free-text contract when no schema is set).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from runtime.execution.subagents import bridge
from runtime.execution.subagents.schema_output import (
    coerce_schema_output,
    extract_json,
    schema_correction,
    schema_instruction,
    validate_against_schema,
)

_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["keep", "drop"]},
        "score": {"type": "integer"},
    },
    "required": ["verdict"],
}


# ── pure helpers ────────────────────────────────────────────────────────────


def test_extract_json_plain_object() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fenced_block() -> None:
    text = 'Here you go:\n```json\n{"verdict": "keep"}\n```\nThanks!'
    assert extract_json(text) == {"verdict": "keep"}


def test_extract_json_embedded_in_prose() -> None:
    text = 'I think the answer is {"verdict": "drop", "score": 3} overall.'
    assert extract_json(text) == {"verdict": "drop", "score": 3}


def test_extract_json_handles_braces_inside_strings() -> None:
    text = '{"note": "use {curly} braces", "n": 2}'
    assert extract_json(text) == {"note": "use {curly} braces", "n": 2}


def test_extract_json_preserves_falsy_values() -> None:
    # An empty list is a valid found value, not "nothing found".
    assert extract_json("[]") == []
    assert extract_json("0") == 0


def test_validate_accepts_valid_object() -> None:
    assert validate_against_schema({"verdict": "keep", "score": 5}, _OBJECT_SCHEMA) is None


def test_validate_flags_missing_required() -> None:
    err = validate_against_schema({"score": 5}, _OBJECT_SCHEMA)
    assert err is not None and "verdict" in err


def test_validate_flags_enum_violation() -> None:
    err = validate_against_schema({"verdict": "maybe"}, _OBJECT_SCHEMA)
    assert err is not None and "enum" in err.lower() or "one of" in err  # noqa: PT018


def test_validate_flags_type_mismatch() -> None:
    err = validate_against_schema({"verdict": "keep", "score": "high"}, _OBJECT_SCHEMA)
    assert err is not None and "score" in err


def test_validate_bool_is_not_integer() -> None:
    # bool is a subclass of int — the validator must not accept True as integer.
    err = validate_against_schema({"verdict": "keep", "score": True}, _OBJECT_SCHEMA)
    assert err is not None and "score" in err


def test_validate_array_items_and_min_items() -> None:
    schema = {"type": "array", "items": {"type": "string"}, "minItems": 2}
    assert validate_against_schema(["a", "b"], schema) is None
    assert validate_against_schema(["a"], schema) is not None  # too few
    assert validate_against_schema(["a", 1], schema) is not None  # wrong item type


def test_coerce_reports_no_json() -> None:
    ok, value, err = coerce_schema_output("sorry, no structured data here", _OBJECT_SCHEMA)
    assert ok is False
    assert value is None
    assert "no JSON value found" in err


def test_coerce_success_returns_value() -> None:
    ok, value, err = coerce_schema_output('{"verdict": "keep"}', _OBJECT_SCHEMA)
    assert ok is True
    assert value == {"verdict": "keep"}
    assert err == ""


def test_prompt_helpers_embed_schema_and_error() -> None:
    instr = schema_instruction(_OBJECT_SCHEMA)
    assert "OUTPUT FORMAT" in instr and "verdict" in instr
    corr = schema_correction("$.verdict: missing", _OBJECT_SCHEMA, "garbage reply")
    assert "FORMAT CORRECTION" in corr and "missing" in corr and "garbage reply" in corr


# ── end-to-end through call_subagent ────────────────────────────────────────


@pytest.fixture()
def fake_runner() -> Iterator[list[dict[str, object]]]:
    """Install a scripted sub-agent runner and capture the prompts it sees.

    The runner pops a reply off ``replies`` per call (repeating the last one if
    exhausted) and records each prompt. Restores the prior runner on teardown.
    """
    state: list[dict[str, object]] = []

    def install(replies: list[str]) -> list[str]:
        seen: list[str] = []
        remaining = list(replies)

        def runner(prompt: str, *, subagent_name: str = "", context: object = None) -> str:
            seen.append(prompt)
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]

        bridge.set_sub_agent_runner(runner)
        state.append({"seen": seen})
        return seen

    previous = bridge.get_sub_agent_runner()
    try:
        yield install  # type: ignore[misc]
    finally:
        bridge.set_sub_agent_runner(previous)


def _call(**kw: object) -> dict[str, object]:
    return bridge.call_subagent(agent_id="x_test_schema_runner", prompt="do it", **kw)


def test_call_subagent_valid_first_try(fake_runner) -> None:  # type: ignore[no-untyped-def]
    seen = fake_runner(['{"verdict": "keep", "score": 4}'])
    result = _call(output_schema=_OBJECT_SCHEMA)
    assert result["schema_ok"] is True
    assert result["parsed"] == {"verdict": "keep", "score": 4}
    assert "schema_error" not in result
    assert len(seen) == 1
    # The schema instruction was injected into the prompt up front.
    assert "OUTPUT FORMAT" in seen[0]


def test_call_subagent_extracts_from_chatty_reply(fake_runner) -> None:  # type: ignore[no-untyped-def]
    seen = fake_runner(['Sure!\n```json\n{"verdict": "drop"}\n```'])
    result = _call(output_schema=_OBJECT_SCHEMA)
    assert result["schema_ok"] is True
    assert result["parsed"] == {"verdict": "drop"}
    assert len(seen) == 1


def test_call_subagent_retries_then_passes(fake_runner) -> None:  # type: ignore[no-untyped-def]
    seen = fake_runner(["not json at all", '{"verdict": "keep"}'])
    result = _call(output_schema=_OBJECT_SCHEMA, schema_max_retries=1)
    assert result["schema_ok"] is True
    assert result["parsed"] == {"verdict": "keep"}
    assert len(seen) == 2
    # The second prompt carried the correction directive.
    assert "FORMAT CORRECTION" in seen[1]


def test_call_subagent_exhausts_retries(fake_runner) -> None:  # type: ignore[no-untyped-def]
    seen = fake_runner(["still not json"])
    result = _call(output_schema=_OBJECT_SCHEMA, schema_max_retries=1)
    assert result["schema_ok"] is False
    assert "schema_error" in result and result["schema_error"]
    assert "parsed" not in result
    # Raw output is always preserved.
    assert result["output"] == "still not json"
    assert len(seen) == 2  # one initial + one retry


def test_call_subagent_zero_retries(fake_runner) -> None:  # type: ignore[no-untyped-def]
    seen = fake_runner(["nope"])
    result = _call(output_schema=_OBJECT_SCHEMA, schema_max_retries=0)
    assert result["schema_ok"] is False
    assert len(seen) == 1  # no retry granted


def test_call_subagent_no_schema_is_unchanged(fake_runner) -> None:  # type: ignore[no-untyped-def]
    seen = fake_runner(["whatever the agent says"])
    result = _call()
    assert result["output"] == "whatever the agent says"
    assert "schema_ok" not in result
    assert "parsed" not in result
    assert len(seen) == 1
    assert "OUTPUT FORMAT" not in seen[0]  # no schema steering injected

