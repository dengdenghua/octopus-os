"""Helper functions and data for ``agent_world_router``.

Extracted from ``agent_world_router.py`` to keep the router file under
1000 lines.  All public names are re-exported by the parent module so
that ``from runtime.sensing.gateway.agent_world_router import PublicName``
continues to work.

Functions that are monkey-patched by tests (``_template_skill_catalog``)
or that directly read the monkey-patched ``_INSTALL_STATE`` variable
(``_read_install_state`` / ``_write_install_state``) remain in the
parent module to preserve patch visibility.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any

from runtime.execution.agents.loader import default_agents_root
from runtime.platform.process.utils import parse_jsonc as _parse_jsonc

_ECHO_AUTHOR = "echo"
_ECHO_AUTHOR_ALIASES = {"preset", "system", "echo", "Echo"}
_SAFE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_AGENCY_AGENTS_ROOT = Path(__file__).with_name("agent_market_sources") / "agency-agents"
_FINANCIAL_SERVICES_ROOT = Path(__file__).with_name("agent_market_sources") / "financial-services"
_HARDWARE_STARTUP_ROOT = Path(__file__).with_name("agent_market_sources") / "hardware-startup"
_AGENT_AVATAR_FILENAMES = tuple(f"avatar.{ext}" for ext in ("png", "webp", "jpg", "jpeg", "svg"))
_AGENT_VISUAL_FILENAMES = tuple(
    f"{view}.{ext}"
    for view in ("front", "side", "back")
    for ext in ("png", "jpg", "jpeg", "webp", "svg")
) + ("reference.png",)
_LOCAL_AGENTS_CACHE_LOCK = threading.RLock()
_LOCAL_AGENTS_CACHE: (
    tuple[
        Path,
        tuple[Any, ...],
        list[dict[str, Any]],
    ]
    | None
) = None
_AGENCY_AGENT_DIRS = {
    "academic": "researcher",
    "design": "creative",
    "engineering": "coder",
    "finance": "specialist",
    "game-development": "creative",
    "marketing": "creative",
    "paid-media": "creative",
    "product": "researcher",
    "project-management": "automation",
    "sales": "assistant",
    "spatial-computing": "specialist",
    "specialized": "specialist",
    "strategy": "researcher",
    "support": "assistant",
    "testing": "coder",
}
BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "test_writer",
        "display_name": "Test Writer",
        "description": "Writes focused unit, integration, and regression tests from changed code.",
        "author": "echo",
        "category": "coder",
        "tags": ["test", "coverage", "pytest"],
        "icon": "🧪",
        "featured": True,
    },
    {
        "id": "code_reviewer",
        "display_name": "Code Reviewer",
        "description": "Finds logic gaps, unsafe changes, and maintainability issues before merge.",
        "author": "echo",
        "category": "coder",
        "tags": ["review", "quality", "diff"],
        "icon": "🧐",
        "featured": True,
    },
    {
        "id": "security_auditor",
        "display_name": "Security Auditor",
        "description": "Audits code for auth flaws, injection risks, secrets, and unsafe file operations.",
        "author": "echo",
        "category": "specialist",
        "tags": ["security", "audit", "owasp"],
        "icon": "🔐",
        "featured": True,
    },
    {
        "id": "api_architect",
        "display_name": "API Architect",
        "description": "Designs service boundaries, request contracts, and migration-friendly API changes.",
        "author": "echo",
        "category": "specialist",
        "tags": ["api", "architecture", "backend"],
        "icon": "🧭",
        "featured": True,
    },
    {
        "id": "frontend_assistant",
        "display_name": "Frontend Assistant",
        "description": "Builds polished UI flows, empty states, loading states, and responsive layouts.",
        "author": "echo",
        "category": "creative",
        "tags": ["ui", "react", "tailwind"],
        "icon": "🎨",
        "featured": True,
    },
    {
        "id": "docs_writer",
        "display_name": "Docs Writer",
        "description": "Turns implementation details into README, migration notes, and user-facing docs.",
        "author": "echo",
        "category": "assistant",
        "tags": ["docs", "readme", "guide"],
        "icon": "📝",
        "featured": False,
    },
    {
        "id": "bug_hunter",
        "display_name": "Bug Hunter",
        "description": "Reproduces bugs, narrows root causes, and proposes the smallest safe fix.",
        "author": "echo",
        "category": "coder",
        "tags": ["bug", "debug", "triage"],
        "icon": "🐛",
        "featured": True,
    },
    {
        "id": "refactor_surgeon",
        "display_name": "Refactor Surgeon",
        "description": "Performs targeted refactors while preserving behavior and minimizing blast radius.",
        "author": "echo",
        "category": "coder",
        "tags": ["refactor", "cleanup", "maintainability"],
        "icon": "✂️",
        "featured": False,
    },
    {
        "id": "release_manager",
        "display_name": "Release Manager",
        "description": "Prepares changelogs, verifies ship readiness, and checks release blocking issues.",
        "author": "echo",
        "category": "automation",
        "tags": ["release", "changelog", "ship"],
        "icon": "🚀",
        "featured": False,
    },
    {
        "id": "data_analyst",
        "display_name": "Data Analyst",
        "description": "Reads datasets, summarizes metrics, and produces analysis notebooks or reports.",
        "author": "echo",
        "category": "researcher",
        "tags": ["data", "analysis", "notebook"],
        "icon": "📊",
        "featured": False,
    },
    {
        "id": "deep_researcher",
        "display_name": "Deep Researcher",
        "description": "Performs broad multi-file and multi-source investigations before implementation.",
        "author": "echo",
        "category": "researcher",
        "tags": ["research", "investigation", "planning"],
        "icon": "🔬",
        "featured": True,
    },
    {
        "id": "performance_engineer",
        "display_name": "Performance Engineer",
        "description": "Finds bottlenecks, reduces bundle size, and improves runtime performance.",
        "author": "echo",
        "category": "specialist",
        "tags": ["performance", "profiling", "optimization"],
        "icon": "⚙️",
        "featured": False,
    },
    {
        "id": "cli_builder",
        "display_name": "CLI Builder",
        "description": "Designs commands, flags, help text, and ergonomic terminal-first workflows.",
        "author": "echo",
        "category": "coder",
        "tags": ["cli", "terminal", "argparse"],
        "icon": "⌨️",
        "featured": False,
    },
    {
        "id": "database_migrator",
        "display_name": "Database Migrator",
        "description": "Plans safe schema changes, backfills, and rollback-aware migrations.",
        "author": "echo",
        "category": "specialist",
        "tags": ["database", "migration", "sql"],
        "icon": "🗃️",
        "featured": False,
    },
    {
        "id": "observability_ops",
        "display_name": "Observability Ops",
        "description": "Improves logs, metrics, tracing, and alertability for production services.",
        "author": "echo",
        "category": "automation",
        "tags": ["logs", "metrics", "tracing"],
        "icon": "📡",
        "featured": False,
    },
    {
        "id": "prompt_designer",
        "display_name": "Prompt Designer",
        "description": "Tunes prompts, structured outputs, and tool-use contracts for AI products.",
        "author": "echo",
        "category": "creative",
        "tags": ["prompt", "llm", "ai"],
        "icon": "✨",
        "featured": False,
    },
    {
        "id": "workflow_automator",
        "display_name": "Workflow Automator",
        "description": "Builds repeatable workflow, task, and ops automations across tools.",
        "author": "echo",
        "category": "automation",
        "tags": ["workflow", "ops", "automation"],
        "icon": "🔁",
        "featured": False,
    },
    {
        "id": "knowledge_curator",
        "display_name": "Knowledge Curator",
        "description": "Collects scattered project knowledge into reusable reference pages and guides.",
        "author": "echo",
        "category": "assistant",
        "tags": ["knowledge", "wiki", "curation"],
        "icon": "📚",
        "featured": False,
    },
    {
        "id": "content_strategist",
        "display_name": "Content Strategist",
        "description": "Creates landing copy, positioning, and campaign messaging for product launches.",
        "author": "echo",
        "category": "creative",
        "tags": ["marketing", "copywriting", "content"],
        "icon": "📣",
        "featured": False,
    },
    {
        "id": "support_triager",
        "display_name": "Support Triager",
        "description": "Turns user reports into repro steps, suspected root causes, and fix tickets.",
        "author": "echo",
        "category": "assistant",
        "tags": ["support", "triage", "issues"],
        "icon": "🧰",
        "featured": False,
    },
    {
        "id": "browser_operator",
        "display_name": "Browser Operator",
        "description": "Handles browser-based workflows, form filling, and visual verification steps.",
        "author": "echo",
        "category": "automation",
        "tags": ["browser", "web", "operator"],
        "icon": "🌐",
        "featured": False,
    },
    {
        "id": "product_analyst",
        "display_name": "Product Analyst",
        "description": "Converts qualitative requests into specs, risks, tradeoffs, and delivery slices.",
        "author": "echo",
        "category": "researcher",
        "tags": ["product", "spec", "analysis"],
        "icon": "🧠",
        "featured": False,
    },
]


def _is_safe_agent_id(agent_id: str) -> bool:
    return bool(_SAFE_AGENT_ID_RE.fullmatch(str(agent_id or "")))


def _require_safe_agent_id(agent_id: str) -> str:
    value = str(agent_id or "").strip()
    if not _is_safe_agent_id(value):
        raise ValueError(
            "invalid agent_id: only alphanumeric characters, hyphens, and underscores are allowed"
        )
    return value


def _require_safe_skill_name(skill_name: str) -> str:
    value = str(skill_name or "").strip()
    if not _SAFE_SKILL_NAME_RE.fullmatch(value):
        raise ValueError(
            "invalid skill name in market template: only alphanumeric characters, "
            "hyphens, and underscores are allowed"
        )
    return value


def _slug_to_title(slug: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part)


def _normalize_local_author(value: Any) -> str:
    author = str(value or "").strip()
    if author in _ECHO_AUTHOR_ALIASES:
        return _ECHO_AUTHOR
    return author or "local"


def _parse_agent_markdown(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, "\n".join(lines[end + 1 :]).strip()


def _parse_agent_key_skills(body: str) -> list[str]:
    section = re.search(
        r"^##+\s+Skills\s+this\s+agent\s+uses\s*$([\s\S]*?)(?=^##+\s+|\Z)",
        body,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not section:
        return []
    skills = re.findall(r"`([^`]+)`", section.group(1))
    return list(dict.fromkeys(skill.strip() for skill in skills if skill.strip()))


def _template_private_skills(template: dict[str, Any]) -> list[str]:
    raw = template.get("private_skills") or []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(str(skill).strip() for skill in raw if str(skill).strip()))


def _load_agency_templates() -> list[dict[str, Any]]:
    if not _AGENCY_AGENTS_ROOT.is_dir():
        return []
    templates: list[dict[str, Any]] = []
    for division, category in _AGENCY_AGENT_DIRS.items():
        division_root = _AGENCY_AGENTS_ROOT / division
        if not division_root.is_dir():
            continue
        for path in sorted(division_root.rglob("*.md")):
            if path.name.upper() in {"README.MD", "EXECUTIVE-BRIEF.MD", "QUICKSTART.MD"}:
                continue
            meta, body = _parse_agent_markdown(path)
            if not body or not meta.get("name") or not meta.get("description"):
                continue
            slug = path.stem.lower().replace("_", "-")
            agent_id = f"agency_{slug.replace('-', '_')}"
            tags = ["agency-agents", division, *[p for p in slug.split("-")[:4] if p != division]]
            templates.append(
                {
                    "id": agent_id,
                    "display_name": meta.get("name") or _slug_to_title(path.stem),
                    "description": meta.get("description")
                    or meta.get("vibe")
                    or f"{_slug_to_title(path.stem)} from The Agency.",
                    "author": "msitarzewski/agency-agents",
                    "category": category,
                    "tags": list(dict.fromkeys(tags)),
                    "icon": meta.get("emoji") or "🤖",
                    "featured": False,
                    "source_repo": "agency-agents",
                    "source_path": str(path.relative_to(_AGENCY_AGENTS_ROOT)),
                    "source_url": f"https://github.com/msitarzewski/agency-agents/blob/main/{path.relative_to(_AGENCY_AGENTS_ROOT).as_posix()}",
                }
            )
    return templates


def _load_financial_services_templates() -> list[dict[str, Any]]:
    agent_root = _FINANCIAL_SERVICES_ROOT / "agent-plugins"
    if not agent_root.is_dir():
        return []
    icons = {
        "earnings-reviewer": "📈",
        "gl-reconciler": "🧾",
        "kyc-screener": "🛡️",
        "market-researcher": "🔎",
        "meeting-prep-agent": "📋",
        "model-builder": "📊",
        "month-end-closer": "🗓️",
        "pitch-agent": "💼",
        "statement-auditor": "✅",
        "valuation-reviewer": "💵",
    }
    display_names = {
        "gl-reconciler": "GL Reconciler",
        "kyc-screener": "KYC Screener",
    }
    templates: list[dict[str, Any]] = []
    for path in sorted(agent_root.glob("*/agents/*.md")):
        meta, body = _parse_agent_markdown(path)
        if not body or not meta.get("name") or not meta.get("description"):
            continue
        slug = str(meta["name"]).strip().lower().replace("_", "-")
        agent_id = f"financial_{slug.replace('-', '_')}"
        repo_path = Path("plugins") / path.relative_to(_FINANCIAL_SERVICES_ROOT)
        key_skills = _parse_agent_key_skills(body)
        skill_source_root = str(
            (path.parent.parent / "skills").relative_to(_FINANCIAL_SERVICES_ROOT)
        )
        tags = [
            "financial-services",
            "finance",
            *[p for p in slug.split("-") if p not in {"agent"}],
        ]
        templates.append(
            {
                "id": agent_id,
                "display_name": display_names.get(slug, _slug_to_title(slug)),
                "description": meta["description"],
                "author": "anthropics/financial-services",
                "category": "financial",
                "tags": list(dict.fromkeys(tags)),
                "icon": icons.get(slug, "💼"),
                "featured": False,
                "source_repo": "financial-services",
                "source_path": str(path.relative_to(_FINANCIAL_SERVICES_ROOT)),
                "source_url": f"https://github.com/anthropics/financial-services/blob/main/{repo_path.as_posix()}",
                "private_skills": key_skills,
                "skill_source_root": skill_source_root,
            }
        )
    return templates


def _load_hardware_startup_templates() -> list[dict[str, Any]]:
    """Load templates from the hardware-startup bundle.

    Mirrors _load_financial_services_templates: walks
    ``agent_market_sources/hardware-startup/agent-plugins/*/agents/*.md``
    and turns each agent markdown into a market template entry.

    Hardware-startup agents (patent/FTO, certification, crowdfunding,
    supply-chain) target the early-stage hardware product lifecycle.
    """
    agent_root = _HARDWARE_STARTUP_ROOT / "agent-plugins"
    if not agent_root.is_dir():
        return []
    icons = {
        "patent-fto-screener": "🔬",
        "certification-readiness": "📜",
        "crowdfunding-launch-manager": "🚀",
        "supply-chain-monitor": "🏭",
    }
    display_names = {
        "patent-fto-screener": "Patent / FTO Screener",
    }
    templates: list[dict[str, Any]] = []
    for path in sorted(agent_root.glob("*/agents/*.md")):
        meta, body = _parse_agent_markdown(path)
        if not body or not meta.get("name") or not meta.get("description"):
            continue
        slug = str(meta["name"]).strip().lower().replace("_", "-")
        agent_id = f"hardware_{slug.replace('-', '_')}"
        key_skills = _parse_agent_key_skills(body)
        skill_source_root = str((path.parent.parent / "skills").relative_to(_HARDWARE_STARTUP_ROOT))
        tags = ["hardware-startup", *[p for p in slug.split("-") if p not in {"agent"}]]
        templates.append(
            {
                "id": agent_id,
                "display_name": display_names.get(slug, _slug_to_title(slug)),
                "description": meta["description"],
                "author": "echo/hardware-startup",
                "category": "specialist",
                "tags": list(dict.fromkeys(tags)),
                "icon": icons.get(slug, "🛠️"),
                "featured": False,
                "source_repo": "hardware-startup",
                "source_path": str(path.relative_to(_HARDWARE_STARTUP_ROOT)),
                "private_skills": key_skills,
                "skill_source_root": skill_source_root,
            }
        )
    return templates


def _template_source_root(template: dict[str, Any]) -> Path:
    if template.get("source_repo") == "financial-services":
        return _FINANCIAL_SERVICES_ROOT
    if template.get("source_repo") == "hardware-startup":
        return _HARDWARE_STARTUP_ROOT
    return _AGENCY_AGENTS_ROOT


def _register_public_prompt_skills(skill_registry: Any, skills_root: Path) -> int:
    from runtime.execution.suckers.market_skills import immutable_prompt_catalog_required

    if skill_registry is None or not skills_root.is_dir() or immutable_prompt_catalog_required():
        return 0
    try:
        from runtime.execution.suckers.market_skills import register_market_skills

        return int(
            register_market_skills(
                skill_registry,
                all_skills_dir=skills_root,
                respect_enabled_flag=False,
                verify_tests=False,
            )
        )
    except Exception:  # noqa: BLE001 - copied skills are optional; agent still installs
        return 0


def _category_for(agent_id: str) -> str:
    if agent_id == "coder":
        return "coder"
    if agent_id in {"general", "echo"}:
        return "assistant"
    if agent_id in {"ecommerce_mind"}:
        return "specialist"
    if agent_id in {"vibe_selling"}:
        return "creative"
    if agent_id in {"desktop_operator", "admin"}:
        return "automation"
    return "assistant"


def _tags_for(agent_id: str, profile: dict[str, Any] | None = None) -> list[str]:
    if profile:
        raw_tags = profile.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(item) for item in raw_tags if str(item).strip()]
            if tags:
                return tags
    mapping = {
        "coder": ["code", "debug", "refactor", "test"],
        "general": ["general", "writing", "research"],
        "ecommerce_mind": ["ecommerce", "growth", "sourcing"],
        "vibe_selling": ["sales", "copywriting", "creative"],
        "desktop_operator": ["desktop", "browser", "automation"],
        "admin": ["system", "admin"],
    }
    return mapping.get(agent_id, [agent_id])


def _model_name_for_wire(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        provider = str(value.get("provider") or "").strip()
        if name and name != "auto":
            return name
        if provider and provider != "auto":
            return provider
    return None


def _path_stamp(path: Path) -> tuple[int, int, int, int] | None:
    """Return the inexpensive metadata used to invalidate file-backed caches."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)


def _local_agents_signature(root: Path) -> tuple[Any, ...]:
    """Fingerprint every file that contributes to the local market projection.

    Directory names make new/deleted agents visible immediately. Individual
    file stamps catch in-place profile, tool registry, avatar, and visual edits
    without rereading and reparsing every profile on every HTTP request.
    """
    entries: list[tuple[Any, ...]] = []
    try:
        agent_dirs = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_")),
            key=lambda path: path.name,
        )
    except OSError:
        agent_dirs = []

    for agent_dir in agent_dirs:
        visuals_dir = agent_dir / "visuals"
        visuals_stamp = _path_stamp(visuals_dir)
        entries.append(
            (
                agent_dir.name,
                _path_stamp(agent_dir / "profile.jsonc"),
                _path_stamp(agent_dir / "agent-core" / "tool-registry.jsonc"),
                tuple(
                    (filename, _path_stamp(agent_dir / filename))
                    for filename in _AGENT_AVATAR_FILENAMES
                ),
                visuals_stamp,
                tuple(
                    (filename, _path_stamp(visuals_dir / filename))
                    for filename in _AGENT_VISUAL_FILENAMES
                )
                if visuals_stamp is not None
                else (),
            )
        )
    return (_path_stamp(root), tuple(entries))


def _template_by_id(agent_id: str) -> dict[str, Any] | None:
    if not _is_safe_agent_id(agent_id):
        return None
    template = next((t for t in BUILTIN_TEMPLATES if t["id"] == agent_id), None)
    if template:
        return template
    return next(
        (
            t
            for t in [
                *_load_agency_templates(),
                *_load_financial_services_templates(),
                *_load_hardware_startup_templates(),
            ]
            if t["id"] == agent_id
        ),
        None,
    )


def _read_agent_profile(agent_root: Path) -> dict[str, Any] | None:
    profile_path = agent_root / "profile.jsonc"
    if profile_path.is_symlink() or not profile_path.is_file():
        return None
    try:
        profile = _parse_jsonc(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return profile if isinstance(profile, dict) else None


def _read_agent_tool_registry(agent_dir: Path) -> dict[str, list[str]]:
    path = agent_dir / "agent-core" / "tool-registry.jsonc"
    if not path.is_file():
        return {"arms": [], "extra_affinity": [], "private_skills": []}
    try:
        data = _parse_jsonc(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"arms": [], "extra_affinity": [], "private_skills": []}

    def _string_list(key: str) -> list[str]:
        raw = data.get(key) or []
        if not isinstance(raw, list):
            return []
        return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))

    return {
        "arms": _string_list("arms"),
        "extra_affinity": _string_list("extra_affinity"),
        "private_skills": _string_list("private_skills"),
    }


def _read_agent_private_skills(agent_dir: Path) -> list[str]:
    raw = _read_agent_tool_registry(agent_dir).get("private_skills") or []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(str(skill).strip() for skill in raw if str(skill).strip()))


def _avatar_url_for(
    agent_id: str,
    agent_dir: Path,
    profile: dict[str, Any] | None = None,
) -> str | None:
    if (
        profile is not None
        and "avatar" in profile
        and (profile.get("avatar") is None or profile.get("avatar") is False)
    ):
        return None
    for ext in ("png", "webp", "jpg", "jpeg", "svg"):
        path = agent_dir / f"avatar.{ext}"
        if path.is_file():
            return f"/api/agents/{agent_id}/avatar?v={int(path.stat().st_mtime)}"
    return None


def _agent_visual_urls_for(agent_id: str, agent_dir: Path) -> dict[str, str]:
    visuals_dir = agent_dir / "visuals"
    urls: dict[str, str] = {}
    for view in ("front", "side", "back"):
        for ext in ("png", "jpg", "jpeg", "webp", "svg"):
            path = visuals_dir / f"{view}.{ext}"
            if path.is_file():
                urls[view] = f"/api/agents/{agent_id}/visuals/{view}?v={int(path.stat().st_mtime)}"
                break

    reference = visuals_dir / "reference.png"
    if reference.is_file():
        version = int(reference.stat().st_mtime)
        for view in ("front", "side", "back"):
            urls.setdefault(view, f"/api/agents/{agent_id}/visuals/{view}?v={version}")
    return urls


def _scan_local_agents(root: Path) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    if root.is_dir():
        for agent_dir in root.iterdir():
            if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
                continue
            profile_path = agent_dir / "profile.jsonc"
            if not profile_path.is_file():
                continue
            try:
                profile = _parse_jsonc(profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            agent_id = str(profile.get("id") or agent_dir.name)
            display_name = str(profile.get("name") or agent_id)
            description = str(profile.get("description") or "")
            icon = str(profile.get("icon") or "") if "icon" in profile else "🤖"
            author = _normalize_local_author(profile.get("creator"))
            category = str(profile.get("category") or _category_for(agent_id))
            tags = _tags_for(agent_id, profile)
            tool_registry = _read_agent_tool_registry(agent_dir)
            private_skills = tool_registry["private_skills"]
            avatar_url = _avatar_url_for(agent_id, agent_dir, profile)
            is_official = author == _ECHO_AUTHOR
            # Agents discovered from ``agents/`` are already present on disk,
            # regardless of whether they came from official presets, packs, or
            # user-created folders.
            is_installed = True
            mtime = profile_path.stat().st_mtime
            agents.append(
                {
                    "id": agent_id,
                    "name": agent_id,
                    "display_name": display_name,
                    "description": description,
                    "author": author,
                    "category": category,
                    "tags": tags,
                    "icon": icon,
                    "avatar_url": avatar_url,
                    "visual_urls": _agent_visual_urls_for(agent_id, agent_dir),
                    "character_profile": profile.get("character_profile") or None,
                    "model": _model_name_for_wire(profile.get("model")),
                    "tool_groups": tool_registry["arms"],
                    "extra_affinity": tool_registry["extra_affinity"],
                    "private_skills": private_skills,
                    "capabilities": profile.get("capabilities") or {},
                    "version": str(profile.get("templateVersion") or "1.0.0"),
                    "downloads": 0,
                    "rating": 4.6 if is_official else 4.2,
                    "rating_count": 0,
                    "is_featured": agent_id
                    in {"general", "coder", "ecommerce_mind", "vibe_selling"},
                    "is_official": is_official,
                    "is_installed": is_installed,
                    "source_kind": str(profile.get("source_kind") or profile.get("source") or ""),
                    "created_at": str(mtime),
                    "key_skills": private_skills or profile.get("key_skills") or [],
                    "available_skills": profile.get("available_skills")
                    or private_skills
                    or profile.get("key_skills")
                    or [],
                }
            )
            seen.add(agent_id)

    # 本地角色库只保留物理存在于 agents/ 下的默认角色(含 echo 9 角色 + 系统内建
    # agent),不再把静态模板目录(BUILTIN_TEMPLATES/agency/financial/hardware,
    # 约 200 余条)当"可装入"项混进来 —— 这批模板已整体发布到公网 registry(见
    # registry_consumer_router 的 /api/registry/roles,role+twin-role 304 条,
    # 是模板目录的超集),改走「云端角色」浏览安装,母本本地只默认这 9(+系统)个。
    return agents


def _list_local_agents() -> list[dict[str, Any]]:
    """Return local market agents, reusing parsed profiles until inputs change."""
    global _LOCAL_AGENTS_CACHE

    root = default_agents_root()
    root_key = root.absolute()
    with _LOCAL_AGENTS_CACHE_LOCK:
        signature = _local_agents_signature(root)
        cached = _LOCAL_AGENTS_CACHE
        if cached is not None and cached[0] == root_key and cached[1] == signature:
            return copy.deepcopy(cached[2])

        agents = _scan_local_agents(root)
        # Do not retain a potentially mixed snapshot if files changed while
        # they were being parsed. The current request still gets its result;
        # the next request will perform a clean rescan.
        if _local_agents_signature(root) == signature:
            _LOCAL_AGENTS_CACHE = (root_key, signature, agents)
        return copy.deepcopy(agents)
