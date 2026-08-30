from __future__ import annotations

import json
import re
import shlex
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

FULL_ACCESS_MARKERS: frozenset[str] = frozenset(
    {
        "*",
        "all",
        "full",
        "inherit_all",
        "\u5168\u90e8",
    }
)

# Project audit is a capability boundary, not merely prompt guidance.  Keep the
# names here (the shared tool-policy module) so the native tool bridge, ReAct
# loop, executor, and delegated-agent paths all resolve the same contract.
AUDIT_READ_ONLY_WORKFLOW_PRESETS: frozenset[str] = frozenset(
    {
        "audit.review",
        "audit.deep",
        # Backward-compatible names that the prompt layer canonicalises to
        # ``audit.deep``.
        "audit.ultracode",
        "ultracode",
    }
)

# These tools do not mutate the user's project themselves.  Orchestration is
# allowed because ``call_subagent`` inherits the same audit contract into every
# child; todo writes only update the turn's UI projection.
_AUDIT_CONTROL_TOOLS: frozenset[str] = frozenset(
    {
        "run_orchestration",
        "todo_read",
        "todo_write",
        "write_todos",
    }
)

_NO_LOCAL_ACCESS_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "todo_read",
        "todo_write",
        "search_skills",
        "query_skill",
        "web_search",
        "search_web",
        "web_fetch",
        "fetch_url",
        "read_url",
    }
)

_READ_ONLY_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
        "format_code",
        "exec_shell",
        "run_tests",
        "update_soul",
        "revert_soul",
        "remember",
        "note_user",
        "diary_write",
    }
)


def workflow_preset_from_context(context: Mapping[str, Any] | None) -> str:
    """Return the per-turn workflow preset from flat or nested metadata."""

    ctx = context or {}
    nested = ctx.get("metadata")
    metadata = nested if isinstance(nested, Mapping) else {}
    return normalize_skill_name(
        ctx.get("workflow_preset") or metadata.get("workflow_preset")
    ).lower()


def is_audit_read_only_context(context: Mapping[str, Any] | None) -> bool:
    return workflow_preset_from_context(context) in AUDIT_READ_ONLY_WORKFLOW_PRESETS


def _verification_command_is_safe(tool_name: str, command: Any) -> bool:
    """Accept focused test/lint commands without exposing a general shell."""

    if command in (None, "", []):
        # The dedicated handlers' auto-detected commands are fixed argv lists.
        return True
    if isinstance(command, list):
        tokens = [str(item).strip() for item in command if str(item).strip()]
    elif isinstance(command, str):
        if any(marker in command for marker in (";", "&&", "||", "|", ">", "<", "`", "$(")):
            return False
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
    else:
        return False
    if not tokens:
        return False

    lowered = [token.lower() for token in tokens]
    program = lowered[0].rsplit("/", 1)[-1]
    if tool_name == "run_tests":
        if program in {"pytest", "py.test", "vitest", "jest", "tox", "nox"}:
            return True
        if program.startswith("python") and lowered[1:3] in (
            ["-m", "pytest"],
            ["-m", "unittest"],
        ):
            return True
        if program in {"npm", "pnpm", "yarn", "bun"}:
            return bool(
                len(lowered) >= 2
                and (
                    lowered[1] == "test"
                    or lowered[1:3] == ["run", "test"]
                    or lowered[1] in {"vitest", "jest"}
                )
            )
        if program == "npx":
            return len(lowered) >= 2 and lowered[1] in {"vitest", "jest"}
        return bool(
            (program == "cargo" and lowered[1:2] == ["test"])
            or (program == "go" and lowered[1:2] == ["test"])
            or (program == "dotnet" and lowered[1:2] == ["test"])
        )

    if tool_name == "lint_check":
        if any(token in {"--fix", "--write", "-w"} for token in lowered[1:]):
            return False
        if program in {"ruff", "eslint", "mypy", "pyright", "basedpyright", "tsc"}:
            return True
        if program.startswith("python") and lowered[1:3] == ["-m", "ruff"]:
            return True
        if program == "npx":
            return len(lowered) >= 2 and lowered[1] in {"eslint", "tsc"}
        return bool(
            (program == "cargo" and lowered[1:2] == ["clippy"])
            or (program == "go" and lowered[1:2] == ["vet"])
        )
    return False


def audit_read_only_tool_denial(
    skill_name: Any,
    args: Mapping[str, Any] | None,
    *,
    context: Mapping[str, Any] | None,
) -> str | None:
    """Return a clear denial when an audit turn requests a write-capable tool.

    This is intentionally allow-list based.  A newly registered tool is denied
    in audit until it is classified as read-only, preventing future write tools
    from silently bypassing the contract.
    """

    if not is_audit_read_only_context(context):
        return None
    name = normalize_skill_name(skill_name)
    payload = args or {}
    if name in _AUDIT_CONTROL_TOOLS:
        return None
    if name in {"run_tests", "lint_check"}:
        if name == "lint_check" and bool(payload.get("fix")):
            return (
                "[audit-read-only] lint_check fix=true is blocked: audit.review/audit.deep "
                "may inspect and verify but cannot modify files. Switch the task to develop "
                "before applying fixes."
            )
        if _verification_command_is_safe(name, payload.get("command")):
            return None
        return (
            f"[audit-read-only] {name} accepted only a focused test/lint command; "
            "arbitrary commands are blocked in audit. Use the tool's auto-detected "
            "command or switch the task to develop."
        )

    from runtime.execution.suckers.layers import is_read_only_skill

    if is_read_only_skill(name):
        return None
    return (
        f"[audit-read-only] tool '{name}' is blocked by workflow preset "
        f"{workflow_preset_from_context(context)!r}: audit tasks may read, search, "
        "and run focused verification, but cannot write or modify project state. "
        "Switch the task to develop before applying changes."
    )


def filter_audit_read_only_tool_specs(
    specs: Iterable[Any],
    *,
    context: Mapping[str, Any] | None,
) -> list[Any]:
    """Narrow an advertised catalog to the executable audit surface."""

    items = list(specs)
    if not is_audit_read_only_context(context):
        return items
    return [
        spec
        for spec in items
        if audit_read_only_tool_denial(
            getattr(spec, "name", ""),
            {},
            context=context,
        )
        is None
    ]


def goal_forbids_local_workspace_access(value: str) -> bool:
    """Whether the user explicitly prohibited even reading local files."""

    text = " ".join(str(value or "").strip().split()).lower()
    return bool(
        re.search(
            r"(?:不要|禁止|不得|不可|严禁|不允许)\s*"
            r"(?:读取|访问|查看|检查|分析)"
            r"[^。；;\n]{0,48}(?:本地|项目|仓库|工作区)"
            r"[^。；;\n]{0,24}(?:文件|代码|目录)",
            text,
        )
        or re.search(
            r"\b(?:do\s+not|don't|never|must\s+not)\s+"
            r"(?:read|access|inspect|analy[sz]e)\b"
            r"[^.\n]{0,64}\b(?:local|workspace|repository|repo|project)\b"
            r"[^.\n]{0,32}\b(?:files?|code|director(?:y|ies))\b",
            text,
        )
    )


def goal_is_read_only(value: str) -> bool:
    """Whether the user requested a non-mutating workspace operation."""

    text = str(value or "").lower()
    return bool(
        re.search(r"\bread[- ]only\b", text)
        or re.search(
            r"\b(?:do\s+not|don't|must\s+not|never)\s+"
            r"(?:modify|change|edit|write|create|update|add|remove|delete|patch)",
            text,
        )
        or re.search(
            r"(?:只读|(?:不要|严禁|禁止|不得|不可|不允许)\s*"
            r"(?:修改|改动|更改|编辑|写入|创建|新增|添加|删除|提交))",
            text,
        )
    )


def filter_tool_specs_for_workspace_contract(
    tool_specs: list[Any],
    goal: str,
    *,
    user_context: Mapping[str, Any] | None = None,
) -> tuple[list[Any], str | None]:
    """Enforce local-workspace restrictions at every tool catalog boundary."""

    if is_audit_read_only_context(user_context):
        return (
            filter_audit_read_only_tool_specs(
                tool_specs,
                context=user_context,
            ),
            "audit_read_only",
        )
    if goal_forbids_local_workspace_access(goal):
        allowed = [
            spec
            for spec in tool_specs
            if str(getattr(spec, "name", "")) in _NO_LOCAL_ACCESS_SAFE_TOOLS
            or str(getattr(spec, "name", "")).startswith("browser_")
        ]
        return allowed, "no_local_access"
    if goal_is_read_only(goal):
        allowed = [
            spec
            for spec in tool_specs
            if str(getattr(spec, "name", "")) not in _READ_ONLY_BLOCKED_TOOLS
        ]
        return allowed, "read_only"
    return tool_specs, None


def normalize_skill_name(value: Any) -> str:
    return str(value).strip()


def coerce_skill_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None and parsed is not value:
            return coerce_skill_names(parsed)
        for sep in ("\uff0c", "\u3001", ";", "\n", "\t"):
            raw = raw.replace(sep, ",")
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(value, Mapping):
        out: list[str] = []
        for key in (
            "tools",
            "skills",
            "skill_pack",
            "skill_packs",
            "plugins",
            "items",
        ):
            out.extend(coerce_skill_names(value.get(key)))
        return out
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            out.extend(coerce_skill_names(item))
        return out
    name = normalize_skill_name(value)
    return [name] if name else []


def dedupe_skill_names(names: Iterable[Any]) -> list[str]:
    return list(
        OrderedDict((name, None) for raw in names if (name := normalize_skill_name(raw))).keys()
    )


@dataclass
class SkillPolicy:
    allowed: tuple[str, ...] = ()
    sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    reason_map: dict[str, tuple[str, ...]] = field(default_factory=dict)
    allow_all: bool = False

    def as_list(self) -> list[str]:
        return ["*"] if self.allow_all else list(self.allowed)

    def allows(self, skill_name: Any) -> bool:
        if self.allow_all:
            return True
        return normalize_skill_name(skill_name) in self.reason_map

    def reasons_for(self, skill_name: Any) -> tuple[str, ...]:
        return self.reason_map.get(normalize_skill_name(skill_name), ())


def build_skill_policy(
    source_map: Mapping[str, Any],
    *,
    sort_allowed: bool = False,
) -> SkillPolicy:
    sources: dict[str, tuple[str, ...]] = {}
    reason_lists: dict[str, list[str]] = {}
    ordered: list[str] = []
    allow_all = False

    for source, raw_names in source_map.items():
        names = dedupe_skill_names(coerce_skill_names(raw_names))
        if not names:
            continue
        sources[str(source)] = tuple(names)
        if any(name.lower() in FULL_ACCESS_MARKERS for name in names):
            allow_all = True
        for name in names:
            ordered.append(name)
            reason_lists.setdefault(name, []).append(str(source))

    reason_map = {
        name: tuple(dedupe_skill_names(reasons)) for name, reasons in reason_lists.items()
    }
    unique_ordered = dedupe_skill_names(ordered)
    allowed = (
        ("*",) if allow_all else tuple(sorted(unique_ordered) if sort_allowed else unique_ordered)
    )
    return SkillPolicy(
        allowed=allowed,
        sources=sources,
        reason_map=reason_map,
        allow_all=allow_all,
    )


def resolve_agent_skill_policy(agent: Any) -> SkillPolicy:
    from runtime.execution.suckers.layers import ATOMIC_SKILL_NAMES

    source_map: dict[str, Any] = {"atomic": sorted(ATOMIC_SKILL_NAMES)}
    try:
        arms = list(agent.arms)
    except (AttributeError, TypeError):
        arms = []
    for idx, arm in enumerate(arms):
        arm_id = str(getattr(arm, "arm_id", "") or "unknown")
        source = f"arm:{arm_id}"
        if source in source_map:
            source = f"{source}#{idx}"
        source_map[source] = getattr(arm, "allowed_skills", ()) or ()

    extra = getattr(agent, "extra_skills", None)
    if extra:
        source_map["agent:extra"] = extra
    return build_skill_policy(source_map, sort_allowed=True)


def filter_allowed_names(
    names: Iterable[str],
    *,
    policy: SkillPolicy | None = None,
    agent: Any = None,
) -> list[str]:
    if policy is None and agent is not None:
        policy = resolve_agent_skill_policy(agent)
    if policy is None or policy.allow_all:
        return list(names)
    allowed = set(policy.allowed)
    return [name for name in names if name in allowed]


def resolve_context_tool_policy(
    *,
    role_allowlist: Any = None,
    context: Mapping[str, Any] | None = None,
) -> SkillPolicy:
    ctx = context or {}
    mode = normalize_skill_name(ctx.get("tool_allowlist_mode")).lower()
    source_map: dict[str, Any] = {}
    if mode in FULL_ACCESS_MARKERS:
        source_map["mode"] = ["*"]
    if role_allowlist:
        source_map["role"] = role_allowlist

    for key, source in (
        ("extra_tool_allowlist", "dynamic"),
        ("extra_tools", "extra_tools"),
        ("extra_skills", "extra_skills"),
    ):
        values = coerce_skill_names(ctx.get(key))
        if values:
            source_map[source] = values

    return build_skill_policy(source_map)
