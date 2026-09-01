"""Hook registry · where handlers register · and dispatch resolves."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from runtime.platform.process.service_provider import get_provider

from .events import HookEvent


@dataclass(frozen=True)
class HookDecision:
    """Structured handler response.

    ``cancelled`` — runtime must abort the action this hook gates.
    ``reason``    — user-facing explanation when cancelled.
    ``modified_args``   — if non-None in a PreToolUse hook, runtime
                          uses these instead of original args.
    ``modified_output`` — if non-None in a PostToolUse hook, runtime
                          uses this instead of the skill's output.
    ``modified_prompt`` — if non-None in a UserPromptSubmit hook,
                          runtime uses this instead of original prompt.

    The modified-X fields are mutually exclusive in practice but
    kept on the same dataclass so handlers can declare a single
    return type (easier API than 4 different decision subclasses).
    """

    cancelled: bool = False
    reason: str = ""
    modified_args: dict[str, Any] | None = None
    modified_output: Any = None
    modified_prompt: str | None = None

    @classmethod
    def pass_through(cls) -> HookDecision:
        return cls()

    @classmethod
    def cancel(cls, reason: str) -> HookDecision:
        return cls(cancelled=True, reason=reason)

    @classmethod
    def modify_args(cls, args: dict[str, Any]) -> HookDecision:
        return cls(modified_args=dict(args))

    @classmethod
    def modify_output(cls, output: Any) -> HookDecision:
        return cls(modified_output=output)

    @classmethod
    def modify_prompt(cls, prompt: str) -> HookDecision:
        return cls(modified_prompt=prompt)


HookHandler = Callable[[HookEvent], HookDecision | None]


@dataclass
class HookRegistry:
    """In-memory handler registration · one registry per app.

    Many handlers can register for the same event type · they run
    in registration order. First cancelled decision wins · later
    handlers don't run. Handlers returning None or
    ``HookDecision.pass_through()`` let the next handler in the
    chain decide.
    """

    _handlers: dict[type, list[HookHandler]] = field(default_factory=dict)

    def register(
        self,
        event_type: type,
        handler: HookHandler,
    ) -> Callable[[], None]:
        """Register a handler and return an idempotent disposer.

        The returned disposer is the lifecycle boundary for plugin-owned
        hooks.  Removing by identity prevents an old plugin generation from
        unregistering a newer handler that happens to share the same name.
        Existing callers may continue to ignore the return value.
        """

        handlers = self._handlers.setdefault(event_type, [])
        handlers.append(handler)

        def dispose() -> None:
            current = self._handlers.get(event_type)
            if not current:
                return
            for index, registered in enumerate(current):
                if registered is handler:
                    current.pop(index)
                    break
            if not current:
                self._handlers.pop(event_type, None)

        return dispose

    def handlers_for(self, event_type: type) -> list[HookHandler]:
        """Return handlers for this exact event type · subclasses
        have their own lists (no polymorphic match)."""
        return list(self._handlers.get(event_type, []))

    def clear(self) -> None:
        """Wipe all registrations · used by tests for isolation."""
        self._handlers.clear()


def get_global_registry() -> HookRegistry:
    """Process-global HookRegistry · used by ``register_hook``
    decorator + runtime dispatch points. Tests needing isolation
    should call ``.clear()`` in a fixture · or build their own
    registry and pass it through explicitly (future enhancement)."""
    provider = get_provider()
    registry = provider.get("hooks_registry")
    if registry is None:
        registry = HookRegistry()
        provider.register_instance("hooks_registry", registry)
    return registry


def register_hook(
    event_type: type,
) -> Callable[[HookHandler], HookHandler]:
    """Decorator · register ``handler`` for ``event_type`` on the
    global registry. Community hooks in ``~/.echo/hooks/*.py``
    use this to plug in.

    Usage::

        from runtime.safety.hooks import register_hook, PreToolUseEvent

        @register_hook(PreToolUseEvent)
        def block_rm_rf(event):
            if event.sucker_id == "exec_shell":
                cmd = event.args.get("cmd", "")
                if "rm -rf /" in cmd:
                    return HookDecision.cancel("refuse rm -rf /")
            return HookDecision.pass_through()
    """

    def _decorator(handler: HookHandler) -> HookHandler:
        get_global_registry().register(event_type, handler)
        return handler

    return _decorator
