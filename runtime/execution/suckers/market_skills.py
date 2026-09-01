from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .registry import Skill, SkillRegistry

_log = logging.getLogger(__name__)

_MAX_INSTRUCTIONS_BYTES = 32 * 1024  # Implementation note.
_MAX_RESOURCES_PER_SKILL = 50  # Implementation note.

# A packaged catalog makes registry refresh an update concern, not a startup
# dependency. Operators may tune the small best-effort window, but it remains
# hard-capped so a bad value cannot restore the historical multi-minute boot.
_PROMPT_REFRESH_DEADLINE_ENV = "ECHO_PROMPT_SKILL_REFRESH_DEADLINE_S"
_PROMPT_REFRESH_DEFAULT_DEADLINE_S = 0.5
_PROMPT_REFRESH_MAX_DEADLINE_S = 5.0
_PROMPT_REFRESH_REQUEST_TIMEOUT_S = 0.4
_PROMPT_REFRESH_MAX_WORKERS = 8
_IMMUTABLE_PROMPT_CATALOG_MODES = frozenset(
    {
        "commercial",
        "production",
        "shared",
        "server",
    }
)

_KNOWN_META_KEYS = frozenset(
    {
        "name",
        "description",
        "enabled",
        "always_apply",
        "allowed-tools",
        "allowed_tools",
        "version",
        "license",
        "tags",
        "affinity",
    }
)

_FRONTMATTER = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)


#


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
        return s[1:-1]
    return s


def _coerce(value: str) -> Any:
    v = value.strip()
    if not v:
        return ""
    lower = v.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower == "null":
        return None
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(p) for p in inner.split(",")]
    return _strip_quotes(v)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text.strip()
    head = m.group(1)
    body = text[m.end() :].strip()
    meta: dict[str, Any] = {}

    #     key: value
    #     key: >
    #       multi-line block (space-indented)
    #     key:
    #       - list
    #       - item
    lines = head.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in raw:
            i += 1
            continue
        key, _, rest = raw.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest == ">" or rest == "|":
            block_lines: list[str] = []
            j = i + 1
            while j < len(lines) and (
                lines[j].startswith(" ") or lines[j].startswith("\t") or not lines[j].strip()
            ):
                if lines[j].strip():
                    block_lines.append(lines[j].strip())
                j += 1
            meta[key] = " ".join(block_lines) if rest == ">" else "\n".join(block_lines)
            i = j
            continue
        if not rest:
            items: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("-"):
                items.append(_strip_quotes(lines[j].lstrip()[1:]))
                j += 1
            if items:
                meta[key] = items
                i = j
                continue
            i += 1
            continue
        meta[key] = _coerce(rest)
        i += 1
    return meta, body


def _make_prompt_handler(
    skill_dir: Path,
    name: str,
    instructions: str,
    allowed_tools: tuple[str, ...],
):
    def _handler(**_kw: Any) -> dict[str, Any]:
        # ``deep-research-swarm`` is the SWARM-mode skill: it dispatches
        # into the multi-agent ``research_swarm_v1`` topology via
        # ``TeamRunner``. ``deep-research`` is the SINGLE-AGENT skill:
        # it returns the 7-phase instruction document so the parent
        # ReAct loop can drive its own atomic web_search / fetch_url
        # calls. They used to share the swarm dispatch path, but that
        # turned the Agent-mode ``deep-research`` into a swarm too —
        # which broke for upstream models without native tool_use
        # (mimo, smaller community models). Keep them separate now.
        if name == "deep-research-swarm":
            verdict = _try_real_research_swarm(_kw)
            if verdict is not None:
                return verdict

        resources: list[dict[str, Any]] = []
        scripts: list[dict[str, Any]] = []
        try:
            for p in sorted(skill_dir.rglob("*")):
                if len(resources) + len(scripts) >= _MAX_RESOURCES_PER_SKILL:
                    break
                if not p.is_file():
                    continue
                if p.name == "SKILL.md":
                    continue
                rel = p.relative_to(skill_dir)
                entry = {
                    "path": str(rel).replace("\\", "/"),
                    "abs_path": str(p.resolve()),
                    "size": p.stat().st_size,
                }
                if p.suffix == ".py" and "scripts" in rel.parts:
                    scripts.append(entry)
                else:
                    resources.append(entry)
        except OSError:  # noqa: BLE001 — directory scan best-effort
            pass
        return {
            "skill": name,
            "instructions": instructions,
            "resources": resources,
            "scripts": scripts,
            "cwd": str(skill_dir.resolve()),
            "allowed_tools": list(allowed_tools),
        }

    _handler.__name__ = f"_prompt_skill_{name}"
    return _handler


def _try_real_research_swarm(kw: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch deep-research to the ``research_swarm_v1`` topology.

    Returns a structured result on success, or ``None`` if the
    underlying multi-agent infrastructure isn't available — caller
    falls back to the legacy "instructions-only" handler so the skill
    never breaks.

    Why this exists: ``deep-research`` historically returned a 7-phase
    instruction document. The model would interpret it as "go
    orchestrate 10+ sub-agents" but actually had no mechanism to do
    so — it'd try in-loop, run out of iterations, and quietly give the
    user a hand-written summary. This wires the skill to the real
    ``TeamRunner`` so a single skill call actually runs the swarm.
    """
    topic = kw.get("topic") or kw.get("query") or kw.get("question") or kw.get("user_request") or ""
    if not isinstance(topic, str) or not topic.strip():
        return None
    try:
        from runtime.safety.organization.forge import load_registry
        from runtime.safety.organization.team_runner import TeamRunner
    except ImportError:
        return None

    try:
        registry = load_registry()
    except Exception:  # noqa: BLE001 — registry load is best-effort
        return None
    topology = None
    for t in registry.values():
        if t.name == "research_swarm_v1":
            topology = t
            break
    if topology is None:
        return None

    try:
        runner = TeamRunner(timeout_seconds=1800)
        result = runner.run(
            topology,
            topic,
            context={"source": "deep-research-skill"},
        )
    except Exception as exc:  # noqa: BLE001 — fall back to legacy on any error
        return {
            "skill": "deep-research-swarm",
            "ok": False,
            "error": f"swarm dispatch failed: {type(exc).__name__}: {exc}",
            "fallback_hint": (
                "TeamRunner failed; the model can fall back to running "
                "research manually with web_search + fetch_url calls."
            ),
        }

    final_text = ""
    role_outputs: list[dict[str, Any]] = []
    try:
        for ro in result.role_outputs or []:
            role_outputs.append(
                {
                    "role": str(getattr(ro, "role", "")),
                    "agent_id": getattr(ro, "agent_id", ""),
                    "output": getattr(ro, "output", ""),
                    "error": getattr(ro, "error", None),
                    "duration_ms": getattr(ro, "duration_ms", 0),
                }
            )
        final_text = (result.final_output or "").strip()
    except AttributeError:  # noqa: BLE001 — result shape may vary across protocol versions
        # Result shape can vary across protocol versions; fall through.
        pass

    if not final_text:
        # No synthesizer output → don't pretend it succeeded; return
        # None so caller can fall back. Better to ship raw instructions
        # than an empty report.
        return None

    return {
        "skill": "deep-research-swarm",
        "ok": True,
        "topic": topic,
        "report": final_text,
        "roles": role_outputs,
        "instructions_mode": False,
    }


def _sanitize_skill_name(raw: str, fallback: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", raw.strip())
    return cleaned or fallback


def _load_enabled_set(all_skills_dir: Path) -> dict[str, bool] | None:
    cfg = all_skills_dir / "skills_config.json"
    if not cfg.is_file():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    out: dict[str, bool] = {}
    for key, val in data.items():
        name = re.sub(r"^\d+_", "", str(key))
        if isinstance(val, dict) and "enabled" in val:
            out[name] = bool(val.get("enabled"))
    return out


def register_market_skills(
    registry: SkillRegistry,
    *,
    all_skills_dir: Path | None = None,
    respect_enabled_flag: bool = True,
    verify_tests: bool = True,
    skip_registered_directories: bool = False,
) -> int:
    if all_skills_dir is None:
        # runtime/execution/suckers/market_skills.py → runtime/execution/all_skills
        all_skills_dir = Path(__file__).resolve().parent.parent / "all_skills"
    all_skills_dir = Path(all_skills_dir)
    if immutable_prompt_catalog_required():
        from runtime.platform.process.paths import bundled_market_skills_dir

        if all_skills_dir.resolve(strict=False) != bundled_market_skills_dir().resolve(
            strict=False
        ):
            _log.warning(
                "refusing mutable prompt-skill catalog %s in %s mode",
                all_skills_dir,
                os.environ.get("ECHO_DEPLOYMENT_MODE", "").strip().lower(),
            )
            return 0
    if not all_skills_dir.is_dir():
        return 0

    enabled_map = _load_enabled_set(all_skills_dir) if respect_enabled_flag else None
    registered = 0
    seen_names: set[str] = set()
    registered_names = set(registry.all_names()) if skip_registered_directories else set()

    for skill_md in sorted(all_skills_dir.glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        dir_name = skill_dir.name
        # Bundled prompt skills are required to use their directory slug as
        # the canonical name.  register_all() deliberately makes one final
        # pass with the enabled flag ignored so disabled bundled fallbacks can
        # fill gaps.  Almost every entry is already present by then, so avoid
        # reopening and reparsing the same SKILL.md files in that pass.
        if dir_name in registered_names:
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _log.warning("market_skills: read %s failed: %s", skill_md, exc)
            continue

        meta, body = _parse_frontmatter(text)
        name = _sanitize_skill_name(str(meta.get("name") or dir_name), dir_name)
        if name in seen_names:
            _log.warning("market_skills: duplicate name %r in %s · skip", name, skill_dir)
            continue
        if registry.has(name):
            seen_names.add(name)
            continue
        description = str(meta.get("description") or "").strip()
        if not description:
            description = f"Prompt skill from {dir_name}."

        # Optional `aliases:` field lets one physical SKILL.md serve
        # multiple trigger names (e.g. EN/CN aliases or progressive
        # rename without a hard cutover). Each alias registers the
        # same handler under a different name so prompts keying on
        # either name still resolve. Aliases that collide with an
        # already-registered skill are silently skipped.
        aliases_raw = meta.get("aliases") or ()
        if isinstance(aliases_raw, str):
            aliases_tuple: tuple[str, ...] = tuple(
                a.strip() for a in aliases_raw.split(",") if a.strip()
            )
        elif isinstance(aliases_raw, (list, tuple)):
            aliases_tuple = tuple(str(a).strip() for a in aliases_raw if str(a).strip())
        else:
            aliases_tuple = ()
        aliases_tuple = tuple(
            _sanitize_skill_name(a, dir_name) for a in aliases_tuple if a and a != name
        )

        if respect_enabled_flag:
            fm_enabled = meta.get("enabled")
            cfg_enabled = (enabled_map or {}).get(dir_name)
            if cfg_enabled is True:
                active = True
            elif fm_enabled is False or cfg_enabled is False and fm_enabled is not True:
                active = False
            else:
                active = True
            if not active:
                continue

        if len(body.encode("utf-8")) > _MAX_INSTRUCTIONS_BYTES:
            body = (
                body.encode("utf-8")[:_MAX_INSTRUCTIONS_BYTES].decode(
                    "utf-8",
                    errors="ignore",
                )
                + "\n\n[... truncated ...]"
            )

        allowed = meta.get("allowed-tools") or meta.get("allowed_tools") or ()
        if isinstance(allowed, str):
            allowed_tuple: tuple[str, ...] = tuple(
                t.strip() for t in allowed.split(",") if t.strip()
            )
        elif isinstance(allowed, (list, tuple)):
            allowed_tuple = tuple(str(t).strip() for t in allowed if str(t).strip())
        else:
            allowed_tuple = ()

        affinity_raw = meta.get("tags") or meta.get("affinity") or []
        if isinstance(affinity_raw, str):
            affinity = [a.strip() for a in affinity_raw.split(",") if a.strip()]
        elif isinstance(affinity_raw, (list, tuple)):
            affinity = [str(a).strip() for a in affinity_raw if str(a).strip()]
        else:
            affinity = []
        if "market" not in affinity:
            affinity.append("market")

        try:
            registry.register(
                Skill(
                    name=name,
                    description=description,
                    affinity=affinity,
                    cost_profile="low",
                    trusted_source=f"skill://all_skills/{dir_name}",
                    handler=_make_prompt_handler(
                        skill_dir,
                        name,
                        body,
                        allowed_tuple,
                    ),
                ),
                verify_tests=verify_tests,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "market_skills: register %r failed: %s",
                name,
                exc,
            )
            continue
        seen_names.add(name)
        registered += 1

        for alias in aliases_tuple:
            if alias in seen_names or registry.has(alias):
                continue
            try:
                registry.register(
                    Skill(
                        name=alias,
                        description=description,
                        affinity=affinity,
                        cost_profile="low",
                        trusted_source=f"skill://all_skills/{dir_name}#alias",
                        handler=_make_prompt_handler(
                            skill_dir,
                            alias,
                            body,
                            allowed_tuple,
                        ),
                    ),
                    verify_tests=verify_tests,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "market_skills: alias %r of %r failed: %s",
                    alias,
                    name,
                    exc,
                )
                continue
            seen_names.add(alias)
            registered += 1

    _log.debug(
        "market_skills: registered %d prompt skills from %s",
        registered,
        all_skills_dir,
    )
    return registered


def _has_market_skill_files(path: Path) -> bool:
    """Whether ``path`` contains a catalog the market loader can consume."""

    return path.is_dir() and any(path.glob("*/SKILL.md"))


def immutable_prompt_catalog_required() -> bool:
    """Whether startup must use only the catalog shipped in this build.

    Registry lockfiles currently identify skills by slug rather than by a
    publisher signature and locally pinned content digest.  A shared or
    production process therefore cannot safely let a remote registry (or a
    previously materialized mutable cache) replace executable prompt text.
    """

    deployment_mode = os.environ.get("ECHO_DEPLOYMENT_MODE", "").strip().lower()
    return deployment_mode in _IMMUTABLE_PROMPT_CATALOG_MODES


def _prompt_refresh_deadline(explicit: float | None) -> float:
    raw: Any = explicit
    if raw is None:
        raw = os.environ.get(_PROMPT_REFRESH_DEADLINE_ENV)
    if raw is None or raw == "":
        return _PROMPT_REFRESH_DEFAULT_DEADLINE_S
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        _log.warning(
            "invalid %s=%r; using %.1fs",
            _PROMPT_REFRESH_DEADLINE_ENV,
            raw,
            _PROMPT_REFRESH_DEFAULT_DEADLINE_S,
        )
        return _PROMPT_REFRESH_DEFAULT_DEADLINE_S
    return max(0.0, min(configured, _PROMPT_REFRESH_MAX_DEADLINE_S))


def _refresh_prompt_catalog_with_deadline(
    lockfile: Path,
    external_skills_dir: Path,
    *,
    deadline_s: float,
) -> None:
    """Best-effort refresh missing external skills within a startup deadline.

    ``bootstrap_skills(force=False)`` only materializes missing directories.
    Existing external handlers and the bundled handlers registered by the
    current process therefore remain stable; newly downloaded skills become
    visible on the next registry construction/startup.
    """

    if deadline_s <= 0:
        _log.info(
            "prompt-skill refresh disabled by %s=0; current process uses its local catalog",
            _PROMPT_REFRESH_DEADLINE_ENV,
        )
        return
    request_timeout_s = min(_PROMPT_REFRESH_REQUEST_TIMEOUT_S, deadline_s)
    _log.info(
        "prompt-skill bounded refresh started for %s "
        "(total deadline %.3fs, request timeout %.3fs, workers %d)",
        lockfile,
        deadline_s,
        request_timeout_s,
        _PROMPT_REFRESH_MAX_WORKERS,
    )
    try:
        from echo_runtime import bootstrap_skills

        synced, present, errors = bootstrap_skills(
            lockfile,
            external_skills_dir,
            request_timeout_s=request_timeout_s,
            max_workers=_PROMPT_REFRESH_MAX_WORKERS,
            total_timeout_s=deadline_s,
        )
    except Exception:  # noqa: BLE001 - the local catalog is already serving startup
        _log.exception(
            "prompt-skill bounded refresh failed for %s; "
            "current process continues with its local catalog",
            lockfile,
        )
        return

    if errors:
        sample = "; ".join(f"{slug}: {reason}" for slug, reason in errors[:3])
        _log.warning(
            "prompt-skill bounded refresh incomplete for %s: %d failure(s) "
            "(sample: %s); current process continues with its local catalog and "
            "successful downloads apply after the next restart",
            lockfile,
            len(errors),
            sample,
        )
        return
    _log.info(
        "prompt-skill bounded refresh complete for %s: %d synced, %d already present; "
        "new skills apply after the next restart",
        lockfile,
        len(synced),
        len(present),
    )


def _bootstrap_prompt_catalog_synchronously(
    lockfile: Path,
    external_skills_dir: Path,
) -> bool:
    """Retain the historical blocking bootstrap when no bundle can serve."""

    try:
        from echo_runtime import bootstrap_skills

        synced, present, errors = bootstrap_skills(lockfile, external_skills_dir)
    except Exception:  # noqa: BLE001 - caller will validate external availability
        _log.exception("prompt-skill bootstrap failed for %s", lockfile)
        return False
    if errors:
        sample = "; ".join(f"{slug}: {reason}" for slug, reason in errors[:3])
        _log.warning(
            "prompt-skill bootstrap incomplete for %s: %d failure(s) (sample: %s)",
            lockfile,
            len(errors),
            sample,
        )
        return False
    if synced:
        _log.info(
            "prompt-skill bootstrap synced %d skill(s); %d already present",
            len(synced),
            len(present),
        )
    return True


def register_prompt_market_skills(
    registry: SkillRegistry,
    *,
    resource_dir: Path | None = None,
    bundled_dir: Path | None = None,
    refresh_deadline_s: float | None = None,
) -> int:
    """Bootstrap and register the external prompt catalog with a bundled fallback.

    A clean checkout intentionally contains only ``skills.lock.json`` plus
    ``skills/public`` metadata. Registry sync may be unavailable at cold
    start, and the mere existence of that metadata directory must not shadow
    the deterministic catalog shipped in the wheel. When that packaged catalog
    exists, ``refresh_deadline_s`` (or
    ``ECHO_PROMPT_SKILL_REFRESH_DEADLINE_S``) bounds the best-effort update;
    the value is hard-capped and ``0`` disables startup refresh entirely.
    """

    from runtime.platform.process.paths import (
        bundled_market_skills_dir,
        resources_root,
    )

    resources = Path(resource_dir) if resource_dir is not None else resources_root()
    external_skills_dir = resources / "skills" / "public"
    packaged_skills_dir = (
        Path(bundled_dir) if bundled_dir is not None else bundled_market_skills_dir()
    )
    skills_lockfile = resources / "skills.lock.json"
    bundled_available = _has_market_skill_files(packaged_skills_dir)

    if immutable_prompt_catalog_required():
        if not bundled_available:
            raise RuntimeError(
                "production prompt-skill catalog is unavailable: "
                f"bundled={packaged_skills_dir}. Remote and mutable external "
                "catalogs are disabled in shared/commercial deployment modes."
            )
        if _has_market_skill_files(external_skills_dir):
            _log.warning(
                "ignoring mutable external prompt-skill catalog %s in %s mode; "
                "using only the build-bound catalog %s",
                external_skills_dir,
                os.environ.get("ECHO_DEPLOYMENT_MODE", "").strip().lower(),
                packaged_skills_dir,
            )
        bundled_count = register_market_skills(
            registry,
            all_skills_dir=packaged_skills_dir,
        )
        if bundled_count:
            _log.info(
                "using immutable build-bound prompt-skill catalog %s (%d registered)",
                packaged_skills_dir,
                bundled_count,
            )
            return bundled_count
        raise RuntimeError(
            "production prompt-skill catalog registered zero usable skills: "
            f"bundled={packaged_skills_dir}. The installation is incomplete."
        )

    # With a packaged catalog, resolve the complete *local* view before any
    # network I/O. Existing external entries retain precedence over bundled
    # fallbacks; the latter only fill missing names. The bounded force=False
    # refresh then writes missing directories for the next construction and
    # never changes this live registry's capability set.
    if not bundled_available and skills_lockfile.is_file():
        _bootstrap_prompt_catalog_synchronously(skills_lockfile, external_skills_dir)

    external_count = 0
    if _has_market_skill_files(external_skills_dir):
        external_count = register_market_skills(
            registry,
            all_skills_dir=external_skills_dir,
        )
        if not external_count:
            _log.warning(
                "external prompt-skill catalog %s contains SKILL.md files but registered"
                " zero usable skills; checking bundled fallback",
                external_skills_dir,
            )

    bundled_count = 0
    if bundled_available:
        bundled_count = register_market_skills(
            registry,
            all_skills_dir=packaged_skills_dir,
        )
        if bundled_count:
            _log.info(
                "using bundled prompt-skill fallback %s (%d registered; %d external)",
                packaged_skills_dir,
                bundled_count,
                external_count,
            )

    registered = external_count + bundled_count
    if registered:
        if bundled_available and skills_lockfile.is_file():
            _refresh_prompt_catalog_with_deadline(
                skills_lockfile,
                external_skills_dir,
                deadline_s=_prompt_refresh_deadline(refresh_deadline_s),
            )
        return registered

    raise RuntimeError(
        "no usable prompt/market skills were registered: "
        f"external={external_skills_dir}, bundled={packaged_skills_dir}, "
        f"lockfile={skills_lockfile}. The installation is incomplete."
    )


def load_single_market_skill(
    registry: SkillRegistry,
    skill_id: str,
    *,
    all_skills_dir: Path | None = None,
    ignore_frontmatter_enabled: bool = True,
    verify_tests: bool = True,
) -> bool:
    if all_skills_dir is None:
        all_skills_dir = Path(__file__).resolve().parent.parent / "all_skills"
    all_skills_dir = Path(all_skills_dir)
    if immutable_prompt_catalog_required():
        from runtime.platform.process.paths import bundled_market_skills_dir

        if all_skills_dir.resolve(strict=False) != bundled_market_skills_dir().resolve(
            strict=False
        ):
            _log.warning(
                "refusing mutable prompt skill %r from %s in %s mode",
                skill_id,
                all_skills_dir,
                os.environ.get("ECHO_DEPLOYMENT_MODE", "").strip().lower(),
            )
            return False
    if not all_skills_dir.is_dir():
        return False

    if registry.has(skill_id):
        return True

    skill_dir = all_skills_dir / skill_id
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return False

    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    meta, body = _parse_frontmatter(text)
    name = _sanitize_skill_name(str(meta.get("name") or skill_id), skill_id)
    if name != skill_id:
        _log.warning(
            "load_single_market_skill: name mismatch dir=%s frontmatter=%s · using %s",
            skill_id,
            meta.get("name"),
            name,
        )
    description = str(meta.get("description") or "").strip() or f"Prompt skill from {skill_id}."

    if not ignore_frontmatter_enabled and meta.get("enabled") is False:
        return False

    if len(body.encode("utf-8")) > _MAX_INSTRUCTIONS_BYTES:
        body = (
            body.encode("utf-8")[:_MAX_INSTRUCTIONS_BYTES].decode(
                "utf-8",
                errors="ignore",
            )
            + "\n\n[... truncated ...]"
        )

    allowed = meta.get("allowed-tools") or meta.get("allowed_tools") or ()
    if isinstance(allowed, str):
        allowed_tuple: tuple[str, ...] = tuple(t.strip() for t in allowed.split(",") if t.strip())
    elif isinstance(allowed, (list, tuple)):
        allowed_tuple = tuple(str(t).strip() for t in allowed if str(t).strip())
    else:
        allowed_tuple = ()

    affinity_raw = meta.get("tags") or meta.get("affinity") or []
    if isinstance(affinity_raw, str):
        affinity = [a.strip() for a in affinity_raw.split(",") if a.strip()]
    elif isinstance(affinity_raw, (list, tuple)):
        affinity = [str(a).strip() for a in affinity_raw if str(a).strip()]
    else:
        affinity = []
    if "market" not in affinity:
        affinity.append("market")

    try:
        registry.register(
            Skill(
                name=name,
                description=description,
                affinity=affinity,
                cost_profile="low",
                trusted_source=f"skill://all_skills/{skill_id}",
                handler=_make_prompt_handler(skill_dir, name, body, allowed_tuple),
            ),
            verify_tests=verify_tests,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "load_single_market_skill: register %r failed: %s",
            name,
            exc,
        )
        return False
    return True


__all__ = [
    "immutable_prompt_catalog_required",
    "register_market_skills",
    "register_prompt_market_skills",
    "load_single_market_skill",
]
