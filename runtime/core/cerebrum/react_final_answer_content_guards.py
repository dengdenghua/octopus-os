"""Final-answer content guards (post-step / pre-Final-Answer gates).

Extracted from ``react_guards.py`` (Wave 3, cluster 4) so the orchestration
module can stay under the size budget. These guards inspect the *proposed
final answer itself* — placeholder prose, fabricated citations, and a
requested-but-undelivered output shape — rather than the trajectory.

Leaf-ish module: depends only on re / react_types plus two sibling leaf
modules (react_code_mode_guards for the shared tool-observation predicate,
react_goal_analysis for the lookup classifier) — must never import
react_guards.
"""

from __future__ import annotations

import re

from runtime.core.cerebrum.react_code_mode_guards import _has_successful_tool_observation
from runtime.core.cerebrum.react_goal_analysis import (
    _final_answer_requests_user_help,
    _goal_requests_research_lookup,
)
from runtime.core.cerebrum.react_types import ReActStep


def _incomplete_final_answer_guard(final_answer: str) -> str | None:
    """Reject placeholder/preparatory prose presented as a terminal answer."""

    raw = str(final_answer or "").strip()
    visible = re.sub(r"</?[a-z_][^>]*>", " ", raw, flags=re.IGNORECASE)
    visible = re.sub(r"\s+", " ", visible).strip()
    if not visible:
        return (
            "The proposed Final Answer is empty or only contains an internal "
            "control marker. Produce the actual user-facing result now."
        )
    # Strip leading hedges/apologies so an answer that *starts* with "抱歉刚才
    # 掉线了，马上把…" or "哈哈报告还在肝" is still classified as the announce it
    # is, instead of escaping every intent prefix because it opens with filler
    # (thread t0Wn5Zhvh3VUFwoAR2uP4M: "抱歉刚才掉线了，马上把4位成员的成果综合出来。"
    # was delivered as a completed turn with zero synthesis output).
    visible_core = re.sub(
        r"^(?:抱歉|不好意思|抱歉抱歉|稍等|稍等片刻|等一下|哈哈|好的|好嘞|好呀|"
        r"ok(?:ay)?|收到|明白|让我(?:先|来)?)[，。,!！；;\s]*",
        "",
        visible,
        flags=re.IGNORECASE,
    )
    # ``我来``/``我这就``/``我直接`` announce an action exactly as ``我将`` does.
    # Their absence let "我来查看黑板…" through as a terminal answer three turns
    # running (thread teD7hPf9dkGOExwO0dIiBE), each time with zero tool calls.
    # ``我继续``/``继续`` are the same future intent in continuation form: after a
    # first "我接下来会核对…" was rejected, the model rephrased to "我继续核对
    # 广义健康板块…确认是否也跟随大涨" which lacked every listed prefix and was
    # delivered as a completed turn with zero tool calls (thread
    # tj1qarRWyf8H5zzT6dR_-u, trn_d1cd69902f864d67 / trn_23c29c3f25ef4e68).
    preparatory_start = re.match(
        r"^(?:我(?:会|将|先|来|要|想|需要|这就|马上|直接|接下来|这就开始|马上开始|"
        r"现在(?:立刻|马上|直接)?|继续|接着|随后|开始)|接下来|下一步|准备|继续|接着|"
        r"let me|i(?:'ll| will| first| am going to)|next[,：:]?)",
        visible_core,
        re.IGNORECASE,
    )
    evidence_action = re.search(
        r"\b(?:grep|read|inspect|check|verify|search|open)\b|"
        r"(?:核对|核实|检查|读取|再读|查看|搜索|检索|调研|打开|确认|探清|摸清|摸透|理清|弄清|摸底|盘点|收集|拉取|采集|搜集|定位|查找|明确|梳理|审查|评估|开始|过一遍|逐项过|"
        # 未来意图的裸动词:查/找/读/搜/分析(如"我来帮你查这三组数据"/"我需要找一下…")。
        # 只有与 preparatory_start/future_action 同时出现才判定为占位,过去式
        # ("我查了…"/"我找到了…"/"我分析了…")因缺少意图前缀仍放行,不会误伤已交付报告。
        r"查(?:一下|一遍|一查)?|找(?:一下|一遍)?|读(?:一下|一遍|一读)?|搜(?:一下|一搜)?|分析|"
        # 看/扫 的意向式("看一下/看看/扫一遍")是"待办动作",不是已交付结论;但"看了/看过/
        # 看见/看法"是过去式或名词,不能当证据动作(否则把已完成的报告误判成预告)。
        r"看(?:一下|一眼|看|一遍|下)|扫(?:一遍|一眼))"
        r"(?:[^。.!！；;\n]{0,16})",
        visible,
        re.IGNORECASE,
    )
    # A result word only counts as delivery when it names something produced,
    # not something about to be looked at. "确认哪些子任务写回了结果" makes 结果
    # the *object of the pending inspection*, yet the bare keyword scored it as
    # a delivered conclusion and cancelled the guard (thread
    # teD7hPf9dkGOExwO0dIiBE, three consecutive no-op turns).
    inspected_result = re.search(
        r"(?:哪些|是否|有没有|有无|什么|多少|如何|怎样)"
        r"[^。.!！；;\n]{0,24}(?:结论|结果|答案|状态|数据)|"
        r"(?:确认|核对|核实|查看|检查|读取|定位|梳理|盘点)"
        r"[^。.!！；;\n]{0,24}(?:的)?(?:实际)?(?:结论|结果|答案|状态)|"
        r"(?:结论|结果|答案|状态)(?:与|和|、)?[^。.!！；;\n]{0,12}(?:缺失|齐不齐|对不对)",
        visible,
        re.IGNORECASE,
    )
    raw_result_signal = re.search(
        r"(?:结论|结果|区别|差异|一致|不同|表明|因此|所以|答案)|"
        r"\b(?:result|conclusion|difference|same|therefore|because|answer)\b",
        visible,
        re.IGNORECASE,
    )
    result_signal = None if inspected_result else raw_result_signal
    negated_completion = re.search(
        r"(?:还|尚|仍)?(?:没有|未|没能)(?:给出|得到|形成|完成|确认|核对)?"
        r"[^。.!！；;\n]{0,24}(?:结论|结果|答案|比较|差异)|"
        r"\b(?:not\s+yet|no\s+(?:result|conclusion|answer)\s+yet|"
        r"have\s+not\s+(?:finished|completed|verified|checked))\b",
        visible,
        re.IGNORECASE,
    )
    future_action = re.search(
        r"(?:^|[。.!！；;，,]\s*)(?:我)?(?:会|将|先|接下来|下一步|准备|继续|接着|随后|现在(?:立刻|马上)?|马上|立刻|立即)|"
        r"(?:我)?先[^。.!！；;\n]{0,32}(?:再读|读取|查看|核对|检查|探清|定位|查找|搜索)|"
        r"\b(?:i(?:'ll| will)|let me|next)\b",
        visible,
        re.IGNORECASE,
    )
    failed_attempt = re.search(
        r"(?:失败|路径不对|未找到|找不到|无法读取|没有读到)|"
        r"\b(?:failed|not found|could not read|unable to read)\b",
        visible,
        re.IGNORECASE,
    )
    # A conclusion that is only *promised* (e.g. "用具体数据支撑结论", "再给出
    # 结论") is not a delivered conclusion. result_signal above would otherwise
    # treat the bare word 结论 as a passed check and let a pure preparatory
    # promise through (regression: trn_514bd9600295430b "我这就开始…支撑结论").
    deferred_conclusion = re.search(
        r"(?:支撑|支持|形成|得出|得到|给出|提炼|汇总|归纳|再给|下)"
        r"[^。.!！；;\n]{0,12}(?:结论|结果|答案)|"
        r"(?:结论|结果|答案)(?:前|之前|就|再|待|尚未|还没|暂未)",
        visible,
        re.IGNORECASE,
    )
    # A turn can end with a *promised* synthesis ("马上综合。"/"马上把…综合出
    # 来。"/"更新任务状态后输出完整报告。") while delivering nothing. That is
    # the same announce-only failure as the read/search prefixes above, just in
    # output form — the model claims the work is about to be emitted instead of
    # emitting it (thread t0Wn5Zhvh3VUFwoAR2uP4M: msgs "四个方向都收齐了，马上
    # 综合。" and "抱歉刚才掉线了，马上把4位成员的成果综合出来。" were each the
    # whole final answer). Only short, body-less promises trip this; any answer
    # with a real markdown body (delivered_report) or a colon+findings tail is
    # left alone.
    promised_delivery = re.search(
        r"(?:马上|立刻|立即|这就|现在)(?:把|将)?[^。.!！；;\n]{0,28}"
        r"(?:综合|汇总|输出|整理|成稿|生成|给出|交付|发你|给到你)"
        r"[^。.!！；;\n]{0,8}(?:出来|一下|给你|给到|好|完|了|给你看|奉上)?[。.!！;\s]*$"
        r"|(?:稍后|之后|之后|再|后|然后|接着)[^。.!！；;\n]{0,16}"
        r"(?:输出|给出|生成|成稿|交付|汇总|综合)[^。.!！；;\n]{0,12}"
        r"(?:完整|最终|正式)?(?:报告|方案|答案|结果|内容|成果)",
        visible,
        re.IGNORECASE,
    )

    # Long-form reports often start with a short roadmap ("我将检查…") before
    # presenting the actual findings.  Do not classify that opening sentence as
    # the whole answer: headings, enumerated findings, and a substantial body
    # are strong delivery evidence.  This keeps the guard focused on genuinely
    # plan-only candidates while preserving its protection for one-line plans.
    # Length plus real markdown structure is delivery evidence on its own, so
    # this escape hatch reads the raw keyword rather than the disambiguated
    # ``result_signal``: an inspection-target mention inside a long structured
    # report must not revoke the exemption a roadmap opening depends on.
    delivered_report = (
        len(visible) >= 120
        and bool(re.search(r"(?:^|\n)\s*(?:#{1,6}\s|\d+[.)、]\s|[-*]\s)", raw))
        and bool(raw_result_signal)
        and not negated_completion
    )
    if delivered_report:
        return None
    if promised_delivery and not delivered_report:
        return (
            "The proposed Final Answer only promises to produce or synthesize "
            "the result (e.g. 「马上综合」/「马上输出完整报告」) without actually "
            "delivering it. It is not a completed answer. Emit the actual "
            "synthesized report/content now — do not end the turn with a promise "
            "to produce it."
        )
    if (
        evidence_action
        and (preparatory_start or future_action)
        and (failed_attempt or negated_completion or deferred_conclusion or not result_signal)
    ):
        return (
            "The proposed Final Answer only announces a future inspection or "
            "search. It is not a completed answer. Execute the stated read/search "
            "action, use its observation, and then answer the user's question "
            "with concrete findings."
        )
    return None


# ── Research / chat citation grounding ────────────────────────
# Non-code turns otherwise reach Final Answer with only the security
# cluster gating them. The check that pays off with the fewest false
# positives is a fabricated citation: if the turn actually fetched
# external content and the answer presents a markdown link ``[t](url)``
# whose URL never appeared in any observation, the model is citing a
# source it never consulted — a real, serious research failure.
# Deliberately narrow: only markdown-link citations (not bare URL
# mentions), only when a fetch/search/browser tool actually ran (so there
# is ground truth), and the nudge offers a clean escape (drop the link) so
# a rare false positive can't wedge the loop.
_MD_CITATION_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_FETCH_TOOL_HINTS = (
    "search",
    "fetch",
    "browse",
    "browser",
    "web",
    "retrieve",
    "scrape",
    "wiki",
    "crawl",
)


def _turn_fetched_external_content(
    steps: list[ReActStep],
    *,
    prior_observations: str = "",
) -> tuple[bool, str]:
    """Return ``(a fetch/search/browser tool ran, all observation text)``.

    ``prior_observations`` merges tool observations from EARLIER turns of the
    same thread, so a fact the model grounded in a previous turn and reuses
    here counts as evidence — the guard polices fabrication, not multi-turn
    research synthesis.
    """
    fetched = False
    blobs: list[str] = []
    for step in steps:
        names = list(step.actions) if step.actions else ([step.action] if step.action else [])
        for res in step.action_results:
            tool = res.get("tool_name")
            if isinstance(tool, str):
                names.append(tool)
            obs = res.get("observation")
            if isinstance(obs, str):
                blobs.append(obs)
        for name in names:
            if any(hint in str(name).lower() for hint in _FETCH_TOOL_HINTS):
                fetched = True
        if step.observation:
            blobs.append(step.observation)
    if prior_observations:
        blobs.append(prior_observations)
    return fetched, "\n".join(blobs)


def _fabricated_citation_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    prior_observations: str = "",
) -> str | None:
    """Reject a research/chat final that cites source links it never fetched."""
    cited = _MD_CITATION_RE.findall(final_answer or "")
    if not cited:
        return None
    fetched, observations = _turn_fetched_external_content(
        steps, prior_observations=prior_observations
    )
    if not fetched:
        # No research happened this turn — any links are the model's own
        # knowledge, not sources claimed from this turn. Don't police them.
        return None
    seen = observations.lower()
    fabricated = [u for u in cited if u.rstrip("/").lower() not in seen and u.lower() not in seen]
    if not fabricated:
        return None
    return (
        f"Your answer cites {len(fabricated)} source link(s) that never "
        f"appeared in this conversation's tool results (e.g. {fabricated[0]}). Do not "
        "present a URL as a source unless you actually fetched it. Either "
        "fetch/verify the link now, cite only URLs that appear in your "
        "search/fetch observations, or drop the link and state the point as "
        "your own reasoning."
    )


# ── External-fact grounding (non-code turns) ────────────────────────────
# The citation guard above catches fabricated *links*; this one catches
# fabricated *numbers*. When a turn actually fetched content, a currency
# amount / percentage / version / dated fact asserted in the answer is
# treated as a claim sourced from that content — if its digits never appear
# in any observation, the claim is ungrounded. Repair-tier (not hard): the
# model can cite the observation it came from or soften to an approximation.
# Deliberately narrow to keep false positives near zero, mirroring the
# citation guard's boundary: fires only on research turns (fetched=True),
# only for external-fact-shaped numbers (never bare integers / single-dot
# decimals), and the numeric core is matched as a substring of the
# observation digit-stream so any overlapping evidence clears it.
#
# The one way a number legitimately misses the observation digit-stream is
# when it is NOT presented as a source echo — the model's own approximation,
# synthesis, or conversion. So a number is skipped when its immediate context
# carries a hedge / own-understanding marker (约 / 据我了解 / approximately /
# i believe) or an aggregation marker (总价 / 合计 / total / sum). This makes
# the guard's advertised escape real (softening actually clears it) and keeps
# honest synthesis / currency conversion out of the false-positive zone —
# a guard that flags its own escape hatch wedges the loop.
_EXTERNAL_FACT_RE = re.compile(
    r"(?:[¥$€£]\s*)\d{1,3}(?:,\d{3})*(?:\.\d+)?"  # currency-prefixed ¥1,200 / $0.80
    r"|\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:亿|万)?\s*(?:元|美元|人民币)"  # currency-suffixed 1,200元 / 17.6 亿美元
    r"|\d+(?:\.\d+)?\s*%"  # percentage
    r"|\b\d+\.\d+\.\d+(?:[-.]\w+)*\b"  # version N.N.N
    r"|\b(?:19|20)\d{2}[-年]\d{1,2}(?:[-月]\d{1,2}日?)?\b"  # dated fact YYYY-M(-D)
)
_HEDGE_OR_OWN_MARKERS = (
    "约",
    "大约",
    "大概",
    "左右",
    "近",
    "差不多",
    "粗略",
    "估计",
    "可能",
    "据我了解",
    "我判断",
    "我的估计",
    "我推测",
    "我估算",
    "我的了解",
    "我记得",
    "approximately",
    "about",
    "around",
    "roughly",
    "approx",
    "~",
    "i believe",
    "my estimate",
    "best guess",
    "as far as i know",
)
_AGGREGATE_MARKERS = (
    "总计",
    "合计",
    "总价",
    "总额",
    "加总",
    "相加",
    "求和",
    "共计",
    "total",
    "sum",
    "combined",
    "aggregate",
)
_NUMBER_CONTEXT_BEFORE = 18
_NUMBER_CONTEXT_AFTER = 4


def _ungrounded_external_fact_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    prior_observations: str = "",
) -> str | None:
    """Reject a research/chat final that asserts external facts it never fetched."""
    fetched, observations = _turn_fetched_external_content(
        steps, prior_observations=prior_observations
    )
    if not fetched:
        # No research happened this turn — any number is the model's own
        # knowledge or reasoning, not a fact claimed from this turn.
        return None
    obs_digits = re.sub(r"\D", "", observations)
    answer = final_answer or ""
    suppress = _HEDGE_OR_OWN_MARKERS + _AGGREGATE_MARKERS
    ungrounded: list[str] = []
    for match in _EXTERNAL_FACT_RE.finditer(answer):
        fact = match.group(0).strip()
        core = re.sub(r"\D", "", fact)
        if not core or core in obs_digits:
            continue
        window_start = max(0, match.start() - _NUMBER_CONTEXT_BEFORE)
        window_end = match.end() + _NUMBER_CONTEXT_AFTER
        context = answer[window_start:window_end].lower()
        if any(marker in context for marker in suppress):
            # Hedged / own-understanding / synthesized number — the model
            # isn't presenting it as a source echo, so don't police it.
            continue
        ungrounded.append(fact)
    if not ungrounded:
        return None
    shown = ", ".join(dict.fromkeys(ungrounded))
    return (
        f"Your answer asserts external fact(s) — {shown} — that never "
        "appeared in this conversation's search/fetch results (this turn or earlier turns). "
        "Presenting a number as a sourced fact it wasn't sourced from is fabrication. Either "
        "cite the observation the figure actually came from, or soften to "
        'an approximation / your own understanding (e.g. "约 ¥…" / '
        '"据我了解…" / "approximately …"). '
        "Fix the specific claims in place and continue with your existing "
        "synthesis — do NOT re-submit essentially the same full report with "
        "only one figure tweaked, and do not emit a second copy of the whole "
        "report; a near-identical re-submission will be rejected again."
    )


def _research_missing_lookup_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str,
    tools_active: bool,
) -> str | None:
    """Reject a research/chat final that announces a lookup but ran no tools.

    The non-code mirror of ``_code_mode_missing_inspection_tool_guard``: same
    contract (the goal demands external evidence work, so a successful tool
    observation is required), differing only in the goal vocabulary (lookup
    verbs vs project inspection) and the tools-visible gate (any tools vs file
    tools). A research turn answering a pure knowledge question from memory
    legitimately runs zero tools, so the classifier is deliberately narrow.
    """
    if not tools_active:
        return None
    if not _goal_requests_research_lookup(goal):
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    if _has_successful_tool_observation(steps):
        return None
    return (
        "The Final Answer announces an external lookup/search this turn never "
        "actually performed. Execute the stated search/fetch/browser tool, use "
        "its observation as evidence, and then answer with concrete findings. "
        "Do not replace the lookup with a plan or a from-memory summary when "
        "the request asked for current/external information."
    )


def _research_low_quality_evidence_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    goal: str,
) -> str | None:
    """Do not treat an empty/drifted search page as completed research."""
    if not _goal_requests_research_lookup(goal):
        return None
    if _final_answer_requests_user_help(final_answer):
        return None

    saw_bad_search = False
    saw_good_search = False
    saw_verified_page = False
    for step in steps:
        entries: list[tuple[str, str]] = [(str(step.action or ""), str(step.observation or ""))]
        entries.extend(
            (
                str(result.get("tool_name") or ""),
                str(result.get("observation") or ""),
            )
            for result in step.action_results
            if isinstance(result, dict)
        )
        for tool_name, observation in entries:
            lowered_tool = tool_name.lower()
            lowered_obs = observation.lower().replace(" ", "")
            if (
                any(marker in lowered_tool for marker in ("web_fetch", "fetch_url", "browser_get"))
                and observation
                and '"error"' not in lowered_obs
                and "error:" not in lowered_obs
            ):
                saw_verified_page = True
            if "web_search" not in lowered_tool and "search(" not in lowered_tool:
                continue
            if '"quality_warning":"low_relevance"' in lowered_obs or re.search(
                r'"result_count":0(?:\D|$)', lowered_obs
            ):
                saw_bad_search = True
                continue
            if re.search(r'"result_count":[1-9]\d*', lowered_obs) or (
                '"results":[' in lowered_obs and '"url":"http' in lowered_obs
            ):
                saw_good_search = True

    if not saw_bad_search or saw_good_search or saw_verified_page:
        return None
    return (
        "The search evidence is empty or explicitly marked low_relevance, so it cannot "
        "support a completed research answer. Reformulate the query, switch search backend, "
        "or search a primary vertical source, then fetch and verify at least one relevant "
        "page before concluding. Do not diagnose the failed search as the research result."
    )


_CHINESE_COUNT_WORDS = {
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _requested_answer_item_count(goal: str) -> int | None:
    text = str(goal or "")
    chinese = re.search(
        r"(?:最后|最终|请|用|给出|总结|归纳|回答)?[^。；;\n]{0,16}"
        r"([二两三四五六七八九十])\s*(?:点|条|项)"
        r"(?:结论|建议|要点|发现|回答|说明)?",
        text,
    )
    if chinese:
        return _CHINESE_COUNT_WORDS.get(chinese.group(1))
    arabic_cn = re.search(
        r"(?:最后|最终|请|用|给出|总结|归纳|回答)?[^。；;\n]{0,16}"
        r"([2-9]|10)\s*(?:点|条|项)"
        r"(?:结论|建议|要点|发现|回答|说明)?",
        text,
    )
    if arabic_cn:
        return int(arabic_cn.group(1))
    english = re.search(
        r"\b(?:give|provide|return|summari[sz]e(?:\s+in)?|with|in)?\s*"
        r"([2-9]|10)\s+(?:points?|findings?|conclusions?|recommendations?|items?)\b",
        text,
        re.IGNORECASE,
    )
    return int(english.group(1)) if english else None


def _answer_item_count(answer: str) -> int:
    text = str(answer or "")
    numbered = re.findall(r"(?m)^\s*(?:\d+|[一二三四五六七八九十])[.)、．]\s+", text)
    bullets = re.findall(r"(?m)^\s*[-*+]\s+\S", text)
    ordinals = re.findall(
        r"(?:^|\n)\s*(?:第[一二三四五六七八九十\d]+[点条项]|"
        r"(?:第一|第二|第三|第四|第五|第六|第七|第八|第九|第十)[：:,，、])",
        text,
    )
    return max(len(numbered), len(bullets), len(ordinals))


def _answer_item_count_guard(goal: str, final_answer: str) -> str | None:
    requested = _requested_answer_item_count(goal)
    if requested is None:
        return None
    delivered = _answer_item_count(final_answer)
    if delivered >= requested:
        return None
    return (
        "The final answer does not satisfy the user's explicit output shape: "
        f"they requested {requested} distinct points, but only {delivered} "
        "recognizable list item(s) were delivered. Rewrite the answer as a "
        f"numbered list with exactly {requested} substantive items grounded in "
        "the available evidence; do not call more tools merely to fix formatting."
    )


def _control_tag_leak_guard(final_answer: str) -> str | None:
    """Reject internal control tags leaking into the user-visible final answer.

    Some model providers (e.g. agnes-2.5-flash via apihub.agnes-ai.com)
    occasionally echo internal control markers — ``<system-reminder>`` todo
    lists, ``<system-prompt>`` fragments, or private tool envelopes like
    ``<|tool_calls_start|>`` — as assistant text instead of keeping them in
    the inference scaffolding layer. When streamed to the user as a final
    answer, these markers expose internal runtime state and replace the actual
    response the user asked for.

    This guard rejects any final answer containing these control tags and
    nudges the model to continue working instead of treating a leaked reminder
    as a terminal reply. The rejection is **hard** (not advisory): delivering
    internal control text as the user-facing answer is never acceptable, even
    on pure-research turns where other protocol guards are relaxed.

    Coverage:
    - ``<system-reminder>`` / ``<system-prompt>`` / ``<system-context>``
    - ``<think>`` / ``</think>`` internal reasoning markers
    - Provider tool-call envelopes: ``<|tool_calls_start|>`` etc.
    - Literal "This is a reminder that your todo list" phrasing (agnes shape)
    """
    text = str(final_answer or "").strip()
    if not text:
        return None

    # XML-style control tags
    control_tags = [
        "<system-reminder>",
        "<system-prompt>",
        "<system-context>",
        "<system-message>",
        "<think>",
        "</think>",
        "<|tool_calls_start|>",
        "<|tool_calls_end|>",
        "<|im_start|>",
        "<|im_end|>",
    ]
    for tag in control_tags:
        if tag in text.lower():
            return (
                f"The proposed Final Answer contains an internal control tag ({tag}) "
                "that must never be shown to the user. This is a system marker, not "
                "actual response content. Continue working on the user's request and "
                "produce a real answer without any control tags or internal reminders."
            )

    # Bracketed prompt envelopes are injected by the convergence/public-update
    # layer to keep the original task and evidence near a model call. Some
    # providers echo them verbatim; reject that before persistence so the UI
    # never has to display duplicated user requests or internal evidence.
    bracketed_control_tags = [
        "[original-user-request]",
        "[/original-user-request]",
        "[just-completed-evidence]",
        "[/just-completed-evidence]",
        "[next-public-scope]",
        "[/next-public-scope]",
        "[bounded-read-evidence]",
        "[/bounded-read-evidence]",
        "[explicit-read-scope]",
    ]
    for tag in bracketed_control_tags:
        if tag in text.lower():
            return (
                f"The proposed Final Answer contains an internal prompt envelope ({tag}) "
                "that must never be shown to the user. Answer the original request "
                "directly without repeating prompt packets, evidence envelopes, or tags."
            )

    # Literal reminder phrasing (agnes-2.5-flash echoes this verbatim)
    if "This is a reminder that your todo list" in text:
        return (
            'The proposed Final Answer is an internal todo-list reminder ("This is '
            'a reminder that your todo list..."), not the user-facing response. '
            "Continue executing the pending tasks and deliver the actual research "
            "findings, analysis, or completed work the user asked for."
        )

    return None


__all__ = [
    "_answer_item_count",
    "_answer_item_count_guard",
    "_control_tag_leak_guard",
    "_fabricated_citation_guard",
    "_incomplete_final_answer_guard",
    "_requested_answer_item_count",
    "_research_missing_lookup_guard",
    "_research_low_quality_evidence_guard",
    "_turn_fetched_external_content",
]
