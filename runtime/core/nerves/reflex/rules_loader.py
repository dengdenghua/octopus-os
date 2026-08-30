from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from runtime.core.nerves.reflex.actions import ActionSpec
from runtime.core.nerves.reflex.gating import GatingSpec
from runtime.core.nerves.reflex.reflex_router import (
    CacheMatcher,
    DeterministicMatcher,
    Reflex,
    RegexMatcher,
)

_LOG = logging.getLogger("echo.reflex.loader")

_RULE_FILENAMES = ("reflex_rules.yaml", "reflex_rules.yml", "reflex_rules.json")


def _default_paths() -> list[Path]:
    """Candidate rule files, resolved rather than assumed.

    This used to be a module-level list of bare ``Path("data")/...`` values,
    documented as safe because "the runtime cwd is the project root". That
    assumption does not hold: a server can be started from anywhere, and its
    working directory can even be deleted underneath it. When it broke, the
    rules silently did not load — no error, just no reflexes.

    ``app_paths().data_dir`` is the same contract the other data-dir readers
    use, and it honours ``ECHO_DATA_DIR``. The cwd-relative paths are kept
    as a trailing fallback so a project-local ``data/`` still works.
    """
    paths: list[Path] = []
    try:
        from runtime.platform.process.paths import app_paths

        root = app_paths().data_dir
        paths.extend(root / name for name in _RULE_FILENAMES)
    except Exception:  # noqa: BLE001 — never let path resolution break loading
        pass
    paths.extend(Path("data") / name for name in _RULE_FILENAMES)
    return paths


def _resolve_case_sensitive(raw: Any) -> bool:
    """Map the optional ``flags: [...]`` list to a case-insensitive
    flag · the underlying ``RegexMatcher`` only exposes a binary
    ``case_insensitive`` toggle so we collapse the flag list down.
    Default is case-insensitive (matches the matcher default).
    """
    if not isinstance(raw, list):
        return True
    # If the user explicitly lists CASE_SENSITIVE we honour it; any
    # other listed flag (MULTILINE, DOTALL, etc.) isn't reachable
    # through the current matcher API · log so we don't silently
    # ignore on a future schema bump.
    for name in raw:
        if not isinstance(name, str):
            continue
        n = name.strip().upper()
        if n in ("CASE_SENSITIVE", "NOCASE_OFF"):
            return False
        if n != "IGNORECASE":
            _LOG.warning(
                "reflex rule: regex flag %r isn't supported by the current matcher · ignored",
                name,
            )
    return True


def _build_response(entry: dict[str, Any]) -> Any:
    """Pick the response payload from an entry · supports the friendly
    ``reply: "..."`` shortcut and the explicit ``response: {...}`` form."""
    if "response" in entry:
        return entry["response"]
    if "reply" in entry:
        return {"reply": str(entry["reply"])}
    if "version" in entry:
        return {"version": str(entry["version"])}
    return {"reply": "(no response configured)"}


def _build_matcher(entry: dict[str, Any]) -> Reflex | None:
    """Build one matcher from a parsed config entry · returns None on
    invalid input so the loader can continue with the rest of the file."""
    if not isinstance(entry, dict):
        _LOG.warning("reflex rule: not a dict, skipped · %r", entry)
        return None
    rid = str(entry.get("id") or "").strip()
    rtype = str(entry.get("type") or "regex").strip().lower()
    priority = int(entry.get("priority") or 10)
    if not rid:
        _LOG.warning("reflex rule missing 'id', skipped · %r", entry)
        return None

    if rtype == "regex":
        pattern = entry.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            _LOG.warning("reflex %s: regex needs 'pattern', skipped", rid)
            return None
        return RegexMatcher(
            rule_id=rid,
            pattern=pattern,
            response=_build_response(entry),
            case_insensitive=_resolve_case_sensitive(entry.get("flags")),
            priority=priority,
        )
    if rtype == "deterministic":
        itype = entry.get("intent_type")
        if not isinstance(itype, str) or not itype:
            _LOG.warning(
                "reflex %s: deterministic needs 'intent_type', skipped",
                rid,
            )
            return None
        return DeterministicMatcher(
            rule_id=rid,
            intent_type=itype,
            response=_build_response(entry),
            priority=priority,
        )
    if rtype == "cache":
        return CacheMatcher(
            rule_id=rid,
            ttl_seconds=int(entry.get("ttl_seconds") or 3600),
            priority=priority,
        )
    _LOG.warning("reflex %s: unknown type %r, skipped", rid, rtype)
    return None


def _attach_action(matcher: Reflex, entry: dict[str, Any]) -> None:
    """Parse the optional ``action:`` block off a YAML entry and stash
    it on the matcher so the runtime can find it after a hit. Stored
    under a non-public attribute name so future Reflex schema changes
    don't collide. Falls through silently when the entry has no action.

    Also stashes:
      * ``reply_on_action_failure`` — optional fallback reply when at
        least one action fails (webhook 500'd, MQTT broker down).
      * ``delegate_to_workflow`` — optional workflow id/name. When set,
        the reflex layer hands the user's input to that workflow and
        uses its output as the reply (instead of the canned ``reply``).
        Action remains orthogonal · runs alongside the workflow.
    """
    spec = ActionSpec.from_entry(entry.get("action"))
    if spec is not None:
        matcher._action_spec = spec  # type: ignore[attr-defined]
    fallback = entry.get("reply_on_action_failure")
    if isinstance(fallback, str) and fallback.strip():
        matcher._reply_on_action_failure = fallback  # type: ignore[attr-defined]
    delegate = entry.get("delegate_to_workflow")
    if isinstance(delegate, str) and delegate.strip():
        matcher._delegate_to_workflow = delegate.strip()  # type: ignore[attr-defined]


def _attach_gating(matcher: Reflex, entry: dict[str, Any]) -> None:
    """Parse the optional ``enabled_when:`` block · enables gray
    release / time-of-day / rollout-percentage gating. No-op when
    the block is missing OR fully empty.
    """
    spec = GatingSpec.from_entry(entry.get("enabled_when"))
    if spec is not None:
        matcher._gating_spec = spec  # type: ignore[attr-defined]


def _attach_variants(matcher: Reflex, entry: dict[str, Any]) -> None:
    raw = entry.get("variants")
    if not isinstance(raw, list) or not raw:
        return
    parsed: list[tuple[int, Any, str]] = []
    for i, v in enumerate(raw):
        if not isinstance(v, dict):
            continue
        weight = max(1, int(v.get("weight") or 1))
        # Reuse _build_response so variant entries support the same
        # ``reply`` / ``response`` / ``version`` shortcuts.
        resp = _build_response(v)
        vid = str(v.get("id") or f"v{i}")
        parsed.append((weight, resp, vid))
    if parsed:
        matcher._variants = parsed  # type: ignore[attr-defined]
        # Per-actor pinning · {actor_id: variant_id}. Filter to
        # known variant_ids so an editor typo doesn't crash the
        # selector (we'll just fall back to weighted random).
        valid_ids = {vid for (_w, _r, vid) in parsed}
        per_actor_raw = entry.get("per_actor")
        if isinstance(per_actor_raw, dict):
            pinned: dict[str, str] = {}
            for actor, vid in per_actor_raw.items():
                if isinstance(actor, str) and isinstance(vid, str) and vid in valid_ids:
                    pinned[actor.strip()] = vid.strip()
            if pinned:
                matcher._per_actor_variant = pinned  # type: ignore[attr-defined]


def find_default_rules_file() -> Path | None:
    """Return the first existing default rules path, or None."""
    for p in _default_paths():
        if p.is_file():
            return p
    return None


def load_rules_from_file(path: str | Path) -> list[Reflex]:
    """Parse a YAML or JSON rules file. Returns an empty list when the
    file is missing or malformed · callers should always be ready for
    [] (e.g. by also seeding their built-in defaults).

    File format detection is by extension · ``.yaml`` / ``.yml`` →
    YAML (requires ``pyyaml``), ``.json`` → stdlib JSON.
    """
    path = Path(path)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("reflex rules: cannot read %s · %s", path, exc)
        return []

    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # type: ignore[import]

            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("reflex rules: parse failed for %s · %s", path, exc)
        return []

    raw_rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(raw_rules, list):
        _LOG.warning(
            "reflex rules: expected 'rules' list at top-level of %s",
            path,
        )
        return []

    out: list[Reflex] = []
    for entry in raw_rules:
        m = _build_matcher(entry)
        if m is not None:
            # All three attachers are no-ops when the entry has no
            # action / variants / enabled_when block · keeps the
            # loader path branch-free.
            _attach_action(m, entry)
            _attach_variants(m, entry)
            _attach_gating(m, entry)
            out.append(m)
    _LOG.info("reflex rules: loaded %d rule(s) from %s", len(out), path)

    # Pick up an optional top-level ``broadcast:`` block · the
    # broadcaster is a process singleton (``broadcast.py``) so
    # both realtime and /v1/chat callers see the same outbound
    # MQTT config without explicit wiring.
    try:
        from runtime.core.nerves.reflex.broadcast import (
            ReflexBroadcaster,
            set_default_broadcaster,
        )

        set_default_broadcaster(ReflexBroadcaster.from_yaml_top_level(data))
    except Exception:  # noqa: BLE001
        pass

    # Pick up optional ``slm:`` and ``fuzzy_cache:`` top-level
    # blocks · these configure the response tiers that sit
    # BETWEEN reflex and the planner. Both are singletons in
    # ``tiers.py`` so a yaml reload re-tunes them without restart.
    try:
        from runtime.core.nerves.reflex.tiers import (
            configure_slm,
            get_default_fuzzy_cache,
        )

        if isinstance(data, dict):
            raw_slm_cfg = data.get("slm")
            slm_cfg: dict[Any, Any] = raw_slm_cfg if isinstance(raw_slm_cfg, dict) else {}
            configure_slm(
                endpoint=slm_cfg.get("endpoint"),
                model=slm_cfg.get("model"),
                timeout_ms=slm_cfg.get("timeout_ms"),
            )
            raw_fc_cfg = data.get("fuzzy_cache")
            fc_cfg: dict[Any, Any] = raw_fc_cfg if isinstance(raw_fc_cfg, dict) else {}
            if fc_cfg:
                fc = get_default_fuzzy_cache()
                if "similarity" in fc_cfg:
                    fc.similarity = float(fc_cfg["similarity"])
                if "ttl_hours" in fc_cfg:
                    fc.ttl_seconds = float(fc_cfg["ttl_hours"]) * 3600
                if "max_entries" in fc_cfg:
                    fc.max_entries = int(fc_cfg["max_entries"])
                if "enabled" in fc_cfg:
                    fc.enabled = bool(fc_cfg["enabled"])
    except Exception:  # noqa: BLE001
        pass
    return out


def merge_with_defaults(
    defaults: list[Reflex],
    overrides: list[Reflex],
) -> list[Reflex]:
    """Combine built-in defaults with file-loaded overrides.

    Rule of precedence: when a file rule shares an ``id`` with a
    default, the file rule wins (allows local deployments to retune
    the ping/version reply without source edits). Otherwise the
    file rules are appended after the defaults · ReflexRouter
    will still sort by priority before matching so order doesn't
    affect correctness.
    """
    by_id: dict[str, Reflex] = {m.rule_id: m for m in defaults}
    for m in overrides:
        by_id[m.rule_id] = m  # override or add
    return list(by_id.values())


__all__ = [
    "find_default_rules_file",
    "load_rules_from_file",
    "merge_with_defaults",
]
