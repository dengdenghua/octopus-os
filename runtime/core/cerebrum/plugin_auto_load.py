"""Auto-activate pinned plugins/skill-packs from user mentions.

When the user @-mentions a plugin (e.g. ``@plugin:web-search``), the
react loop calls :func:`auto_load_pinned_plugins` before the model's
first turn. The function:

1. Looks at the mention's plugin id.
2. Asks the PluginHub whether it's already loaded and started.
3. If not, calls ``hub.load(name)`` then ``hub.start(name)``.
4. Records the outcome so we can surface a tool-result-style note in
   the next observation, telling the user (and the model) that a new
   capability has just become available mid-turn.

The function is a no-op when:
- the mention list is empty,
- the PluginHub isn't installed in this build,
- the plugin id can't be matched to any known plugin.

Failures are logged but never raised — auto-load is a quality-of-life
feature, not a critical path. If a plugin can't be loaded the model
still has the original prompt with the mention text, and the user-
facing error surfaces in the activation report.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginActivation:
    """Outcome of trying to load + start a single pinned plugin."""

    plugin_id: str
    was_already_loaded: bool
    was_already_started: bool
    loaded_now: bool
    started_now: bool
    error: str | None = None

    @property
    def is_available(self) -> bool:
        return self.error is None and (self.was_already_started or self.started_now)


@dataclass(frozen=True)
class PluginActivationReport:
    """Aggregate report rendered for the model's next observation."""

    activations: tuple[PluginActivation, ...]

    def render_observation(self) -> str:
        """Render a one-paragraph note for the next ReAct observation.

        Returns an empty string when nothing happened, so the caller
        can skip injecting it.
        """
        if not self.activations:
            return ""
        newly_loaded = [
            a for a in self.activations if (a.loaded_now or a.started_now) and a.is_available
        ]
        already = [
            a
            for a in self.activations
            if a.was_already_started and not a.loaded_now and not a.started_now
        ]
        failures = [a for a in self.activations if a.error]
        parts: list[str] = []
        if newly_loaded:
            parts.append(
                "Activated pinned plugins: "
                + ", ".join(f"`{a.plugin_id}`" for a in newly_loaded)
                + ".",
            )
        if already:
            parts.append(
                "Already running: " + ", ".join(f"`{a.plugin_id}`" for a in already) + ".",
            )
        if failures:
            parts.append(
                "Failed to activate: "
                + ", ".join(f"`{a.plugin_id}` ({a.error})" for a in failures)
                + ".",
            )
        return " ".join(parts)


def auto_load_pinned_plugins(
    plugin_ids: Iterable[str],
    *,
    hub: Any = None,
) -> PluginActivationReport:
    """Try to load + start each plugin in ``plugin_ids``.

    Parameters
    ----------
    plugin_ids :
        Iterable of plugin names from
        :class:`~runtime.core.cerebrum.input_mentions.InputMentions`
        ``.plugins``.
    hub :
        Optional :class:`PluginHub`. Default uses the singleton from
        ``runtime.platform.plugins.plugin_hub.get_plugin_hub``. Tests can pass
        a stub.
    """
    plugin_ids = tuple(dict.fromkeys(p for p in plugin_ids if p))
    if not plugin_ids:
        return PluginActivationReport(())

    if hub is None:
        try:
            from runtime.platform.plugins.plugin_hub import get_plugin_hub

            hub = get_plugin_hub()
        except (ImportError, AttributeError, RuntimeError) as exc:
            _LOG.debug("plugin hub unavailable: %s", exc)
            return PluginActivationReport(())

    activations: list[PluginActivation] = []
    for plugin_id in plugin_ids:
        activations.append(_activate_one(plugin_id, hub))
    return PluginActivationReport(tuple(activations))


def _activate_one(plugin_id: str, hub: Any) -> PluginActivation:
    """Load+start a single plugin, capturing errors."""
    was_loaded = False
    was_started = False
    try:
        existing = hub.get_plugin(plugin_id) if hasattr(hub, "get_plugin") else None
    except (AttributeError, KeyError):
        existing = None
    if existing is not None:
        was_loaded = True
        try:
            state = str(getattr(existing, "state", "") or "")
        except (AttributeError, TypeError):
            state = ""
        was_started = state.lower() in {"started", "running", "active"}

    if was_started:
        return PluginActivation(
            plugin_id=plugin_id,
            was_already_loaded=True,
            was_already_started=True,
            loaded_now=False,
            started_now=False,
        )

    loaded_now = False
    started_now = False
    error: str | None = None

    if not was_loaded:
        try:
            result = hub.load(plugin_id) if hasattr(hub, "load") else None
            loaded_now = result is not None
            if not loaded_now:
                error = "load returned no plugin"
        except (AttributeError, TypeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — broad catch is intentional
            # The PluginHub's load path can raise arbitrary exceptions
            # because plugins are user code. We swallow them so a bad
            # plugin doesn't poison the model turn.
            error = f"{type(exc).__name__}: {exc}"

    if error is None:
        try:
            started_now = bool(
                hub.start(plugin_id) if hasattr(hub, "start") else False,
            )
            if not started_now:
                error = "start returned False"
        except (AttributeError, TypeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

    return PluginActivation(
        plugin_id=plugin_id,
        was_already_loaded=was_loaded,
        was_already_started=was_started,
        loaded_now=loaded_now,
        started_now=started_now,
        error=error,
    )


__all__ = [
    "PluginActivation",
    "PluginActivationReport",
    "auto_load_pinned_plugins",
]
