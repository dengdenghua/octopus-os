"""Three-tier smart model routing.

Three tiers of model, configurable per deployment:

    LOCAL       — runs on user's box (Ollama / LM Studio / vLLM /
                  llama.cpp). Zero cloud cost, fully private. Right
                  for trivial / short Q&A where latency is what
                  matters and the model just needs to be coherent.

    VALUE       — cheap cloud (glm-4-flash / haiku / gpt-4o-mini /
                  deepseek-chat). Cents per turn. Right for simple
                  single-tool calls, short coding answers, well-
                  scoped Q&A.

    PERFORMANCE — frontier cloud (claude-sonnet-4 / opus / gpt-5 /
                  gemini-2.5-pro). Right for multi-step / code /
                  research / agentic loops where reliability is
                  worth the spend.

Routing decision = ``estimate_turn_complexity()`` * ``select_tier()`` *
``resolve_model_for_tier()``.

Operators configure each tier via:
  * env: ``ECHO_MODEL_LOCAL`` / ``ECHO_MODEL_VALUE`` /
    ``ECHO_MODEL_PERFORMANCE``
  * service_provider keys: ``smart_routing.local`` /
    ``smart_routing.value`` / ``smart_routing.performance``
  * full kill-switch: ``ECHO_SMART_ROUTING=off``

When a tier resolves to None (operator hasn't configured a local
model, etc.), the router falls back to the next tier up — never
down — so we never demote to "no model".
"""

from __future__ import annotations

import os
import re

# Three-tier verdict.
# - "local"       — short ack / chitchat / trivially answerable
# - "value"       — single-shot Q&A, single tool call, short coding
# - "performance" — multi-step / code mode / research / topology
ComplexityVerdict = str  # "local" | "value" | "performance"


def _looks_like_chitchat(text: str) -> bool:
    """Whether the message is a short ack / chat opener.

    Note: this is intentionally NARROW. Mode flags (code_mode,
    has_topology, etc.) are checked FIRST in
    ``estimate_turn_complexity`` so that a 3-char code-mode message
    like "改一下" doesn't get misclassified as chitchat.
    """
    if not isinstance(text, str):
        return False
    s = text.strip()
    if not s:
        return False
    if len(s) <= 2:  # 1-2 chars almost certainly an ack
        return True
    pattern = re.compile(
        r"^\s*("
        r"你好|您好|早上好|晚上好|嗨|哈喽|hello|hi|hey|"
        r"谢谢|多谢|感谢|thx|thanks|"
        r"再见|拜拜|bye|byebye|"
        r"哈+|呵+|嘿+|嘻+|嗯+|哦+|噢+|"
        r"行|好的|好啊|可以|没问题|收到|明白|懂了|"
        r"666+|nice|cool|awesome"
        r")\s*[!。?\.\?\!,~。！？]*\s*$",
        re.IGNORECASE,
    )
    return bool(pattern.match(s))


def is_smart_routing_enabled() -> bool:
    env_val = os.environ.get("ECHO_SMART_ROUTING")
    if env_val is not None:
        return env_val.strip().lower() not in {"0", "false", "off", "no", "disabled"}
    try:
        from runtime.platform.process.service_provider import get_provider

        cfg_val = get_provider().get("smart_routing_enabled")
        if cfg_val is False:
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def resolve_tier_model(tier: str) -> str | None:
    """Public alias for :func:`_resolve_tier_model`.

    Kept as a thin runtime delegate so tests that monkeypatch the
    private symbol keep working; external callers should use this name.
    """
    return _resolve_tier_model(tier)


def _resolve_tier_model(tier: str) -> str | None:
    """Resolve the model name configured for a tier, or None.

    Resolution order per tier:
      1. Tier-specific env: ``ECHO_MODEL_<TIER>``
      2. Tier-specific service_provider key: ``smart_routing.<tier>``
      3. Legacy fallback: ``ECHO_SMART_ROUTING_CHEAP_MODEL`` /
         ``ECHO_SUBAGENT_CHEAP_MODEL`` for ``value``
      4. **Auto-derive across custom_models.json entries** — the first
         configured model is the cheap local/value slot and the last
         configured model is the performance slot. Several one-model
         entries therefore form a useful cheap→strong ladder.
      5. Built-in default for ``value`` only (``glm-4-flash``); other
         tiers return None and the router escalates upward.

    Tier values can be either:
      * a **plain model name** (e.g. ``"gpt-4o-mini"``) — passed
        through verbatim, the dispatcher then routes through the
        matching custom-model entry if one exists, or to a built-in
        preset otherwise.
      * an **entry reference** — the name of an entry in
        ``custom_models.json`` (e.g. ``"openai-prod"``). The
        reference is resolved against the entry's ``models`` list:
          - ``value``       → ``entry.models[0]`` (cheap slot)
          - ``performance`` → ``entry.models[-1]`` (strongest slot)
        The choice lets a single custom-model entry cover both the
        value and performance tiers without operators duplicating
        api keys. Auto mode does *not* read this directly — it
        rewrites ``request.model`` to the concrete value returned
        here and the dispatcher's multi-model rewrite forwards.
    """
    upper = tier.upper()
    env_key = f"ECHO_MODEL_{upper}"
    val = os.environ.get(env_key)
    if val and val.strip():
        return _resolve_tier_value(val.strip(), tier)
    try:
        from runtime.platform.process.service_provider import get_provider

        cfg_val = get_provider().get(f"smart_routing.{tier}")
        if isinstance(cfg_val, str) and cfg_val.strip():
            return _resolve_tier_value(cfg_val.strip(), tier)
    except Exception:  # noqa: BLE001
        pass

    if tier == "value":
        # Legacy compatibility: keep older env vars working.
        for legacy_env in (
            "ECHO_SMART_ROUTING_CHEAP_MODEL",
            "ECHO_SUBAGENT_CHEAP_MODEL",
        ):
            legacy = os.environ.get(legacy_env)
            if legacy and legacy.strip():
                return legacy.strip()

    # Auto-derivation: use the ordered custom-model catalog when nothing
    # else is configured (local/value → first usable model, performance
    # → last usable model across all entries).
    # Lets a single imported API key power smart routing without
    # any env tuning.
    auto_model = _auto_derive_tier_from_custom_models(tier)
    if auto_model:
        return auto_model

    if tier == "value":
        # Conservative default — works for most accounts that have
        # a Z.ai / GLM key.
        return "glm-4-flash"

    # local / performance: no built-in default. Operators must opt in
    # by setting ECHO_MODEL_LOCAL (e.g. "ollama/qwen2.5:7b") or
    # ECHO_MODEL_PERFORMANCE. None → router escalates.
    return None


def _entry_tier_slot(entry: dict, tier: str) -> str | None:
    """Pick the model name an entry offers for one smart-routing tier.

    ``value``/``local`` take the cheap slot (``models[0]``); ``performance``
    takes the strongest slot (``models[-1]``). Mirrors ``_lookup_entry_reference``
    slot semantics so tagged auto-derive and explicit entry references agree.
    Returns ``None`` when the entry has no usable model id.
    """
    raw_models = entry.get("models")
    if isinstance(raw_models, list) and raw_models:
        upstreams = [str(m).strip() for m in raw_models if str(m or "").strip()]
        if not upstreams:
            return None
        return upstreams[-1] if tier == "performance" else upstreams[0]
    # Legacy single-model entry predating the ``models`` list refactor.
    primary = entry.get("model")
    if isinstance(primary, str) and primary.strip():
        if tier == "performance":
            perf = entry.get("model_performance")
            if isinstance(perf, str) and perf.strip():
                return perf.strip()
        return primary.strip()
    return None


def _auto_derive_tier_from_custom_models(tier: str) -> str | None:
    """Pick a tier model from the custom-model catalog.

    Preference is the operator's EXPLICIT ``tier`` tag — ``performance`` for
    the performance slot, ``economy`` (falling back to ``balanced``) for the
    local/value slot — so declared cost tiers win regardless of catalog
    order. When no entry carries a tag, falls back to the historical position
    heuristic (first usable model = cheap slot, last = strongest) so untagged
    legacy catalogs keep working.
    """
    try:
        from runtime.platform.process.paths import app_paths

        path = app_paths().custom_models_path
        if not path.exists():
            return None
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or not data:
        return None

    # 1) Tagged pass — the operator's declared cost tier is ground truth.
    def _collect_tagged(want: tuple[str, ...]) -> list[str]:
        hits: list[str] = []
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("tier") or "").strip().lower() not in want:
                continue
            slot = _entry_tier_slot(entry, tier)
            if slot:
                hits.append(slot)
        return sorted(hits)

    if tier == "performance":
        tagged = _collect_tagged(("performance",))
        if tagged:
            return tagged[0]
    else:
        # value/local prefer the declared ``economy`` tier; ``balanced``
        # only steps in when no economy entry exists, so a balanced model
        # never steals the cheap slot from a declared-economy one.
        economy = _collect_tagged(("economy",))
        if economy:
            return economy[0]
        balanced = _collect_tagged(("balanced",))
        if balanced:
            return balanced[0]

    # 2) Legacy fallback — position heuristic for untagged catalogs.
    candidates: list[str] = []
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        raw_models = entry.get("models")
        if isinstance(raw_models, list) and raw_models:
            upstreams = [str(m).strip() for m in raw_models if str(m or "").strip()]
            if not upstreams:
                continue
            candidates.extend(upstreams)
            continue
        # Legacy single-model entry.
        legacy = entry.get("model")
        if isinstance(legacy, str) and legacy.strip():
            if tier == "performance":
                performance = entry.get("model_performance")
                if isinstance(performance, str) and performance.strip():
                    candidates.append(performance.strip())
                    continue
            candidates.append(legacy.strip())
    if not candidates:
        return None
    return candidates[-1] if tier == "performance" else candidates[0]


def _resolve_code_model() -> str | None:
    """Resolve a code-specialized model without overriding operator pins.

    ``ECHO_MODEL_CODE`` / ``smart_routing.code`` are explicit. When
    neither is set, an explicitly configured performance tier still wins;
    otherwise the custom-model catalog is searched for a tool-capable entry
    whose id/name/display/model identifies it as a coding model.
    """
    explicit = os.environ.get("ECHO_MODEL_CODE")
    if explicit and explicit.strip():
        return _resolve_tier_value(explicit.strip(), "performance")
    try:
        from runtime.platform.process.service_provider import get_provider

        provider = get_provider()
        configured = provider.get("smart_routing.code")
        if isinstance(configured, str) and configured.strip():
            return _resolve_tier_value(configured.strip(), "performance")
        if provider.get("smart_routing.performance"):
            return None
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("ECHO_MODEL_PERFORMANCE"):
        return None

    try:
        from runtime.platform.process.paths import app_paths

        path = app_paths().custom_models_path
        if not path.exists():
            return None
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    for entry_id, entry in data.items():
        if not isinstance(entry, dict) or entry.get("supports_tool_use") is False:
            continue
        raw_models = entry.get("models")
        models = (
            [str(model).strip() for model in raw_models if str(model or "").strip()]
            if isinstance(raw_models, list)
            else []
        )
        haystack = " ".join(
            [
                str(entry_id),
                str(entry.get("name") or ""),
                str(entry.get("display_name") or ""),
                *models,
            ]
        ).lower()
        if re.search(r"(?:^|[\s_.:/-])(code|coder|coding)(?:$|[\s_.:/-])", haystack):
            return models[0] if models else None
    return None


def _resolve_tier_value(raw: str, tier: str) -> str | None:
    """Map a tier config string to a usable model name.

    * Strings that look like entry names *and* match an entry in
      ``custom_models.json`` resolve to one slot of that entry's
      ``models`` list:
        - ``value``       → ``entry.models[0]``   (cheap slot)
        - ``performance`` → ``entry.models[-1]``  (strongest slot)
      Single-item entries naturally return the same model for both
      tiers.
    * Strings that don't match any entry pass through as plain
      model names — a typo in the entry reference doesn't silently
      swallow the config; the dispatcher will 404 if the alias
      isn't a built-in preset either, surfacing the bad config
      instead of falling back to the next tier.
    * Strings that match an entry but whose chosen slot is empty
      return ``None`` so the router escalates to the next tier
      rather than shipping an empty model name downstream.

    The colon and whitespace checks preserve compatibility with
    the original "plain model name fast path" — only single-token
    strings are eligible for entry-reference expansion.
    """
    if ":" not in raw and " " not in raw:
        slot, missing = _lookup_entry_reference(raw, tier)
        if slot is not None:
            return slot
        if missing:
            # String isn't an entry id · treat as plain model name.
            return raw
        # Entry exists but the chosen slot is unusable · let the
        # router escalate to the next tier rather than ship a
        # malformed model name.
        return None
    # Colon or whitespace present · treat as a plain model name
    # (e.g. the legacy ``"<entry>:<role>"`` syntax degrades to a
    # model name rather than being silently swallowed).
    return raw


def _lookup_entry_reference(name: str, tier: str) -> tuple[str | None, bool]:
    """Resolve a bare entry name to one slot of its ``models`` list.

    Returns ``(slot_model | None, missing_entry)``:

      * ``slot_model`` is the chosen slot's model name, or ``None``
        if the entry doesn't carry a usable model id.
      * ``missing_entry`` is ``True`` when the name isn't in
        ``custom_models.json`` at all (caller should passthrough
        the raw string as a plain model name); ``False`` when the
        entry exists but the chosen slot is empty / missing (caller
        should return ``None`` so the router escalates).
    """
    try:
        from runtime.platform.process.paths import app_paths

        path = app_paths().custom_models_path
        if not path.exists():
            return None, True
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, True
    if not isinstance(data, dict):
        return None, True
    entry = data.get(name)
    if not isinstance(entry, dict):
        return None, True
    raw_models = entry.get("models")
    if isinstance(raw_models, list) and raw_models:
        upstreams = [str(m).strip() for m in raw_models if str(m or "").strip()]
        if not upstreams:
            return None, False
        if tier == "performance":
            return upstreams[-1], False
        # ``local`` / ``value`` both pick the cheap slot. ``local``
        # shouldn't normally hit this path (operators point local
        # at a plain model name), but if they do, default to the
        # first slot rather than the strongest so the cheap tier
        # stays cheap.
        return upstreams[0], False
    # Legacy fallback — entry predates the ``models`` list refactor.
    primary = entry.get("model")
    if isinstance(primary, str) and primary.strip():
        if tier == "performance":
            perf = entry.get("model_performance")
            if isinstance(perf, str) and perf.strip():
                return perf.strip(), False
        return primary.strip(), False
    return None, False


def get_tier_config() -> dict[str, str | None]:
    """Snapshot of the operator-configured tier → model map.

    Useful for the settings UI. Returns the 3 tier names with their
    currently-resolved model names (or None if unconfigured).
    """
    return {
        "local": _resolve_tier_model("local"),
        "value": _resolve_tier_model("value"),
        "performance": _resolve_tier_model("performance"),
    }


def estimate_turn_complexity(
    text: str,
    *,
    has_explicit_model: bool = False,
    has_topology: bool = False,
    is_code_mode: bool = False,
    is_swarm_mode: bool = False,
    is_research_mode: bool = False,
    is_goal_mode: bool = False,
    looks_tool_intent: bool = False,
    requires_todo_protocol: bool = False,
) -> ComplexityVerdict:
    """Return a three-tier verdict for the turn.

    Decision priority — first matching rule wins. Mode flags take
    precedence over text length so a 3-char code-mode message
    "改一下" doesn't get mis-classified as a chat ack.
    """
    # ── Hard "performance" cases — these always need the strongest
    # tier regardless of message length ────────────────────────
    if has_explicit_model:
        # User pinned — respect it; classifier becomes informational.
        return "performance"
    if has_topology or is_swarm_mode:
        return "performance"
    if is_research_mode or is_goal_mode:
        return "performance"
    if is_code_mode:
        return "performance"
    if requires_todo_protocol:
        return "performance"

    # ── Now classify by content ────────────────────────────────
    raw = (text or "").strip()
    if not raw:
        return "local"

    if _looks_like_chitchat(raw):
        return "local"

    if looks_tool_intent:
        # Single-shot tool usage; value tier is enough.
        return "value"

    # Short single-line messages → value. (A heuristic EchoRouter
    # gate once sat here to second-guess this window, but real-traffic
    # measurement showed it changed the verdict on <1% of messages and
    # that single change was a mis-promotion of a read-only task to the
    # expensive tier — net-negative, so it was removed 2026-06-04.)
    if len(raw) < 200 and "\n" not in raw:
        return "value"

    # Long unclassified message — be conservative, go performance.
    return "performance"


def select_model_for_complexity(
    verdict: ComplexityVerdict,
    *,
    user_model: str | None,
    is_code_mode: bool = False,
) -> tuple[str | None, str]:
    """Map a verdict to the actual model name to call.

    Returns ``(model_name | None, reason)``:
      * ``None`` means "leave the request's model field alone" — used
        when the user pinned a model, when smart routing is off, or
        when we can't beat what the runtime would pick anyway.
      * Non-None means "rewrite ``request.model`` to this value".

    Escalation: if a tier has no configured model, fall up
    (local → value → performance) so we never silently demote.
    """
    # Explicit user choice always wins.
    if user_model and user_model.strip() and user_model not in {"echo-agent", "auto"}:
        return None, "user_pinned"

    if not is_smart_routing_enabled():
        return None, "smart_routing_disabled"

    if is_code_mode:
        code_model = _resolve_code_model()
        if code_model:
            return code_model, "smart_routing:code->specialist"

    tier_chain = _escalation_chain(verdict)
    for tier in tier_chain:
        m = _resolve_tier_model(tier)
        if m:
            reason = f"smart_routing:{verdict}->{tier}"
            if tier != verdict:
                reason += "(escalated)"
            return m, reason

    return None, f"smart_routing:{verdict}->no_tier_configured"


def _escalation_chain(verdict: ComplexityVerdict) -> list[str]:
    """Return tier preference list. Always escalates upward."""
    if verdict == "local":
        return ["local", "value", "performance"]
    if verdict == "value":
        return ["value", "performance"]
    return ["performance"]


__all__ = [
    "ComplexityVerdict",
    "estimate_turn_complexity",
    "get_tier_config",
    "is_smart_routing_enabled",
    "select_model_for_complexity",
    "_resolve_code_model",
    "resolve_tier_model",
    "_resolve_tier_model",
]
