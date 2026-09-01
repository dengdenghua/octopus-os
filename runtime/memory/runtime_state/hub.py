from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from runtime.safety.auth.scope import TenantScope

MemoryKind = Literal[
    "fact",
    "memory_md",
    "learned_rule",
    "learned_memory",
    "intelligence_report",
]


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    kind: MemoryKind
    content: str
    source: str
    scope: str = "global"
    scope_key: str = ""
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass(frozen=True)
class MemoryQuery:
    text: str
    agent_id: str | None = None
    project: str | None = None
    team_id: str | None = None
    include_global: bool = True
    limit: int = 12
    tenant_scope: TenantScope | None = None


class MemoryHub:
    """Read-only facade over Echo' currently fragmented memories.

    The hub deliberately does not own writes yet. It normalizes existing
    memory sources into one retrieval shape so the execution paths can stop
    caring whether a useful note came from user_store, MEMORY.md, planner
    learning, or an intelligence report.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        planner: Any = None,
    ) -> None:
        if repo_root is None:
            from runtime.platform.process.paths import project_root

            self.repo_root = project_root()
        else:
            self.repo_root = Path(repo_root).resolve()
        self.planner = planner

    def retrieve(self, query: MemoryQuery) -> list[MemoryRecord]:
        records = self.collect(query)
        scored = [
            record
            for record in (self._with_score(record, query.text) for record in records)
            if record.score > 0 or not query.text.strip()
        ]
        scored.sort(
            key=lambda record: (
                record.score,
                record.confidence,
                _scope_rank(record.scope),
                record.created_at,
            ),
            reverse=True,
        )
        limit = max(0, int(query.limit or 0))
        return scored[:limit] if limit else []

    def collect(self, query: MemoryQuery) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        records.extend(self._collect_user_store(query))
        records.extend(self._collect_memory_md(query))
        records.extend(self._collect_planner_sections())
        return _dedupe(records)

    def _collect_user_store(self, query: MemoryQuery) -> list[MemoryRecord]:
        try:
            from runtime.memory import user_store
        except ImportError:
            return []

        try:
            scope = query.tenant_scope
            if scope is None:
                try:
                    from runtime.memory.journal.journal_context import (
                        current_owner_actor_id,
                        current_tenant_id,
                    )

                    tenant_id = current_tenant_id()
                    actor_id = current_owner_actor_id()
                    if tenant_id and actor_id:
                        scope = TenantScope(tenant_id, actor_id)
                except (ImportError, RuntimeError):
                    scope = None
            memory = user_store.read_memory(scope)
        except (OSError, TypeError, ValueError):
            return []
        try:
            config = user_store.read_config(scope)
        except (OSError, TypeError, ValueError, AttributeError):
            config = {}
        if not config.get("enabled", True) or not config.get(
            "injection_enabled",
            True,
        ):
            return []

        out: list[MemoryRecord] = []
        for fact in memory.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            if not _fact_visible(fact, query):
                continue
            content = _clean_text(fact.get("content"))
            if not content:
                continue
            category = str(fact.get("category") or "context")
            kind: MemoryKind = (
                "intelligence_report" if category == "intelligence_report" else "fact"
            )
            out.append(
                MemoryRecord(
                    id=str(fact.get("id") or f"user_store:{len(out)}"),
                    kind=kind,
                    content=content,
                    source="user_store",
                    scope=str(fact.get("scope") or "global"),
                    scope_key=_scope_key_for_fact(fact),
                    confidence=_float_between(
                        fact.get("confidence"),
                        default=0.75,
                    ),
                    tags=[category],
                    created_at=str(fact.get("createdAt") or ""),
                    evidence_refs=[str(fact.get("source") or "manual")],
                )
            )
        return out

    def _collect_memory_md(self, query: MemoryQuery) -> list[MemoryRecord]:
        paths: list[tuple[str, str, Path]] = []
        if query.include_global:
            paths.append(
                (
                    "global",
                    "",
                    Path(os.environ.get("ECHO_HOME") or Path.home() / ".echo") / "MEMORY.md",
                )
            )
        paths.append(
            (
                "project",
                str(query.project or self.repo_root),
                self.repo_root / ".echo" / "MEMORY.md",
            )
        )
        if query.team_id:
            try:
                from runtime.memory.runtime_state.scope_paths import safe_path_segment

                clean_team_id = safe_path_segment(query.team_id, "team")
            except (ImportError, AttributeError):
                clean_team_id = re.sub(r"\s+", "-", query.team_id.strip())
            paths.append(
                (
                    "team",
                    query.team_id,
                    self.repo_root / "teams" / clean_team_id / "team-core" / "MEMORY.md",
                )
            )
            if query.agent_id:
                paths.append(
                    (
                        "team-agent",
                        f"{query.team_id}:{query.agent_id}",
                        self.repo_root
                        / "teams"
                        / clean_team_id
                        / "agents"
                        / query.agent_id
                        / "MEMORY.md",
                    )
                )
        if query.agent_id:
            paths.append(
                (
                    "agent",
                    query.agent_id,
                    self.repo_root / "agents" / query.agent_id / "agent-core" / "MEMORY.md",
                )
            )

        out: list[MemoryRecord] = []
        for scope, scope_key, path in paths:
            for idx, line in enumerate(_read_memory_lines(path)):
                out.append(
                    MemoryRecord(
                        id=f"memory_md:{scope}:{path}:{idx}",
                        kind="memory_md",
                        content=line,
                        source=f"memory_md:{scope}",
                        scope=scope,
                        scope_key=scope_key,
                        confidence=0.7,
                        evidence_refs=[str(path)],
                    )
                )
        return out

    def _collect_planner_sections(self) -> list[MemoryRecord]:
        if self.planner is None:
            return []
        records: list[MemoryRecord] = []
        section_specs: tuple[tuple[str, MemoryKind, str], ...] = (
            ("learned_rules_section", "learned_rule", "planner:learned_rules"),
            (
                "learned_memories_section",
                "learned_memory",
                "planner:learned_memories",
            ),
        )
        for attr, kind, source in section_specs:
            text = str(getattr(self.planner, attr, "") or "")
            for idx, item in enumerate(_section_bullets(text)):
                records.append(
                    MemoryRecord(
                        id=f"{source}:{idx}",
                        kind=kind,
                        content=item,
                        source=source,
                        scope="global",
                        confidence=0.78,
                    )
                )
        return records

    def _with_score(self, record: MemoryRecord, query: str) -> MemoryRecord:
        score = _score(query, record.content, record.tags)
        return MemoryRecord(
            id=record.id,
            kind=record.kind,
            content=record.content,
            source=record.source,
            scope=record.scope,
            scope_key=record.scope_key,
            confidence=record.confidence,
            tags=list(record.tags),
            created_at=record.created_at,
            evidence_refs=list(record.evidence_refs),
            score=score,
        )


def retrieve_relevant(
    text: str,
    *,
    agent_id: str | None = None,
    project: str | None = None,
    limit: int = 12,
    repo_root: str | Path | None = None,
    planner: Any = None,
) -> list[MemoryRecord]:
    return MemoryHub(repo_root=repo_root, planner=planner).retrieve(
        MemoryQuery(
            text=text,
            agent_id=agent_id,
            project=project,
            limit=limit,
        )
    )


def format_records_for_prompt(
    records: list[MemoryRecord],
    *,
    max_chars: int = 1800,
) -> str:
    if not records:
        return ""
    lines = ["RELEVANT LONG-TERM MEMORY:"]
    total = len(lines[0]) + 1
    for record in records:
        content = _clean_text(record.content)
        if not content:
            continue
        prefix = f"- [{record.scope}/{record.kind}/{record.source}] "
        line = prefix + content
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[: max(0, remaining - 1)].rstrip() + "..."
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _fact_visible(fact: dict[str, Any], query: MemoryQuery) -> bool:
    scope = str(fact.get("scope") or "global")
    if scope == "global":
        return query.include_global
    if scope == "project":
        return bool(query.project) and str(fact.get("project") or "") == query.project
    if scope == "agent":
        return bool(query.agent_id) and str(fact.get("agent_id") or "") == query.agent_id
    return False


def _scope_key_for_fact(fact: dict[str, Any]) -> str:
    scope = str(fact.get("scope") or "global")
    if scope == "project":
        return str(fact.get("project") or "")
    if scope == "agent":
        return str(fact.get("agent_id") or "")
    return ""


def _read_memory_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        if line == "_No memories yet._" or line.endswith("-->"):
            continue
        lines.append(_clean_text(line))
    return [line for line in lines if line]


def _section_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("- ["):
            continue
        bullets.append(_clean_text(line[2:].strip()))
    return [item for item in bullets if item]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _tokenize(text: str) -> list[str]:
    text = text.casefold()
    tokens: list[str] = []
    current: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if current:
                tokens.append("".join(current))
                current = []
            tokens.append(ch)
        elif ch.isalnum():
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _score(query: str, content: str, tags: list[str]) -> float:
    q = _clean_text(query).casefold()
    haystack = f"{content} {' '.join(tags)}".casefold()
    if not q:
        return 1.0
    score = 0.0
    if q in haystack:
        score += 2.0
    q_tokens = _tokenize(q)
    h_tokens = set(_tokenize(haystack))
    if q_tokens:
        hits = sum(1 for token in q_tokens if token in h_tokens or token in haystack)
        score += hits / len(q_tokens)
    return round(score, 4)


def _scope_rank(scope: str) -> int:
    return {
        "agent": 5,
        "team-agent": 4,
        "team": 3,
        "project": 2,
        "global": 1,
    }.get(scope, 0)


def _float_between(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _dedupe(records: list[MemoryRecord]) -> list[MemoryRecord]:
    seen: set[tuple[str, str, str]] = set()
    out: list[MemoryRecord] = []
    for record in records:
        key = (
            record.kind,
            re.sub(r"\s+", " ", record.content).casefold(),
            record.scope,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out
