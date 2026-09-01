from __future__ import annotations

import re
from typing import Any

from ..openai_formatting import (  # noqa: E402,I001
    _pick_output_keys,  # noqa: F401
    _pick_preview_keys,  # noqa: F401
    _short,  # noqa: F401
)
from ..openai_formatting import (
    step_effective_success as _step_effective_success,
)
from ..openai_formatting import (
    summarize_step_for_stream as _summarize_step_for_stream,
)

_RESEARCH_REPORT_KEYWORDS = (
    "deep research",
    "market research",
    "industry report",
    "competitive analysis",
    "competitor",
    "white paper",
    "调研",
    "深度研究",
    "市场研究",
    "市场调研",
    "行业报告",
    "研究报告",
    "竞品",
    "竞争分析",
    "报告",
)


def _research_context_mode(user_context: dict[str, Any] | None) -> str:
    if not isinstance(user_context, dict):
        return ""
    metadata = user_context.get("metadata")
    if isinstance(metadata, dict):
        mode = metadata.get("mode")
        if isinstance(mode, str):
            return mode.strip().lower()
    mode = user_context.get("mode")
    return mode.strip().lower() if isinstance(mode, str) else ""


def _is_research_report_context(
    goal: str,
    user_context: dict[str, Any] | None,
) -> bool:
    text = (goal or "").strip().lower()
    if not text:
        return False
    if any(keyword.lower() in text for keyword in _RESEARCH_REPORT_KEYWORDS):
        return True
    if _research_context_mode(user_context) == "deep" and len(text) >= 8:
        return True
    if isinstance(user_context, dict):
        plan = user_context.get("thinking_plan")
        if isinstance(plan, dict) and plan.get("suggest_deep_research") is True:
            return True
    return False


def _research_report_section_score(text: str) -> int:
    lowered = text.lower()
    score = 0
    section_groups = (
        ("执行摘要", "executive summary", "summary"),
        ("调研范围", "方法", "method", "scope"),
        ("关键发现", "findings", "结论"),
        ("证据", "来源", "evidence", "source"),
        ("风险", "不确定", "limitations", "risk"),
        ("建议", "下一步", "recommendation", "next"),
    )
    for group in section_groups:
        if any(item in lowered for item in group):
            score += 1
    score += min(3, len(re.findall(r"^##\s+", text, flags=re.MULTILINE)))
    return score


def _looks_like_complete_research_report(text: str) -> bool:
    clean = (text or "").strip()
    if len(clean) < 500:
        return False
    if clean.startswith(("{", "[")) and '"nodes"' in clean:
        return False
    if re.search(r"^\s*[✓✔]\s+\w+", clean) and "step(s)" in clean:
        return False
    if "Tool outputs:" in clean and "Write the final reply" in clean:
        return False
    return _research_report_section_score(clean) >= 6


def _assistant_text_from_trajectory(traj: Any) -> str:
    if not traj.steps:
        return "(no steps executed)"

    lines: list[str] = []
    for step in traj.steps:
        line = _summarize_step_for_stream(step)
        lines.append(line)

    effective_success = bool(traj.outcome.success) and all(
        _step_effective_success(step) for step in traj.steps
    )
    status_tag = "OK" if effective_success else "FAILED"
    summary = f"[{status_tag} · {traj.step_count} step(s)]"
    return "\n".join(lines + ["", summary])


def _format_research_report_fallback(
    *,
    goal: str,
    trajectory: Any,
    user_context: dict[str, Any] | None,
) -> str:
    from .tool_converter import _trajectory_research_evidence, _trajectory_tool_lines

    evidence = _trajectory_research_evidence(trajectory)
    tool_lines = _trajectory_tool_lines(trajectory)
    mode = _research_context_mode(user_context) or "research"
    source_count = len(evidence)
    step_count = int(getattr(trajectory, "step_count", 0) or len(tool_lines))

    if evidence:
        finding_lines = [
            f"{index}. **{item['title']}**：{item['snippet'] or '返回了可追踪来源，但摘要为空。'}"
            + (f"（{item['url']}）" if item["url"] else "")
            for index, item in enumerate(evidence[:8], 1)
        ]
    else:
        finding_lines = [
            "1. 本轮没有拿到足够可追踪的网页/材料证据，不能负责任地给出完整事实性结论。",
            "2. 已保留执行记录，建议扩大搜索源或补充材料后重新运行。",
        ]

    evidence_table = [
        "| # | 来源 | 查询/渠道 | 摘要 |",
        "| --- | --- | --- | --- |",
    ]
    for index, item in enumerate(evidence[:12], 1):
        source = _markdown_table_cell(item["title"])
        if item["url"]:
            source = f"[{source}]({item['url']})"
        evidence_table.append(
            "| "
            f"{index} | {source} | {_markdown_table_cell(item['query'] or item['tool'])} | "
            f"{_markdown_table_cell(item['snippet'] or '无摘要')} |"
        )
    if len(evidence_table) == 2:
        evidence_table.append("| 1 | 暂无 | 当前工具输出未包含可引用来源 | 需要继续检索 |")

    execution_lines = tool_lines or ["1. 未记录到工具步骤。"]
    coverage_note = (
        "当前报告已按正式报告格式收束，但证据量偏少；以下结论只代表本轮工具返回结果，"
        "不能替代完整多源交叉验证。"
        if source_count < 5
        else "当前报告基于本轮可追踪来源整理，并保留了证据表供复核。"
    )

    return "\n".join(
        [
            f"# {goal.strip()} 深度研究报告",
            "",
            "## 执行摘要",
            f"- 运行模式：{mode}。",
            f"- 本轮完成 {step_count} 个执行步骤，整理到 {source_count} 条可追踪来源/证据。",
            f"- {coverage_note}",
            "",
            "## 调研范围与方法",
            "- 先读取本轮对话目标和已提供材料，再使用工具返回的搜索/网页结果作为证据池。",
            "- 只把工具结果中可追踪的标题、链接、摘要写入报告；证据不足的位置明确标记为待补查。",
            "- 不把临时推理过程当作结论，最终报告只保留可读的结论、证据和下一步。",
            "",
            "## 关键发现",
            *finding_lines,
            "",
            "## 证据与来源",
            *evidence_table,
            "",
            "## 不确定性与缺口",
            "- 若搜索只命中少量网页，市场规模、份额、价格区间等量化结论需要二次核验。",
            "- 若缺少官方资料、行业报告或用户评论样本，竞品对比和机会判断只能作为初步假设。",
            "- 若需要发布级报告，应补充发布时间、地区范围、样本来源和冲突证据。",
            "",
            "## 结论与建议",
            "- 先把本轮来源分成官方信息、第三方评测、渠道/电商、社区反馈四类，再补齐缺口。",
            "- 对关键判断至少保留两类来源交叉验证，避免只依赖单一搜索结果。",
            "- 下一轮建议扩大搜索到新闻、厂商官网、论坛/社区、评测站和渠道报价，并输出对比表。",
            "",
            "## 执行记录",
            *execution_lines,
        ]
    )


def _clean_report_text(value: Any, *, max_len: int = 320) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _markdown_table_cell(value: Any) -> str:
    return _clean_report_text(value, max_len=220).replace("|", "\\|")
