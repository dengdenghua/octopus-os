from __future__ import annotations

import concurrent.futures
import contextlib
import contextvars
import sys
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.memory.journal import journal_context
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    ParsedIntent,
)
from runtime.platform.step_format import (
    step_effective_success as _step_effective_success,
)
from runtime.platform.step_format import (
    summarize_step_for_stream as _summarize_step_for_stream,
)

from .base import Channel, InboundMessage, OutboundMessage
from .store import ThreadConversationStore


class ChannelRoutingError(RuntimeError):
    pass


# Task/thread-local delivery target · set around an inbound IM turn so an
# agent (e.g. 章鱼助手 echo) can remember which channel/thread a cron
# delivery (订阅推送) should be pushed back to. ContextVars propagate to
# child tasks/threads spawned within the turn, so skills called by the
# planner can read it via ``current_channel_target()``.
_current_channel_target: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "ch_current_channel_target", default=None
)


def current_channel_target() -> tuple[str, str] | None:
    """Return the ``(channel_id, thread_id)`` of the current IM turn, if any."""
    return _current_channel_target.get()


class ChannelManager:
    def __init__(
        self,
        *,
        stack: Any,
        agent_registry: Any,
        store: ThreadConversationStore | None = None,
        default_agent_id: str | None = None,
        budget_tokens: int = 50_000,
        budget_usd: float = 0.50,
        strict_gate: bool = False,
    ) -> None:
        """ChannelManager · registers routes inbound/outbound.

        Parameters
        ----------
        strict_gate:
            When True, ``register()`` raises ``RuntimeError`` if the
            adapter's ``send()`` does not call ``self.safe_send()`` /
            ``check_outbound()`` before any network call. Default
            False (advisory warning) for backward compatibility —
            production deployments should set True to enforce the
            constitution gate chain (PRIV-2, PRIV-4). Source code
            that cannot be introspected still only warns (not the
            developer's fault).
        """
        self._stack = stack
        self._agent_registry = agent_registry
        self._store = store or ThreadConversationStore()
        self._default_agent_id = default_agent_id
        self._budget_tokens = budget_tokens
        self._budget_usd = budget_usd
        self._strict_gate = strict_gate
        self._channels: dict[str, Channel] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="ch-send"
        )

    def register(self, channel: Channel) -> None:
        if not channel.channel_id:
            raise ValueError(
                f"channel {type(channel).__name__} missing channel_id",
            )
        if channel.channel_id in self._channels:
            raise ValueError(
                f"duplicate channel_id: {channel.channel_id!r}",
            )
        channel.bind_dispatcher(self.process_inbound)

        # Constitution-gate audit · does this adapter's send() call
        # safe_send / check_outbound before any network call?
        # Advisory by default (warning) · strict in production
        # (raises RuntimeError, refuses registration). See
        # docs/constitution.md · PRIV-2, PRIV-4.
        _audit_channel_for_gate(channel, strict=self._strict_gate)

        self._channels[channel.channel_id] = channel

    def has(self, channel_id: str) -> bool:
        return channel_id in self._channels

    def get(self, channel_id: str) -> Channel:
        return self._channels[channel_id]

    def channel_ids(self) -> list[str]:
        return sorted(self._channels)

    def __len__(self) -> int:
        return len(self._channels)

    # ─── Lifecycle ──────────────────────────────

    def start_all(self) -> None:
        for ch in self._channels.values():
            ch.start()

    def stop_all(self) -> None:
        for ch in self._channels.values():
            with contextlib.suppress(Exception):
                ch.stop()
        self.shutdown()

    def send_async(self, channel_id: str, msg: OutboundMessage) -> concurrent.futures.Future:
        ch = self._channels.get(channel_id)
        if ch is None:
            raise ChannelRoutingError(f"unknown channel: {channel_id!r}")
        return self._executor.submit(ch.send, msg)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    def process_inbound(self, msg: InboundMessage) -> OutboundMessage:
        if msg.channel_id not in self._channels:
            raise ChannelRoutingError(
                f"unknown channel: {msg.channel_id!r}",
            )
        if not msg.content or not msg.content.strip():
            raise ChannelRoutingError("empty content")

        with trace_stage(
            "channels.process_inbound",
            channel_id=msg.channel_id,
            thread_id=msg.thread_id,
        ):
            conv_id = self._store.get_or_create(
                msg.channel_id,
                msg.thread_id,
            )
            agent = self._pick_agent(msg)

            intent = ParsedIntent(
                raw=msg.content,
                intent_type="task",
                normalized_goal=msg.content,
            )

            with journal_context(
                agent_id=agent.agent_id,
                conversation_id=conv_id,
            ):
                tok = _current_channel_target.set((msg.channel_id, msg.thread_id))
                try:
                    reply_text = self._plan_and_run(agent, intent)
                finally:
                    _current_channel_target.reset(tok)

            outbound_meta = dict(msg.metadata)
            outbound_meta["agent_id"] = agent.agent_id
            outbound_meta["conversation_id"] = conv_id
            out = OutboundMessage(
                channel_id=msg.channel_id,
                thread_id=msg.thread_id,
                content=reply_text,
                metadata=outbound_meta,
            )
            self._channels[msg.channel_id].send(out)
            return out

    def send_to_channel(
        self,
        channel_id: str,
        msg: OutboundMessage,
    ) -> str | None:
        ch = self._channels.get(channel_id)
        if ch is None:
            raise ChannelRoutingError(f"unknown channel: {channel_id!r}")
        ch.send(msg)
        return None

    def edit_on_channel(
        self,
        channel_id: str,
        msg: OutboundMessage,
        original_message_id: str,
    ) -> None:
        ch = self._channels.get(channel_id)
        if ch is None:
            raise ChannelRoutingError(f"unknown channel: {channel_id!r}")
        if ch.supports_edit:
            ch.edit(msg, original_message_id)
        else:
            ch.send(msg)

    def channel_supports_edit(self, channel_id: str) -> bool:
        ch = self._channels.get(channel_id)
        return ch.supports_edit if ch is not None else False

    def deliver_cron_result(
        self,
        channel_id: str,
        thread_id: str,
        result_text: str,
    ) -> None:
        ch = self._channels.get(channel_id)
        if ch is None:
            return
        msg = OutboundMessage(
            channel_id=channel_id,
            thread_id=thread_id,
            content=result_text,
            metadata={"source": "cron"},
        )
        ch.send(msg)

    def _pick_agent(self, msg: InboundMessage) -> Any:
        """metadata['agent_id'] > default_agent_id > registry.pick_for_intent."""
        explicit = msg.metadata.get("agent_id")
        if isinstance(explicit, str) and explicit:
            if self._agent_registry.has(explicit):
                return self._agent_registry.get(explicit)
            raise ChannelRoutingError(
                f"metadata agent_id not found: {explicit!r}",
            )

        if self._default_agent_id and self._agent_registry.has(
            self._default_agent_id,
        ):
            return self._agent_registry.get(self._default_agent_id)

        intent = ParsedIntent(
            raw=msg.content,
            intent_type="task",
            normalized_goal=msg.content,
        )
        picked = self._agent_registry.pick_for_intent(intent)
        if picked is None:
            raise ChannelRoutingError(
                f"no agent matches intent from channel={msg.channel_id!r} "
                f"(goal={msg.content[:60]!r}) · "
                "set default_agent_id or msg.metadata['agent_id']",
            )
        return picked

    def _plan_and_run(self, agent: Any, intent: ParsedIntent) -> str:
        plan_kwargs: dict[str, Any] = {
            "allowed_skills": agent.allowed_skill_union(),
        }
        try:
            from runtime.execution.agents.loader import compose_runtime_soul

            runtime_soul = compose_runtime_soul(agent)
        except (ImportError, TypeError, AttributeError, OSError):  # noqa: BLE001
            runtime_soul = agent.soul
        if runtime_soul:
            plan_kwargs["soul"] = runtime_soul

        # Planner can fail in three ways that all surface as the same
        # symptom from a channel adapter (Discord/Telegram/etc. just
        # see "(task success · no output content)"):
        #   1. PlannerError — no rule matched, no fallback skill set
        #   2. LLMPlanner returns empty nodes (parsing glitch / model
        #      bailed) — graph runs zero steps, trajectory is empty
        #   3. Any unexpected exception during plan() — bubbles up
        #      and the channel renders nothing
        # Catch all three here and produce a self-explaining reply
        # rather than a silent dead bubble.
        try:
            try:
                graph = self._stack.planner.plan(intent, **plan_kwargs)
            except TypeError:
                graph = self._stack.planner.plan(intent)
        except Exception as exc:  # noqa: BLE001 — bottom of channel pipeline
            return f"（无法为该请求生成执行计划：{type(exc).__name__}: {exc}）"

        if not graph.nodes:
            return (
                "（计划器未生成任何执行步骤 · 可能是技能集合里没有匹配项 · "
                f"原始请求："
                f"{intent.normalized_goal[:120]}）"
            )

        arm_id = ArmId("unassigned")
        if len(agent.arms) > 0:
            first = next(iter(agent.arms))
            arm_id = ArmId(str(first.arm_id))

        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(
                tokens=self._budget_tokens,
                usd=self._budget_usd,
            ),
        )
        traj = self._stack.runtime.run(
            graph,
            budget=budget,
            caller=f"channels/{agent.agent_id}",
            arm_id=arm_id,
        )
        return self._assistant_text_from_trajectory(traj)

    @staticmethod
    def _assistant_text_from_trajectory(traj: Any) -> str:
        """Render a trajectory as a channel-bound reply.

        Two passes, in priority order:

        1. **Content pass** — if any step's output carries a real
           ``content``/``text`` payload (the shape skills should
           emit when they have user-facing text), prefer that.
           Multiple content payloads concatenate with newlines.
        2. **Step-summary fallback** — when nothing carried text,
           render every step as a one-line ``✓ skill(args) → out``
           summary using the same formatter the OpenAI gateway
           uses. This is what users actually want when a skill ran
           but emitted only structured output (e.g. a search that
           returned a list of result dicts).

        Only when both passes produce nothing — typically because
        the trajectory has zero steps — do we fall back to the
        ``(task … · no output content)`` sentinel.
        """
        content_lines: list[str] = []
        for step in traj.steps:
            out = step.result.output
            if out is None:
                continue
            if isinstance(out, dict):
                if "content" in out and isinstance(out["content"], str):
                    content_lines.append(out["content"])
                elif "text" in out and isinstance(out["text"], str):
                    content_lines.append(out["text"])
            elif isinstance(out, str) and out.strip():
                content_lines.append(out)

        if content_lines:
            return "\n".join(content_lines)

        # No text payload — render step-by-step summaries so the
        # user can at least see what ran.
        summary_lines: list[str] = []
        for step in traj.steps:
            try:
                summary_lines.append(_summarize_step_for_stream(step))
            except (AttributeError, TypeError):  # noqa: BLE001 — formatter is best-effort
                continue

        if summary_lines:
            effective_ok = bool(traj.outcome.success) and all(
                _step_effective_success(s) for s in traj.steps
            )
            tag = "OK" if effective_ok else "FAILED"
            return "\n".join(
                summary_lines + ["", f"[{tag} · {len(traj.steps)} step(s)]"],
            )

        status = "success" if traj.outcome.success else "failed"
        return f"(task {status} · no output content)"


# ═══════════════════════════════════════════════════════════
# Constitution gate audit
# ═══════════════════════════════════════════════════════════


def _audit_channel_for_gate(channel: Channel, *, strict: bool = False) -> None:
    """Scan a channel's ``send`` implementation · warn (or raise)
    if it appears to bypass the constitution gate.

    Parameters
    ----------
    strict:
        When True, raise ``RuntimeError`` instead of logging a
        warning when ``send()`` does not call ``safe_send`` /
        ``check_outbound``. Source-not-inspectable still only
        warns (can't verify, not the developer's fault).

    Detection is conservative:

    * Inspect ``send``'s source code
    * Look for a call to ``safe_send`` or ``check_outbound``
    * Neither present → warn (or raise if strict)
    * Either present → trust the author (they know what they're
      doing · maybe the check happens in a helper)

    False negatives are possible (someone could do the check in
    a differently-named wrapper); false positives are limited to
    "you passed the audit but actually don't gate". In advisory
    mode the warning is non-blocking · registration succeeds
    regardless. In strict mode registration is refused.
    """
    import inspect
    import logging as _logging

    _logger = _logging.getLogger("runtime.adapters.channels.constitution_audit")

    send_method = getattr(type(channel), "send", None)
    if send_method is None:
        return  # abstract base used directly; let the __init__ fail

    try:
        src = inspect.getsource(send_method)
    except (OSError, TypeError):
        # Couldn't introspect via the standard path. This often happens
        # when the class lives in a module imported through an editable
        # install whose linecache points to a stale/doubled path (e.g.
        # pytest collecting from a non-canonical CWD on Windows). Try
        # one more time by resolving the source file through the module
        # the method belongs to — that path is set at import time and
        # is more reliable than ``__code__.co_filename``.
        src = None
        try:
            mod = sys.modules.get(getattr(send_method, "__module__", ""))
            mod_file = getattr(mod, "__file__", None)
            if mod_file:
                with open(mod_file, encoding="utf-8") as fh:
                    mod_src = fh.read()
                # Re-parse with linecache to pick up the source for the
                # specific method via its qualname.
                import re as _re_fb

                qual = getattr(send_method, "__qualname__", "")
                # ``qualname`` is e.g. ``MyAdapter.send``; the class
                # block is what we want to scope the search to.
                cls_name = qual.rsplit(".", 1)[0] if "." in qual else ""
                if cls_name:
                    # Find the class block start, then scan for ``def send``
                    # within it. This is intentionally permissive — false
                    # positives in scoping just reduce the audit coverage.
                    cls_match = _re_fb.search(
                        rf"^class\s+{_re_fb.escape(cls_name)}\b",
                        mod_src,
                        _re_fb.MULTILINE,
                    )
                    if cls_match:
                        # Capture from class declaration to next dedented
                        # ``class``/``def`` at column 0, or end of file.
                        tail = mod_src[cls_match.start() :]
                        next_top = _re_fb.search(
                            r"\n(?:class|def)\s",
                            tail[1:],
                        )
                        cls_body = tail if next_top is None else tail[: next_top.end()]
                        src = cls_body
        except (OSError, UnicodeDecodeError, AttributeError):  # noqa: BLE001 — fallback path; original audit warning fires below
            src = None
        if src is None:
            _logger.warning(
                "Channel adapter %s.send source not inspectable · "
                "cannot verify constitution-gate usage. See "
                "docs/constitution.md · consider calling self.safe_send(msg).",
                type(channel).__name__,
            )
            return

    # Signal: a CALL to safe_send / check_outbound · not just a
    # mention. Require the ``(`` · so a comment like
    # ``# DELIBERATELY omits self.safe_send`` doesn't fool the
    # audit. Also strip Python comments so even a "safe_send(" in
    # a comment doesn't count. This is a simple heuristic · an AST
    # parse would be more correct but adds a dependency for a
    # soft-warning path that's fine being fuzzy.
    import re as _re

    # Strip line comments · keep docstrings (they'd be above the
    # body, don't matter for call detection).
    src_no_comments = _re.sub(r"#.*$", "", src, flags=_re.MULTILINE)
    gate_markers = (
        "safe_send(",
        "check_outbound(",
        "@constitutional_gate",
    )
    if any(marker in src_no_comments for marker in gate_markers):
        return

    msg = (
        f"Channel adapter {type(channel).__name__}.send does NOT appear to call "
        "self.safe_send() or constitution.check_outbound() before "
        "sending. Outbound messages from this channel will NOT be "
        "PII-scrubbed or secret-blocked. See docs/constitution.md "
        "(PRIV-2, PRIV-4). Standard template:\n"
        "    def send(self, msg):\n"
        "        verdict = self.safe_send(msg)\n"
        "        if verdict.action == 'block':\n"
        "            return\n"
        "        self._platform_api(verdict.sanitized)"
    )
    _logger.warning("%s", type(channel).__name__)
    _logger.warning("%s", msg)
    if strict:
        raise RuntimeError(
            f"{type(channel).__name__}.send bypasses the constitution gate "
            f"(no safe_send/check_outbound call found). "
            f"Registration refused in strict_gate mode. "
            f"See docs/constitution.md · PRIV-2, PRIV-4."
        )
