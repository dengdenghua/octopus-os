"""Bootstrap wiring for the constitution's LLM-judge tier.

``gate.check_outbound`` already consults ``get_judge()`` on every
outbound message (Pass 3), but the default judge is a null allow-all:
nothing registered a real one at process start, which left the whole
tier dormant. This module is that missing registration step.

On by default unless explicitly disabled — the judge adds one model call
(~Haiku, cached 60s) per unique outbound message. Enable with::

    safety:
      enable_llm_judge: true
      # optional: judge model override
      llm_judge_model: claude-haiku-4-5-20251001

or ``ECHO_ENABLE_LLM_JUDGE=1``. Whether a judge ``block`` verdict
hard-enforces or stays audit-only is the existing profile decision
(``profiles.enforces_judge_verdict`` — strict enforces, normal/lax
audit). Judge failures fail open; the regex rule layer remains the
hard floor either way.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger("runtime.safety.validation.bootstrap")

_ENV_VAR = "ECHO_ENABLE_LLM_JUDGE"
_YAML_KEY = "enable_llm_judge"
_MODEL_YAML_KEY = "llm_judge_model"


def _read_safety_value(key: str) -> Any:
    for path in ("config.local.yaml", "config.yaml", "config.example.yaml"):
        try:
            if not os.path.exists(path):
                continue
            import yaml  # type: ignore[import-untyped]

            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh.read()) or {}
            if not isinstance(data, dict):
                continue
            safety = data.get("safety") or {}
            if not isinstance(safety, dict):
                continue
            if key in safety:
                return safety[key]
        except Exception:  # noqa: BLE001 — settings must not crash bootstrap
            continue
    return None


def llm_judge_enabled(
    explicit: bool | None = None,
    *,
    config_value: bool | None = None,
) -> bool:
    """Resolve the judge opt-in. Precedence, highest first:
    1. ``explicit`` kwarg (tests).
    2. ``ECHO_ENABLE_LLM_JUDGE`` env var (emergency override).
    3. ``config_value`` from the LOADED ``--config`` (respects a config
       file outside cwd — the bug this fixed).
    4. ``safety.enable_llm_judge`` re-read from a cwd yaml (legacy).
    Default True — the judge uses a lightweight model (~Haiku, cached 60s)
    per unique outbound message and fails open on any error, so the cost
    and risk of enabling it by default are minimal. The regex rule layer
    remains the hard floor regardless."""
    if explicit is not None:
        return explicit
    raw_env = os.environ.get(_ENV_VAR, "").strip().lower()
    if raw_env in ("1", "true", "yes", "on"):
        return True
    if raw_env in ("0", "false", "no", "off"):
        return False
    if config_value is not None:
        return bool(config_value)
    value = _read_safety_value(_YAML_KEY)
    if value is not None:
        return value is True
    return True


def maybe_register_llm_judge(
    router: Any,
    *,
    enabled: bool | None = None,
    config_value: bool | None = None,
    model: str | None = None,
) -> bool:
    """Register the LLM judge when enabled and a router is available.

    ``config_value`` carries the flag read off the loaded ``--config``;
    see :func:`llm_judge_enabled` for precedence. Returns True when a
    judge was registered. Never raises — a broken judge bootstrap must
    not take down serve startup; the rule layer keeps protecting
    outbound traffic regardless.
    """
    if not llm_judge_enabled(enabled, config_value=config_value):
        return False
    if router is None:
        _log.warning("%s set but no model router available · judge not registered", _ENV_VAR)
        return False
    try:
        from .judge import set_judge
        from .llm_judge import build_judge_from_router

        effective_model = model
        if effective_model is None:
            configured = _read_safety_value(_MODEL_YAML_KEY)
            if isinstance(configured, str) and configured.strip():
                effective_model = configured.strip()
        kwargs: dict[str, Any] = {}
        if effective_model:
            kwargs["model"] = effective_model
        set_judge(build_judge_from_router(router, **kwargs))
        _log.info(
            "constitution LLM judge registered (model=%s)",
            effective_model or "default",
        )
        return True
    except Exception as exc:  # noqa: BLE001 — fail open, rule layer remains
        _log.warning(
            "LLM judge registration failed (%s: %s) · rule layer remains the floor",
            type(exc).__name__,
            exc,
        )
        return False


__all__ = ["llm_judge_enabled", "maybe_register_llm_judge"]
