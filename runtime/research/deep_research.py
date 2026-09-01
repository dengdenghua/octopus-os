"""Deep research planning primitives.

This module builds the job plan for a GPT-style deep research workflow:
user-provided files/sites/text are treated as first-class materials. The
planner may split larger work across role-specific subagents, but small runs
stay narrow instead of forcing a fixed swarm template.

The planner is deterministic on purpose. It gives the UI and API a stable
contract before any LLM-driven splitter or search backend is plugged in.

This module was split into ``_deep_research_models`` (Pydantic contracts and
type aliases) and ``_deep_research_helpers`` (pure helper functions). The
``DeepResearchPlanner`` class lives here and re-exports the public contracts
so existing importers keep working unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.research._deep_research_helpers import (
    _URL_RE,
    _coerce_confidence,
    _coerce_source_kind,
    _coerce_stance,
    _coerce_str,
    _dedupe_evidence,
    _dedupe_materials,
    _default_roles,
    _default_sources,
    _evidence_pool_lines,
    _evidence_table,
    _material_lines,
    _normalize_roles,
    _resolve_subagent_budget,
    _route_decision_audit_lines,
    _searches_for_role,
    _seed_evidence,
    _source_instruction_lines,
    _step_for_role,
    _synthesis_step,
    _url_materials,
    _with_evidence_pool,
)
from runtime.research._deep_research_models import (
    DeepResearchRequest,
    ResearchDepth,
    ResearchEvidence,
    ResearchJob,
    ResearchMaterial,
    ResearchPrefetchAction,
    ResearchPrefetchLog,
    ResearchPrefetchStatus,
    ResearchRole,
    ResearchRouteDecision,
    ResearchSource,
    ResearchSourceKind,
    ResearchSourceProvider,
    ResearchStep,
    ResearchStepStatus,
)


class DeepResearchPlanner:
    """Deterministic planner for GPT-style deep research jobs.

    Pydantic models live in ``_deep_research_models`` and pure helpers in
    ``_deep_research_helpers``; this class orchestrates them into a job plan.
    """

    def build_plan(
        self,
        request: DeepResearchRequest,
        *,
        thread_materials: list[ResearchMaterial] | None = None,
    ) -> ResearchJob:
        materials = list(request.materials)
        if request.include_thread_uploads and thread_materials:
            materials.extend(thread_materials)
        materials.extend(_url_materials(request.urls))
        materials = _dedupe_materials(materials)

        sources = _default_sources(request.topic, request.source_kinds, materials)
        subagent_budget = _resolve_subagent_budget(
            request,
            materials=materials,
            sources=sources,
        )
        roles = _normalize_roles(request.roles or _default_roles(request.topic, subagent_budget))[
            :subagent_budget
        ]
        steps = [
            _step_for_role(
                role,
                topic=request.topic,
                sources=sources,
                materials=materials,
                searches=_searches_for_role(
                    total=request.max_searches,
                    role_index=index,
                    role_count=len(roles),
                ),
            )
            for index, role in enumerate(roles)
        ]
        steps.append(_synthesis_step(request.topic, roles, sources, materials))

        return ResearchJob(
            job_id=f"research_{uuid4().hex[:12]}",
            thread_id=request.thread_id,
            lead_agent_name=request.lead_agent_name,
            topic=request.topic,
            depth=request.depth,
            locale=request.locale,
            created_at=datetime.now(UTC).isoformat(),
            materials=materials,
            sources=sources,
            evidence=_seed_evidence(request.topic, sources, materials),
            roles=roles,
            steps=steps,
            max_searches=request.max_searches,
            final_report_format=request.final_report_format,
        )

    def dispatch_tasks(self, job: ResearchJob) -> list[dict[str, Any]]:
        """Convert research steps into parallel-agent dispatch tasks."""
        tasks: list[dict[str, Any]] = []
        for step in job.steps:
            if step.role_id == "synthesis":
                continue
            role = next((r for r in job.roles if r.id == step.role_id), None)
            tasks.append(
                {
                    "task_id": step.id,
                    "description": step.prompt,
                    "subagent_name": role.subagent_name if role else "virtual-researcher",
                    "priority": 0,
                    "depends_on": [],
                }
            )
        return tasks

    def attach_evidence_pool(
        self,
        job: ResearchJob,
        evidence: list[ResearchEvidence],
        prefetch_logs: list[ResearchPrefetchLog] | None = None,
    ) -> ResearchJob:
        """Attach prefetch evidence and refresh worker prompts with an evidence pool."""
        if evidence:
            job.evidence = _dedupe_evidence([*job.evidence, *evidence])
        if prefetch_logs:
            job.prefetch_logs.extend(prefetch_logs)
        evidence_lines = _evidence_pool_lines(job.evidence)
        for step in job.steps:
            if step.role_id == "synthesis":
                continue
            step.prompt = _with_evidence_pool(step.prompt, evidence_lines)
        return job

    def synthesize_report(
        self,
        job: ResearchJob,
        *,
        aggregated_content: str,
    ) -> str:
        """Build a deterministic final report from completed role outputs.

        This is intentionally a stable baseline. A later LLM synthesis pass can
        replace this while keeping the same persisted `final_report` contract.
        """
        source_lines = _source_instruction_lines(job.sources)
        material_lines = _material_lines(job.materials)
        evidence_lines = _evidence_table(job.evidence)
        route_lines = _route_decision_audit_lines(job.route_decisions)
        lead = job.lead_agent_name or "current lead agent"
        return f"""# {job.topic} 深度研究报告

负责人: {lead}
研究任务: {job.job_id}
研究深度: {job.depth}

## 执行摘要
- 本报告汇总 {len(job.roles)} 个临时虚拟研究角色的并行结果，并由负责人统一收束。
- 临时虚拟角色只服务本次任务，不携带独立长期记忆；稳定结论只回写负责人记忆。
- 报告优先保留可追踪来源、冲突点和证据强弱，避免把未经验证的信息写成确定事实。

## 调研范围与方法
1. 围绕「{job.topic}」拆分市场、用户、产品价格、渠道销售、反方验证等角度。
2. 按来源类型分流搜索和材料读取，优先使用用户材料、官方资料、第三方报告、社区/评论等可复核来源。
3. 将各角色输出统一合并为结论、证据、缺口和建议，供后续二次验证或正式发布。

## 治理与路由审计
{route_lines}

## 研究来源
{source_lines}

## 用户材料
{material_lines}

## 关键发现与分角色结果
{aggregated_content.strip() or "- 暂无角色输出。"}

## 证据与来源 / Evidence Table
{evidence_lines}

## 不确定性与缺口
- 若证据表缺少发布日期、样本量或原始出处，对应结论应标记为初步判断。
- 市场规模、份额、销量、价格区间等量化信息需要至少两类来源交叉验证。
- 对社区评价、论坛讨论和电商评论要注意样本偏差，不能直接等同于总体用户需求。

## 结论与建议
1. 将高置信结论沉淀为负责人记忆，保留 job_id 方便追溯。
2. 对低置信或冲突结论追加二次搜索任务，优先补官方/行业/渠道三类来源。
3. 若要输出对外版报告，建议补充表格化竞品对比、价格区间、渠道证据和引用日期。
"""

    def extract_evidence_from_outputs(
        self,
        job: ResearchJob,
        *,
        aggregated_content: str,
    ) -> list[ResearchEvidence]:
        """Parse structured evidence hints from subagent output.

        Supported line format:
        EVIDENCE {"title":"...","url":"https://...","claim":"..."}
        """
        evidence = list(job.evidence)
        seen = {(ev.url or "", ev.claim or "", ev.title or "") for ev in evidence}
        for raw_line in aggregated_content.splitlines():
            line = raw_line.strip()
            if not line.upper().startswith("EVIDENCE"):
                continue
            payload = line[len("EVIDENCE") :].strip(" :-")
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            ev = ResearchEvidence(
                job_id=job.job_id,
                step_id=_coerce_str(data.get("step_id")),
                role_id=_coerce_str(data.get("role_id")),
                title=_coerce_str(data.get("title")) or "Evidence",
                url=_coerce_str(data.get("url")),
                source_kind=_coerce_source_kind(data.get("source_kind")),
                published_at=_coerce_str(data.get("published_at")),
                quote_or_summary=_coerce_str(data.get("quote_or_summary") or data.get("summary")),
                claim=_coerce_str(data.get("claim")),
                stance=_coerce_stance(data.get("stance")),
                confidence=_coerce_confidence(data.get("confidence")),
            )
            key = (ev.url or "", ev.claim or "", ev.title or "")
            if key in seen:
                continue
            seen.add(key)
            evidence.append(ev)
        for url in _URL_RE.findall(aggregated_content):
            key = (url, "", url)
            if key in seen:
                continue
            seen.add(key)
            evidence.append(
                ResearchEvidence(
                    job_id=job.job_id,
                    title=url,
                    url=url,
                    source_kind="web",
                    quote_or_summary="URL mentioned by a virtual research worker.",
                    claim=job.topic,
                    stance="context",
                    confidence=0.4,
                )
            )
        return evidence

    def build_lead_memory_entry(self, job: ResearchJob) -> str:
        """Summarize a completed research job for the lead agent memory."""
        source_kinds = ", ".join(s.kind for s in job.sources if s.enabled) or "none"
        report_excerpt = (job.final_report or "").strip().replace("\n", " ")
        if len(report_excerpt) > 700:
            report_excerpt = report_excerpt[:700].rstrip() + "..."
        return (
            f"Deep research completed for topic '{job.topic}' "
            f"(job={job.job_id}, sources={source_kinds}, searches={job.max_searches}). "
            f"Final report summary: {report_excerpt}"
        )

    def write_lead_memory(
        self,
        job: ResearchJob,
        *,
        agents_root: Path | None = None,
    ) -> ResearchJob:
        """Persist the research memory to the selected lead agent only."""
        lead = (job.lead_agent_name or "").strip()
        if not lead or lead.startswith("virtual-research-"):
            return job
        if job.memory_written_at:
            return job
        if not job.final_report:
            return job
        if "/" in lead or "\\" in lead or lead in {".", ".."}:
            return job

        if agents_root is None:
            from runtime.platform.process.paths import project_root

            root = project_root() / "agents"
        else:
            root = agents_root
        core = root / lead / "agent-core"
        core.mkdir(parents=True, exist_ok=True)
        path = core / "MEMORY.md"
        entry = self.build_lead_memory_entry(job)
        now = datetime.now(UTC).isoformat()
        line = f"- [{now} · deep-research,{job.job_id}] {entry}\n"

        if path.exists():
            text = path.read_text(encoding="utf-8")
            if job.job_id in text:
                job.memory_entry = entry
                job.memory_written_at = now
                job.memory_path = str(path)
                return job
            if "_No memories yet._" in text:
                cleaned = (
                    "\n".join(
                        ln for ln in text.splitlines() if ln.strip() != "_No memories yet._"
                    ).rstrip()
                    + "\n"
                )
                path.write_text(cleaned, encoding="utf-8")

        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        job.memory_entry = entry
        job.memory_written_at = now
        job.memory_path = str(path)
        return job


__all__ = [
    "DeepResearchPlanner",
    "DeepResearchRequest",
    "ResearchDepth",
    "ResearchEvidence",
    "ResearchJob",
    "ResearchMaterial",
    "ResearchPrefetchAction",
    "ResearchPrefetchLog",
    "ResearchPrefetchStatus",
    "ResearchRole",
    "ResearchRouteDecision",
    "ResearchSource",
    "ResearchSourceKind",
    "ResearchSourceProvider",
    "ResearchStep",
    "ResearchStepStatus",
]
