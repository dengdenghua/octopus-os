"""Sub-agent identity helpers: codename, avatar, and cheap-model resolution.

Extracted from ``bridge.py`` as part of a structural refactor. These are
pure functions / constants with no dependency on bridge module-level state,
so they are safe to import eagerly.
"""

from __future__ import annotations

import os
import random
import uuid

# Cheap-subagent model resolution: operators override via the
# ``ECHO_SUBAGENT_CHEAP_MODEL`` env var or the ``subagent_cheap_model``
# service-provider key; otherwise ``_resolve_cheap_custom_model`` picks a
# self-configured OpenAI-compatible model from ``custom_models.json``. When
# nothing resolves we return None (see ``_resolve_cheap_subagent_model``)
# and the ephemeral runner falls back to the planner/main model — the one
# model we know is configured and working. There is deliberately NO
# hard-coded last resort: a model id the operator never declared would just
# 404 (or land on a fallback endpoint that 404s it), so it is never worth
# inventing one here.


# ── Sub-agent visualisation: codename + avatar ────────────────
#
# Every spawned sub-agent gets a friendly codename ("Spark / Nova /
# Quark / Atlas / ...") and a role-specific emoji avatar. Both flow
# out as ``subagent_spawned`` lifecycle events so the frontend
# Workbench panel can show a card the moment the agent starts —
# instead of waiting for its first tool call to leak the role string
# through ``sub_tool_*`` events.

_CODENAME_POOL: tuple[str, ...] = (
    "Spark",
    "Nova",
    "Quark",
    "Atlas",
    "Echo",
    "Lyra",
    "Vega",
    "Pixel",
    "Halo",
    "Comet",
    "Drift",
    "Ember",
    "Flux",
    "Glow",
    "Helios",
    "Iris",
    "Juno",
    "Kite",
    "Lumen",
    "Maple",
    "Nimbus",
    "Orbit",
    "Prism",
    "Quest",
    "Rune",
    "Sable",
    "Tide",
    "Umbra",
    "Volt",
    "Whisk",
    "Xeno",
    "Yarrow",
    "Zenith",
    "Aurora",
    "Blaze",
    "Cinder",
    "Dune",
    "Frost",
)

# Role → emoji avatar. Falls back to 🐙 (echo mascot) for unknown
# roles. Kept short so the UI doesn't have to ship an icon library
# just for sub-agent tiles.
_ROLE_AVATAR: dict[str, str] = {
    "researcher": "🔍",
    "research": "🔍",
    "explorer": "🧭",
    "fact_checker": "✅",
    "fact-checker": "✅",
    "critic": "🛡️",
    "reviewer": "🛡️",
    "security": "🛡️",
    "security-review": "🛡️",
    "performance": "⚡",
    "style": "🎨",
    "synthesizer": "✍️",
    "writer": "✍️",
    "architect": "🏗️",
    "designer": "📐",
    "implementer": "🔧",
    "coder": "🔧",
    "reproducer": "🐛",
    "hypothesizer": "💡",
    "verifier": "🧪",
    "debugger": "🐛",
    "planner": "📋",
    "evaluator": "⚖️",
    "generator": "✨",
}
_DEFAULT_AVATAR = "🐙"


def _codename_for_role(role: str) -> str:
    """Pick a stable-but-friendly codename for a sub-agent.

    Random within the pool so callers can't accidentally rely on a
    specific name; UI uses the codename only as a display label, not
    an identifier. Counter suffix prevents collisions inside one
    parent turn.
    """
    name = random.choice(_CODENAME_POOL)
    suffix = uuid.uuid4().hex[:3]
    return f"{name}-{suffix}"


def _avatar_for_role(role: str) -> str:
    if not isinstance(role, str):
        return _DEFAULT_AVATAR
    key = role.strip().lower()
    return _ROLE_AVATAR.get(key, _DEFAULT_AVATAR)


# URL markers for single-model "Agent Plan" style endpoints. Cheap-routed
# subagents must never be pointed at these (see _is_agent_plan_endpoint).
_AGENT_PLAN_URL_MARKERS: tuple[str, ...] = (
    "/plan/",
    "/api/plan",
    "agent-plan",
)


def _is_agent_plan_endpoint(base_url: str) -> bool:
    """True when ``base_url`` is a single-model "Agent Plan" style endpoint.

    Volcengine's Agent Plan (``ark.cn-beijing.volces.com/api/plan/v3``)
    answers HTTP 404 for any model id outside its allowlist (kimi-k3 /
    ark-code-latest / ...). Routing an arbitrary cheap model id such as
    ``glm-4-flash`` there fails every call, so the cheap-model picker must
    never select these.
    """
    url = (base_url or "").strip().lower()
    return any(marker in url for marker in _AGENT_PLAN_URL_MARKERS)


def _resolve_cheap_custom_model() -> str | None:
    """Pick a cheap-routable model id from ``custom_models.json``, or None.

    Only entries the operator tagged ``tier: "economy"`` (the cost tier of a
    ``performance`` / ``balanced`` / ``economy`` scale; legacy ``"cheap"`` is
    accepted too) are candidates — we never guess cheapness from a model id,
    since a name marker (``flash``/``mini``/…) or alphabetical order says
    nothing about price and can route cheap work onto the most expensive
    model. A candidate must also be OpenAI-compatible (provider ``openai`` /
    empty), declare a ``base_url``, and NOT be a single-model Agent-Plan
    endpoint — those 404 for any model id outside their allowlist. The pick
    is deterministic (model ids sorted) so operators and tests get a stable
    choice. Returns ``None`` when no entry is tagged economy; callers then
    leave ``model_name`` unset so the ephemeral runner falls back to the
    planner/main model (the one guaranteed to be configured).
    """
    try:
        from runtime.platform.models.custom_model_flags import read_custom_models

        data = read_custom_models()
    except Exception:  # noqa: BLE001 — best-effort, never break dispatch
        return None
    if not isinstance(data, dict):
        return None
    candidates: list[str] = []
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or "").lower()
        if provider not in ("openai", ""):
            continue
        base_url = entry.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            continue
        if _is_agent_plan_endpoint(base_url):
            continue
        # Cheapness is an explicit operator declaration, not a heuristic:
        # only entries tagged ``economy`` (the legacy ``cheap`` value is
        # accepted too) are candidates for cheap subagent routing.
        tier = str(entry.get("tier") or "").strip().lower()
        if tier not in ("economy", "cheap"):
            continue
        model_id = str(entry.get("id") or entry.get("name") or "").strip()
        if not model_id:
            raw_models = entry.get("models")
            if isinstance(raw_models, list) and raw_models:
                model_id = str(raw_models[0]).strip()
        if model_id:
            candidates.append(model_id)
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


def _resolve_cheap_subagent_model() -> str | None:
    """Resolve the model name used for cheap-routed subagent calls.

    Resolution order:
    1. ``ECHO_SUBAGENT_CHEAP_MODEL`` env var (operator override)
    2. ``subagent_cheap_model`` service-provider config key
    3. a ``custom_models.json`` entry the operator explicitly declared
       cheap (``tier: "cheap"``) — the only trustworthy signal, since a
       hard-coded default used to land on a single-model Agent-Plan
       endpoint and 404 every call
    4. ``None`` — the bridge leaves ``model_name`` unset and the ephemeral
       runner falls back to the planner/main model. Deliberately NO hard-
       coded last resort: a model id the operator never configured would
       only 404, while the planner default is the one model we know works.
    """
    env_val = os.environ.get("ECHO_SUBAGENT_CHEAP_MODEL")
    if env_val and env_val.strip():
        return env_val.strip()
    try:
        from runtime.platform.process.service_provider import get_provider

        cfg_val = get_provider().get("subagent_cheap_model")
        if isinstance(cfg_val, str) and cfg_val.strip():
            return cfg_val.strip()
    except Exception:  # noqa: BLE001 — config lookup is best-effort
        pass
    custom = _resolve_cheap_custom_model()
    if custom:
        return custom
    # No cheap model configured anywhere → leave model_name unset so the
    # ephemeral runner uses its default (the planner/main model). Better
    # than inventing a model id the operator may not have — that 404s.
    return None
