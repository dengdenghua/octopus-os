"""Skill metadata assembly for the meta router.

Extracted from ``meta_router.py`` in the god-file split campaign. Owns the
skill-market classification, the file-backed SKILL.md catalog parsing, and
the category-derivation helpers that power the ``/api/skills`` wire payload
and the @-mention autocomplete.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]

    YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    YAML_AVAILABLE = False
    yaml = None  # type: ignore[assignment]

_INTERNAL_SKILL_GROUPS = {
    "agent_meta",
    "blackboard",
    "browser",
    "browser_act",
    "builtin",
    "code_intel",
    "computer",
    "cron",
    "fs_search",
    "fs_write",
    "git",
    "lsp",
    "memory",
    "shell",
    "skill_library",
    "web",
}

_INTERNAL_SKILL_PREFIXES = (
    "bb_",
    "browser_",
    "computer_",
    "git_",
    "live_browser_",
    "lsp_",
)

_INTERNAL_SKILL_NAMES = {
    "append_text_file",
    "apply_skill",
    "ask_user_question",
    "background_exec",
    "cancel_scheduled_task",
    "edit_file",
    "edit_text_file",
    "exec_shell",
    "exit_plan_mode",
    "file_stats",
    "find-skills",
    "glob_files",
    "grep_text",
    "hash_text",
    "ipython",
    "keyboard_type",
    "kill_background_exec",
    "kill_shell",
    "learn_skill_from_text",
    "list_cwd",
    "list_learned_skills",
    "list_scheduled_tasks",
    "mouse_click",
    "multi_edit_file",
    "query_skill",
    "read_background_output",
    "read_file",
    "read_file_range",
    "read_shell_output",
    "run_orchestration",
    "run_pipeline",
    "schedule_task",
    "search_capabilities",
    "search_skills",
    "todo_read",
    "todo_write",
    "tree",
    "use_capability",
    "write_text_file",
}

_DUPLICATE_SKILL_CANONICALS = {
    "ad-creative": "ad-copywriter",
    "earnings-preview-single": "earnings-preview",
    "gitlab-cli-skills": "gitlab-cli-guide",
    "http-load-profiler": "http-load-tester",
    "seo-analyzer": "seo-audit",
    "test-driven-dev": "tdd-coach",
    "test-driven-development": "tdd-coach",
}

_PROVIDER_SKILL_CANONICALS = {
    "agnes-image-generate": "generate_image",
    "seedream-image-generate": "generate_image",
    "agnes-video-generate": "generate_video",
    "agnes-video-poll": "generate_video",
    "seedance-video-generate": "generate_video",
    "generate_sound_effects": "generate_speech",
    "generate_speech": "speech-synthesis",
}

_INTERNAL_EXECUTION_MODES = {
    "deep-research-swarm": "deep-research",
    "pptx-author": "pptx",
    "xlsx-author": "xlsx",
}


def _skill_market_profile(
    *,
    name: str,
    description: str,
    trusted_source: str,
    group: str | None,
    kind: str,
) -> dict[str, str | None]:
    """Classify how a skill should appear in the user-facing market.

    This does not disable or unregister skills. It only gives the UI a
    stable visibility hint so internal primitives remain callable while
    the marketplace stays oriented around user-level capabilities.
    """
    skill_name = name.strip()
    lower_name = skill_name.lower()
    lower_desc = description.lower()

    if lower_name in _DUPLICATE_SKILL_CANONICALS:
        return {
            "market_visibility": "duplicate",
            "market_reason": "同类技能已有主入口，避免重复展示",
            "canonical_skill": _DUPLICATE_SKILL_CANONICALS[lower_name],
        }
    if lower_name in _PROVIDER_SKILL_CANONICALS:
        return {
            "market_visibility": "provider",
            "market_reason": "模型或供应商后端入口，归并到主技能下",
            "canonical_skill": _PROVIDER_SKILL_CANONICALS[lower_name],
        }
    if lower_name in _INTERNAL_EXECUTION_MODES:
        return {
            "market_visibility": "internal",
            "market_reason": "执行模式或无界面变体，由主技能自动选择",
            "canonical_skill": _INTERNAL_EXECUTION_MODES[lower_name],
        }
    if trusted_source.startswith("skill://forged/") or lower_name.startswith("forged_"):
        return {
            "market_visibility": "deprecated",
            "market_reason": "自动组合技能，用户价值低，保留给内部兼容",
            "canonical_skill": None,
        }
    if kind != "domain":
        return {
            "market_visibility": "internal",
            "market_reason": "系统或自动化原子技能，不作为市场能力展示",
            "canonical_skill": None,
        }
    if group in _INTERNAL_SKILL_GROUPS:
        return {
            "market_visibility": "internal",
            "market_reason": "底层工具技能，由 agent 自动调用",
            "canonical_skill": None,
        }
    if lower_name in _INTERNAL_SKILL_NAMES or lower_name.startswith(_INTERNAL_SKILL_PREFIXES):
        return {
            "market_visibility": "internal",
            "market_reason": "底层工具技能，由 agent 自动调用",
            "canonical_skill": None,
        }
    if "only when a user wants to create a reusable skill" in lower_desc:
        return {
            "market_visibility": "internal",
            "market_reason": "技能开发辅助入口，不混入普通能力市场",
            "canonical_skill": None,
        }
    return {
        "market_visibility": "market",
        "market_reason": None,
        "canonical_skill": None,
    }


# File-backed SKILL.md catalog. These entries power the frontend skill
# browser for prompt/workflow skills that do not have Python handlers.
_FRONTMATTER_PATTERN = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)


def _default_skill_library_dir() -> Path:
    """Preferred skill library directory (``skills/public/``).

    Falls back to the legacy in-package ``all_skills/`` directory when the
    external resources root is unavailable.
    """
    from runtime.platform.process.paths import resources_root

    external = resources_root() / "skills" / "public"
    if external.is_dir():
        return external
    return Path(__file__).resolve().parents[2] / "execution" / "all_skills"


def _permission_group_for_skill(skill_id: str) -> str | None:
    try:
        from runtime.execution.misc.capability_permissions import (
            permission_group_for_skill,
        )

        return permission_group_for_skill(skill_id)
    except (ImportError, AttributeError, KeyError):
        return None


# ═══════════════════════════════════════════════════════════
# Skill kind classification
#
# Source-of-truth lives in ``runtime.execution.all_skills.skill_kind`` ·
# keeps the system/automation/domain buckets consistent between the
# ``/api/skills`` wire payload and the ReAct catalog filter. We just
# re-import here for backward-compat local use (lazy to avoid circular
# import at module load time).
# ═══════════════════════════════════════════════════════════


def _skill_group_for(name: str) -> str | None:
    try:
        from runtime.execution.all_skills import skill_group

        return skill_group(name)
    except (ImportError, AttributeError, KeyError):
        return None


def _skill_kind(group: str | None, name: str = "") -> str:
    """Classify into ``"system" | "automation" | "domain"``.

    Thin wrapper · delegates to ``all_skills.skill_kind`` so the
    classification rules stay in one place.
    """
    try:
        from runtime.execution.all_skills import skill_kind as _classify

        return _classify(name)
    except (ImportError, AttributeError, KeyError):
        return "domain"


@lru_cache(maxsize=1)
def _dynamic_plugin_skill_names() -> set[str]:
    roots: list[Path] = []
    try:
        project = _default_skill_library_dir().parents[2]
    except Exception:
        project = Path.cwd()
    # 统一读 echo 名下插件(~/.echo/plugins/codex);旧 ~/.codex 缓存先同步一次
    try:
        from runtime.platform.plugins.codex_discovery import (
            sync_codex_cache_to_echo,
        )

        sync_codex_cache_to_echo()
    except Exception:  # noqa: BLE001
        pass
    roots.extend(
        [
            project / ".echo" / "plugins" / "codex",
            Path.home() / ".echo" / "plugins" / "codex",
        ]
    )
    names: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            try:
                rel_parts = skill_md.relative_to(root).parts
            except ValueError:
                continue
            if "skills" not in rel_parts:
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _ = _parse_catalog_frontmatter(text)
            name = _clean_text(meta.get("name")) or skill_md.parent.name
            if name:
                names.add(name)
                names.add(skill_md.parent.name)
    return names


def _is_hidden_skill_catalog_entry(
    name: str,
    trusted_source: str,
    dynamic_names: set[str] | None = None,
    seen_sources: set[str] | None = None,
) -> bool:
    if not name:
        return False
    canonical_source = trusted_source.split("#", 1)[0]
    if seen_sources is not None and canonical_source in seen_sources:
        return True
    names = dynamic_names if dynamic_names is not None else _dynamic_plugin_skill_names()
    if name in names and trusted_source.startswith("skill://all_skills/"):
        return True
    if not canonical_source.startswith("skill://all_skills/"):
        if seen_sources is not None:
            seen_sources.add(canonical_source)
        return False
    rel = canonical_source.removeprefix("skill://all_skills/")
    if "#" in trusted_source or "/" in rel:
        return True
    if seen_sources is not None:
        seen_sources.add(canonical_source)
    return False


def _load_file_skill_catalog(
    roots: Sequence[Path],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            if _should_skip_catalog_path(skill_md, root):
                continue
            try:
                skill = _skill_wire_from_md(skill_md, root=root)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("skipping catalog file %s after parse failure: %s", skill_md, exc)
                continue
            if skill is not None:
                out.append(skill)
    return out


def _should_skip_catalog_path(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part.startswith(".") or part == "__pycache__" for part in parts)


def _skill_wire_from_md(
    skill_md: Path,
    *,
    root: Path,
) -> dict[str, Any] | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None

    meta, body = _parse_catalog_frontmatter(text)
    name = _clean_text(meta.get("name")) or skill_md.parent.name
    description = (
        _clean_text(meta.get("description"))
        or _first_markdown_paragraph(body)
        or "File-backed skill from the bundled skill library."
    )
    affinity = _coerce_string_list(meta.get("affinity") or meta.get("tags"))
    for tag in _infer_catalog_affinity(name, description):
        if tag not in affinity:
            affinity.append(tag)
    trusted_source = _clean_text(meta.get("trusted_source"))
    if not trusted_source:
        rel_dir = _safe_relative_posix(skill_md.parent, root)
        trusted_source = f"skill://all_skills/{rel_dir}"
    cost_profile = _clean_text(meta.get("cost_profile"))
    has_tests = bool(meta.get("tests")) or (skill_md.parent / "tests").exists()
    market_profile = _skill_market_profile(
        name=name,
        description=description,
        trusted_source=trusted_source,
        group=None,
        kind="domain",
    )
    return {
        "name": name,
        "description": description,
        "affinity": affinity,
        "cost_profile": cost_profile,
        "trusted_source": trusted_source,
        "has_tests": has_tests,
        "category": _derive_skill_category(name, affinity),
        "group": None,
        "kind": "domain",
        **market_profile,
    }


def _parse_catalog_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    clean_text = text.lstrip("\ufeff")
    match = _FRONTMATTER_PATTERN.match(clean_text)
    if not match:
        return {}, text
    body = clean_text[match.end() :]
    raw_frontmatter = match.group(1)
    if YAML_AVAILABLE:
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except (yaml.YAMLError, TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            return parsed, body
        return {}, body
    return _parse_simple_frontmatter(raw_frontmatter), body


def _parse_simple_frontmatter(raw: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            data[key] = value.strip().strip("'\"")
    return data


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,;\n]", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_items = list(value)
    else:
        raw_items = [value]
    out: list[str] = []
    for item in raw_items:
        tag = _clean_text(item).lower()
        if tag and tag not in out:
            out.append(tag)
    return out


def _clean_text(value: Any, *, max_len: int = 640) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _first_markdown_paragraph(body: str) -> str:
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if lines:
                break
            continue
        if line.startswith(("#", "```", "|", "<!--")):
            continue
        lines.append(line)
    return _clean_text(" ".join(lines))


def _safe_relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _infer_catalog_affinity(name: str, description: str) -> list[str]:
    text = f"{name} {description}".lower()
    inferred: list[str] = []
    keyword_tags = [
        (
            "ecommerce",
            (
                "1688",
                "alibaba",
                "amazon",
                "amz",
                "shopify",
                "etsy",
                "dropshipping",
                "cross-border",
                "e-commerce",
                "ecommerce",
            ),
        ),
        (
            "sourcing",
            (
                "supplier",
                "sourcing",
                "product selection",
            ),
        ),
        (
            "research",
            (
                "research",
                "analysis",
                "market",
                "competitor",
                "insight",
                "analytics",
            ),
        ),
        (
            "content",
            (
                "copywriting",
                "description",
                "marketing",
                "social",
                "generate",
                "content",
            ),
        ),
        (
            "file",
            (
                "xlsx",
                "docx",
                "pdf",
                "pptx",
                "remotion",
                "website",
            ),
        ),
    ]
    for tag, keywords in keyword_tags:
        if any(keyword in text for keyword in keywords):
            inferred.append(tag)
    return inferred


# ═══════════════════════════════════════════════════════════
# Skill category derivation
# ═══════════════════════════════════════════════════════════
#
#

SKILL_CATEGORIES: dict[str, str] = {
    "sourcing": "货源与选品",
    "research": "市场调研与分析",
    "file": "文件与编码",
    "comm": "通讯与协作",
    "browse": "浏览与搜索",
    "content": "内容生成",
    "system": "系统",
    "memory": "记忆",
    "other": "其他",
}

_CATEGORY_PRIORITY: list[tuple[str, set[str]]] = [
    (
        "sourcing",
        {
            "sourcing",
            "supplier",
            "ecommerce",
            "shopify",
            "1688",
            "aliexpress",
            "cj",
            "amazon",
            "amz",
            "alibaba",
            "etsy",
            "dropshipping",
            "product-selection",
            "selection",
        },
    ),
    ("research", {"research", "analysis", "market", "competitor", "intelligence", "data"}),
    ("browse", {"browse", "browser", "search", "web"}),
    ("file", {"file", "write", "edit", "code", "git", "io"}),
    ("comm", {"im", "slack", "email", "wechat", "dingtalk", "telegram", "feishu", "discord"}),
    ("content", {"write_content", "generate", "translate", "summarize", "transform"}),
    ("memory", {"memory", "kg", "recall"}),
    ("system", {"system", "os", "process", "sandbox", "shell"}),
]


def _derive_skill_category(name: str, affinity: list[str]) -> str:
    tags = {t.lower() for t in affinity}
    name_l = name.lower()
    for cat_id, keywords in _CATEGORY_PRIORITY:
        if tags & keywords:
            return cat_id
        for kw in keywords:
            if kw in name_l:
                return cat_id
    return "other"


def _resolve_thread_active_agents(thread_id: str, registry: Any) -> set[str]:
    """Best-effort resolution of agents active in a thread.

    Strategy (each step is wrapped in suppress so a missing module or
    schema doesn't break the endpoint):

    1. team-room membership: if the thread is bound to a team room,
       use the room's member list.
    2. message history: scan recent messages for sender_agent_id.

    Returns an empty set when nothing can be resolved.
    """
    agent_ids: set[str] = set()
    if not thread_id:
        return agent_ids

    # 1) team room membership
    with suppress(Exception):
        rooms_module = __import__(
            "runtime.safety.organization.team_rooms",
            fromlist=["get_team_rooms_store"],
        )
        get_store = getattr(rooms_module, "get_team_rooms_store", None)
        if callable(get_store):
            store = get_store()
            room = None
            for candidate in store.list_rooms():
                if getattr(candidate, "thread_id", None) == thread_id:
                    room = candidate
                    break
            if room is not None:
                for member in getattr(room, "members", []) or []:
                    member_id = str(
                        getattr(member, "agent_id", "") or getattr(member, "name", "") or member,
                    )
                    if member_id:
                        agent_ids.add(member_id)

    # 2) message senders (last 100 messages, dedupe)
    with suppress(Exception):
        threads_module = __import__(
            "runtime.memory.thread_state",
            fromlist=["get_thread_state_store"],
        )
        get_store = getattr(threads_module, "get_thread_state_store", None)
        if callable(get_store):
            store = get_store()
            messages = list(store.list_messages(thread_id, limit=100))[-100:]
            for msg in messages:
                sender = str(
                    getattr(msg, "sender_agent_id", "") or getattr(msg, "agent_id", "") or "",
                )
                if sender:
                    agent_ids.add(sender)

    # 3) registry sanity-filter — only return ids the registry knows
    if hasattr(registry, "has"):
        return {aid for aid in agent_ids if registry.has(aid)}
    return agent_ids


__all__ = [
    "SKILL_CATEGORIES",
    "_derive_skill_category",
    "_resolve_thread_active_agents",
]

_LOG = logging.getLogger(__name__)
