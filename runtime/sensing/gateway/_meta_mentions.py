"""@-mention autocomplete builder for the meta router.

Extracted from ``meta_router.py`` in the god-file split campaign. The
handler body was large enough that it was the single biggest chunk of the
closure factory; moving it here keeps ``meta_router.py`` under the
1000-line gate.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from runtime.sensing.gateway._meta_skill_metadata import _resolve_thread_active_agents


def _build_mentions_autocomplete(
    *,
    registry: Any,
    q: str,
    workspace: str,
    thread_id: str,
    actor: str,
    scope: str,
    limit: int,
) -> dict[str, Any]:
    """Autocomplete suggestions for @-mentions in chat input.

    Returns mention items across multiple categories:
      - agent: registered agents (id, name, description)
      - plugin: installed plugins
      - skill: registered skills
      - pack: dynamic skill packs (research / web / browser / files / code)
      - file/symbol/folder/git/docs/web/terminal: workspace context
        (basic best-effort; rich workspace mentions live in their
        own routers if installed)

    Query parameters:
      q: filter substring (case-insensitive)
      workspace: workspace path for file/symbol scope (currently unused
        here; placeholder for future workspace router integration)
      thread_id: when provided, agent results are filtered to those
        active in the thread first, then padded with global agents
      actor: when provided, items previously used by this actor
        are ranked higher (cross-thread mention history)
      scope: "all" (default), "agent", "plugin", "skill", "pack"
      limit: max items to return (default 20, max 100)
    """
    del workspace  # reserved for future workspace-aware filtering
    max_items = max(1, min(100, int(limit or 20)))
    query = (q or "").strip().lower()
    category_filter = (scope or "all").strip().lower()
    if query.startswith("@"):
        query = query[1:]

    type_prefix = ""
    if ":" in query:
        type_prefix, _, query = query.partition(":")

    # Look up cross-thread history for ranking. Failures are
    # silently ignored — autocomplete still works without history.
    history_boost: dict[tuple[str, str], int] = {}
    if actor:
        try:
            from runtime.memory.users.mention_history import (
                get_mention_history_store,
            )

            store = get_mention_history_store()
            for stat in store.top_for_actor(actor, limit=50):
                history_boost[(stat.type, stat.identifier)] = stat.count
        except (
            ImportError,
            AttributeError,
            OSError,
        ):  # best-effort · autocomplete still works without history
            pass

    items: list[dict[str, Any]] = []

    def _matches(haystack: str) -> bool:
        if not query:
            return True
        return query in (haystack or "").lower()

    # ── Agents ───────────────────────────────────────────
    if (category_filter in {"all", "agent"}) and (not type_prefix or type_prefix == "agent"):
        try:
            agents_iter = list(registry.iter_agents()) if hasattr(registry, "iter_agents") else []
        except (AttributeError, TypeError):
            agents_iter = []
        # Surface thread's active agents first when thread_id given
        active_agent_ids: set[str] = set()
        if thread_id:
            with suppress(Exception):
                active_agent_ids = _resolve_thread_active_agents(thread_id, registry)
        ranked: list[tuple[bool, dict[str, Any]]] = []
        for agent in agents_iter:
            agent_id = str(getattr(agent, "id", "") or getattr(agent, "name", ""))
            if not agent_id:
                continue
            display = str(getattr(agent, "display_name", "") or agent_id)
            desc = str(getattr(agent, "description", "") or "")
            if not (_matches(agent_id) or _matches(display) or _matches(desc)):
                continue
            ranked.append(
                (
                    agent_id in active_agent_ids,
                    {
                        "type": "agent",
                        "label": display,
                        "value": f"@agent:{agent_id}",
                        "description": desc[:120],
                        "icon": "agent",
                    },
                )
            )
        ranked.sort(key=lambda pair: (not pair[0], pair[1]["label"].lower()))
        for _is_active, payload in ranked:
            items.append(payload)
            if len(items) >= max_items:
                break

    # ── Plugins ──────────────────────────────────────────
    if (
        (category_filter in {"all", "plugin"})
        and (not type_prefix or type_prefix == "plugin")
        and len(items) < max_items
    ):
        try:
            plugin_router_module = __import__(
                "runtime.sensing.gateway.plugins_router",
                fromlist=["_LATEST_PLUGINS"],
            )
            plugins_list = getattr(plugin_router_module, "_LATEST_PLUGINS", []) or []
        except (ImportError, AttributeError):
            plugins_list = []
        for plugin in plugins_list:
            pid = str(plugin.get("id") or plugin.get("name") or "")
            if not pid:
                continue
            name = str(plugin.get("name") or pid)
            desc = str(plugin.get("description") or "")
            if not (_matches(pid) or _matches(name) or _matches(desc)):
                continue
            items.append(
                {
                    "type": "plugin",
                    "label": name,
                    "value": f"@plugin:{pid}",
                    "description": desc[:120],
                    "icon": "plugin",
                }
            )
            if len(items) >= max_items:
                break

    # ── Skills ───────────────────────────────────────────
    if (
        (category_filter in {"all", "skill"})
        and (not type_prefix or type_prefix == "skill")
        and len(items) < max_items
    ):
        try:
            skill_iter = list(registry.iter_skills()) if hasattr(registry, "iter_skills") else []
        except (AttributeError, TypeError):
            skill_iter = []
        for skill in skill_iter:
            sname = str(getattr(skill, "name", "") or "")
            if not sname:
                continue
            sdesc = str(getattr(skill, "description", "") or "")
            if not (_matches(sname) or _matches(sdesc)):
                continue
            items.append(
                {
                    "type": "skill",
                    "label": sname,
                    "value": f"@skill:{sname}",
                    "description": sdesc[:120],
                    "icon": "skill",
                }
            )
            if len(items) >= max_items:
                break

    # ── Skill Packs ──────────────────────────────────────
    if (
        (category_filter in {"all", "pack"})
        and (not type_prefix or type_prefix == "pack")
        and len(items) < max_items
    ):
        try:
            from runtime.execution.suckers.delegation_skills import (
                _DYNAMIC_SKILL_PACKS,
            )

            pack_dict = _DYNAMIC_SKILL_PACKS
        except (ImportError, AttributeError):
            pack_dict = {}
        for pack_name, pack_skills in pack_dict.items():
            pdesc = f"Bundled skills: {', '.join(pack_skills[:6])}" + (
                "..." if len(pack_skills) > 6 else ""
            )
            if not (_matches(pack_name) or _matches(pdesc)):
                continue
            items.append(
                {
                    "type": "pack",
                    "label": pack_name,
                    "value": f"@pack:{pack_name}",
                    "description": pdesc[:120],
                    "icon": "pack",
                }
            )
            if len(items) >= max_items:
                break

    # Re-rank by cross-thread mention history when actor is given.
    # Items the actor has used before float to the top of their
    # bucket; ordering within "never used" is preserved.
    if history_boost:

        def _rank(item: dict[str, Any]) -> tuple[int, int]:
            key = (str(item.get("type", "")), str(item.get("value", "")).split(":", 1)[-1])
            # Negate so higher-count items sort first.
            return (0 if key in history_boost else 1, -history_boost.get(key, 0))

        items.sort(key=_rank)

    return {"items": items[:max_items], "count": len(items[:max_items])}
