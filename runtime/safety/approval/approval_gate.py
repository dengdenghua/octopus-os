"""Tool-approval provider — pluggable, transport-agnostic.

The previous implementation kept a process-wide ``dict[str, threading.Event]``
and exposed ``request_approval``/``wait_for_approval``/``submit_decision``
free functions. That design forced the SSE+POST flow, deadlocked under
multi-worker uvicorn, and could not be unit-tested without monkey-patching
the global state.

This module replaces that with an :class:`ApprovalProvider` interface plus
two implementations:

  * :class:`AutoApproveProvider` — always grants. Useful in tests, in
    ``approvalPolicy="never"`` paths, and for headless batch jobs.
  * :class:`AutoDenyProvider`    — always denies. The default when no
    provider is wired, so a missing wiring fails closed rather than
    silently auto-accepting destructive tools.

Live UIs supply their own provider — for the realtime gateway, the
emitter is the provider (see :class:`RpcConnection.request_approval`).
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

_logger = logging.getLogger(__name__)

ApprovalRiskLevel = Literal["low", "medium", "high", "critical"]
ApprovalRiskAction = Literal["allow", "audit", "ask", "confirm", "deny"]


@dataclass(frozen=True, slots=True)
class ApprovalRisk:
    level: ApprovalRiskLevel
    categories: tuple[str, ...] = ()
    reason: str = ""

    @property
    def requires_approval(self) -> bool:
        return self.level in {"medium", "high", "critical"}

    def with_injection_taint(self) -> ApprovalRisk:
        """Annotate that this approval was forced by prompt-injection
        taint in the turn (untrusted content carried injection markers),
        so the UI / audit log shows *why* a normally-auto tool is asking."""
        if "prompt_injection_taint" in self.categories:
            return self
        return ApprovalRisk(
            level=self.level,
            categories=(*self.categories, "prompt_injection_taint"),
            reason=(
                f"{self.reason}; forced approval — untrusted content with "
                "injection markers entered this turn"
            ).lstrip("; "),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "categories": list(self.categories),
            "reason": self.reason,
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True, slots=True)
class ApprovalRiskPolicy:
    """Risk-level policy matrix for tool approval routing."""

    low: ApprovalRiskAction = "allow"
    medium: ApprovalRiskAction = "ask"
    high: ApprovalRiskAction = "ask"
    critical: ApprovalRiskAction = "confirm"

    def action_for(self, risk: ApprovalRisk) -> ApprovalRiskAction:
        return getattr(self, risk.level)

    @classmethod
    def from_mapping(cls, value: object) -> ApprovalRiskPolicy:
        if not isinstance(value, dict):
            return cls()
        allowed = {"allow", "audit", "ask", "confirm", "deny"}
        data: dict[str, ApprovalRiskAction] = {}
        for level in ("low", "medium", "high", "critical"):
            raw = value.get(level)
            if isinstance(raw, str) and raw in allowed:
                data[level] = raw  # type: ignore[assignment]
        return cls(**data)

    def to_dict(self) -> dict[str, ApprovalRiskAction]:
        return {
            "low": self.low,
            "medium": self.medium,
            "high": self.high,
            "critical": self.critical,
        }


# Heuristic catalog of tool names that require explicit approval when no
# more specific policy is supplied. Mirrors the original ``approval_gate``
# defaults; centralizing here keeps the rule set in one place.
DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {
        "exec_shell",
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_code",
        "delete_file",
        "git_commit",
        "git_push",
        "git_checkout",
        "git_create_pr",
        "git_merge",
        "git_reset",
        # Computer-automation API surface (preview-confirm-execute) ·
        # ``computer_observe`` and ``computer_preview_action`` are
        # deliberately omitted: the former is read-only status and a
        # screenshot, the latter only queues a token without executing.
        # ``computer_plan_next`` is included because it ships a fresh
        # screenshot to a vision model (egress) before any user
        # confirmation, and ``computer_execute_token`` is the one that
        # actually moves the mouse / keyboard via the queued token.
        "computer_plan_next",
        "computer_execute_token",
    }
)

DANGEROUS_PREFIXES: tuple[str, ...] = (
    "exec_shell",
    "write_",
    "edit_",
    "delete_",
    "git_",
    "send_",
    "http_",
    "fetch_",
    "email_",
    "slack_",
    # Desktop GUI control · pyautogui-driven mouse / keyboard / screen
    # primitives. Screen capture is included (not just input) because
    # whatever's on screen — open password managers, draft emails,
    # private chat — leaves the host the moment the screenshot lands
    # in a tool observation. Treat it as data egress.
    "mouse_",
    "keyboard_",
    "screen_",
    # Browser automation · both the headless Playwright pool
    # (``browser_*``) and the live bridge into the desktop Electron
    # webview (``live_browser_*``). The latter operates the user's
    # real, logged-in browser, so it is at least as sensitive as the
    # former. Gating both at the prefix level avoids per-skill drift
    # as new browser primitives get added.
    "browser_",
    "live_browser_",
    # Mobile/Tentacle device control. These names appear as canonical
    # Android skill ids (``android.tap``) and MCP-safe tool ids
    # (``android_tap``).
    "android.",
    "android_",
)


def is_dangerous_tool(tool_name: str) -> bool:
    """Return True iff ``tool_name`` should default to needing approval.

    Pure function; safe to call from anywhere. Decisions about whether
    to *consult* the user (vs. an auto-approve override) belong to the
    caller — typically the ``approval_policy`` resolution layer.
    """
    if tool_name in DANGEROUS_TOOLS:
        return True
    return any(tool_name.startswith(prefix) for prefix in DANGEROUS_PREFIXES)


def assess_approval_risk(
    tool_name: str,
    args_preview: str = "",
) -> ApprovalRisk:
    """Classify a tool call by capability risk, independent of UI policy."""

    name = (tool_name or "").strip()
    preview = (args_preview or "").lower()
    categories: list[str] = []
    level: ApprovalRiskLevel = "low"

    def bump(next_level: ApprovalRiskLevel, category: str) -> None:
        nonlocal level
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if order[next_level] > order[level]:
            level = next_level
        if category not in categories:
            categories.append(category)

    if name.startswith(("read_", "list_", "glob_", "grep_", "file_stats")):
        bump("low", "local_read")
    if name.startswith(
        (
            "fetch_",
            "http_",
            "send_",
            "email_",
            "slack_",
            "upload_",
            "publish_",
            "post_",
            "webhook_",
            "deploy_",
        )
    ):
        bump("medium", "network_or_egress")
    if name.startswith(("write_", "append_", "edit_", "delete_")) or name in DANGEROUS_TOOLS:
        bump("high", "filesystem_write")
    if name.startswith(
        ("browser_", "live_browser_", "mouse_", "keyboard_", "screen_", "computer_")
    ):
        bump("high", "interactive_control")
    if name.startswith(("android.", "android_")):
        bump("high", "mobile_device_control")
    if name.startswith("git_"):
        bump("high", "vcs_mutation")
    # Command execution — incl. background / renamed aliases that the
    # bare ``exec_shell`` check missed (a prompt-injected agent can route
    # a shell through any of these).
    if name in {
        "exec_shell",
        "shell_command",
        "bash",
        "background_exec",
        "run_command",
        "exec_command",
        "run_python",
        "python_exec",
        "run_code",
        "subprocess_run",
    } or name.startswith(("exec_shell", "background_exec", "run_command")):
        bump("high", "shell_execution")
        destructive_markers = (
            "rm -rf",
            "del /",
            "remove-item",
            "format ",
            "drop database",
            "truncate table",
            "git reset --hard",
            "git clean -fd",
            "push --force",
            "--force-with-lease",
            "chmod 777",
        )
        if any(marker in preview for marker in destructive_markers):
            bump("critical", "destructive_command")
    # MCP / remote tools: their output is attacker-influenceable and their
    # tool NAME is freeform, so the prefix rules above don't see the
    # capability. Treat any mcp_* tool as at least medium, and infer a
    # dangerous capability from danger keywords in the name (e.g.
    # ``mcp_<server>_exec_shell`` / ``..._write_file`` / ``..._upload``).
    # ``use_chatgpt_connector`` hands a free-form instruction to a third-party
    # connector running under the user's own credentials, so it carries the same
    # external-side-effect risk as an mcp_* call without matching the prefix.
    # Left at low it was strictly more permissive than write_text_file (high),
    # despite acting outside the machine with the user's identity.
    if name.startswith(("mcp_", "mcp__")) or name == "use_chatgpt_connector":
        bump("medium", "external_mcp")
        _low = name.lower()
        if any(
            k in _low
            for k in (
                "exec",
                "shell",
                "bash",
                "subprocess",
                "run_command",
                "run_code",
            )
        ):
            bump("high", "shell_execution")
        elif any(
            k in _low
            for k in (
                "write",
                "delete",
                "remove",
                "edit_",
                "upload",
                "publish",
                "deploy",
            )
        ):
            bump("high", "filesystem_write")
    if "api_key" in preview or "secret" in preview or "token" in preview:
        bump("high", "secret_material")
    # Credential probing — reading credentials / session material from
    # non-standard sources (browser profiles, keychains, credential files,
    # logs) is how a hijacked agent exfiltrates auth. Mirrors codex
    # policy.md "Credential Probing": high risk, denied at low/unknown
    # authorization.
    if name.startswith(("read_", "list_", "glob_", "grep_", "cat_", "file_stats")) and any(
        marker in preview for marker in _CREDENTIAL_PROBE_MARKERS
    ):
        bump("high", "credential_probing")
    # Sensitive egress — network actions moving secret material out.
    # Codex requires BOTH payload AND destination authorization; we can't
    # resolve the destination here, so flag sensitive_egress so the policy
    # layer treats it as high-risk unless the user explicitly authorized
    # that payload for that destination.
    if name.startswith(
        (
            "fetch_",
            "http_",
            "send_",
            "email_",
            "slack_",
            "upload_",
            "publish_",
            "post_",
            "webhook_",
        )
    ) and any(marker in preview for marker in _SENSITIVE_EGRESS_MARKERS):
        bump("high", "sensitive_egress")
    if level == "low" and is_dangerous_tool(name):
        bump("medium", "dangerous_tool_catalog")

    return ApprovalRisk(
        level=level,
        categories=tuple(categories),
        reason=", ".join(categories) or "no elevated risk detected",
    )


# Tools that persist attacker-influenceable content into DURABLE agent state
# (MEMORY.md / SOUL.md / USER.md) that is re-loaded into a LATER turn's system
# prompt. Taint is per-turn and resets each turn, so a poisoned fact/lesson
# written under taint would re-enter a future CLEAN turn's context — cross-turn
# injection laundering. These are LOW capability-risk, so the approval gate
# never escalates them; they must be blocked at the chokepoint while tainted.
_DURABLE_PERSISTENCE_WRITES: frozenset[str] = frozenset(
    {"remember", "update_soul", "note_user"},
)

# Non-standard credential locations a compromised agent might read to
# exfiltrate auth material. Lowercased args preview is matched against
# these (codex policy.md "Credential Probing").
_CREDENTIAL_PROBE_MARKERS: tuple[str, ...] = (
    ".aws/credentials",
    ".aws/config",
    "browser profile",
    "cookies.sqlite",
    "login data",
    "keychain",
    "credential",
    "id_rsa",
    ".ssh/",
    "session.json",
    "secrets.json",
    ".npmrc",
    ".pypirc",
)

# Payload markers that make a network egress action sensitive — such an
# action needs explicit payload+destination authorization (codex policy.md
# "Data Exfiltration").
_SENSITIVE_EGRESS_MARKERS: tuple[str, ...] = (
    "api_key",
    "secret",
    "token",
    "password",
    "private_key",
    "credentials",
    "session",
    "authorization",
    "bearer",
)


def is_durable_persistence_write(tool_name: str) -> bool:
    """Whether ``tool_name`` persists content into durable agent state that a
    later turn re-loads into its system prompt (the cross-turn injection
    laundering surface)."""
    return (tool_name or "").strip() in _DURABLE_PERSISTENCE_WRITES


def injection_taint_block(
    tool_name: str,
    args_preview: str = "",
    *,
    defer_if_handled: bool = True,
) -> str | None:
    """The single, path-independent enforcement point for prompt-injection
    taint, called by the executor before every tool runs.

    Returns a block reason when the turn is injection-tainted (untrusted
    content with injection markers entered it) AND either:
      • this tool is risky (medium+) and no approval-capable loop has reviewed
        this specific call — i.e. it reached the executor via the parallel
        dispatch, the agentic-fallback loop, or a subagent, none of which can
        ask a human; or
      • this tool writes durable agent state (memory/soul/user profile) — a
        cross-turn laundering vector that is low-risk (so the approval gate
        never escalates it) yet persists the injection into a future turn.
    Returns ``None`` to allow (clean turn, low-risk non-persistence tool, or a
    risky call already reviewed by the single-action approval gate).
    Fail-closed: when in doubt after taint, the tool is blocked rather than
    auto-run.

    ``defer_if_handled`` lets a caller opt OUT of the gate_already_handled
    deferral. The executor passes True: a risky tool the single-action gate
    already reviewed shouldn't double-block. But a META-SKILL dispatching to an
    INNER handler (use_capability, a forged composite) must pass False — the
    single-action gate reviewed the OUTER meta-skill (typically low-risk), not
    the inner tool, so the inner call is effectively unreviewed."""
    from runtime.safety.validation.prompt_injection import (
        injection_gate_already_handled,
        injection_taint_gates,
    )

    if not injection_taint_gates():
        return None
    # Durable-persistence writes are blocked on EVERY path while tainted —
    # including the single-action path that set gate_already_handled — because
    # the single-action approval gate won't have escalated a low-risk write, so
    # deferring to it would let the poison through.
    if is_durable_persistence_write(tool_name):
        return (
            f"{(tool_name or '').strip()} blocked — writing untrusted content "
            "to durable agent state (memory/soul/user profile) while the turn "
            "is injection-tainted would launder the injection into a future "
            "turn's system prompt"
        )
    if defer_if_handled and injection_gate_already_handled():
        return None
    risk = assess_approval_risk(tool_name, args_preview)
    if risk.level in {"medium", "high", "critical"}:
        return (
            f"{tool_name} (risk={risk.level}: {risk.reason}) blocked — "
            "untrusted content with prompt-injection markers entered this "
            "turn and this execution path cannot request human approval"
        )
    return None


def needs_approval(tool_name: str, auto_approve: bool = False) -> bool:
    """Backwards-compatible helper. Prefer ``is_dangerous_tool``."""
    if auto_approve:
        return False
    return assess_approval_risk(tool_name).requires_approval


def needs_approval_for_chain(tool_names: list[str], auto_approve: bool = False) -> bool:
    if auto_approve:
        return False
    return any(assess_approval_risk(name).requires_approval for name in tool_names)


def approval_action_for_tool(
    tool_name: str,
    args_preview: str = "",
    *,
    policy: ApprovalRiskPolicy | dict[str, str] | None = None,
) -> tuple[ApprovalRisk, ApprovalRiskAction, ApprovalRiskPolicy]:
    risk = assess_approval_risk(tool_name, args_preview)
    resolved_policy = (
        policy
        if isinstance(policy, ApprovalRiskPolicy)
        else ApprovalRiskPolicy.from_mapping(policy)
    )
    return risk, resolved_policy.action_for(risk), resolved_policy


# ── Provider protocol ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """All the context an approval decision could possibly need."""

    thread_id: str
    tool_name: str
    tool_call_id: str
    args_preview: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approved: bool
    reason: str | None = None


class ApprovalProvider(ABC):
    """Adapter between a planner's "needs human consent" event and a
    transport (web UI, CLI prompt, Slack, auto-deny, …).

    Implementations must be thread-safe — the same provider may be
    invoked from any worker thread the executor places ReAct on.
    """

    @abstractmethod
    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        """Block the calling thread until the user decides or timeout.

        Implementations may raise on transport failure; callers should
        treat exceptions as denial unless they have a more specific
        policy.
        """


class AutoApproveProvider(ApprovalProvider):
    """Always grants. Convenient for tests and non-interactive runs."""

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        return ApprovalDecision(approved=True, reason="auto-approve")


class AutoDenyProvider(ApprovalProvider):
    """Default fail-closed provider.
    When no UI is wired, the safe behavior is to refuse rather than
    silently auto-accepting whatever the planner proposes. The reason
    string is surfaced to the planner so it can adjust its plan.
    """

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        _logger.info(
            "AutoDenyProvider rejecting %s (no interactive UI wired)",
            req.tool_name,
        )
        return ApprovalDecision(
            approved=False,
            reason="no interactive approval UI is wired in this runtime",
        )


# ── Rule-based provider (static policy → cheap) ───────────────
#
# Two-layer permission: static rules absorb the bulk
# of routine tool calls (no human round-trip), and an underlying
# provider handles whatever the rules do not decide. This drops
# approval UI traffic dramatically for sessions where the user has
# pre-declared trusted operations (e.g. "always allow reads",
# "never allow rm -rf").


@dataclass(frozen=True, slots=True)
class ApprovalRule:
    """A single allow/deny rule.

    ``tool`` is matched with ``fnmatch`` against ``ApprovalRequest.tool_name``
    (``"*"`` = any). ``args_contains``, if non-empty, requires the
    substring to appear in ``args_preview`` (cheap containment check —
    deliberately not a regex, the rule set is expected to stay small
    and auditable).
    """

    effect: Literal["allow", "deny"]
    tool: str = "*"
    args_contains: str = ""
    reason: str = ""

    def matches(self, req: ApprovalRequest) -> bool:
        if not fnmatch.fnmatchcase(req.tool_name, self.tool):
            return False
        if self.args_contains and self.args_contains not in req.args_preview:  # noqa: SIM103 — early-return guards read better
            return False
        return True


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    """Ordered rule list. First match wins; miss → fallback provider."""

    rules: tuple[ApprovalRule, ...] = field(default_factory=tuple)

    def decide(self, req: ApprovalRequest) -> ApprovalDecision | None:
        for rule in self.rules:
            if rule.matches(req):
                return ApprovalDecision(
                    approved=(rule.effect == "allow"),
                    reason=rule.reason or f"rule:{rule.effect}:{rule.tool}",
                )
        return None


def _fire_permission_denied(req: ApprovalRequest, reason: str | None) -> None:
    """Dispatch a PermissionDenied hook (best-effort, never breaks approval)."""
    try:
        from runtime.safety.hooks import dispatch_permission_denied

        dispatch_permission_denied(
            sucker_id=req.tool_name,
            args={"args_preview": req.args_preview, "detail": req.detail},
            reason=reason or "",
        )
    except Exception:  # noqa: BLE001 — hooks are best-effort
        pass


class RuleBasedProvider(ApprovalProvider):
    """Decorator that consults a static policy before delegating.

    A hit on the policy short-circuits; a miss falls through to
    ``fallback`` (typically a UI-backed provider or AutoDeny).
    """

    def __init__(self, policy: ApprovalPolicy, fallback: ApprovalProvider) -> None:
        self._policy = policy
        self._fallback = fallback

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        decision = self._policy.decide(req)
        if decision is not None:
            if not decision.approved:
                _fire_permission_denied(req, decision.reason)
            _logger.debug(
                "RuleBasedProvider short-circuit: %s → %s (%s)",
                req.tool_name,
                "allow" if decision.approved else "deny",
                decision.reason,
            )
            return decision
        # No policy hit → offer hooks a chance to grant or deny before
        # falling through to the interactive ask. A cancel from a
        # PermissionRequest hook replaces the gate's decision (lets
        # automation deny unsafe calls without a human prompt).
        hook_denied = None
        try:
            from runtime.safety.hooks import dispatch_permission_request

            hook_decision = dispatch_permission_request(
                sucker_id=req.tool_name,
                args={"args_preview": req.args_preview, "detail": req.detail},
                caller="approval",
            )
            if hook_decision.cancelled:
                hook_denied = hook_decision.reason or "denied by PermissionRequest hook"
        except Exception:  # noqa: BLE001 — hooks are best-effort, never break approval
            hook_denied = None
        if hook_denied:
            decision = ApprovalDecision(approved=False, reason=hook_denied)
            _fire_permission_denied(req, decision.reason)
            return decision
        decision = self._fallback.request(req, timeout=timeout)
        if not decision.approved:
            _fire_permission_denied(req, decision.reason)
        return decision


__all__ = [
    "ApprovalDecision",
    "ApprovalPolicy",
    "ApprovalProvider",
    "ApprovalRisk",
    "ApprovalRiskAction",
    "ApprovalRiskLevel",
    "ApprovalRiskPolicy",
    "ApprovalRequest",
    "ApprovalRule",
    "AutoApproveProvider",
    "AutoDenyProvider",
    "DANGEROUS_PREFIXES",
    "DANGEROUS_TOOLS",
    "RuleBasedProvider",
    "approval_action_for_tool",
    "assess_approval_risk",
    "is_dangerous_tool",
    "needs_approval",
    "needs_approval_for_chain",
    "DenialCircuitBreaker",
]


# ── Denial circuit breaker ────────────────────────────────────
#
# A hijacked or stubborn agent may retry the same denied action over and
# over, burning budget against a wall the guard already hit. Codex's
# guardian tracks repeated same-guard rejections and stops retrying after
# N (``count_denial_for_circuit_breaker``). This is the Echo analogue:
# a per-key (thread/action fingerprint) counter that flips OPEN after
# ``limit`` consecutive denials, so the executor can stop retrying and
# surface a terminal "denied + circuit open" instead of spinning.


class DenialCircuitBreaker:
    """Thread-safe consecutive-denial counter with an open state.

    ``key`` is typically ``(thread_id, tool_name, fingerprint)`` so the
    breaker only trips for the SAME action being retried, never for
    unrelated approvals. Call ``note_denial`` on each denial and
    ``note_clear`` when the action is approved / the goal moves on.
    """

    def __init__(self, limit: int = 3) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._limit = limit
        self._counts: dict[tuple[str, ...], int] = {}
        self._lock = threading.Lock()

    def is_open(self, key: tuple[str, ...]) -> bool:
        with self._lock:
            return self._counts.get(key, 0) >= self._limit

    def note_denial(self, key: tuple[str, ...]) -> int:
        with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            return count

    def note_clear(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._counts.pop(key, None)
