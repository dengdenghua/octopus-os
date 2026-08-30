"""Standard Echo role contract rendered for the Codex engine.

Codex is an execution backend, not a second persona registry.  The functions
here translate an ordinary ``Agent`` (soul, memory, modes and Echo skills)
into server-owned App Server inputs while keeping every path supplied by the
browser or model out of the trusted skill-instruction resolver.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_MAX_DEVELOPER_INSTRUCTIONS = 160_000
_MAX_EXPLICIT_SKILLS = 8
_MAX_SKILL_INSTRUCTIONS = 48_000
_SKILL_MENTION = re.compile(
    r"(?<![\w-])\$([A-Za-z0-9][A-Za-z0-9_-]{0,127})|"
    r"@skill:([A-Za-z0-9][A-Za-z0-9_-]{0,127})"
)


def _bounded(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 28)].rstrip() + "\n\n…[server-side truncation]"


def _prompt_skill_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    try:
        from runtime.platform.process.paths import (
            bundled_market_skills_dir,
            resources_root,
        )

        roots.extend(
            (
                resources_root() / "skills" / "public",
                bundled_market_skills_dir(),
            )
        )
    except (ImportError, OSError, ValueError):
        pass
    roots.append(Path(__file__).resolve().parents[1] / "all_skills")
    resolved: list[Path] = []
    for root in roots:
        try:
            candidate = root.expanduser().resolve(strict=False)
        except OSError:
            continue
        if candidate not in resolved:
            resolved.append(candidate)
    return tuple(resolved)


def _trusted_prompt_skill_file(skill: Any) -> Path | None:
    """Resolve a registry-owned prompt skill; never accept a caller path."""

    source = str(getattr(skill, "trusted_source", "") or "")
    if not source.startswith("skill://"):
        # Plugin prompt actions remain executable through the controlled
        # dynamic-tool broker. Their install roots are policy-owned but may be
        # mutable, so they are not copied into the role prompt here.
        return None
    identifier = source.removeprefix("skill://").split("#", 1)[0].strip("/")
    parts = [part for part in identifier.split("/") if part]
    if not parts:
        return None
    slug = parts[-1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", slug):
        return None
    for root in _prompt_skill_roots():
        candidate = (root / slug / "SKILL.md").resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def resolve_explicit_skill_instructions(
    text: str,
    *,
    registry: Any,
    agent: Any,
) -> str:
    """Resolve explicit ``$skill``/``@skill:name`` mentions from the registry.

    Only the current agent allowlist, current tenant view, enabled state and
    trusted Echo prompt-skill roots participate. Unknown or action/plugin
    skills stay available through dynamic tools but never turn into a host
    filesystem path supplied to App Server.
    """

    names: list[str] = []
    for match in _SKILL_MENTION.finditer(text or ""):
        name = str(match.group(1) or match.group(2) or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= _MAX_EXPLICIT_SKILLS:
            break
    if not names:
        return ""

    try:
        policy = agent.skill_policy()
    except (AttributeError, TypeError, ValueError):
        return ""
    sections: list[str] = []
    for name in names:
        if not policy.allows(name):
            continue
        try:
            if not registry.has(name) or not registry.is_enabled(name):
                continue
            skill = registry.get(name)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        path = _trusted_prompt_skill_file(skill)
        if path is None:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            from runtime.execution.suckers.market_skills import _parse_frontmatter

            _metadata, body = _parse_frontmatter(raw)
        except (ImportError, TypeError, ValueError):
            body = raw
        body = _bounded(body, limit=_MAX_SKILL_INSTRUCTIONS)
        if body:
            sections.append(f"<explicit-echo-skill name={name!r}>\n{body}\n</explicit-echo-skill>")
    if not sections:
        return ""
    return (
        "The user explicitly selected the following Echo prompt skill(s). "
        "Their content was resolved from the current trusted registry; follow "
        "it for this turn. Supporting actions remain available only through "
        "the advertised Echo dynamic tools.\n\n" + "\n\n".join(sections)
    )


def compose_codex_role_instructions(
    agent: Any,
    *,
    context: Mapping[str, Any] | None,
    goal: str,
    registry: Any = None,
) -> str:
    """Render the same role/mode contract used by Echo' native engine."""

    ctx = dict(context or {})
    nested = ctx.get("metadata")
    metadata = dict(nested) if isinstance(nested, Mapping) else {}
    metadata.update(ctx)
    sections: list[str] = []
    try:
        from runtime.execution.agents.loader import compose_runtime_soul

        soul = compose_runtime_soul(agent, metadata=metadata)
    except (ImportError, AttributeError, TypeError, ValueError, OSError):
        soul = str(getattr(agent, "soul", "") or "")
    if soul:
        sections.append(soul)

    try:
        from runtime.core.cerebrum._react_context_code import (
            _build_code_agent_mode_prompt,
            _build_personal_agent_mode_prompt,
            _build_workflow_preset_prompt,
        )

        workflow = _build_workflow_preset_prompt(str(metadata.get("workflow_preset") or ""))
        if workflow:
            sections.append(workflow)
        agent_mode = str(metadata.get("agent_mode") or "").strip()
        if agent_mode:
            sections.append(_build_code_agent_mode_prompt(agent_mode))
        personal_mode = _build_personal_agent_mode_prompt(str(metadata.get("personal_mode") or ""))
        if personal_mode:
            sections.append(personal_mode)
    except (ImportError, AttributeError, TypeError, ValueError):
        pass

    for key, label, limit in (
        ("personal_instructions", "personal-instructions", 8_000),
        ("mode_contract", "mode-contract", 8_000),
        ("system_addendum", "system-addendum", 16_000),
    ):
        value = _bounded(metadata.get(key), limit=limit)
        if value:
            sections.append(f"<{label}>\n{value}\n</{label}>")

    if registry is not None:
        explicit = resolve_explicit_skill_instructions(
            goal,
            registry=registry,
            agent=agent,
        )
        if explicit:
            sections.append(explicit)

    sections.append(
        "<echo-codex-role-contract>\n"
        "You are the standard Echo role identified above; Codex App Server "
        "is only this role's coding engine. Preserve the role name, soul, "
        "memory, selected mode, group/project identity and response style.\n"
        "Echo is the capability authority. Use only the dynamic tools "
        "advertised for this turn for Echo skills, plugins, apps and "
        "delegation. Their results and denials are authoritative. Do not "
        "discover or enable ambient user Codex MCP servers, plugins, skills, "
        "apps, hooks or subagents. Slash commands have already been expanded "
        "by Echo before this turn.\n"
        "</echo-codex-role-contract>"
    )
    return _bounded(
        "\n\n".join(section for section in sections if section), limit=_MAX_DEVELOPER_INSTRUCTIONS
    )


__all__ = [
    "compose_codex_role_instructions",
    "resolve_explicit_skill_instructions",
]
