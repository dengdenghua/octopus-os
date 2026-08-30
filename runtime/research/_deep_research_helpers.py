"""Pure helper functions for deep-research planning.

Extracted from ``deep_research.py`` as part of a structural split. These
are stateless helpers consumed by the planner module; they rely only on the
Pydantic contracts in ``_deep_research_models`` and never refer back to the
planner class, so there is no circular import.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

from runtime.research._deep_research_models import (
    DeepResearchRequest,
    ResearchEvidence,
    ResearchMaterial,
    ResearchRole,
    ResearchRouteDecision,
    ResearchSource,
    ResearchSourceKind,
    ResearchStep,
)

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _url_materials(urls: list[str]) -> list[ResearchMaterial]:
    out: list[ResearchMaterial] = []
    for url in urls:
        clean = url.strip()
        if not clean:
            continue
        out.append(ResearchMaterial(kind="url", title=clean, url=clean))
    return out


def _dedupe_materials(materials: list[ResearchMaterial]) -> list[ResearchMaterial]:
    seen: set[tuple[str, str]] = set()
    out: list[ResearchMaterial] = []
    for material in materials:
        key_value = material.url or material.path or material.text or material.title or material.id
        key = (material.kind, key_value.strip() if isinstance(key_value, str) else str(key_value))
        if key in seen:
            continue
        seen.add(key)
        out.append(material)
    return out


def _seed_evidence(
    topic: str,
    sources: list[ResearchSource],
    materials: list[ResearchMaterial],
) -> list[ResearchEvidence]:
    evidence: list[ResearchEvidence] = []
    provided = next((s for s in sources if s.kind == "provided_url"), None)
    for mat in materials:
        if mat.kind not in ("url", "site") or not mat.url:
            continue
        evidence.append(
            ResearchEvidence(
                title=mat.title or mat.url,
                url=mat.url,
                source_kind="provided_url",
                quote_or_summary=mat.notes or "User-provided source for this research run.",
                claim=topic,
                stance="context",
                confidence=0.6,
                step_id=None,
                role_id=None,
            )
        )
    if not evidence and provided is not None:
        evidence.append(
            ResearchEvidence(
                title=provided.label,
                source_kind=provided.kind,
                quote_or_summary=provided.query_hint,
                claim=topic,
                stance="context",
                confidence=0.3,
            )
        )
    return evidence


def _dedupe_evidence(evidence: list[ResearchEvidence]) -> list[ResearchEvidence]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ResearchEvidence] = []
    for item in evidence:
        key = (item.url or "", item.claim or "", item.title or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _default_sources(
    topic: str,
    kinds: list[ResearchSourceKind],
    materials: list[ResearchMaterial],
) -> list[ResearchSource]:
    labels: dict[ResearchSourceKind, str] = {
        "web": "Web search",
        "news": "News and recent market signals",
        "academic": "Academic and standards literature",
        "company_site": "Official company/product sites",
        "ecommerce": "Retail/ecommerce listings",
        "social": "Social media and creator content",
        "forum": "Forums, communities, reviews",
        "uploaded_file": "Uploaded files",
        "provided_url": "User-provided sites",
        "local_file": "Local files",
    }
    sources: list[ResearchSource] = []
    for kind in dict.fromkeys(kinds):
        if kind == "uploaded_file" and not any(m.kind == "file" for m in materials):
            continue
        if kind == "provided_url" and not any(m.kind in ("url", "site") for m in materials):
            continue
        route = _source_route(topic, kind, materials)
        sources.append(
            ResearchSource(
                kind=kind,
                label=labels[kind],
                query_hint=_query_hint(topic, kind),
                provider=route["provider"],
                query_templates=route["query_templates"],
                site_filters=route["site_filters"],
                freshness_days=route["freshness_days"],
            )
        )
    return sources


def _source_route(
    topic: str,
    kind: ResearchSourceKind,
    materials: list[ResearchMaterial],
) -> dict[str, Any]:
    provided_hosts = _material_hosts(materials)
    routes: dict[ResearchSourceKind, dict[str, Any]] = {
        "web": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} market overview competitors trends",
                f"{topic} report analysis forecast",
            ],
            "site_filters": [],
            "freshness_days": None,
        },
        "news": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} latest news market report 2026",
                f"{topic} shipment revenue funding acquisition latest",
            ],
            "site_filters": [
                "Reuters",
                "Bloomberg",
                "PR Newswire",
                "company newsroom",
            ],
            "freshness_days": 365,
        },
        "academic": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} research paper benchmark methodology",
                f"{topic} standard whitepaper pdf",
            ],
            "site_filters": [
                "site:scholar.google.com",
                "site:arxiv.org",
                "site:ieee.org",
                "filetype:pdf",
            ],
            "freshness_days": None,
        },
        "company_site": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} official product specification pricing",
                f"{topic} datasheet release notes support lifecycle",
            ],
            "site_filters": ["official domains", *provided_hosts],
            "freshness_days": None,
        },
        "ecommerce": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} price best seller reviews",
                f"{topic} Amazon JD Tmall price ranking",
            ],
            "site_filters": [
                "site:amazon.com",
                "site:jd.com",
                "site:tmall.com",
                "site:newegg.com",
            ],
            "freshness_days": 180,
        },
        "social": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} YouTube review Reddit discussion",
                f"{topic} creator review sentiment complaints",
            ],
            "site_filters": [
                "site:youtube.com",
                "site:x.com",
                "site:bilibili.com",
                "site:zhihu.com",
            ],
            "freshness_days": 365,
        },
        "forum": {
            "provider": "web_search",
            "query_templates": [
                f"{topic} forum review complaint problem",
                f"{topic} reddit community user experience",
            ],
            "site_filters": [
                "site:reddit.com",
                "site:forums.servethehome.com",
                "site:community.synology.com",
                "site:forums.unraid.net",
            ],
            "freshness_days": 730,
        },
        "uploaded_file": {
            "provider": "uploaded_file",
            "query_templates": [
                "extract claims, tables, assumptions, and citations from uploaded files",
            ],
            "site_filters": [],
            "freshness_days": None,
        },
        "provided_url": {
            "provider": "fetch_url",
            "query_templates": [
                "open user-provided URLs directly, extract claims, dates, and source authority",
            ],
            "site_filters": provided_hosts,
            "freshness_days": None,
        },
        "local_file": {
            "provider": "local_file",
            "query_templates": [
                "read local files and extract facts, tables, assumptions, and citations",
            ],
            "site_filters": [],
            "freshness_days": None,
        },
    }
    return routes[kind]


def _material_hosts(materials: list[ResearchMaterial]) -> list[str]:
    hosts: list[str] = []
    for material in materials:
        if material.kind not in ("url", "site") or not material.url:
            continue
        parsed = urlparse(material.url)
        host = parsed.netloc.lower().removeprefix("www.")
        if host and host not in hosts:
            hosts.append(f"site:{host}")
    return hosts[:8]


def _query_hint(topic: str, kind: ResearchSourceKind) -> str:
    suffix = {
        "web": "market overview competitors trends",
        "news": "latest news funding shipment revenue report",
        "academic": "research paper standard benchmark methodology",
        "company_site": "official product specification pricing datasheet",
        "ecommerce": "price reviews ranking sales channels",
        "social": "user pain points creator reviews sentiment",
        "forum": "reddit forum community review problem complaint",
        "uploaded_file": "extract claims data tables assumptions",
        "provided_url": "extract facts claims pricing products",
        "local_file": "extract facts tables references",
    }[kind]
    return f"{topic} {suffix}"


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_source_kind(value: Any) -> ResearchSourceKind | None:
    value = _coerce_str(value)
    allowed = {
        "web",
        "news",
        "academic",
        "company_site",
        "ecommerce",
        "social",
        "forum",
        "uploaded_file",
        "provided_url",
        "local_file",
    }
    return value if value in allowed else None  # type: ignore[return-value]


def _coerce_stance(value: Any) -> Literal["support", "contradict", "context"]:
    value = _coerce_str(value)
    if value in {"support", "contradict", "context"}:
        return value  # type: ignore[return-value]
    return "context"


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _normalize_roles(roles: list[ResearchRole]) -> list[ResearchRole]:
    normalized: list[ResearchRole] = []
    seen_ids: set[str] = set()
    for index, role in enumerate(roles):
        fallback_id = f"role_{index + 1}"
        role_id = _slug_role_id(role.id or fallback_id) or fallback_id
        if role_id in seen_ids:
            role_id = f"{role_id}_{index + 1}"
        seen_ids.add(role_id)

        subagent_name = _virtual_subagent_name(role.subagent_name, role_id)
        normalized.append(
            role.model_copy(
                update={
                    "id": role_id,
                    "name": role.name.strip() or role_id.replace("_", " ").title(),
                    "subagent_name": subagent_name,
                    "focus": role.focus.strip() or "General research and source verification.",
                    "deliverable": role.deliverable.strip() or "Research findings with evidence.",
                    "search_angles": [
                        angle.strip() for angle in role.search_angles if angle.strip()
                    ],
                }
            )
        )
    return normalized


def _virtual_subagent_name(value: str, role_id: str) -> str:
    raw = (value or "").strip()
    suffix = raw[len("virtual-research-") :] if raw.startswith("virtual-research-") else role_id
    suffix = _slug_role_id(suffix) or role_id
    return f"virtual-research-{suffix}"


def _slug_role_id(value: str) -> str:
    value = _SAFE_ID_RE.sub("-", value.strip().lower()).strip("-_")
    value = re.sub(r"-{2,}", "-", value)
    return value[:64]


def _resolve_subagent_budget(
    request: DeepResearchRequest,
    *,
    materials: list[ResearchMaterial],
    sources: list[ResearchSource],
) -> int:
    if request.max_subagents is not None:
        return request.max_subagents
    if request.roles:
        return max(1, min(12, len(request.roles)))

    topic = request.topic.strip()
    topic_l = topic.lower()
    score = 0
    if request.depth == "deep":
        score += 1
    elif request.depth == "quick":
        score -= 1
    if request.max_searches >= 240:
        score += 1
    if request.max_searches >= 600:
        score += 1
    if len(materials) >= 3:
        score += 1
    if len(materials) >= 8:
        score += 1
    if len([source for source in sources if source.enabled]) >= 6:
        score += 1
    if len(re.findall(r"[\w\u4e00-\u9fff]+", topic)) >= 14:
        score += 1
    if re.search(
        r"market|competitor|research|report|architecture|migration|audit|"
        r"compare|strategy|roadmap|调研|研究|报告|竞品|市场|架构|迁移|审计|"
        r"对比|策略|路线图",
        topic_l,
    ):
        score += 1

    if score <= 0:
        return 1
    if score <= 2:
        return 2
    if score <= 4:
        return 3
    return 5


def _default_roles(topic: str, max_subagents: int) -> list[ResearchRole]:
    roles = [
        ResearchRole(
            id="market_landscape",
            name="市场格局研究员",
            subagent_name="virtual-research-market-landscape",
            focus="行业规模、主要玩家、产品线、区域差异和趋势",
            deliverable="市场格局摘要、主流厂商表、关键趋势和不确定性",
            search_angles=[
                "global and China market overview",
                "major vendors and product families",
                "recent launches and demand shifts",
            ],
        ),
        ResearchRole(
            id="user_needs",
            name="用户与场景分析师",
            subagent_name="virtual-research-user-needs",
            focus="目标用户、使用场景、购买动机、痛点和未满足需求",
            deliverable="用户分层、场景地图、痛点证据和机会假设",
            search_angles=[
                "buyer personas and jobs to be done",
                "reviews complaints forum discussions",
                "home prosumer SMB enterprise use cases",
            ],
        ),
        ResearchRole(
            id="product_pricing",
            name="产品与价格分析师",
            subagent_name="virtual-research-product-pricing",
            focus="价格区间、性能、功能、规格、套件和差异化",
            deliverable="价格/性能/功能对比表和关键选型标准",
            search_angles=[
                "pricing tiers and best sellers",
                "spec comparison performance benchmark",
                "feature gaps and buying criteria",
            ],
        ),
        ResearchRole(
            id="channel_sales",
            name="渠道与销售研究员",
            subagent_name="virtual-research-channel-sales",
            focus="销售渠道、分销模式、内容渠道、区域打法和市场份额线索",
            deliverable="渠道地图、销售模式、份额 proxy 和证据链接",
            search_angles=[
                "retail ecommerce distributors",
                "market share shipment proxy",
                "content channel and affiliate strategy",
            ],
        ),
        ResearchRole(
            id="skeptic",
            name="反方验证员",
            subagent_name="virtual-research-skeptic",
            focus="识别夸大、过时、样本偏差、证据不足和相互矛盾的信息",
            deliverable="风险清单、证据可信度评分、需要二次验证的问题",
            search_angles=[
                "contradictory evidence",
                "source reliability",
                "missing data and caveats",
            ],
        ),
    ]
    # Keep the defaults general but let small runs retain the core angles.
    if max_subagents <= 3:
        return [roles[0], roles[1], roles[2]]
    return roles


def _searches_for_role(*, total: int, role_index: int, role_count: int) -> int:
    if role_count <= 0:
        return total
    base = max(1, total // role_count)
    remainder = total % role_count
    return base + (1 if role_index < remainder else 0)


def _source_instruction_lines(sources: list[ResearchSource]) -> str:
    lines: list[str] = []
    for source in sources:
        if not source.enabled:
            continue
        queries = "; ".join(source.query_templates[:3]) or source.query_hint
        filters = ", ".join(source.site_filters[:6])
        freshness = f"; freshness <= {source.freshness_days} days" if source.freshness_days else ""
        suffix = f"; filters: {filters}" if filters else ""
        lines.append(
            f"- {source.label} [{source.provider}]: {source.query_hint}"
            f"\n  queries: {queries}{suffix}{freshness}"
        )
    return "\n".join(lines) or "- No enabled sources recorded."


def _evidence_pool_lines(evidence: list[ResearchEvidence]) -> str:
    if not evidence:
        return "- No pre-seeded evidence. Build evidence from source routes and materials."
    lines: list[str] = []
    for item in evidence[:20]:
        source = item.title or item.url or item.source_kind or "Evidence"
        url = f" ({item.url})" if item.url else ""
        summary = item.quote_or_summary or item.claim or "Context evidence"
        lines.append(
            f"- {source}{url}: {summary[:500]} "
            f"[{item.source_kind or 'source'}; confidence={item.confidence:.2f}]"
        )
    return "\n".join(lines)


def _with_evidence_pool(prompt: str, evidence_lines: str) -> str:
    marker = "\n\n初始证据池：\n"
    prompt = prompt.split(marker, 1)[0].rstrip()
    return (
        f"{prompt}{marker}{evidence_lines}\n\n"
        "请先核验初始证据池，再补充新的搜索结果；如果证据冲突，请明确标注。"
    )


def _step_for_role(
    role: ResearchRole,
    *,
    topic: str,
    sources: list[ResearchSource],
    materials: list[ResearchMaterial],
    searches: int,
) -> ResearchStep:
    source_ids = [s.id for s in sources if s.enabled]
    material_lines = _material_lines(materials)
    source_lines = _source_instruction_lines(sources)
    angle_lines = "\n".join(f"- {angle}" for angle in role.search_angles)
    prompt = f"""你是{role.name}。围绕主题「{topic}」做深度研究。

你的角度：
{role.focus}

优先搜索/核验这些方向：
{angle_lines}

可用来源：
{source_lines}

工具路由：优先按上面 provider 使用 web_search / fetch_url / uploaded_file 等渠道，并保留 query、url、发布日期和冲突信息。

用户提供材料：
{material_lines}

搜索预算：最多 {searches} 次搜索或页面读取。要求记录关键来源、日期、证据强弱和冲突点。

输出：
{role.deliverable}

证据要求：每个关键结论后至少给出 1 条 EVIDENCE JSON 行，格式如下：
EVIDENCE {{"title":"source title","url":"https://example.com","source_kind":"web","published_at":"YYYY-MM-DD or unknown","claim":"supported claim","stance":"support","confidence":0.7,"quote_or_summary":"short source summary"}}
"""
    return ResearchStep(
        id=f"step_{role.id}",
        title=role.deliverable,
        role_id=role.id,
        source_ids=source_ids,
        expected_searches=searches,
        prompt=prompt.strip(),
    )


def _synthesis_step(
    topic: str,
    roles: list[ResearchRole],
    sources: list[ResearchSource],
    materials: list[ResearchMaterial],
) -> ResearchStep:
    role_lines = "\n".join(f"- {r.name}: {r.deliverable}" for r in roles)
    source_lines = _source_instruction_lines(sources)
    prompt = f"""汇总主题「{topic}」的深度研究结果。

你将收到以下角色的研究结果：
{role_lines}

需要交叉核验的来源类型：
{source_lines}

用户提供材料：
{_material_lines(materials)}

最终报告结构：
1. 执行摘要
2. 关键结论和证据
3. 市场/用户/产品/渠道分章节
4. 对比表
5. 风险、冲突信息和证据等级
6. 机会建议和下一步验证清单
"""
    return ResearchStep(
        id="step_synthesis",
        title="汇总趋势、痛点与机会并形成结论建议",
        role_id="synthesis",
        source_ids=[s.id for s in sources if s.enabled],
        expected_searches=0,
        prompt=prompt.strip(),
    )


def _evidence_table(evidence: list[ResearchEvidence]) -> str:
    if not evidence:
        return "- No structured evidence recorded yet."
    lines = [
        "| Claim | Source | Stance | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    for ev in evidence[:30]:
        claim = _escape_table(ev.claim or ev.quote_or_summary or "Context")
        source = _escape_table(ev.title or ev.url or ev.source_kind or "Source")
        if ev.url:
            source = f"[{source}]({ev.url})"
        lines.append(f"| {claim} | {source} | {ev.stance} | {ev.confidence:.2f} |")
    return "\n".join(lines)


def _route_decision_audit_lines(decisions: list[ResearchRouteDecision]) -> str:
    if not decisions:
        return "- 未记录子代理路由拦截或警告；本次研究未发现需要单独审计的路由风险。"
    lines: list[str] = []
    for decision in decisions:
        step = decision.step_id or decision.task_id or "unknown-step"
        role = decision.role or "unknown-role"
        reason = decision.reason or "no reason recorded"
        lines.append(
            f"- {step} · {role} · {decision.action} "
            f"({decision.verdict}, risk={decision.risk_level}): {reason}"
        )
    return "\n".join(lines)


def _escape_table(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _material_lines(materials: list[ResearchMaterial]) -> str:
    if not materials:
        return "- 无用户材料；需要完全依赖外部搜索并标注来源。"
    lines: list[str] = []
    for mat in materials:
        target = mat.url or mat.path or (mat.text[:80] if mat.text else "")
        title = mat.title or target or mat.id
        lines.append(f"- [{mat.kind}] {title}: {target}")
    return "\n".join(lines)
