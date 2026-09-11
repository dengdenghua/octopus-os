r"""
Regression tests for ``LLMPlanner._extract_json``.

Context
-------

Pre-2026-04 the extractor was a single greedy ``r"\\{.*\\}"``.
Any LLM response containing more than one ``{`` — an example in
prose, a brace inside a string literal, a fenced code block before
the real plan — made it grab from the first open-brace through the
last close-brace, then hand that mega-string to ``json.loads`` and
surface a cryptic "Expecting property name" error.

These tests pin the new behavior:

* Fenced ``\`\`\`json`` blocks are the preferred form (matches the
  planner prompt's stated output contract).
* Free-form text with embedded example braces still extracts the
  real object.
* Braces inside JSON string literals never break the scanner.
* Truly-absent JSON and malformed JSON surface distinguishable errors.
"""

from __future__ import annotations

import pytest

from runtime.core.cerebrum.llm_planner import (
    LLMPlanner,
    PlannerError,
    _scan_balanced_object,
)

# ═══════════════════════════════════════════════════════════
# _scan_balanced_object · pure function
# ═══════════════════════════════════════════════════════════


class TestScanBalancedObject:
    def test_simple_object(self):
        assert _scan_balanced_object('{"a": 1}', 0) == '{"a": 1}'

    def test_nested_object(self):
        txt = '{"a": {"b": {"c": 1}}}'
        assert _scan_balanced_object(txt, 0) == txt

    def test_braces_in_string_are_ignored(self):
        """The pre-fix naive regex grabbed through string-internal
        braces. The new scanner must respect quotes."""
        txt = '{"path": "a{b}c", "x": 1}'
        assert _scan_balanced_object(txt, 0) == txt

    def test_escaped_quote_does_not_end_string(self):
        """An escaped ``\\"`` inside a JSON string literal mustn't
        be interpreted as end-of-string — that would let braces
        in the rest of the string corrupt depth tracking."""
        txt = r'{"msg": "he said \"hi\" { fake } still in string"}'
        assert _scan_balanced_object(txt, 0) == txt

    def test_unbalanced_returns_none(self):
        assert _scan_balanced_object('{"a": 1', 0) is None

    def test_wrong_start_returns_none(self):
        """Defensive: caller must pass an index pointing at ``{``."""
        assert _scan_balanced_object("not a brace", 0) is None


# ═══════════════════════════════════════════════════════════
# _extract_json (via a minimal planner instance)
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def planner() -> LLMPlanner:
    """We only exercise ``_extract_json`` — a method that doesn't touch
    router/registry/composer. Build a planner with stubs so the test
    stays hermetic."""
    # __init__ signature needs router / registry / composer. Use
    # ``object.__new__`` to skip init — none of those deps are
    # touched by the method under test.
    return LLMPlanner.__new__(LLMPlanner)


class TestExtractJSONFenced:
    def test_fenced_block_happy_path(self, planner):
        text = """Sure, here is the plan:

```json
{"reasoning": "short", "nodes": [{"skill": "read_file"}]}
```

Let me know if you want changes."""
        out = planner._extract_json(text)
        assert out["reasoning"] == "short"
        assert out["nodes"][0]["skill"] == "read_file"

    def test_fenced_block_without_language_tag(self, planner):
        """Some models emit ``` ``` ``` without the ``json`` tag."""
        text = '```\n{"nodes": []}\n```'
        out = planner._extract_json(text)
        assert out == {"nodes": []}

    def test_fenced_case_insensitive(self, planner):
        """`\\`\\`\\`JSON` and `\\`\\`\\`Json` both in the wild."""
        text = '```JSON\n{"ok": true}\n```'
        assert planner._extract_json(text) == {"ok": True}


class TestExtractJSONAdversarial:
    # NOTE on ordering — the scanner returns the FIRST valid dict it
    # encounters in document order. That's a deliberate tradeoff:
    # * Fenced output (the common case) is unambiguous.
    # * Un-fenced output from a degraded model is rare; we'd rather
    #   return something that parses than try to heuristically pick
    #   "the real plan" from multiple candidates.
    # The ``test_two_json_objects_returns_first_that_matches`` case
    # below pins this — a future change that tries to prefer later
    # candidates would break both tests and force a design discussion.

    def test_braces_inside_string_literal(self, planner):
        """String-internal braces were the killer case · scanner
        must treat them as opaque."""
        text = '{"template": "see {here}/file.txt", "nodes": []}'
        out = planner._extract_json(text)
        assert out["template"] == "see {here}/file.txt"

    def test_two_json_objects_returns_first_that_matches(self, planner):
        """When the model emits two candidate objects (e.g. a tiny
        example followed by the real plan) we should parse both and
        prefer the first valid dict. Not wrong per se — just
        pinning the documented behavior so a later change doesn't
        silently flip it."""
        text = """{"example": true}

{"nodes": [{"skill": "x"}]}"""
        out = planner._extract_json(text)
        assert out == {"example": True}  # first-match wins

    def test_trailing_prose_after_json(self, planner):
        text = '{"nodes": []}\n\nThat\'s the plan!'
        assert planner._extract_json(text) == {"nodes": []}

    def test_leading_prose_no_fence(self, planner):
        text = 'The plan is:\n{"nodes": [1, 2, 3]}'
        assert planner._extract_json(text) == {"nodes": [1, 2, 3]}


class TestExtractJSONErrors:
    def test_no_json_at_all(self, planner):
        with pytest.raises(PlannerError, match="lacks JSON"):
            planner._extract_json("Sorry I can't help with that.")

    def test_all_candidates_malformed(self, planner):
        """Ensure the error message tells you WHICH kind of failure
        so on-call debugging doesn't have to guess whether the
        regex missed or the parser choked."""
        with pytest.raises(PlannerError, match="parse failed"):
            planner._extract_json("{ this is {not} valid json }")

    def test_non_dict_json_rejected(self, planner):
        """A bare array is valid JSON but not a valid plan."""
        with pytest.raises(PlannerError):
            planner._extract_json("[1, 2, 3]")


class TestExtractJSONFencedFallback:
    def test_malformed_fence_falls_back_to_scan(self, planner):
        """A fenced block that ISN'T valid JSON must not short-circuit
        the scan — otherwise a corrupt fenced section would prevent
        us from finding a valid one later in the text."""
        text = """```json
{this is bad
```

{"nodes": []}"""
        out = planner._extract_json(text)
        assert out == {"nodes": []}
