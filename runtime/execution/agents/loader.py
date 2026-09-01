# ruff: noqa: E402 — module-level imports below are intentionally late
"""Load agents from the on-disk ``agents/<id>/`` tree.

The layout mirrors the upstream Accio agent store so agents are a
user-editable filesystem surface rather than hardcoded Python strings.
See ``agents/<id>/agent-core/`` for the persona knowledge files.

System prompt composition (what the LLM actually sees for a turn):

    1. ``agents/_shared/IDENTITY_BANNER.md``   — HARD SYSTEM RULE
    2. ``<agent>/agent-core/SOUL.md``          — persona
    3. ``<agent>/agent-core/IDENTITY.md``      — name / role / style
    4. ``<agent>/agent-core/AGENTS.md``        — working rules
    5. ``<agent>/agent-core/BOOTSTRAP.md``     — first-start checklist
    6. ``<agent>/agent-core/USER.md``          — user profile (learned)
    7. ``<agent>/agent-core/MEMORY.md``        — long-term memory
    8. ``<agent>/agent-core/TOOLS.md``         — auto-injected tool list

Empty files / missing sections are skipped gracefully. ``MEMORY.md`` and
``USER.md`` are expected to be empty on a fresh clone and grow over time
as the agent writes to them.

Arms are resolved from ``tool-registry.jsonc::arms`` against a name →
factory registry defined in ``runtime.execution.arms.presets``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.arms.base import ArmPool, Worker
from runtime.execution.arms.presets import (
    make_browser_interact_arm,
    make_browser_read_arm,
    make_desktop_operator_arm,
    make_fs_writer_arm,
    make_git_arm,
    make_shell_arm,
    make_web_read_arm,
)
from runtime.memory.runtime_state.scope_paths import visible_memory_tier_paths

from .base import Agent

# ═══════════════════════════════════════════════════════════
# arm factory registry · tool-registry.jsonc references these by name
# ═══════════════════════════════════════════════════════════

ArmFactory = Callable[[GraphRuntime], Worker]

_ARM_FACTORIES: dict[str, ArmFactory] = {
    "web_read": make_web_read_arm,
    "browser_read": make_browser_read_arm,
    "browser_interact": make_browser_interact_arm,
    "fs_writer": make_fs_writer_arm,
    "git": make_git_arm,
    "shell": make_shell_arm,
    "desktop_operator": make_desktop_operator_arm,
}


# ═══════════════════════════════════════════════════════════
# path helpers
# ═══════════════════════════════════════════════════════════


def _repo_root() -> Path:
    """Root that carries the bundled ``agents/`` presets.

    ``resources_root()``, not ``project_root()``: the 24 shipped agent
    profiles are read-only bundled assets, and ``project_root()`` walks up
    from the working directory — so anything started outside the checkout
    resolved ``<cwd>/agents`` and failed with "missing .../profile.jsonc"
    for profiles that were tracked in the repo the whole time.

    ``resources_root()`` resolves relative to the installed package (and
    honours ``ECHO_RESOURCES_DIR`` for the container layout), which is the
    same contract the skill and prompt catalogs use.
    """
    from runtime.platform.process.paths import resources_root

    return resources_root()


def _configured_agents_root() -> Path | None:
    raw = os.environ.get("ECHO_AGENTS_ROOT") or os.environ.get(
        "ECHO_AGENTS_DIR",
    )
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def default_agents_root() -> Path:
    configured = _configured_agents_root()
    if configured is not None:
        return configured
    return _repo_root() / "agents"


# ═══════════════════════════════════════════════════════════
# JSONC → JSON (strip // line comments + /* ... */ blocks)
# ═══════════════════════════════════════════════════════════

from runtime.platform.process.utils import parse_jsonc as _parse_jsonc


def _render_agent_identity_banner(agent_id: str, display_name: str) -> str:
    """Render the speaker identity rule for one concrete agent."""
    agent_id = str(agent_id or "").strip()
    display_name = str(display_name or agent_id or "Assistant").strip()
    return (
        "# HARD SYSTEM RULE - AGENT IDENTITY\n\n"
        f"You are **{display_name}**"
        + (f" (agent id: `{agent_id}`)" if agent_id else "")
        + ". This is your active persona for this turn. Echo is the "
        "runtime/product name, not your speaking name.\n\n"
        "When asked who you are / 你是谁 / 你叫什么, answer as this agent. "
        f'Begin with "我是 {display_name}" in Chinese or '
        f'"I\'m {display_name}" in English. Do NOT answer with the '
        "product/runtime name unless it is this agent's own display name. "
        "Do NOT name the underlying model provider."
    )


# ═══════════════════════════════════════════════════════════
# persona file composition
# ═══════════════════════════════════════════════════════════


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError):
        return ""


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError):
        return ""


# Per-tier cap for memory injected into the system prompt. A large
# memory file (e.g. a grown project index in ``.echo/MEMORY.md``)
# must never be able to blow the planner/react context budget — past
# the ceiling the compression engine drops the *entire* system segment,
# taking the agent's identity/soul with it. Bound each tier instead and
# keep the section readable.
_MAX_MEMORY_TIER_CHARS = 2000


def _bounded_memory_text(text: str) -> str:
    """Trim one memory tier to ``_MAX_MEMORY_TIER_CHARS`` with a marker."""
    text = (text or "").strip()
    if len(text) <= _MAX_MEMORY_TIER_CHARS:
        return text
    head = text[:_MAX_MEMORY_TIER_CHARS].rstrip()
    return f"{head}\n\n…(记忆过长，已截断为前 {_MAX_MEMORY_TIER_CHARS} 字符，原文 {len(text)} 字符)"


def _compose_soul(
    agent_dir: Path,
    shared_dir: Path,
    profile: dict | None = None,
) -> str:
    """Concatenate the knowledge files into a single system prompt.

    Default injection (per-turn, ~1000 tokens total):
        - IDENTITY_BANNER (HARD SYSTEM RULE · forbidden phrases)
        - SOUL.md (persona)
        - IDENTITY.md (role + communication style)
        - short identity footer reminder

    Optional sections — off by default to save tokens, opt-in via
    ``profile.jsonc::systemPrompt``::

        "systemPrompt": {
            "includeAgentsMd": true,     // working rules
            "includeBootstrapMd": true,  // first-run checklist
            "includeUserMd": true,       // auto-skipped if empty
            "includeMemoryMd": true      // auto-skipped if empty
        }

    ``USER.md`` / ``MEMORY.md`` are ALSO skipped if they're template-
    only (no real content) — agents start with empty memory and only
    pay tokens for it once they've written something worth remembering.
    """
    core = agent_dir / "agent-core"
    flags = (profile or {}).get("systemPrompt") or {}

    agent_id = str((profile or {}).get("id") or agent_dir.name)
    display_name = str((profile or {}).get("name") or agent_id)
    banner = _render_agent_identity_banner(agent_id, display_name)
    legacy_banner = _read_or_empty(shared_dir / "IDENTITY_BANNER.md")
    soul = _read_or_empty(core / "SOUL.md")
    identity = _read_or_empty(core / "IDENTITY.md")

    parts: list[str] = []
    if banner:
        parts.append(banner)
    if legacy_banner:
        parts.append("## Vendor Identity Guard\n\n" + legacy_banner)
    if soul:
        parts.append("## Persona\n\n" + soul)
    if identity:
        parts.append("## Identity\n\n" + identity)

    # Opt-in extras
    if flags.get("includeAgentsMd"):
        txt = _read_or_empty(core / "AGENTS.md")
        if txt:
            parts.append("## Working Rules\n\n" + txt)
    if flags.get("includeBootstrapMd"):
        txt = _read_or_empty(core / "BOOTSTRAP.md")
        if txt:
            parts.append("## Bootstrap\n\n" + txt)

    # USER / MEMORY: opt-in AND auto-skip when empty/template-only.
    # Default on — they're empty on fresh clones so they cost nothing,
    # but become free-standing memory when the agent actually writes.
    if flags.get("includeUserMd", True):
        txt = _read_or_empty(core / "USER.md")
        if txt and not _is_template_only(txt):
            parts.append("## User Profile\n\n" + txt)

    # Memory · three-tier layering:
    #   1. ~/.echo/MEMORY.md       (global · user-wide)
    #   2. <repo>/.echo/MEMORY.md  (project · repo-scoped)
    #   3. agents/<id>/agent-core/MEMORY.md  (agent-specific · existing)
    # Higher tiers override nothing · they STACK · in priority order
    # (global first / agent last · so agent-specific memory has
    # recency-bias weight for weak models).
    # Each tier independently gated by _is_template_only so empty
    # scaffolds cost zero tokens.
    if flags.get("includeMemoryMd", True):
        for tier_name, tier_path in _memory_tier_paths(agent_dir, core):
            txt = _read_or_empty(tier_path)
            if txt and not _is_template_only(txt):
                parts.append(
                    f"## Long-term Memory ({tier_name})\n\n{_bounded_memory_text(txt)}",
                )

    # Constitution summary · internalize the five principles. On by
    # default · tiny footprint (~250 tokens) · flip to False for
    # research builds that intentionally remove the safety layer
    # during evals (NOT recommended for user-facing agents).
    if flags.get("includeConstitution", True):
        try:
            from runtime.safety.validation import (
                get_constitution_summary,
            )

            parts.append(get_constitution_summary())
        except ImportError:  # noqa: BLE001 — optional integration; proceed without
            pass

    # Short identity reminder at tail (recency bias for weak models).
    if banner:
        parts.append(
            "## REMINDER\n\n"
            f"You are {display_name}. If asked who you are, say "
            f'"我是 {display_name}" or "I\'m {display_name}". '
            "Do not collapse this agent persona into the Echo product name."
        )
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Memory tier resolution · three-tier layering
# ═══════════════════════════════════════════════════════════


def _repo_root_for_agent_dir(agent_dir: Path) -> Path:
    if agent_dir.parent.name == "agents":
        return agent_dir.parent.parent
    return _repo_root()


def _memory_tier_paths(
    agent_dir: Path,
    core: Path,
    metadata: dict[str, Any] | None = None,
) -> list[tuple[str, Path]]:
    """Return visible memory tiers for an agent.

    Static loading sees global/project/agent. Runtime calls that pass
    session metadata also see team and team-agent layers.
    """
    repo_root = _repo_root_for_agent_dir(agent_dir)
    tiers = visible_memory_tier_paths(
        repo_root=repo_root,
        agent_id=agent_dir.name,
        metadata=metadata,
    )
    return [(name, core / "MEMORY.md") if name == "agent" else (name, path) for name, path in tiers]


_LONG_TERM_MEMORY_RE = re.compile(
    r"(?:\n{2}|^)## Long-term Memory \([^)]+\)\n\n.*?(?=\n{2}## |\Z)",
    re.DOTALL,
)


def _strip_long_term_memory_sections(text: str) -> str:
    return _LONG_TERM_MEMORY_RE.sub("", text or "").strip()


def _runtime_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if metadata is not None:
        return metadata
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        if session is not None and isinstance(session.metadata, dict):
            return session.metadata
    except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
        pass
    return {}


def render_runtime_memory_sections(
    agent_id: str,
    *,
    metadata: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> str:
    """Render fresh memory tiers for the current turn/session."""
    if not agent_id:
        return ""
    meta = _runtime_metadata(metadata)
    root = repo_root or _repo_root()
    parts: list[str] = []
    for tier_name, tier_path in visible_memory_tier_paths(
        repo_root=root,
        agent_id=agent_id,
        metadata=meta,
    ):
        txt = _read_or_empty(tier_path)
        if txt and not _is_template_only(txt):
            parts.append(
                f"## Long-term Memory ({tier_name})\n\n{_bounded_memory_text(txt)}",
            )
    return "\n\n".join(parts)


def compose_runtime_soul(
    agent: Any,
    *,
    metadata: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> str:
    """Return agent.soul with fresh scoped memory for this turn.

    Agent instances are loaded once at startup, so memory embedded in
    ``agent.soul`` can go stale. Strip those static memory blocks and
    append the current global/project/team/team-agent/agent tiers.
    """
    base_soul = str(getattr(agent, "soul", "") or "")
    agent_id = str(getattr(agent, "agent_id", "") or "")
    memory = render_runtime_memory_sections(
        agent_id,
        metadata=metadata,
        repo_root=repo_root,
    )
    stripped = _strip_long_term_memory_sections(base_soul)
    runtime_soul = f"{stripped}\n\n{memory}" if memory and stripped else memory or stripped
    try:
        from runtime.safety.evolution.runtime_deployment import (
            default_runtime_selector,
        )

        return default_runtime_selector().apply_role(runtime_soul, agent_id)
    except (ImportError, OSError, TypeError, ValueError):
        return runtime_soul


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _is_template_only(text: str) -> bool:
    """Return True if ``text`` is just scaffold content (headings +
    HTML comments + "(unknown)" / "_no memories yet._" placeholders).

    Used to skip injecting USER.md / MEMORY.md into the system prompt
    when the agent hasn't written real content yet — saves tokens on
    every turn.
    """
    # 1) drop multi-line HTML comments wholesale (the regex spans
    #    newlines via DOTALL so "<!-- line1\n line2 -->" collapses)
    stripped_html = _HTML_COMMENT_RE.sub("", text)

    # 2) collect non-blank, non-heading lines
    signal_lines = [
        line.strip()
        for line in stripped_html.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not signal_lines:
        return True

    # 3) drop sentinel placeholder phrases
    sentinel_exact = {
        "(unknown)",
        "(none recorded yet)",
        "_No memories yet._",
        "_no memories yet._",
    }
    sentinel_patterns = [
        # "- **Name**: (unknown)"
        re.compile(r"^-\s*\*\*[^*]+\*\*:\s*\(unknown\)\s*$"),
        # "- (none recorded yet)" / "- (unknown)"
        re.compile(r"^-\s*\(\s*(none recorded yet|unknown)\s*\)\s*$"),
    ]

    def is_sentinel(ln: str) -> bool:
        if ln in sentinel_exact:
            return True
        return any(p.match(ln) for p in sentinel_patterns)

    real = [ln for ln in signal_lines if not is_sentinel(ln)]
    return len(real) == 0


# ═══════════════════════════════════════════════════════════
# Agent construction
# ═══════════════════════════════════════════════════════════


def _build_arms(
    runtime: GraphRuntime,
    arm_ids: list[str],
    agent_id: str,
    private_skills: list[str] | None = None,
) -> ArmPool:
    arms: list[Worker] = []
    for arm_id in arm_ids:
        factory = _ARM_FACTORIES.get(arm_id)
        if factory is None:
            # Fail loud — a typo in tool-registry.jsonc shouldn't be
            # silent, or the agent would load with fewer arms than its
            # persona claims to have.
            raise ValueError(
                f"agent {agent_id!r} references unknown arm {arm_id!r}. "
                f"Known arms: {sorted(_ARM_FACTORIES)}"
            )
        arms.append(factory(runtime))

    # If the agent declares individual ``private_skills`` (finer grain
    # than the arm-level bundles), synthesize a per-agent "private arm"
    # that whitelists them for execution. Without this, the planner
    # could plan a call but no arm would accept the skill → runtime fail.
    if private_skills:
        from runtime.platform.models import ArmId, SkillId

        arms.append(
            Worker(
                arm_id=ArmId(f"{agent_id}_private_arm"),
                affinity=[],
                allowed_skills=[SkillId(s) for s in private_skills],
                runtime=runtime,
                display_name=f"{agent_id} private skills",
                description=(
                    "Agent-private skill whitelist — individual skills beyond the bundled arms."
                ),
                icon="🔑",
            )
        )

    return ArmPool(arms)


def _resolve_profile_model(profile: dict[str, Any]) -> str | None:
    """Resolve ``profile.jsonc::model`` to a concrete model-id string, or
    None when the agent expresses no preference.

    Two shapes exist in the wild: a bare string (``"claude-x"``) and a
    ``{"provider": ..., "name": ...}`` object. Every shipped agent uses
    the object form with ``"auto"``/``"auto"`` — i.e. "let the dispatch
    router decide" — which resolves to None (unchanged behavior). Only a
    concrete name yields a preference. Returning the raw object would be
    wrong: ``Agent.model`` is a string, and downstream resolvers
    (``resolve_turn_model``, ``stack_runner``) treat a non-string as a
    literal model id — a dict there would poison the request.
    """
    raw = profile.get("model")
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
        if not name or name.lower() == "auto":
            return None
        provider = str(raw.get("provider") or "").strip()
        if provider and provider.lower() != "auto":
            return f"{provider}/{name}"
        return name
    return None


@dataclass(frozen=True)
class AgentTemplate:
    """The runtime-independent result of parsing an ``agents/<id>/`` folder.

    Holds everything ``load_agent`` needs *except* the live arms, which
    require a ``GraphRuntime`` to build. Splitting parse from instantiate
    lets tooling — a template registry, a validator, a multi-tenant
    catalog — read and check an agent folder without standing up a
    runtime. ``instantiate(template, runtime)`` turns one into an Agent.
    """

    agent_id: str
    display_name: str
    description: str
    icon: str
    soul: str
    model: str | None
    arm_ids: list[str]
    affinity: list[str]
    private_skills: list[str]
    capabilities: dict[str, Any]
    budget: dict[str, Any]


def parse_template(agent_dir: Path, shared_dir: Path) -> AgentTemplate:
    """Parse an ``agents/<id>/`` folder into a runtime-independent template.

    Reads ``profile.jsonc`` + ``agent-core/tool-registry.jsonc`` and
    composes the static soul. No ``GraphRuntime`` needed — arm instances
    are built later by ``instantiate``.
    """
    profile_path = agent_dir / "profile.jsonc"
    if not profile_path.exists():
        raise FileNotFoundError(f"missing {profile_path}")
    profile = _parse_jsonc(profile_path.read_text(encoding="utf-8"))

    tool_registry_path = agent_dir / "agent-core" / "tool-registry.jsonc"
    tool_registry = (
        _parse_jsonc(tool_registry_path.read_text(encoding="utf-8"))
        if tool_registry_path.exists()
        else {"arms": [], "extra_affinity": []}
    )

    agent_id = str(profile.get("id") or agent_dir.name)

    # Capability flags · read by scope resolver and feature gates.
    # Lives in profile.jsonc so it travels with the agent folder —
    # if someone clones an agent dir to spin up a new persona, the
    # capability set comes with it.
    caps_raw = profile.get("capabilities") or {}
    # ``budget: { max_tokens: 100000, max_usd: 1.0, max_iterations: 30 }``,
    budget_raw = profile.get("budget") or {}

    return AgentTemplate(
        agent_id=agent_id,
        display_name=str(profile.get("name") or agent_id),
        description=str(profile.get("description") or ""),
        icon=str(profile.get("icon") or ""),
        soul=_compose_soul(agent_dir, shared_dir, profile=profile),
        model=_resolve_profile_model(profile),
        arm_ids=list(tool_registry.get("arms") or []),
        affinity=list(tool_registry.get("extra_affinity") or []),
        private_skills=list(tool_registry.get("private_skills") or []),
        capabilities=caps_raw if isinstance(caps_raw, dict) else {},
        budget=budget_raw if isinstance(budget_raw, dict) else {},
    )


def instantiate(template: AgentTemplate, runtime: GraphRuntime) -> Agent:
    """Build a live ``Agent`` from a parsed template against ``runtime``.

    This is the runtime-bound half of loading — it wires the arm
    factories (which need the runtime) onto the already-parsed template.
    """
    arms = _build_arms(
        runtime,
        template.arm_ids,
        template.agent_id,
        private_skills=template.private_skills,
    )
    return Agent(
        agent_id=template.agent_id,
        display_name=template.display_name,
        description=template.description,
        soul=template.soul,
        icon=template.icon,
        arms=arms,
        model=template.model,
        extra_affinity=template.affinity,
        extra_skills=template.private_skills,
        capabilities=template.capabilities,
        budget=template.budget,
    )


def load_agent(agent_dir: Path, runtime: GraphRuntime, shared_dir: Path) -> Agent:
    """Parse an agent folder and instantiate it against ``runtime``.

    A thin composition of ``parse_template`` (pure) + ``instantiate``
    (needs the runtime to build arms). Callers that only need metadata
    should call ``parse_template`` directly and skip the runtime.
    """
    return instantiate(parse_template(agent_dir, shared_dir), runtime)


_LOAD_ALL_SKIP_IDS: frozenset[str] = frozenset()


def load_all_agents(
    runtime: GraphRuntime,
    agents_root: Path | None = None,
) -> list[Agent]:
    """Scan ``agents/<id>/`` and load every folder that contains a
    ``profile.jsonc``. Skips ``_shared/``, any entry starting with
    ``.`` or ``_``, and any id in ``_LOAD_ALL_SKIP_IDS``."""
    agents_root = agents_root or default_agents_root()
    shared_dir = agents_root / "_shared"
    if not agents_root.is_dir():
        return []

    out: list[Agent] = []
    import logging as _logging

    _logger = _logging.getLogger(__name__)
    for entry in sorted(agents_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith((".", "_")):
            continue
        if entry.name in _LOAD_ALL_SKIP_IDS:
            continue
        profile_path = entry / "profile.jsonc"
        if not profile_path.exists():
            continue
        try:
            profile = _parse_jsonc(profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            profile = {}
        if isinstance(profile, dict) and profile.get("autoload") is False:
            continue
        # Per-entry isolation: one malformed profile.jsonc (e.g. mid-
        # write during a watcher refresh, or a bad manual edit) must
        # not abort the whole-registry reload. Log + skip.
        try:
            out.append(load_agent(entry, runtime, shared_dir))
        except Exception as exc:  # noqa: BLE001 — broad catch by design
            _logger.warning(
                "load_all_agents: skipping %s · %s: %s",
                entry.name,
                type(exc).__name__,
                exc,
            )
    return out
