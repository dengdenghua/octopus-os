"""citation-grounding guard — research/chat turns must not cite links they
never fetched.

The guard is deliberately narrow to keep false positives near zero: it fires
only for markdown-link citations, only when a fetch/search/browser tool
actually ran this turn, and only for URLs absent from every observation. It
offers a clean escape (drop the link), so even a rare false positive can't
wedge the loop.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    GuardContext,
    _fabricated_citation_guard,
    _invoke_fabricated_citation,
    evaluate_guards,
)
from runtime.core.cerebrum.react_types import ReActStep


def _search_step(observation: str) -> ReActStep:
    return ReActStep(iteration=1, action='web_search({"q": "x"})', observation=observation)


def _ctx(steps: list[ReActStep], final_answer: str, *, is_code_mode: bool = False) -> GuardContext:
    return GuardContext(steps=steps, final_answer=final_answer, is_code_mode=is_code_mode)


ANSWER_FAKE_CITE = "The market grew 12% ([source](https://example.com/report-2026))."


def test_fires_on_fabricated_citation_after_fetch() -> None:
    steps = [_search_step("Results: growth was strong per analysts.")]
    msg = _fabricated_citation_guard(steps, ANSWER_FAKE_CITE)
    assert msg is not None
    assert "never" in msg and "https://example.com/report-2026" in msg


def test_no_fire_in_code_mode() -> None:
    steps = [_search_step("...")]
    assert _invoke_fabricated_citation(_ctx(steps, ANSWER_FAKE_CITE, is_code_mode=True)) is None


def test_no_fire_when_no_fetch_tool_ran() -> None:
    # Pure-knowledge turn (no search/fetch): a link is the model's own
    # knowledge, not a claimed source — don't police it.
    steps = [ReActStep(iteration=1, action='calculate({"expr": "1+1"})', observation="2")]
    assert _fabricated_citation_guard(steps, ANSWER_FAKE_CITE) is None


def test_no_fire_when_cited_url_is_in_observation() -> None:
    steps = [_search_step("Top hit: https://example.com/report-2026 — market grew 12%.")]
    assert _fabricated_citation_guard(steps, ANSWER_FAKE_CITE) is None


def test_no_fire_on_bare_url_mention() -> None:
    # Only markdown-link citations are policed; a bare URL mention isn't.
    steps = [_search_step("some results")]
    answer = "For background see https://example.com/report-2026 (general reference)."
    assert _fabricated_citation_guard(steps, answer) is None


def test_secondary_reference_seen_in_page_content_is_ok() -> None:
    # The model fetched a page whose content contained the cited URL — that's
    # a legit secondary reference, not a fabrication.
    steps = [
        ReActStep(
            iteration=1,
            action='web_fetch({"url": "https://a.com"})',
            observation="Page a.com says: also see https://example.com/report-2026 for data.",
        )
    ]
    assert _fabricated_citation_guard(steps, ANSWER_FAKE_CITE) is None


def test_fetch_and_observation_collected_from_action_results() -> None:
    # Tool ran via the multi-action action_results path, not the flat fields.
    step = ReActStep(
        iteration=1,
        action_results=[
            {"tool_name": "web_fetch", "ok": True, "observation": "nothing relevant here"}
        ],
    )
    assert _fabricated_citation_guard([step], ANSWER_FAKE_CITE) is not None
    # and if the url IS in that observation, no fire
    step_ok = ReActStep(
        iteration=1,
        action_results=[
            {
                "tool_name": "web_fetch",
                "ok": True,
                "observation": "found https://example.com/report-2026",
            }
        ],
    )
    assert _fabricated_citation_guard([step_ok], ANSWER_FAKE_CITE) is None


def test_end_to_end_via_registry() -> None:
    steps = [_search_step("Results: strong growth.")]
    hit = evaluate_guards(_ctx(steps, ANSWER_FAKE_CITE))
    assert hit is not None
    label, _msg = hit
    assert label == "citation-grounding guard"


# ── Cross-turn grounding ────────────────────────────────────────────────
# A source link fetched in an EARLIER turn of the same thread and cited here
# is grounded, not fabricated — the guard must merge prior-turn observations.


def test_no_fire_when_citation_grounded_in_prior_turn_observation() -> None:
    steps = [_search_step("本轮只补了竞争格局。")]
    prior = "Top hit: https://example.com/report-2026 — market grew 12%."
    msg = _fabricated_citation_guard(steps, ANSWER_FAKE_CITE, prior_observations=prior)
    assert msg is None


def test_fires_when_citation_absent_from_both_current_and_prior() -> None:
    steps = [_search_step("本轮只补了竞争格局。")]
    prior = "Top hit: https://other.example/foo — unrelated."
    msg = _fabricated_citation_guard(steps, ANSWER_FAKE_CITE, prior_observations=prior)
    assert msg is not None
    assert "https://example.com/report-2026" in msg


def test_prior_observations_flow_through_invoke_wrapper() -> None:
    steps = [_search_step("本轮只补了竞争格局。")]
    ctx = _ctx(steps, ANSWER_FAKE_CITE)
    ctx.prior_grounding_text = "Top hit: https://example.com/report-2026 — market grew 12%."
    assert _invoke_fabricated_citation(ctx) is None

