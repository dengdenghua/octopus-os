"""Per-model capability facts from the bundled models.dev snapshot.

Our OpenAI-compat profiles are per-PROVIDER, which is right for request quirks
a whole vendor shares and wrong for facts that differ between one vendor's own
models. Measured examples:

* ``kimi-k3`` answers HTTP 400 when sent ``temperature``; its siblings on the
  same endpoint accept it. Today that only works because an operator hand-set
  ``omit_sampling_parameters`` on that entry — any unconfigured model on a
  relay gets the wrong default.
* A relay's ``deepseek-v4-flash`` has a 1M input window where the hand-written
  entry claimed 128k, so context budgeting truncated eight times too early.

The snapshot is vendored (``resources/models/capabilities.json``) rather than
fetched, so startup makes no network call and offline installs behave the
same. Refresh it with ``make refresh-model-capabilities``.

Precedence is deliberate: an operator's own ``custom_models.json`` declaration
always wins. This module only fills in what the operator did not say.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

_logger = logging.getLogger(__name__)

_SNAPSHOT_RELATIVE = ("resources", "models", "capabilities.json")


@lru_cache(maxsize=1)
def _snapshot() -> dict[str, dict[str, Any]]:
    """Load and cache the bundled snapshot.

    Every failure mode degrades to an empty mapping: a missing or corrupt
    snapshot must leave the runtime exactly as it behaved before this module
    existed, never break a model call.
    """
    try:
        from runtime.platform.process.paths import resources_root

        path = resources_root().joinpath(*_SNAPSHOT_RELATIVE)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError) as exc:
        _logger.debug("model capability snapshot unavailable: %s", exc)
        return {}
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, dict):
        return {}
    return {k: v for k, v in models.items() if isinstance(v, dict)}


def _record(model: str) -> dict[str, Any]:
    """Capability record for ``model``, tolerating our own id decorations.

    Model names reaching the router may carry the ``::1m`` long-context
    suffix, and relays sometimes namespace ids as ``vendor/model``. Try the
    literal id first, then those two normalizations, before giving up.
    """
    snapshot = _snapshot()
    if not snapshot:
        return {}
    probe = (model or "").strip()
    if not probe:
        return {}
    for candidate in (probe, probe.removesuffix("::1m"), probe.rsplit("/", 1)[-1]):
        record = snapshot.get(candidate)
        if isinstance(record, dict):
            return record
    return {}


def known_model_context_window(model: str) -> int | None:
    """Upstream-declared input window, or None when unknown."""
    value = _record(model).get("context")
    return value if isinstance(value, int) and value > 0 else None


def model_rejects_temperature(model: str) -> bool:
    """Whether sending ``temperature`` to this model is an error.

    Only an explicit upstream ``temperature: false`` returns True; an unknown
    model is assumed to accept sampling parameters, as before.
    """
    return _record(model).get("temperature") is False


def model_is_reasoning(model: str) -> bool:
    """Whether the model spends output tokens on reasoning before writing.

    Callers use this to raise an output-token floor: a budget that only covers
    the thinking yields HTTP 200 with empty content.
    """
    return _record(model).get("reasoning") is True


def reset_capability_cache() -> None:
    """Drop the cached snapshot. For tests that patch the resources root."""
    _snapshot.cache_clear()


__all__ = [
    "known_model_context_window",
    "model_is_reasoning",
    "model_rejects_temperature",
    "reset_capability_cache",
]
