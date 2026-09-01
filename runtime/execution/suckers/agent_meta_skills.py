from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from runtime.platform.process.session import current_session

from .capability_skills import (
    CAPABILITY_SKILL_NAMES,
    register_capability_skills,
)
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_VALID_STATUS = {"pending", "in_progress", "completed"}
_TODO_LOCK = threading.Lock()
_TODO_BY_SCOPE: dict[str, list[dict[str, Any]]] = {}
_LATEST_SCOPE = "__latest__"


def _todo_item_id(content: str, occurrence: int) -> str:
    """Return a compact deterministic identity for a checklist item."""

    digest = hashlib.sha1(  # nosec B324 — non-security checklist item ID, only needs determinism
        f"{content.casefold()}\0{occurrence}".encode(), usedforsecurity=False
    ).hexdigest()[:12]
    return f"task-{digest}"


def _todo_scope() -> str:
    session = current_session()
    if session is None:
        return _LATEST_SCOPE
    return (
        session.thread_id
        or session.conversation_id
        or session.turn_id
        or session.agent_id
        or _LATEST_SCOPE
    )


def _coerce_todo_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return _coerce_todo_items(parsed)
    if isinstance(value, dict):
        nested = value.get("items") or value.get("todos") or value.get("tasks")
        return _coerce_todo_items(nested)
    return []


def _todo_write(
    items: list = None,  # type: ignore[assignment]  # None default = optional in schema, but array when present
    todos: list = None,  # type: ignore[assignment]  # alias
    tasks: list = None,  # type: ignore[assignment]  # alias
    **extra: Any,
) -> dict[str, Any]:
    """Validate and normalize the agent's task list.

    Args:
        items · the COMPLETE list (not a diff). ``todos`` and ``tasks`` are accepted
          as a compatibility alias. Each item dict:
            - ``content`` · imperative phrase, e.g. "Run tests".
              ``text`` / ``title`` / ``task`` / ``name`` / ``description`` are accepted as aliases.
            - ``status`` · one of ``pending`` / ``in_progress`` /
              ``completed``. Unknown values silently coerce to
              ``pending`` so a sloppy call doesn't fail-hard.
            - ``activeForm`` · present-continuous phrase shown
              while status is ``in_progress``, e.g. "Running
              tests". ``active_form`` is accepted as an alias.
              Defaults to ``content`` when missing.
            - ``id`` · stable task identity. Optional on the first call; keep
              the returned value on later updates. ``taskId`` / ``task_id``
              are accepted aliases.

    Returns:
        ``{"ok": True, "count": <n>, "todos": [...]}`` · the
        normalized list. The UI watches for ``tool_use`` events
        with ``name="todo_write"`` and extracts ``input.items``
        directly, but we also echo the cleaned list in the result
        so the model gets confirmation of what was
        accepted.

    Design choices:
        * Empty list is a valid call (clears the plan).
        * Items with empty content/text/title/task are dropped silently ·
          models sometimes stub a placeholder and fill in later.
        * No uniqueness check on ``content`` · the agent can
          legitimately have two "Read file" steps in a plan.
        * No max-length cap on item count · practical limit is
          context budget on the model side.
    """
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    saw_in_progress = False
    raw = _coerce_todo_items(items)
    if not raw:
        raw = _coerce_todo_items(todos)
    if not raw:
        raw = _coerce_todo_items(tasks)
    # Surface a hard error when the model passed the checklist under an
    # unrecognized key (e.g. ``list``, ``todo_list``, ``plan``, or
    # serialized as a string under ``params``).  Without this the tool
    # silently returns count=0 / ok=True, the model thinks the call
    # succeeded, retries the same wrong shape, and the turn three-
    # strikes into an interrupt.  Returning ok=False with the accepted
    # key names lets the model self-correct on the next round.
    #
    # The check covers three value shapes the model has been observed
    # producing:
    #   1. a bare list under a wrong key (``list=[...]``)
    #   2. a dict under a wrong key (``params={...}``)
    #   3. a JSON-serialized string under a wrong key
    #      (``params='{"todo_list": [...]}'``) — the model wraps the
    #      entire payload as a string, so we parse it to see whether
    #      it carries a todo-shaped list.
    if not raw and extra:

        def _looks_like_todos(v: Any) -> bool:
            if isinstance(v, list) and v:
                return True
            if isinstance(v, dict) and v:
                # dict carrying a list under any key (e.g. {"todo_list": [...]})
                return any(isinstance(iv, list) and iv for iv in v.values())
            if isinstance(v, str) and v.strip():
                # serialized JSON: try to parse and look for a list inside
                try:
                    parsed = json.loads(v)
                except json.JSONDecodeError:
                    return True  # non-empty string that isn't JSON — still suspicious
                return _looks_like_todos(parsed)
            return False

        misplaced = sorted(k for k, v in extra.items() if _looks_like_todos(v))
        if misplaced:
            return {
                "ok": False,
                "count": 0,
                "todos": [],
                "normalized": False,
                "warnings": [],
                "error": (
                    f"todo_write received a checklist under unrecognized "
                    f"key(s) {misplaced}, but none under the accepted keys "
                    f"'items' / 'todos' / 'tasks'. Re-issue the call as "
                    f"todo_write(items=[...]) so the checklist is recorded."
                ),
            }
    scope = _todo_scope()
    with _TODO_LOCK:
        previous = [dict(item) for item in _TODO_BY_SCOPE.get(scope, [])]
    previous_ids_by_content: dict[str, list[str]] = {}
    for item in previous:
        previous_content = str(item.get("content") or "").strip().casefold()
        previous_id = str(item.get("id") or "").strip()
        if previous_content and previous_id:
            previous_ids_by_content.setdefault(previous_content, []).append(previous_id)
    content_occurrences: dict[str, int] = {}

    for t in raw:
        if not isinstance(t, dict):
            continue
        content = str(
            t.get("content")
            or t.get("text")
            or t.get("title")
            or t.get("task")
            or t.get("name")
            or t.get("description")
            or ""
        ).strip()
        if not content:
            continue
        content_key = content.casefold()
        occurrence = content_occurrences.get(content_key, 0)
        content_occurrences[content_key] = occurrence + 1
        explicit_id = str(t.get("id") or t.get("taskId") or t.get("task_id") or "").strip()
        matching_previous_ids = previous_ids_by_content.get(content_key, [])
        item_id = (
            explicit_id
            or (
                matching_previous_ids[occurrence] if occurrence < len(matching_previous_ids) else ""
            )
            or _todo_item_id(content, occurrence)
        )
        status = t.get("status", "pending")
        if status not in _VALID_STATUS:
            status = "pending"
        if status == "in_progress":
            if saw_in_progress:
                status = "pending"
                if not warnings:
                    warnings.append(
                        "Only one todo can be in_progress; later items were reset to pending."
                    )
            else:
                saw_in_progress = True
        active = str(t.get("activeForm") or t.get("active_form") or content).strip()
        out.append(
            {
                "id": item_id,
                "content": content,
                "status": status,
                "activeForm": active,
            }
        )
    with _TODO_LOCK:
        _TODO_BY_SCOPE[scope] = [dict(item) for item in out]
        _TODO_BY_SCOPE[_LATEST_SCOPE] = [dict(item) for item in out]
    return {
        "ok": True,
        "count": len(out),
        "todos": out,
        "normalized": bool(warnings),
        "warnings": warnings,
    }


def _todo_read(**_: Any) -> dict[str, Any]:
    """Return the current turn/thread task list, if any."""
    scope = _todo_scope()
    with _TODO_LOCK:
        todos = _TODO_BY_SCOPE.get(scope)
        if todos is None and scope != _LATEST_SCOPE:
            todos = _TODO_BY_SCOPE.get(_LATEST_SCOPE)
        out = [dict(item) for item in (todos or [])]
    return {
        "ok": True,
        "count": len(out),
        "todos": out,
        "scope": None if scope == _LATEST_SCOPE else scope,
    }


AGENT_META_SKILL_NAMES = [
    "todo_read",
    "todo_write",
    "search_skills",
    "query_skill",
    "execute_skill",
    *CAPABILITY_SKILL_NAMES,
]


def _query_skill_for_registry(registry: SkillRegistry):
    def _query_skill(name: str = "", **_: Any) -> dict[str, Any]:
        skill_name = str(name or "").strip()
        if not skill_name:
            return {"ok": False, "error": "skill name is required"}
        try:
            skill = registry.get(skill_name)
        except KeyError:
            return {"ok": False, "error": f"skill not found: {skill_name}"}
        try:
            enabled = bool(registry.is_enabled(skill_name))
        except (AttributeError, TypeError, ValueError):
            enabled = True
        return {
            "ok": True,
            "name": skill.name,
            "summary": skill.effective_summary,
            "description": skill.description,
            "affinity": list(skill.affinity),
            "cost_profile": skill.cost_profile,
            "trusted_source": skill.trusted_source,
            "enabled": enabled,
        }

    return _query_skill


def _search_skills_for_registry(registry: SkillRegistry):
    def _search_skills(
        query: str = "",
        limit: int = 10,
        include_disabled: bool = False,
        **extra: Any,
    ) -> dict[str, Any]:
        # Reject queries passed under an unrecognized key (e.g. ``q``,
        # ``search``, ``keyword``) — without this the empty ``query``
        # silently returns every skill, the model thinks the search
        # succeeded, and it can loop on the same wrong shape.
        if not query and extra:
            misplaced = sorted(k for k, v in extra.items() if isinstance(v, str) and v.strip())
            if misplaced:
                return {
                    "ok": False,
                    "error": (
                        f"search_skills received a query under unrecognized "
                        f"key(s) {misplaced}, but 'query' is empty. "
                        f'Re-issue as search_skills(query="...").'
                    ),
                }
        q = str(query or "").strip().lower()
        tokens = [t for t in q.replace("_", " ").replace("-", " ").split() if t]
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 10
        lim = max(1, min(lim, 25))

        matches: list[tuple[int, dict[str, Any]]] = []
        for name in registry.all_names():
            try:
                skill = registry.get(name)
                enabled = bool(registry.is_enabled(name))
            except (KeyError, TypeError, ValueError):
                continue
            if not enabled and not include_disabled:
                continue
            summary = skill.effective_summary
            affinity = list(skill.affinity)
            haystack = " ".join(
                [
                    skill.name,
                    summary,
                    skill.description or "",
                    " ".join(affinity),
                ]
            ).lower()

            if not q:
                score = 1
            elif skill.name.lower() == q:
                score = 100
            elif q in skill.name.lower():
                score = 80
            elif q in haystack:
                score = 50
            else:
                hits = sum(1 for token in tokens if token in haystack)
                if hits == 0:
                    continue
                score = hits * 10
                if all(token in haystack for token in tokens):
                    score += 20

            matches.append(
                (
                    score,
                    {
                        "name": skill.name,
                        "summary": summary,
                        "affinity": affinity,
                        "cost_profile": skill.cost_profile,
                        "enabled": enabled,
                    },
                )
            )

        matches.sort(key=lambda item: (-item[0], item[1]["name"]))
        results = [item for _, item in matches[:lim]]
        return {
            "ok": True,
            "query": q,
            "count": len(results),
            "results": results,
            "total_matches": len(matches),
        }

    return _search_skills


def _execute_skill_for_registry(registry: SkillRegistry):
    """Build a fail-closed dispatcher for omitted, read-only skills.

    This closes the progressive-disclosure gap without turning a low-risk
    meta-tool into an approval bypass. Side-effecting or ambiguously tagged
    skills must still be surfaced through the normal executor/native tool
    path, where approval and durable effect receipts are available.
    """

    def _execute_skill(
        name: str = "",
        args: Any = None,
        **extra: Any,
    ) -> dict[str, Any]:
        skill_name = str(name or "").strip()
        if not skill_name:
            return {"ok": False, "error": "skill name is required"}
        if args is None and extra:
            misplaced = sorted(
                key
                for key, value in extra.items()
                if isinstance(value, (dict, list, str)) and value
            )
            if misplaced:
                return {
                    "ok": False,
                    "error": (
                        "execute_skill received arguments under unrecognized "
                        f"key(s) {misplaced}; pass them under args={{{{...}}}}"
                    ),
                }
        try:
            skill = registry.get(skill_name)
        except KeyError:
            return {"ok": False, "error": f"skill not found: {skill_name}"}
        try:
            enabled = bool(registry.is_enabled(skill.name))
        except (AttributeError, TypeError, ValueError):
            enabled = True
        if not enabled:
            return {"ok": False, "name": skill.name, "error": "skill is disabled"}

        # Never allow meta-dispatch recursion. It obscures the true target and
        # can turn a read-only wrapper into a route toward a mutating action.
        affinities = {str(tag).strip().lower() for tag in skill.affinity}
        if skill.name == "execute_skill" or "meta" in affinities:
            return {
                "ok": False,
                "name": skill.name,
                "error": "meta skills cannot be dispatched through execute_skill",
            }

        from runtime.execution.tool_engine.effect_receipts import is_side_effecting

        if is_side_effecting(skill.affinity):
            return {
                "ok": False,
                "name": skill.name,
                "error": (
                    "skill is side-effecting or lacks an explicit read-only affinity; "
                    "invoke it through the normal tool/capability path so approval and "
                    "effect receipts are enforced"
                ),
            }

        if args is None:
            call_args: dict[str, Any] = {}
        elif isinstance(args, dict):
            call_args = dict(args)
        elif isinstance(args, str):
            try:
                parsed = json.loads(args)
            except json.JSONDecodeError:
                return {"ok": False, "name": skill.name, "error": "args must be an object"}
            if not isinstance(parsed, dict):
                return {"ok": False, "name": skill.name, "error": "args must be an object"}
            call_args = dict(parsed)
        else:
            return {"ok": False, "name": skill.name, "error": "args must be an object"}

        # Model-controlled auth/sandbox overrides are stripped exactly as on
        # the executor path before the shared inner-dispatch gates run.
        from runtime.safety.auth import strip_model_controlled_overrides

        call_args, stripped = strip_model_controlled_overrides(call_args)
        from runtime.execution.tool_engine.skill_gate import gate_inner_dispatch

        block = gate_inner_dispatch(
            skill,
            call_args,
            caller="execute_skill",
        )
        if block is not None:
            return {"ok": False, "name": skill.name, "error": block.message}
        try:
            result = skill.handler(**call_args)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "name": skill.name,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "ok": True,
            "name": skill.name,
            "result": result,
            **({"stripped_overrides": stripped} if stripped else {}),
        }

    return _execute_skill


def register_agent_meta_skills(registry: SkillRegistry) -> int:
    """Register the agent-meta skills. Returns the count registered."""
    capability_count = register_capability_skills(registry)
    registry.register(
        Skill(
            name="todo_read",
            description=(
                "用途: 读取当前 turn / thread 的实时任务计划 (上次 todo_write 的最新快照)；恢复长任务、检查清单状态时调用。\n"
                "何时不用: 想新建或更新任务清单用 todo_write (传完整新列表)；想看某个 skill 的元数据用 query_skill；这不是给用户的「最终答复」，只是内部进度查询。\n"
                "关键参数: 无必填参数 (按当前会话作用域自动定位)。\n"
                "示例: todo_read({})"
            ),
            affinity=["meta", "plan", "ui"],
            cost_profile="low",
            trusted_source="skill://public/todo_read",
            handler=_todo_read,
            tests=[
                SkillTestCase(
                    name="empty_initial_list_is_ok",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "count", "todos"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="todo_write",
            description=(
                "用途: 维护 agent 的任务清单 (实时进度面板) — 多步任务一开始全列出 (pending)，每完成 / 切换一步重传完整列表 (一项 in_progress, 完成的 completed)。\n"
                "何时不用: 只是看当前清单用 todo_read；单步小任务不必拆；不要拿这个工具传 diff (必须传完整列表)；最终答复仍走正常输出，不要塞这里。\n"
                "关键参数: items (必填, list[{id?, content, status: pending|in_progress|completed, activeForm}]; todos 是兼容别名; 同时只能一个 in_progress, 否则后续被降级为 pending)。首次可省略 id，后续更新应原样保留返回的 id。\n"
                '示例: todo_write({"items": [{"id": "tests", "content": "Run tests", "status": "in_progress", "activeForm": "Running tests"}]})'
            ),
            affinity=["meta", "plan", "ui"],
            cost_profile="low",
            trusted_source="skill://public/todo_write",
            handler=_todo_write,
            tests=[
                SkillTestCase(
                    name="empty_list_is_ok",
                    tier="golden",
                    args={"items": []},
                    expect=SkillExpect(schema_keys=["ok", "count", "todos"]),
                ),
                SkillTestCase(
                    name="happy_path_three_items",
                    tier="golden",
                    args={
                        "items": [
                            {
                                "content": "Read README",
                                "status": "completed",
                                "activeForm": "Reading README",
                            },
                            {
                                "content": "Run tests",
                                "status": "in_progress",
                                "activeForm": "Running tests",
                            },
                            {"content": "Commit", "status": "pending", "activeForm": "Committing"},
                        ]
                    },
                    expect=SkillExpect(schema_keys=["ok", "count", "todos"]),
                ),
                SkillTestCase(
                    name="invalid_status_coerces_to_pending",
                    tier="golden",
                    args={"items": [{"content": "x", "status": "bogus", "activeForm": "X-ing"}]},
                    expect=SkillExpect(schema_keys=["ok", "count", "todos"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="search_skills",
            summary="Search the full registered skill catalog by keyword.",
            description=(
                "Purpose: search the entire registered skill catalog by keyword "
                "when the compact prompt catalog was truncated or you do not know "
                "the exact skill name. Search matches name, summary, affinity, "
                "and description, then returns compact candidates. Call "
                "query_skill(name=...) for full details before using an unfamiliar "
                "candidate.\n"
                "When not to use: if the needed skill is already visible and its "
                "parameters are obvious; call that skill directly.\n"
                "Key params: query (keyword), limit (1-25, default 10), "
                "include_disabled (default false).\n"
                'Example: search_skills({"query": "subagent parallel"})'
            ),
            affinity=["meta", "skill", "catalog", "search"],
            cost_profile="low",
            trusted_source="skill://public/search_skills",
            handler=_search_skills_for_registry(registry),
            tests=[
                SkillTestCase(
                    name="empty_query_lists_candidates",
                    tier="golden",
                    args={"query": "", "limit": 3},
                    expect=SkillExpect(schema_keys=["ok", "count", "results"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="query_skill",
            summary="Load full details for one registered skill.",
            description=(
                "用途: 当 catalog 里的简短 summary 不够用时，按名字拉一个已注册 skill 的完整元数据 (description / 参数契约 / affinity / 是否启用)；调不熟的工具前先查一查。\n"
                "何时不用: 列出全部 skill 不要用本工具 (走 catalog / registry.list)；要执行 skill 直接调用对应工具名，不要先 query 再调 (浪费 token)；skill 不存在会返回 ok=false。\n"
                "关键参数: name (必填, 已注册的 skill 名)。\n"
                '示例: query_skill({"name": "edit_file"})'
            ),
            affinity=["meta", "skill", "catalog"],
            cost_profile="low",
            trusted_source="skill://public/query_skill",
            handler=_query_skill_for_registry(registry),
            tests=[
                SkillTestCase(
                    name="missing_name_returns_error",
                    tier="golden",
                    args={"name": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="execute_skill",
            summary="Execute a discovered read-only skill by name.",
            description=(
                "Purpose: execute an enabled read-only skill discovered via search_skills "
                "when that skill was omitted from the compact/native tool catalog. "
                "Side-effecting, dangerous, meta, disabled, or ambiguously tagged skills "
                "are rejected and must use the normal tool/capability path.\n"
                "Key params: name (required), args (object, default {}).\n"
                'Example: execute_skill({"name": "code_search", "args": {"query": "TODO"}})'
            ),
            affinity=["meta", "skill", "catalog", "execute"],
            cost_profile="low",
            trusted_source="skill://public/execute_skill",
            handler=_execute_skill_for_registry(registry),
            tests=[
                SkillTestCase(
                    name="missing_name_returns_error",
                    tier="golden",
                    args={"name": ""},
                    expect=SkillExpect(schema_keys=["ok", "error"]),
                ),
            ],
        )
    )
    return capability_count + 5


__all__ = [
    "AGENT_META_SKILL_NAMES",
    "register_agent_meta_skills",
]
