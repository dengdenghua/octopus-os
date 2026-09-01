"""Workbench snapshot + workspace-focus helpers for the realtime runtime.

Pure presentation logic split out of ``realtime_cerebrum.py``: translate
todo previews and tool items into ``AgentPhaseSnapshot`` /
``WorkbenchSnapshotV2`` / ``WorkspaceFocus`` payloads the frontend
renders as the workbench. No I/O, no runtime state.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Literal

from runtime.protocol import (
    AgentPhaseSnapshot,
    CommandExecutionItem,
    EvidenceReference,
    FileChangeItem,
    GroundingSource,
    TurnStatus,
    WorkbenchSnapshotV2,
    WorkspaceFocus,
)


def _phases_from_todo_preview(
    preview: Any,
    *,
    active_item_id: str | None = None,
) -> list[AgentPhaseSnapshot] | None:
    data = _coerce_preview_record(preview)
    if data is None:
        return None
    raw = data.get("items") or data.get("todos") or data.get("plan")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    parsed: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = _first_string(
            entry,
            ("activeForm", "active_form", "content", "text", "title", "task"),
        )
        if not title:
            continue
        parsed.append((title, _todo_phase_status(entry.get("status"))))
    if len(parsed) < 2:
        return None
    total = len(parsed)
    return [
        AgentPhaseSnapshot(
            id=f"todo-phase:{index}",
            index=index + 1,
            total=total,
            title=_phase_title(title),
            phase_kind=_phase_kind(title),
            status=status,  # type: ignore[arg-type]
            active_item_id=active_item_id if status == "running" else None,
        )
        for index, (title, status) in enumerate(parsed)
    ]


_PLAN_FILE_RE = re.compile(r"^plan(?:[-_.].*)?\.md$", re.IGNORECASE)
_PLAN_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[([ xX])\]\s*(.+)$")
_PLAN_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_PLAN_UNORDERED_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_PLAN_HEADING_RE = re.compile(r"^\s*#{2,6}\s+(.+)$")
_PLAN_SECTION_WORDS_RE = re.compile(
    r"plan|计划|steps?|步骤|deliverables?|交付物?|goals?|目标|objectives?|目的"
    r"|overview|概述|summary|摘要|conclusions?|结论",
    re.IGNORECASE,
)


def _is_plan_file(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    basename = path.replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_PLAN_FILE_RE.match(basename))


def _is_plan_section_heading(title: str) -> bool:
    """Drop organizational section headers (``## Plan``, ``## Deliverable``)
    so the heading fallback only keeps task-shaped headings."""
    cleaned = re.sub(r"^[\d一二三四五六七八九十]+[.)、:：\s]*", "", title).strip()
    return bool(_PLAN_SECTION_WORDS_RE.fullmatch(cleaned))


def _parse_plan_md(content: str) -> list[tuple[str, str]]:
    """Extract structured checklist items from a ``plan.md`` body.

    Returns ``(title, status)`` pairs with status ``done`` (checked box) or
    ``pending`` (everything else). Prefers checkbox items, then ordered /
    unordered list items, then level-2+ headings. Code fences are skipped and
    the first non-empty category wins — the three are not mixed.
    """
    categories: list[list[tuple[str, str]]] = [[], [], []]
    in_code_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue

        checkbox = _PLAN_CHECKBOX_RE.match(line)
        if checkbox:
            status = "done" if checkbox.group(1).lower() == "x" else "pending"
            categories[0].append((checkbox.group(2).strip(), status))
            continue

        ordered = _PLAN_ORDERED_RE.match(line)
        if ordered:
            categories[1].append((ordered.group(1).strip(), "pending"))
            continue

        unordered = _PLAN_UNORDERED_RE.match(line)
        if unordered:
            categories[1].append((unordered.group(1).strip(), "pending"))
            continue

        heading = _PLAN_HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip()
            if not _is_plan_section_heading(title):
                categories[2].append((title, "pending"))
            continue

    for items in categories:
        if items:
            return items
    return []


def _phases_from_plan_md(
    preview: Any,
    *,
    active_item_id: str | None = None,
) -> list[AgentPhaseSnapshot] | None:
    """Project a ``plan.md`` (``write_text_file`` payload) into phases.

    Research/planning turns write their plan as a ``plan.md`` file instead of
    ``todo_write`` entries. This mirrors ``_phases_from_todo_preview`` so the
    workbench「进展」panel shows the plan structure rather than falling back to
    the raw turn-iteration outline. The plan is a static snapshot — the first
    incomplete item is marked ``running`` so the outline has a current step.
    """
    data = _coerce_preview_record(preview)
    if data is None:
        return None
    if not _is_plan_file(data.get("path")):
        return None
    content = data.get("content") or data.get("text") or ""
    if not isinstance(content, str) or not content.strip():
        return None
    parsed = _parse_plan_md(content)
    if len(parsed) < 2:
        return None
    total = len(parsed)
    first_incomplete = next(
        (index for index, (_, status) in enumerate(parsed) if status != "done"),
        None,
    )
    return [
        AgentPhaseSnapshot(
            id=f"plan-phase:{index}",
            index=index + 1,
            total=total,
            title=_phase_title(title),
            phase_kind=_phase_kind(title),
            status=("running" if index == first_incomplete else status),  # type: ignore[arg-type]
            active_item_id=active_item_id if index == first_incomplete else None,
        )
        for index, (title, status) in enumerate(parsed)
    ]


def _phases_with_active_item(
    phases: list[AgentPhaseSnapshot],
    workspace_focus: WorkspaceFocus | None,
) -> list[AgentPhaseSnapshot]:
    if workspace_focus is None:
        return list(phases)
    return [
        phase.model_copy(update={"active_item_id": workspace_focus.item_id})
        if phase.status == "running"
        else phase
        for phase in phases
    ]


def _terminal_workbench_phases(
    phases: list[AgentPhaseSnapshot],
    terminal_status: TurnStatus,
) -> list[AgentPhaseSnapshot]:
    if terminal_status == TurnStatus.COMPLETED:
        # A final answer closes the turn, not necessarily the current task.
        # Only an explicit todo/phase receipt may turn a phase into ``done``.
        # In particular, a model can deliver a partial answer without having
        # emitted its last checklist update; painting the running row green
        # here creates the false "everything completed" state users reported.
        return [
            phase.model_copy(
                update={
                    "status": "pending" if phase.status == "running" else phase.status,
                    "active_item_id": None,
                }
            )
            for phase in phases
        ]
    if terminal_status == TurnStatus.FAILED:
        marked = False
        terminal_phases: list[AgentPhaseSnapshot] = []
        for phase in phases:
            if phase.status == "done":
                terminal_phases.append(phase.model_copy(update={"active_item_id": None}))
                continue
            if not marked:
                marked = True
                terminal_phases.append(
                    phase.model_copy(update={"status": "error", "active_item_id": None})
                )
                continue
            terminal_phases.append(phase.model_copy(update={"active_item_id": None}))
        return terminal_phases
    if terminal_status == TurnStatus.PAUSED:
        # Preserve todo truth.  A budget/system pause is not an approval and
        # must not paint the current phase as waiting for confirmation or as
        # still executing after the worker has yielded.
        return [
            phase.model_copy(
                update={
                    "status": "pending" if phase.status == "running" else phase.status,
                    "active_item_id": None,
                }
            )
            for phase in phases
        ]
    if terminal_status in {TurnStatus.INTERRUPTED, TurnStatus.CANCELLED}:
        return [
            phase.model_copy(
                update={
                    "status": "pending" if phase.status == "running" else phase.status,
                    "active_item_id": None,
                }
            )
            for phase in phases
        ]
    return list(phases)


def _workbench_snapshot(
    *,
    version: int,
    phases: list[AgentPhaseSnapshot],
    workspace_focus: WorkspaceFocus | None,
    evidence: list[EvidenceReference] | None = None,
) -> WorkbenchSnapshotV2:
    current_phase = _current_workbench_phase(phases)
    current_phase_is_actionable = current_phase is not None and current_phase.status in {
        "running",
        "waiting_approval",
        "error",
    }
    current_item_id = (
        workspace_focus.item_id
        if workspace_focus is not None and current_phase_is_actionable
        else current_phase.active_item_id
        if current_phase is not None and current_phase_is_actionable
        else None
    )
    return WorkbenchSnapshotV2(
        version=version,
        status=_workbench_status(phases),
        phases=phases,
        current_phase_id=current_phase.id if current_phase is not None else None,
        current_item_id=current_item_id,
        workspace_focus=workspace_focus,
        evidence=list(evidence or []),
    )


def _grounding_evidence(sources: list[GroundingSource]) -> list[EvidenceReference]:
    return [
        EvidenceReference(
            id=f"grounding:{source.kind}:{source.path}",
            kind="file",
            title=source.title or source.path.rsplit("/", 1)[-1],
            uri=source.path,
            status="observed",
            origin="grounding",
        )
        for source in sources
        if source.path.strip()
    ]


def _tool_evidence(
    item: CommandExecutionItem,
    *,
    phase_id: str | None = None,
) -> list[EvidenceReference]:
    """Extract confirmed local references from a successful read/search tool."""

    if item.status != "completed":
        return []
    command = item.command.strip().lower()
    read_tools = {"read_file", "read_text_file", "read_file_range"}
    search_tools = {"grep", "grep_text", "glob", "glob_files", "search_files"}
    if command not in read_tools | search_tools:
        return []

    paths: list[str] = []
    preview = item.input_preview
    if command in read_tools and isinstance(preview, dict):
        candidate = preview.get("path") or preview.get("file_path")
        if isinstance(candidate, str):
            paths.append(candidate)

    output = item.aggregated_output
    if output:
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(output)
            if isinstance(parsed, dict) and (parsed.get("error") or parsed.get("ok") is False):
                return []
            _collect_evidence_paths(parsed, paths)

    result: list[EvidenceReference] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = raw_path.strip()
        if not _is_concrete_evidence_path(path) or path in seen:
            continue
        seen.add(path)
        result.append(
            EvidenceReference(
                id=f"tool:{item.id}:{path}",
                kind="file",
                title=path.replace("\\", "/").rsplit("/", 1)[-1],
                uri=path,
                status="observed",
                origin="tool",
                source_item_id=item.id,
                phase_id=phase_id,
            )
        )
    return result


def _collect_evidence_paths(value: Any, paths: list[str]) -> None:
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            paths.append(path)
        for key, nested in value.items():
            if key != "content":
                _collect_evidence_paths(nested, paths)
    elif isinstance(value, list):
        for nested in value:
            _collect_evidence_paths(nested, paths)


def _is_concrete_evidence_path(path: str) -> bool:
    if not path or path in {".", ".."} or "\n" in path or "\r" in path:
        return False
    if any(char in path for char in "*?[]{}"):
        return False
    normalized = path.replace("\\", "/")
    leaf = normalized.rsplit("/", 1)[-1]
    return bool(leaf and ("." in leaf or "/" in normalized))


def _workbench_status(
    phases: list[AgentPhaseSnapshot],
) -> Literal["pending", "running", "done", "error", "waiting_approval"]:
    if any(phase.status == "error" for phase in phases):
        return "error"
    if any(phase.status == "waiting_approval" for phase in phases):
        return "waiting_approval"
    if any(phase.status == "running" for phase in phases):
        return "running"
    if phases and all(phase.status == "done" for phase in phases):
        return "done"
    return "pending" if phases else "running"


def _current_workbench_phase(
    phases: list[AgentPhaseSnapshot],
) -> AgentPhaseSnapshot | None:
    for status in ("running", "waiting_approval", "error", "pending"):
        for phase in phases:
            if phase.status == status:
                return phase
    return phases[-1] if phases else None


def _coerce_preview_record(preview: Any) -> dict[str, Any] | None:
    if isinstance(preview, dict):
        return preview
    if isinstance(preview, str) and preview.strip():
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(preview)
            if isinstance(parsed, dict):
                return parsed
    return None


def _todo_phase_status(value: Any) -> str:
    if value in ("completed", "done"):
        return "done"
    if value in ("in_progress", "running"):
        return "running"
    if value in ("blocked", "waiting_approval"):
        return "waiting_approval"
    if value in ("error", "failed"):
        return "error"
    return "pending"


def _phase_title(title: str, _index: int = 0) -> str:
    clean = re.sub(r"\s+", " ", title).strip()
    without_machine_prefix = re.sub(
        r"^(?:phase|阶段|step|步骤)\s*[\d一二三四五六七八九十]+(?:\.\d+)?\s*[:：.)、-]?\s*",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()
    return without_machine_prefix or clean or "进行中"


# Coarse business-phase mapping mirrors the frontend ``businessAgentPhaseKey``
# so the workbench outline can show a readable localized label instead of the
# raw todo wording. Order matters: deploying > testing > implementing >
# planning > exploring — a title like "test the deploy script" classifies as
# deploying because deploy takes precedence.
_PHASE_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deploying", re.compile(r"deploy|release|publish|ship|部署|上线|发布")),
    (
        "testing",
        re.compile(r"test|verify|validat|check|qa|lint|build|测试|验证|确认|检查|构建|打包"),
    ),
    (
        "implementing",
        re.compile(
            r"implement|edit|fix|code|refactor|write|add|update|modify|change|create|patch"
            r"|实现|修改|修复|改|新增|添加|更新|重构|接入|迁移|搭建"
        ),
    ),
    ("planning", re.compile(r"plan|design|scope|spec|todo|规划|计划|设计|方案")),
    (
        "exploring",
        re.compile(
            r"explore|read|inspect|analy[sz]e|investigat|research|scan|review|understand|study"
            r"|浏览|阅读|了解|分析|调研|研究|排查|查看|梳理"
        ),
    ),
)


def _phase_kind(title: str) -> str:
    """Map a free-form todo title to a coarse business phase kind.

    Returns one of ``planning``/``exploring``/``implementing``/``testing``/
    ``deploying``/``other``. Mirrors the frontend ``businessAgentPhaseKey``
    mapping so the UI can render a localized label; ``"other"`` means no
    keyword matched and the raw title is shown unchanged.
    """
    text = title.lower()
    for kind, pattern in _PHASE_KIND_PATTERNS:
        if pattern.search(text):
            return kind
    return "other"


def _workspace_focus_for_tool(item: CommandExecutionItem) -> WorkspaceFocus:
    preview = _coerce_preview_record(item.input_preview) or {}
    name = item.command or "tool"
    target = _first_string(
        preview,
        (
            "command",
            "cmd",
            "path",
            "file_path",
            "filepath",
            "url",
            "query",
            "pattern",
            "cwd",
        ),
    )
    lower = name.lower()
    if name == "todo_write":
        view = "trace"
        title = "Updating plan"
    elif re.search(r"shell|bash|terminal|cmd|exec|python|powershell|cli", lower):
        view = "terminal"
        title = f"Running {name}"
    elif re.search(r"browser|url|web|fetch|screenshot", lower):
        view = "browser"
        title = f"Browsing with {name}"
    elif re.search(r"edit|write|replace|patch|diff|create|delete|artifact", lower):
        view = "diff"
        title = f"Editing with {name}"
    else:
        view = "trace"
        title = name.replace("_", " ")
    return WorkspaceFocus(
        item_id=item.id,
        view=view,  # type: ignore[arg-type]
        title=title,
        subtitle=target or None,
    )


def _workspace_focus_for_file_change(item: FileChangeItem) -> WorkspaceFocus:
    first_path = item.changes[0].path if item.changes else ""
    title = f"Editing {first_path}" if first_path else "File changes"
    return WorkspaceFocus(
        item_id=item.id,
        view="diff",
        title=title,
        subtitle=first_path or None,
    )


def _first_string(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
