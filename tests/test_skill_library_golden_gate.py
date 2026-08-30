"""Unit test for C1 · skill golden-test gate.

Verifies that ``learn_skill_from_text(golden_samples=[...])`` gates
template promotion on structural quality. Uses a monkey-patched
``_llm_call`` so the test is deterministic and doesn't spend tokens.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# The good template: 3 H2 sections
GOOD_TEMPLATE_JSON = (
    '{"description": "test-skill",'
    '"when_to_use": "unit test",'
    '"template": "## Intro\\nX\\n\\n## Details\\nY\\n\\n## Conclusion\\nZ",'
    '"style_notes": ["terse"]}'
)

# A "good" apply output that preserves all 3 H2s
GOOD_APPLY_OUTPUT = (
    "## Intro\n\nSome text here.\n\n## Details\n\nMore text here.\n\n## Conclusion\n\nSummary."
)

# A "bad" apply output with no H2s (fails structural check)
BAD_APPLY_OUTPUT = "Just a paragraph with no headers at all." * 10


def test_golden_gate_passes_when_all_samples_good(tmp_path: Path) -> None:
    """With 3 good sample outputs, template should be persisted."""
    from runtime.memory import skill_library as sl

    # Patch skills dir to a tempdir
    with patch.object(sl, "_skills_dir", lambda aid: tmp_path / aid):
        call_count = 0

        def fake_llm_call(*, system, user, model=None, max_tokens=2500, temperature=0.2):
            nonlocal call_count
            call_count += 1
            # First call = extract; subsequent = apply samples
            if call_count == 1:
                return GOOD_TEMPLATE_JSON, {"model": "fake"}
            return GOOD_APPLY_OUTPUT, {"model": "fake"}

        with patch.object(sl, "_llm_call", fake_llm_call):
            res = sl.learn_skill_from_text(
                agent_id="coder",
                name="good-skill",
                sample_text="Some sample text.",
                golden_samples=["topic A", "topic B", "topic C"],
            )

    assert res["ok"] is True, f"expected ok, got {res}"
    assert "golden_report" in res
    assert res["golden_report"]["pass_count"] == 3
    assert res["golden_report"]["total"] == 3
    assert res["golden_report"]["pass_rate"] == 1.0
    assert (tmp_path / "coder" / "good-skill.md").exists()


def test_golden_gate_rejects_when_most_samples_bad(tmp_path: Path) -> None:
    """With 3 bad apply outputs, template should NOT be persisted."""
    from runtime.memory import skill_library as sl

    with patch.object(sl, "_skills_dir", lambda aid: tmp_path / aid):
        call_count = 0

        def fake_llm_call(*, system, user, model=None, max_tokens=2500, temperature=0.2):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return GOOD_TEMPLATE_JSON, {"model": "fake"}
            return BAD_APPLY_OUTPUT, {"model": "fake"}

        with patch.object(sl, "_llm_call", fake_llm_call):
            res = sl.learn_skill_from_text(
                agent_id="coder",
                name="bad-skill",
                sample_text="Some sample text.",
                golden_samples=["topic A", "topic B", "topic C"],
            )

    assert res["ok"] is False, f"expected fail, got {res}"
    assert "golden-test gate failed" in res["error"]
    assert res["golden_report"]["pass_count"] == 0
    assert not (tmp_path / "coder" / "bad-skill.md").exists()


def test_golden_gate_skipped_when_no_samples(tmp_path: Path) -> None:
    """Backward compat: no golden_samples → no gate, persist directly."""
    from runtime.memory import skill_library as sl

    with patch.object(sl, "_skills_dir", lambda aid: tmp_path / aid):

        def fake_llm_call(*, system, user, model=None, max_tokens=2500, temperature=0.2):
            return GOOD_TEMPLATE_JSON, {"model": "fake"}

        with patch.object(sl, "_llm_call", fake_llm_call):
            res = sl.learn_skill_from_text(
                agent_id="coder",
                name="no-gate",
                sample_text="Some sample text.",
            )

    assert res["ok"] is True
    assert "golden_report" not in res
    assert (tmp_path / "coder" / "no-gate.md").exists()


def test_golden_gate_threshold_partial_pass(tmp_path: Path) -> None:
    """With threshold=0.5 and 1/3 pass, should reject (below 0.5 = 1/3 = 0.33)."""
    from runtime.memory import skill_library as sl

    with patch.object(sl, "_skills_dir", lambda aid: tmp_path / aid):
        call_count = 0
        outputs = [GOOD_TEMPLATE_JSON, GOOD_APPLY_OUTPUT, BAD_APPLY_OUTPUT, BAD_APPLY_OUTPUT]

        def fake_llm_call(*, system, user, model=None, max_tokens=2500, temperature=0.2):
            nonlocal call_count
            out = outputs[call_count] if call_count < len(outputs) else BAD_APPLY_OUTPUT
            call_count += 1
            return out, {"model": "fake"}

        with patch.object(sl, "_llm_call", fake_llm_call):
            res = sl.learn_skill_from_text(
                agent_id="coder",
                name="partial",
                sample_text="Some sample text.",
                golden_samples=["A", "B", "C"],
                golden_pass_threshold=0.5,
            )

    # 1/3 passed = 0.33 < 0.5 threshold → reject
    assert res["ok"] is False
    assert res["golden_report"]["pass_count"] == 1
    assert res["golden_report"]["pass_rate"] < 0.5
