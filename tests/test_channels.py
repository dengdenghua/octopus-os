"""Implementation note."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from runtime.adapters.channels import (
    Channel,
    ChannelManager,
    ChannelRoutingError,
    InboundMessage,
    OutboundMessage,
    SlackChannel,
    SlackSignatureError,
    ThreadConversationStore,
)
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.agents import AgentRegistry, make_general_agent


class _FakeExecutor:
    journal = None


def _rt():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


# ═══════════════════════════════════════════════════════════
# ThreadConversationStore
# ═══════════════════════════════════════════════════════════


class TestStore:
    def test_get_returns_none_before_create(self):
        s = ThreadConversationStore()
        assert s.get("slack", "C1:1.0") is None

    def test_get_or_create_generates_id(self):
        s = ThreadConversationStore()
        cid = s.get_or_create("slack", "C1:1.0")
        assert isinstance(cid, str) and len(cid) == 32  # uuid4 hex
        # Implementation note.
        assert s.get_or_create("slack", "C1:1.0") == cid

    def test_different_threads_get_different_ids(self):
        s = ThreadConversationStore()
        a = s.get_or_create("slack", "C1:1.0")
        b = s.get_or_create("slack", "C2:2.0")
        assert a != b

    def test_same_thread_different_channel_different_ids(self):
        s = ThreadConversationStore()
        a = s.get_or_create("slack", "X")
        b = s.get_or_create("feishu", "X")
        assert a != b

    def test_empty_inputs_rejected(self):
        s = ThreadConversationStore()
        with pytest.raises(ValueError):
            s.get_or_create("", "x")
        with pytest.raises(ValueError):
            s.get_or_create("x", "")

    def test_persist_roundtrip(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        s = ThreadConversationStore(path=path)
        cid = s.get_or_create("slack", "C1:1")
        assert path.exists()
        # Implementation note.
        s2 = ThreadConversationStore(path=path)
        assert s2.get("slack", "C1:1") == cid
        assert len(s2) == 1

    def test_delete_creates_tombstone(self, tmp_path: Path):
        path = tmp_path / "store.jsonl"
        s = ThreadConversationStore(path=path)
        cid = s.get_or_create("slack", "T")
        assert s.delete("slack", "T") is True
        assert s.delete("slack", "T") is False  # Implementation note.
        # Implementation note.
        s2 = ThreadConversationStore(path=path)
        assert s2.get("slack", "T") is None
        # Implementation note.
        assert s2.get_or_create("slack", "T") != cid


# ═══════════════════════════════════════════════════════════
# Channel ABC
# ═══════════════════════════════════════════════════════════


class TestChannelABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Channel()  # type: ignore[abstract]


# ═══════════════════════════════════════════════════════════
# Fake channel + Stack
# ═══════════════════════════════════════════════════════════


class _FakeChannel(Channel):
    def __init__(self, channel_id: str = "fake"):
        self.channel_id = channel_id
        self.started = False
        self.stopped = False
        self.sent: list[OutboundMessage] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


def _build_stack(tmp_path: Path):
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import JSONLJournal
    from runtime.platform.models import BudgetSpec, SkillId
    from runtime.safety.auth import TrustEngine

    journal = JSONLJournal(tmp_path / "events.jsonl")
    registry = SkillRegistry()
    register_all(registry)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    planner = StaticPlanner(
        rules=[
            Rule(
                name="default",
                intent_types=["task"],
                skill_sequence=[SkillId("list_cwd")],
            )
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        fallback_skill=SkillId("list_cwd"),
    )

    class _S:
        pass

    s = _S()
    s.planner = planner
    s.runtime = runtime
    s.registry = registry
    s.journal = journal
    return s


@pytest.fixture
def stack(tmp_path):
    return _build_stack(tmp_path)


@pytest.fixture
def agent_reg():
    reg = AgentRegistry()
    reg.register(make_general_agent(_rt()))
    return reg


# ═══════════════════════════════════════════════════════════
# ChannelManager
# ═══════════════════════════════════════════════════════════


class TestManagerLifecycle:
    def test_register_and_listing(self, stack, agent_reg):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        m.register(_FakeChannel("a"))
        m.register(_FakeChannel("b"))
        assert m.has("a") and m.has("b")
        assert m.channel_ids() == ["a", "b"]
        assert len(m) == 2

    def test_duplicate_channel_rejected(self, stack, agent_reg):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
        )
        m.register(_FakeChannel("x"))
        with pytest.raises(ValueError, match="duplicate"):
            m.register(_FakeChannel("x"))

    def test_empty_channel_id_rejected(self, stack, agent_reg):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
        )
        with pytest.raises(ValueError, match="channel_id"):
            m.register(_FakeChannel(""))

    def test_start_stop_proxies(self, stack, agent_reg):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        ch = _FakeChannel("x")
        m.register(ch)
        m.start_all()
        assert ch.started
        m.stop_all()
        assert ch.stopped


class TestManagerProcessInbound:
    def test_happy_path_default_agent(self, stack, agent_reg, tmp_path):
        store = ThreadConversationStore(path=tmp_path / "s.jsonl")
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
            store=store,
        )
        ch = _FakeChannel("slack")
        m.register(ch)

        out = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="C:1.0",
                content="list files",
            )
        )
        assert isinstance(out, OutboundMessage)
        assert out.channel_id == "slack"
        assert out.thread_id == "C:1.0"
        assert out.metadata["agent_id"] == "general"
        assert out.metadata["conversation_id"]
        assert len(ch.sent) == 1
        assert ch.sent[0].content

    def test_conversation_id_stable_across_messages(
        self,
        stack,
        agent_reg,
        tmp_path,
    ):
        store = ThreadConversationStore(path=tmp_path / "s.jsonl")
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
            store=store,
        )
        m.register(_FakeChannel("slack"))
        out1 = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="a",
            )
        )
        out2 = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="b",
            )
        )
        # Implementation note.
        assert out1.metadata["conversation_id"] == out2.metadata["conversation_id"]

    def test_metadata_agent_overrides_default(
        self,
        stack,
        agent_reg,
        tmp_path,
    ):
        from runtime.execution.agents import make_coder_agent

        agent_reg.register(make_coder_agent(_rt()))
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        m.register(_FakeChannel("slack"))
        # Implementation note.
        out = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="read a file",
                metadata={"agent_id": "coder"},
            )
        )
        assert out.metadata["agent_id"] == "coder"

    def test_metadata_unknown_agent_raises(
        self,
        stack,
        agent_reg,
    ):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        m.register(_FakeChannel("slack"))
        with pytest.raises(ChannelRoutingError, match="agent_id"):
            m.process_inbound(
                InboundMessage(
                    channel_id="slack",
                    thread_id="T",
                    content="x",
                    metadata={"agent_id": "ghost"},
                )
            )

    def test_unknown_channel_raises(self, stack, agent_reg):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        with pytest.raises(ChannelRoutingError, match="unknown"):
            m.process_inbound(
                InboundMessage(
                    channel_id="nope",
                    thread_id="T",
                    content="x",
                )
            )

    def test_empty_content_rejected(self, stack, agent_reg):
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        m.register(_FakeChannel("slack"))
        with pytest.raises(ChannelRoutingError, match="empty"):
            m.process_inbound(
                InboundMessage(
                    channel_id="slack",
                    thread_id="T",
                    content="   ",
                )
            )

    def test_no_default_no_match_raises(self, stack):
        """Implementation note."""
        empty_reg = AgentRegistry()
        m = ChannelManager(
            stack=stack,
            agent_registry=empty_reg,
        )
        m.register(_FakeChannel("slack"))
        with pytest.raises(ChannelRoutingError, match="no agent matches"):
            m.process_inbound(
                InboundMessage(
                    channel_id="slack",
                    thread_id="T",
                    content="something unrelated to any affinity",
                )
            )

    def test_planner_failure_yields_explanatory_reply(
        self,
        stack,
        agent_reg,
    ):
        """A PlannerError must NOT escape · channel users see a
        readable fallback rather than an unhandled exception."""

        class _BoomPlanner:
            def plan(self, intent, **kwargs):
                from runtime.core.cerebrum.planner import PlannerError

                raise PlannerError("simulated · no rule matched")

        stack.planner = _BoomPlanner()
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        ch = _FakeChannel("slack")
        m.register(ch)
        out = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="something",
            )
        )
        assert "无法为该请求生成执行计划" in out.content
        assert "PlannerError" in out.content
        # The fallback must still flow through the channel.send path.
        assert len(ch.sent) == 1

    def test_empty_graph_yields_explanatory_reply(
        self,
        stack,
        agent_reg,
    ):
        """LLM planner returning an empty node list shouldn't surface
        as a silent ``(task success · no output content)`` reply."""

        class _FakeEmptyGraph:
            # Duck-type a TaskGraph for the ``not graph.nodes`` check.
            # The real model rejects empty nodes via pydantic, but at
            # runtime an LLM planner can still hand back a malformed
            # object (or a future planner could relax the constraint).
            nodes: list = []

        class _EmptyGraphPlanner:
            def plan(self, intent, **kwargs):
                return _FakeEmptyGraph()

        stack.planner = _EmptyGraphPlanner()
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        ch = _FakeChannel("slack")
        m.register(ch)
        out = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="hello",
            )
        )
        assert "计划器未生成任何执行步骤" in out.content
        assert "no output content" not in out.content

    def test_steps_without_text_payload_render_summary(
        self,
        stack,
        agent_reg,
    ):
        """When skills produce only structured output (no
        ``content``/``text`` field), the reply should still show what
        ran — not the cryptic ``(task success · no output content)``."""
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        ch = _FakeChannel("slack")
        m.register(ch)

        # ``list_cwd`` returns a dict shaped like {"items": [...]} —
        # no "content" key — so the old code emitted a JSON dump or
        # the no-output sentinel. New code emits a step summary line.
        out = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="list files",
            )
        )
        # Either the skill happens to expose a text payload (then
        # it rides the content-pass) or we fall back to the summary
        # pass, which always carries a status tag.
        assert "no output content" not in out.content


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _slack_sig(secret: str, timestamp: str, body: bytes) -> str:
    base = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


class TestSlackSignature:
    def test_valid_signature_passes(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        body = b'{"type":"ping"}'
        ts = "1_700_000_000".replace("_", "")
        sig = _slack_sig("s", ts, body)
        ch.verify_signature(body=body, timestamp=ts, signature=sig, now=int(ts))

    def test_body_tamper_detected(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        ts = "1700000000"
        original = b'{"type":"ping"}'
        sig = _slack_sig("s", ts, original)
        # Implementation note.
        with pytest.raises(SlackSignatureError, match="signature"):
            ch.verify_signature(
                body=b'{"type":"evil"}',
                timestamp=ts,
                signature=sig,
                now=int(ts),
            )

    def test_expired_rejected(self):
        ch = SlackChannel(
            bot_token="t",
            signing_secret="s",
            max_age_seconds=300,
        )
        ts = "1700000000"
        body = b"{}"
        sig = _slack_sig("s", ts, body)
        # Implementation note.
        with pytest.raises(SlackSignatureError, match="too old"):
            ch.verify_signature(
                body=body,
                timestamp=ts,
                signature=sig,
                now=int(ts) + 600,
            )

    def test_bad_timestamp_format(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        with pytest.raises(SlackSignatureError, match="bad timestamp"):
            ch.verify_signature(
                body=b"",
                timestamp="not-a-number",
                signature="v0=...",
            )

    def test_missing_headers(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        with pytest.raises(SlackSignatureError):
            ch.verify_signature(body=b"", timestamp="", signature="v0=abc")
        with pytest.raises(SlackSignatureError):
            ch.verify_signature(body=b"", timestamp="1", signature="")


# ═══════════════════════════════════════════════════════════
# SlackChannel · parse_event
# ═══════════════════════════════════════════════════════════


class TestSlackParseEvent:
    def test_message_event(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        msg = ch.parse_event(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "C01",
                    "ts": "1700000000.000100",
                    "user": "U42",
                    "text": "hello agent",
                },
            }
        )
        assert msg is not None
        assert msg.channel_id == "slack"
        assert msg.thread_id == "C01:1700000000.000100"
        assert msg.sender_id == "U42"
        assert msg.content == "hello agent"
        assert msg.metadata["platform"] == "slack"

    def test_threaded_reply_uses_thread_ts(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        msg = ch.parse_event(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "C01",
                    "ts": "1700000005.000200",
                    "thread_ts": "1700000000.000100",
                    "user": "U42",
                    "text": "reply in thread",
                },
            }
        )
        assert msg is not None
        # Implementation note.
        assert msg.thread_id == "C01:1700000000.000100"

    def test_bot_message_filtered(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        msg = ch.parse_event(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "C",
                    "ts": "1",
                    "user": "U",
                    "text": "hi",
                    "bot_id": "B01",
                },
            }
        )
        assert msg is None

    def test_url_verification_returns_none(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        assert (
            ch.parse_event(
                {
                    "type": "url_verification",
                    "challenge": "abc",
                }
            )
            is None
        )

    def test_empty_text_returns_none(self):
        ch = SlackChannel(bot_token="t", signing_secret="s")
        msg = ch.parse_event(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "C",
                    "ts": "1",
                    "user": "U",
                    "text": "   ",
                },
            }
        )
        assert msg is None


# ═══════════════════════════════════════════════════════════
# SlackChannel · send
# ═══════════════════════════════════════════════════════════


class _FakeHttpResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _FakeHttpClient:
    def __init__(self, resp: _FakeHttpResp | None = None):
        self.resp = resp or _FakeHttpResp()
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, **_kw):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.resp


class TestSlackSend:
    def test_post_to_slack_api(self):
        http = _FakeHttpClient()
        ch = SlackChannel(
            bot_token="xoxb-test",
            signing_secret="s",
            http_client=http,
        )
        ch.send(
            OutboundMessage(
                channel_id="slack",
                thread_id="C01:1700000000.000100",
                content="hi",
            )
        )
        assert len(http.calls) == 1
        call = http.calls[0]
        assert call["url"].endswith("/chat.postMessage")
        assert call["json"]["channel"] == "C01"
        assert call["json"]["text"] == "hi"
        assert call["json"]["thread_ts"] == "1700000000.000100"
        assert call["headers"]["Authorization"] == "Bearer xoxb-test"

    def test_slack_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(body={"ok": False, "error": "channel_not_found"}))
        ch = SlackChannel(bot_token="t", signing_secret="s", http_client=http)
        with pytest.raises(RuntimeError, match="channel_not_found"):
            ch.send(
                OutboundMessage(
                    channel_id="slack",
                    thread_id="C:1",
                    content="x",
                )
            )

    def test_http_error_raises(self):
        http = _FakeHttpClient(_FakeHttpResp(status_code=500, body={"ok": False}))
        ch = SlackChannel(bot_token="t", signing_secret="s", http_client=http)
        with pytest.raises(RuntimeError, match="HTTP 500"):
            ch.send(
                OutboundMessage(
                    channel_id="slack",
                    thread_id="C:1",
                    content="x",
                )
            )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_slack_inbound_to_reply(self, stack, agent_reg):
        http = _FakeHttpClient()
        slack = SlackChannel(
            bot_token="t",
            signing_secret="s",
            http_client=http,
        )
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        m.register(slack)

        # Implementation note.
        event_payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C_test",
                "ts": "1700000000.000100",
                "user": "U_user",
                "text": "list files please",
            },
        }
        msg = slack.parse_event(event_payload)
        assert msg is not None
        out = m.process_inbound(msg)
        assert out.metadata["agent_id"] == "general"
        assert len(http.calls) == 1
        post_body = http.calls[0]["json"]
        assert post_body["channel"] == "C_test"
        assert post_body["thread_ts"] == "1700000000.000100"

    def test_journal_tagged_with_conv_and_agent(self, stack, agent_reg):
        """Implementation note."""
        slack = SlackChannel(
            bot_token="t",
            signing_secret="s",
            http_client=_FakeHttpClient(),
        )
        m = ChannelManager(
            stack=stack,
            agent_registry=agent_reg,
            default_agent_id="general",
        )
        m.register(slack)
        out = m.process_inbound(
            InboundMessage(
                channel_id="slack",
                thread_id="T",
                content="list",
            )
        )
        # Implementation note.
        events = stack.journal.read_all()
        assert events
        # Implementation note.
        agent_events = [e for e in events if e.agent_id == "general"]
        conv_events = [e for e in events if e.conversation_id == out.metadata["conversation_id"]]
        assert agent_events, "no events tagged agent_id=general"
        assert conv_events, "no events with conversation_id"
