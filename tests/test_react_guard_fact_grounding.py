"""fact-grounding guard — research/chat turns must not assert external facts
they never fetched.

The citation guard catches fabricated *links*; this one catches fabricated
*numbers*. When a turn actually fetched content, a currency amount /
percentage / version / dated fact asserted in the answer is treated as a
claim sourced from that content — if its digits never appear in any
observation, the claim is ungrounded.

Deliberately narrow to keep false positives near zero, mirroring the
citation guard: fires only when a fetch/search/browser tool actually ran,
only for external-fact-shaped numbers (never bare integers / single-dot
decimals), and the numeric core is substring-matched against the
observation digit-stream so any overlapping evidence clears it. Repair-tier
(not hard), with a clean escape (cite the observation or soften).
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    GUARD_REGISTRY,
    GuardContext,
    _invoke_ungrounded_fact,
    _ungrounded_external_fact_guard,
    evaluate_guards,
    guard_disposition,
)
from runtime.core.cerebrum.react_types import ReActStep


def _search_step(observation: str) -> ReActStep:
    return ReActStep(iteration=1, action='web_search({"q": "x"})', observation=observation)


def _ctx(steps: list[ReActStep], final_answer: str, *, is_code_mode: bool = False) -> GuardContext:
    return GuardContext(steps=steps, final_answer=final_answer, is_code_mode=is_code_mode)


def test_fires_on_ungrounded_currency_after_fetch() -> None:
    steps = [_search_step("Analysts noted strong growth.")]
    msg = _ungrounded_external_fact_guard(steps, "该产品定价 ¥1,200 起。")
    assert msg is not None
    assert "¥1,200" in msg


def test_fires_on_ungrounded_percent_after_fetch() -> None:
    steps = [_search_step("Q3 report published.")]
    msg = _ungrounded_external_fact_guard(steps, "市场份额 35%。")
    assert msg is not None
    assert "35%" in msg


def test_fires_on_ungrounded_version_after_fetch() -> None:
    steps = [_search_step("Release notes cover the API.")]
    msg = _ungrounded_external_fact_guard(steps, "最新版本 2.1.0。")
    assert msg is not None
    assert "2.1.0" in msg


def test_fires_on_ungrounded_date_after_fetch() -> None:
    steps = [_search_step("Roadmap shared.")]
    msg = _ungrounded_external_fact_guard(steps, "发布于 2026-03。")
    assert msg is not None
    assert "2026-03" in msg


def test_no_fire_when_no_fetch_tool_ran() -> None:
    # Pure-knowledge turn (no search/fetch): a number is the model's own
    # knowledge or reasoning, not a fact claimed from this turn.
    steps = [ReActStep(iteration=1, action='calculate({"expr": "1+1"})', observation="2")]
    assert _ungrounded_external_fact_guard(steps, "该服务定价 $0.80/百万 token。") is None


def test_no_fire_when_fact_is_in_observation() -> None:
    steps = [_search_step("该产品定价 ¥1,200 起。")]
    assert _ungrounded_external_fact_guard(steps, "该产品定价 ¥1,200 起。") is None


def test_no_fire_in_code_mode() -> None:
    steps = [_search_step("...")]
    assert _invoke_ungrounded_fact(_ctx(steps, "定价 ¥1,200。", is_code_mode=True)) is None


def test_no_fire_on_bare_integer() -> None:
    # Bare integers aren't external-fact-shaped; "3" must not trip the guard.
    steps = [_search_step("...")]
    assert _ungrounded_external_fact_guard(steps, "有 3 个候选方案。") is None


def test_no_fire_on_single_dot_decimal() -> None:
    # Single-dot decimals (5.6 倍, 2.1 release) are too ambiguous to police.
    steps = [_search_step("...")]
    assert _ungrounded_external_fact_guard(steps, "同比增长 5.6 倍。") is None


def test_repair_tier_not_hard() -> None:
    assert guard_disposition("fact-grounding guard", "research") == "repair"


def test_hedged_approximation_is_own_judgment() -> None:
    # "约" is the guard's advertised escape — softening must actually clear it,
    # or the guard would flag its own escape hatch and wedge the loop.
    steps = [_search_step("Analysts noted strong growth.")]
    assert _ungrounded_external_fact_guard(steps, "定价约 ¥1,200 起。") is None


def test_own_understanding_escapes() -> None:
    steps = [_search_step("Analysts noted strong growth.")]
    assert _ungrounded_external_fact_guard(steps, "据我了解定价 ¥1,200。") is None


def test_english_hedge_escapes() -> None:
    steps = [_search_step("Analysts noted strong growth.")]
    assert _ungrounded_external_fact_guard(steps, "Approximately ¥1,200 per unit.") is None


def test_aggregate_total_escapes() -> None:
    # A summed figure legitimately misses the observation digit-stream: the
    # parts are sourced but the total is the model's computation.
    steps = [_search_step("价格500元,运费700元。")]
    assert _ungrounded_external_fact_guard(steps, "总价合计 ¥1,200。") is None


def test_currency_conversion_hedged_escapes() -> None:
    # Converting $10 to ¥72 produces a number absent from the observation;
    # the hedge marks it as the model's conversion, not a source echo.
    steps = [_search_step("price $10 per unit.")]
    assert _ungrounded_external_fact_guard(steps, "约 ¥72。") is None


def test_unhedged_asserted_fact_still_fires() -> None:
    # The clean fabrication case — confident, unhedged, no aggregation —
    # must still be caught after the suppression carve-outs.
    steps = [_search_step("Analysts noted strong growth.")]
    msg = _ungrounded_external_fact_guard(steps, "该产品定价 ¥1,200 起。")
    assert msg is not None
    assert "¥1,200" in msg


def test_registered_in_registry() -> None:
    assert any(s.label == "fact-grounding guard" for s in GUARD_REGISTRY)


def test_end_to_end_via_registry() -> None:
    steps = [_search_step("Q3 report published.")]
    hit = evaluate_guards(_ctx(steps, "市场份额 35%。"))
    assert hit is not None
    label, msg = hit
    assert label == "fact-grounding guard"
    assert "35%" in msg


# ── Cross-turn grounding ────────────────────────────────────────────────
# A figure sourced by a search in an EARLIER turn of the same thread and
# reused in this turn is grounded, not fabricated. The guard must merge
# prior-turn observations (thread txhjBkLKtmrjdfdJp0FQhN regressed here:
# turn-1 search facts reused in the turn-2 report were falsely flagged,
# and the model's "收到 grounding 检查…" acknowledgment leaked into the
# user-visible answer).


def test_no_fire_when_fact_grounded_in_prior_turn_observation() -> None:
    steps = [_search_step("本轮只补了竞争格局。")]
    prior = "Global Market Insights: 2025 智能床垫市场 17.6 亿美元，CAGR 6.6%。"
    msg = _ungrounded_external_fact_guard(
        steps, "全球智能床垫 2025 年约 17.6 亿美元，CAGR 6.6%。", prior_observations=prior
    )
    assert msg is None


def test_fires_when_fact_absent_from_both_current_and_prior() -> None:
    steps = [_search_step("本轮只补了竞争格局。")]
    # NB: no hedge marker ("约" etc.) in the number's context window, so the
    # fact is presented as a sourced figure and must be policed.
    prior = "Global Market Insights: 智能床垫 2025 年全球规模 33.4 亿美元。"
    msg = _ungrounded_external_fact_guard(
        steps, "全球智能床垫 2025 年全球规模 17.6 亿美元。", prior_observations=prior
    )
    assert msg is not None
    assert "17.6 亿" in msg or "17.6亿美元" in msg


def test_prior_observations_flow_through_invoke_wrapper() -> None:
    steps = [_search_step("本轮只补了竞争格局。")]
    ctx = _ctx(
        steps,
        "全球智能床垫 2025 年约 17.6 亿美元。",
    )
    ctx.prior_grounding_text = "Global Market Insights: 智能床垫市场 17.6 亿美元。"
    assert _invoke_ungrounded_fact(ctx) is None


def test_fires_on_ungrounded_yiyuan_figure_after_fetch() -> None:
    # "X 亿美元" is the canonical shape for market-size figures; the guard
    # must recognize the 亿 (hundred-million) classifier before 美元.
    steps = [_search_step("Analysts noted strong growth.")]
    msg = _ungrounded_external_fact_guard(steps, "智能床垫全球规模 17.6 亿美元。")
    assert msg is not None
    assert "17.6 亿美元" in msg

