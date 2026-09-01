"""Prompt-injection taint gate for ephemeral sub-agent tool calls.

Ephemeral sub-agents execute skills via a direct ``handler(**input)`` call
(see ``ephemeral_runner._execute_tool_in_subagent``) — they bypass
``stack.executor`` and therefore the injection-taint chokepoint AND the human
approval channel. So the gate has to live here, fail-closed:

  * **Inheritance** — the spawning parent stamps its taint into
    ``call.context`` (see ``bridge.call_subagent``). The timeout dispatch path
    runs the sub-agent in a fresh thread whose taint contextvar started clean,
    so we re-mark the inherited taint before any tool runs.
  * **Block** — when the delegating turn is injection-tainted, a risky tool
    here cannot be escalated to a human, so the only safe action is to refuse.
  * **Escalate** — if a tool ingests injection-flagged untrusted content, the
    turn taint is raised so a LATER risky tool in the same ephemeral run is
    gated too (mirrors the executor chokepoint's post-success scan).

Every entry point is best-effort: taint propagation must never crash a
sub-agent run.
"""

from __future__ import annotations

from typing import Any

_RISKY_LEVELS = ("medium", "high", "critical")


def mark_inherited_ephemeral_taint(context: Any) -> None:
    """Re-mark the spawning parent's prompt-injection taint inside an
    ephemeral sub-agent run, reading it from the sub-agent ``context``."""
    try:
        if not isinstance(context, dict):
            return
        inherited = context.get("_inherited_injection_taint")
        if isinstance(inherited, str) and inherited not in ("", "none"):
            from runtime.safety.validation.prompt_injection import (
                mark_injection_taint,
            )

            mark_injection_taint(inherited)
    except Exception:  # noqa: BLE001 - taint propagation is best-effort
        pass


def ephemeral_injection_taint_block(call: Any, tool_name: str) -> str | None:
    """Fail-closed taint gate. Returns a block message when a risky tool must
    be refused because the delegating turn is injection-tainted, else
    ``None``."""
    try:
        # Cover the timeout dispatch path (fresh thread, clean contextvar).
        mark_inherited_ephemeral_taint(getattr(call, "context", None))
        from runtime.safety.validation.prompt_injection import (
            injection_taint_gates,
        )

        if not injection_taint_gates():
            return None
        from runtime.safety.approval.approval_gate import (
            assess_approval_risk,
            is_durable_persistence_write,
        )

        # Durable-persistence writes (memory/soul) launder injection across
        # turns even though they're low-risk — block them too.
        if is_durable_persistence_write(tool_name):
            return (
                f"(blocked: prompt_injection_taint) tool '{tool_name}' refused "
                "— writing to durable agent state while the delegating turn is "
                "injection-tainted would launder the injection into a later turn."
            )
        try:
            args_preview = str(getattr(call, "input", ""))[:200]
        except Exception:  # noqa: BLE001
            args_preview = ""
        risk = assess_approval_risk(tool_name, args_preview)
        if risk.level in _RISKY_LEVELS:
            return (
                f"(blocked: prompt_injection_taint) tool '{tool_name}' "
                f"(risk={risk.level}) refused — the delegating turn ingested "
                "untrusted content carrying injection markers, so a sub-agent "
                "may not run a risky action on its behalf."
            )
    except Exception:  # noqa: BLE001 - gate must never crash the sub-agent
        return None
    return None


def scan_and_escalate_ephemeral_taint(
    tool_name: str,
    affinity: Any,
    rendered: str,
) -> None:
    """Raise the turn taint if an untrusted tool returned injection-flagged
    content, so a later risky tool in the same ephemeral run is gated too."""
    try:
        from runtime.safety.validation.prompt_injection import (
            is_untrusted_tool,
            mark_injection_taint,
            scan_for_injection,
        )

        if is_untrusted_tool(str(tool_name), list(affinity or [])):
            scan = scan_for_injection(rendered)
            if scan.flagged:
                mark_injection_taint(scan.severity)
    except Exception:  # noqa: BLE001 - scan is best-effort defense-in-depth
        pass
