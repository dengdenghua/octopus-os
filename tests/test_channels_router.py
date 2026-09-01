"""Implementation note."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.adapters.channels import (  # noqa: E402
    Channel,
    ChannelManager,
    OutboundMessage,
    SlackChannel,
)
from runtime.core.graph_runtime import GraphRuntime  # noqa: E402
from runtime.execution.agents import AgentRegistry, make_general_agent  # noqa: E402
from runtime.safety.auth import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.channels_router import create_channels_router  # noqa: E402

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeExecutor:
    journal = None


def _rt():
    return GraphRuntime(executor=_FakeExecutor(), journal=None)


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


class _FakeHttpResp:
    def __init__(self, body=None):
        self.status_code = 200
        self._body = body or {"ok": True}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _FakeHttpClient:
    def __init__(self):
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, **_kw):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeHttpResp()


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _build_app(
    tmp_path: Path,
    *,
    bot_token="xoxb-test",
    signing_secret="sec",
    identity_store=None,
    require_auth=False,
    state_path: Any = None,
) -> tuple[FastAPI, ChannelManager, _FakeHttpClient]:
    """Implementation note."""
    stack = _build_stack(tmp_path)
    reg = AgentRegistry()
    reg.register(make_general_agent(_rt()))

    http = _FakeHttpClient()
    slack = SlackChannel(
        bot_token=bot_token,
        signing_secret=signing_secret,
        http_client=http,
    )
    m = ChannelManager(
        stack=stack,
        agent_registry=reg,
        default_agent_id="general",
    )
    m.register(slack)

    app = FastAPI()
    app.include_router(
        create_channels_router(
            manager=m,
            identity_store=identity_store,
            require_auth=require_auth,
            state_path="" if state_path is None else state_path,
        )
    )
    return app, m, http


# ═══════════════════════════════════════════════════════════
# GET /api/channels
# ═══════════════════════════════════════════════════════════


class TestListChannels:
    def test_lists_slack(self, tmp_path: Path):
        """Implementation note."""
        app, _, _ = _build_app(tmp_path)
        r = TestClient(app).get("/api/channels")
        assert r.status_code == 200
        data = r.json()

        # Implementation note.
        slack_entries = [d for d in data if d["platform"] == "slack"]
        assert len(slack_entries) == 1
        assert slack_entries[0]["channel_id"] == "slack"
        assert slack_entries[0]["type"] == "SlackChannel"
        assert slack_entries[0]["connected"] is True
        assert slack_entries[0]["display_name"] == "Slack"

        # Implementation note.
        platforms = {d["platform"] for d in data}
        assert {"wechat", "dingtalk", "feishu", "telegram", "discord"} <= platforms

        # Implementation note.
        for d in data:
            assert set(d["metrics"].keys()) == {
                "pairings_count",
                "group_count",
                "pending_count",
            }
            assert "assigned_agent_id" in d


class TestChannelAssignment:
    """Implementation note."""

    def test_initial_assignment_is_none(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path)
        c = TestClient(app)
        r = c.get("/api/channels/slack/assistant")
        assert r.status_code == 200
        assert r.json()["agent_id"] is None

    def test_assign_then_readback(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path)
        c = TestClient(app)
        r = c.post(
            "/api/channels/slack/assistant",
            json={"agent_id": "coder"},
        )
        assert r.status_code == 200
        assert r.json()["agent_id"] == "coder"

        # Implementation note.
        r2 = c.get("/api/channels/slack/assistant")
        assert r2.json()["agent_id"] == "coder"

        # Implementation note.
        r3 = c.get("/api/channels")
        slack = next(d for d in r3.json() if d["platform"] == "slack")
        assert slack["assigned_agent_id"] == "coder"

    def test_assign_rejects_empty(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path)
        c = TestClient(app)
        r = c.post("/api/channels/slack/assistant", json={"agent_id": ""})
        assert r.status_code == 400
        r2 = c.post("/api/channels/slack/assistant", json={})
        assert r2.status_code == 400

    def test_delete_assignment(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path)
        c = TestClient(app)
        c.post(
            "/api/channels/slack/assistant",
            json={"agent_id": "coder"},
        )
        r = c.delete("/api/channels/slack/assistant")
        assert r.status_code == 200
        assert r.json()["dropped"] == "coder"
        # Re-read → None
        r2 = c.get("/api/channels/slack/assistant")
        assert r2.json()["agent_id"] is None

    def test_assign_unregistered_platform_still_works(
        self,
        tmp_path: Path,
    ):
        """Implementation note."""
        app, _, _ = _build_app(tmp_path)
        c = TestClient(app)
        r = c.post(
            "/api/channels/wechat/assistant",
            json={"agent_id": "general"},
        )
        assert r.status_code == 200
        r2 = c.get("/api/channels")
        wechat = next(d for d in r2.json() if d["platform"] == "wechat")
        assert wechat["assigned_agent_id"] == "general"
        assert wechat["connected"] is False


class TestPairings:
    """Implementation note."""

    def test_inbound_registers_user_pairing(self, tmp_path: Path):
        """Implementation note."""
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        c = TestClient(app)

        # Initial · 0
        r = c.get("/api/channels")
        slack = next(d for d in r.json() if d["platform"] == "slack")
        assert slack["metrics"]["pairings_count"] == 0

        # Implementation note.
        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "user": "U1234ALICE",
                    "channel": "D_DIRECT_MSG",  # D = direct message
                    "text": "hi",
                    "ts": "1234.5678",
                },
            }
        ).encode()
        ts = str(int(time.time()))
        sig = _slack_sig("sec", ts, body)
        r2 = c.post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": ts,
                "x-slack-signature": sig,
            },
        )
        assert r2.status_code == 200
        assert r2.json().get("dispatched") is True

        # Now · 1 user paired, 0 groups
        r3 = c.get("/api/channels")
        slack = next(d for d in r3.json() if d["platform"] == "slack")
        assert slack["metrics"]["pairings_count"] == 1
        assert slack["metrics"]["group_count"] == 0

    def test_duplicate_sender_counts_once(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        c = TestClient(app)
        for _ in range(3):  # Implementation note.
            body = json.dumps(
                {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "user": "U_SAME",
                        "channel": "D_X",
                        "text": "hi",
                        "ts": "1234.5678",
                    },
                }
            ).encode()
            ts = str(int(time.time()))
            sig = _slack_sig("sec", ts, body)
            c.post(
                "/api/channels/slack/inbound",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-slack-request-timestamp": ts,
                    "x-slack-signature": sig,
                },
            )
        r = c.get("/api/channels")
        slack = next(d for d in r.json() if d["platform"] == "slack")
        assert slack["metrics"]["pairings_count"] == 1

    def test_group_channel_counts_as_group(self, tmp_path: Path):
        """Implementation note."""
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        c = TestClient(app)
        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "user": "U_BOB",
                    "channel": "C_PUBLIC_CHAN",
                    "text": "hi",
                    "ts": "1234.5678",
                },
            }
        ).encode()
        ts = str(int(time.time()))
        sig = _slack_sig("sec", ts, body)
        c.post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": ts,
                "x-slack-signature": sig,
            },
        )
        r = c.get("/api/channels")
        slack = next(d for d in r.json() if d["platform"] == "slack")
        assert slack["metrics"]["group_count"] == 1
        assert slack["metrics"]["pairings_count"] == 1  # Implementation note.

    def test_state_persists_across_router_rebuild(
        self,
        tmp_path: Path,
    ):
        """Implementation note."""
        state_file = tmp_path / "channel_state.json"

        # Implementation note.
        app1, mgr1, _ = _build_app(
            tmp_path,
            signing_secret="sec",
            state_path=state_file,
        )
        c1 = TestClient(app1)
        c1.post(
            "/api/channels/slack/assistant",
            json={"agent_id": "coder"},
        )
        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "user": "U_ALICE",
                    "channel": "C_PUB",
                    "text": "hi",
                    "ts": "1234.5678",
                },
            }
        ).encode()
        ts = str(int(time.time()))
        c1.post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": ts,
                "x-slack-signature": _slack_sig("sec", ts, body),
            },
        )

        # Implementation note.
        assert state_file.exists()
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        assert saved["assignments"]["slack"] == "coder"
        assert "U_ALICE" in saved["users"]["slack"]
        # Implementation note.
        # Implementation note.
        assert any(t.startswith("C_PUB") for t in saved["groups"]["slack"])

        # Implementation note.
        app2, mgr2, _ = _build_app(
            tmp_path,
            signing_secret="sec",
            state_path=state_file,
        )
        assert mgr2 is not mgr1
        c2 = TestClient(app2)
        r = c2.get("/api/channels/slack/assistant")
        assert r.json()["agent_id"] == "coder"
        r2 = c2.get("/api/channels")
        slack = next(d for d in r2.json() if d["platform"] == "slack")
        assert slack["assigned_agent_id"] == "coder"
        assert slack["metrics"]["pairings_count"] == 1
        assert slack["metrics"]["group_count"] == 1

    def test_corrupt_state_file_falls_back_silently(
        self,
        tmp_path: Path,
    ):
        """Implementation note."""
        state_file = tmp_path / "busted.json"
        state_file.write_text("{not json", encoding="utf-8")
        app, _, _ = _build_app(tmp_path, state_path=state_file)
        c = TestClient(app)
        r = c.get("/api/channels")
        assert r.status_code == 200

    def test_unknown_schema_version_ignored(self, tmp_path: Path):
        """Implementation note."""
        state_file = tmp_path / "future.json"
        state_file.write_text(
            json.dumps({"version": 999, "assignments": {"slack": "x"}}),
            encoding="utf-8",
        )
        app, _, _ = _build_app(tmp_path, state_path=state_file)
        r = TestClient(app).get("/api/channels/slack/assistant")
        # Implementation note.
        assert r.json()["agent_id"] is None


class TestCredentials:
    """Implementation note."""

    def _build_empty(self, tmp_path: Path, state_path: Path):
        """Implementation note."""
        from runtime.adapters.channels import ChannelManager
        from runtime.execution.agents import (
            AgentRegistry,
            make_general_agent,
        )

        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        reg.register(make_general_agent(_rt()))
        m = ChannelManager(
            stack=stack,
            agent_registry=reg,
            default_agent_id="general",
        )  # Implementation note.
        app = FastAPI()
        app.include_router(
            create_channels_router(
                manager=m,
                state_path=state_path,
            )
        )
        return app, m

    def test_post_slack_creds_registers_channel(
        self,
        tmp_path: Path,
    ):
        state_file = tmp_path / "s.json"
        app, m = self._build_empty(tmp_path, state_file)
        c = TestClient(app)

        # Implementation note.
        r0 = c.get("/api/channels")
        slack0 = next(d for d in r0.json() if d["platform"] == "slack")
        assert slack0["connected"] is False

        # Implementation note.
        r = c.post(
            "/api/channels/credentials/slack",
            json={
                "bot_token": "xoxb-test-1234",
                "signing_secret": "secretXX",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["connected"] is True
        # Implementation note.
        assert m.has("slack")

        # Implementation note.
        r2 = c.get("/api/channels")
        slack = next(d for d in r2.json() if d["platform"] == "slack")
        assert slack["connected"] is True

    def test_get_credentials_is_masked(self, tmp_path: Path):
        state_file = tmp_path / "s.json"
        app, _ = self._build_empty(tmp_path, state_file)
        c = TestClient(app)
        c.post(
            "/api/channels/credentials/slack",
            json={
                "bot_token": "xoxb-supersecret-1234",
                "signing_secret": "s3cr3tZZZZ",
            },
        )
        r = c.get("/api/channels/credentials")
        creds = r.json()["credentials"]
        assert "slack" in creds
        # Implementation note.
        assert creds["slack"]["bot_token"].endswith("1234")
        assert "●" in creds["slack"]["bot_token"]
        assert creds["slack"]["signing_secret"].endswith("ZZZZ")
        assert "supersecret" not in creds["slack"]["bot_token"]

    def test_unknown_platform_rejected(self, tmp_path: Path):
        state_file = tmp_path / "s.json"
        app, _ = self._build_empty(tmp_path, state_file)
        r = TestClient(app).post(
            "/api/channels/credentials/mars",
            json={"bot_token": "x", "signing_secret": "y"},
        )
        assert r.status_code == 404

    def test_missing_required_field_rejected(self, tmp_path: Path):
        state_file = tmp_path / "s.json"
        app, _ = self._build_empty(tmp_path, state_file)
        r = TestClient(app).post(
            "/api/channels/credentials/slack",
            json={"bot_token": "only"},  # Implementation note.
        )
        assert r.status_code == 400

    def test_not_yet_supported_platform_defensive(self, tmp_path: Path):
        """Implementation note."""
        from runtime.sensing.gateway.channels_router import (
            _construct_channel,
            _UnsupportedPlatformError,
        )

        with pytest.raises(_UnsupportedPlatformError):
            _construct_channel("mars_messenger", {"bot_token": "x"})

    def test_delete_credentials_unregisters(self, tmp_path: Path):
        state_file = tmp_path / "s.json"
        app, m = self._build_empty(tmp_path, state_file)
        c = TestClient(app)
        c.post(
            "/api/channels/credentials/slack",
            json={"bot_token": "xoxb-a", "signing_secret": "b"},
        )
        assert m.has("slack")
        r = c.delete("/api/channels/credentials/slack")
        assert r.status_code == 200
        assert r.json()["dropped"] is True
        assert not m.has("slack")

    def test_credentials_persist_across_restart(
        self,
        tmp_path: Path,
    ):
        state_file = tmp_path / "s.json"
        app1, m1 = self._build_empty(tmp_path, state_file)
        TestClient(app1).post(
            "/api/channels/credentials/slack",
            json={
                "bot_token": "xoxb-persist-end",
                "signing_secret": "sigZZZZ",
            },
        )
        assert m1.has("slack")

        # Implementation note.
        app2, m2 = self._build_empty(tmp_path, state_file)
        assert m2 is not m1
        # Implementation note.
        assert m2.has("slack")

    def test_dingtalk_creds_register(self, tmp_path: Path):
        app, m = self._build_empty(tmp_path, tmp_path / "s.json")
        r = TestClient(app).post(
            "/api/channels/credentials/dingtalk",
            json={
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=TOK",
                "secret": "SEC_ABCDEFG",
            },
        )
        assert r.status_code == 200, r.text
        assert m.has("dingtalk")

    def test_dingtalk_creds_optional_secret_omitted(self, tmp_path: Path):
        app, m = self._build_empty(tmp_path, tmp_path / "s.json")
        r = TestClient(app).post(
            "/api/channels/credentials/dingtalk",
            json={
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=T",
            },
        )
        assert r.status_code == 200, r.text
        assert m.has("dingtalk")

    def test_telegram_creds_register(self, tmp_path: Path):
        app, m = self._build_empty(tmp_path, tmp_path / "s.json")
        r = TestClient(app).post(
            "/api/channels/credentials/telegram",
            json={"bot_token": "123:ABCxyz"},
        )
        assert r.status_code == 200, r.text
        assert m.has("telegram")

    def test_feishu_requires_three_fields(self, tmp_path: Path):
        app, _ = self._build_empty(tmp_path, tmp_path / "s.json")
        c = TestClient(app)
        # Implementation note.
        r = c.post(
            "/api/channels/credentials/feishu",
            json={"app_id": "cli_x", "app_secret": "s"},
        )
        assert r.status_code == 400

        r2 = c.post(
            "/api/channels/credentials/feishu",
            json={
                "app_id": "cli_x",
                "app_secret": "s",
                "verification_token": "vt",
            },
        )
        assert r2.status_code == 200

    def test_discord_requires_bot_token_and_public_key(
        self,
        tmp_path: Path,
    ):
        app, _ = self._build_empty(tmp_path, tmp_path / "s.json")
        # Implementation note.
        r = TestClient(app).post(
            "/api/channels/credentials/discord",
            json={
                "bot_token": "xxx",
                "public_key": "00" * 32,  # 32 bytes hex = ed25519 pub
            },
        )
        assert r.status_code == 200, r.text

    def test_wechat_credentials_require_bot_token(self, tmp_path: Path):
        """Implementation note."""
        app, _ = self._build_empty(tmp_path, tmp_path / "s.json")
        r = TestClient(app).post(
            "/api/channels/credentials/wechat",
            json={"any": "field"},
        )
        assert r.status_code == 400
        assert "bot_token required" in r.text

    def test_wechat_with_bot_token_registers(self, tmp_path: Path):
        app, m = self._build_empty(tmp_path, tmp_path / "s.json")
        r = TestClient(app).post(
            "/api/channels/credentials/wechat",
            json={"bot_token": "tok-xyz-1234"},
        )
        assert r.status_code == 200, r.text
        assert m.has("weixin_bot")
        # Implementation note.
        r2 = TestClient(app).get("/api/channels")
        wechat = next(d for d in r2.json() if d["platform"] == "wechat")
        assert wechat["connected"] is True


class TestWeChatQR:
    """Implementation note."""

    def _build_with_mock_qr(self, tmp_path: Path, scripted_responses: list):
        """Implementation note."""
        from runtime.adapters.channels import ChannelManager
        from runtime.adapters.channels import weixin_bot as _wxmod
        from runtime.execution.agents import (
            AgentRegistry,
            make_general_agent,
        )

        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        reg.register(make_general_agent(_rt()))
        m = ChannelManager(
            stack=stack,
            agent_registry=reg,
            default_agent_id="general",
        )

        # Mock _request on WeixinBotChannel to avoid real HTTP
        responses = list(scripted_responses)

        def _mock_request(self, method, path, **kw):  # noqa: ANN001, ARG001
            if not responses:
                raise RuntimeError("mock responses exhausted")
            return responses.pop(0)

        original = _wxmod.WeixinBotChannel._request
        _wxmod.WeixinBotChannel._request = _mock_request  # type: ignore[method-assign]

        app = FastAPI()
        app.include_router(
            create_channels_router(
                manager=m,
                state_path=tmp_path / "s.json",
            )
        )

        # Provide teardown via pytest: we can use pytest's finalizer but
        # since we're not in a fixture, restore lazily after test call.
        # Use closure to restore on app request handler cleanup.
        app.state._wx_original_request = original  # type: ignore[attr-defined]
        return app, m

    def test_qr_start_and_confirm_flow(self, tmp_path: Path):
        """Implementation note."""
        from runtime.adapters.channels import weixin_bot as _wxmod

        responses = [
            # Implementation note.
            {
                "qrcode": "QR_ABCDEF",
                "qrcode_img_content": "iVBORw0KGgo=",  # 1×1 PNG base64
            },
            # poll #1 → pending
            {"status": "pending"},
            # poll #2 → confirmed
            {
                "status": "confirmed",
                "bot_token": "tok-ilink-wechat-FINAL",
                "baseurl": "https://ilinkai.weixin.qq.com",
            },
        ]
        app, m = self._build_with_mock_qr(tmp_path, responses)
        try:
            c = TestClient(app)
            r1 = c.post("/api/channels/wechat/qr/start")
            assert r1.status_code == 200
            qr = r1.json()
            assert qr["qrcode"] == "QR_ABCDEF"
            assert qr["qrcode_img_content"].startswith("data:image/png;base64,")

            r2 = c.post(
                "/api/channels/wechat/qr/poll",
                json={"qrcode": qr["qrcode"]},
            )
            assert r2.status_code == 200
            assert r2.json()["status"] == "pending"
            assert r2.json()["confirmed"] is False

            r3 = c.post(
                "/api/channels/wechat/qr/poll",
                json={"qrcode": qr["qrcode"]},
            )
            assert r3.status_code == 200
            assert r3.json()["status"] == "confirmed"
            assert r3.json()["confirmed"] is True
            # Implementation note.
            assert m.has("weixin_bot")
        finally:
            # Implementation note.
            _wxmod.WeixinBotChannel._request = app.state._wx_original_request

    def test_qr_poll_unknown_qrcode_returns_404(self, tmp_path: Path):
        from runtime.adapters.channels import weixin_bot as _wxmod

        app, _ = self._build_with_mock_qr(tmp_path, [])
        try:
            r = TestClient(app).post(
                "/api/channels/wechat/qr/poll",
                json={"qrcode": "NEVER_STARTED"},
            )
            assert r.status_code == 404
        finally:
            _wxmod.WeixinBotChannel._request = app.state._wx_original_request


class TestCredentialEncryption:
    """Implementation note."""

    def test_file_on_disk_is_encrypted_envelope(self, tmp_path: Path):
        """Implementation note."""
        from runtime.adapters.channels import ChannelManager
        from runtime.execution.agents import (
            AgentRegistry,
            make_general_agent,
        )

        state_file = tmp_path / "s.json"
        creds_file = tmp_path / "s.credentials.json"

        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        reg.register(make_general_agent(_rt()))
        m = ChannelManager(
            stack=stack,
            agent_registry=reg,
            default_agent_id="general",
        )
        app = FastAPI()
        app.include_router(
            create_channels_router(
                manager=m,
                state_path=state_file,
            )
        )
        c = TestClient(app)
        c.post(
            "/api/channels/credentials/slack",
            json={
                "bot_token": "xoxb-SUPERSECRET-1234",
                "signing_secret": "sign-SECRET-xxxxxxxxxx",
            },
        )
        raw = creds_file.read_text(encoding="utf-8")
        # Implementation note.
        assert "xoxb-SUPERSECRET" not in raw
        assert "sign-SECRET" not in raw
        data = json.loads(raw)
        assert data["_enc"] == "aes-gcm"
        assert "nonce" in data and "ciphertext" in data

        # Implementation note.
        r = c.get("/api/channels/credentials").json()["credentials"]
        assert "slack" in r
        assert r["slack"]["bot_token"].endswith("1234")

    def test_round_trip_across_restart_with_encryption(
        self,
        tmp_path: Path,
    ):
        """Implementation note."""
        from runtime.adapters.channels import ChannelManager
        from runtime.execution.agents import (
            AgentRegistry,
            make_general_agent,
        )

        state_file = tmp_path / "s.json"

        def _build():
            stack = _build_stack(tmp_path)
            reg = AgentRegistry()
            reg.register(make_general_agent(_rt()))
            mgr = ChannelManager(
                stack=stack,
                agent_registry=reg,
                default_agent_id="general",
            )
            app = FastAPI()
            app.include_router(
                create_channels_router(
                    manager=mgr,
                    state_path=state_file,
                )
            )
            return app, mgr

        app1, m1 = _build()
        TestClient(app1).post(
            "/api/channels/credentials/slack",
            json={
                "bot_token": "xoxb-PERSIST-ROUND-TRIP",
                "signing_secret": "sig-rt-9999",
            },
        )
        assert m1.has("slack")

        # Implementation note.
        app2, m2 = _build()
        assert m2 is not m1
        assert m2.has("slack")
        # Implementation note.
        r = TestClient(app2).get("/api/channels/credentials").json()
        assert r["credentials"]["slack"]["bot_token"].endswith("TRIP")

    def test_legacy_plaintext_file_still_readable(
        self,
        tmp_path: Path,
    ):
        """Implementation note."""
        from runtime.adapters.channels import ChannelManager
        from runtime.execution.agents import (
            AgentRegistry,
            make_general_agent,
        )

        state_file = tmp_path / "s.json"
        creds_file = tmp_path / "s.credentials.json"
        # Implementation note.
        creds_file.parent.mkdir(parents=True, exist_ok=True)
        creds_file.write_text(
            json.dumps(
                {
                    "slack": {
                        "bot_token": "xoxb-LEGACY-PLAIN",
                        "signing_secret": "legacy-sig",
                    },
                }
            ),
            encoding="utf-8",
        )

        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        reg.register(make_general_agent(_rt()))
        m = ChannelManager(
            stack=stack,
            agent_registry=reg,
            default_agent_id="general",
        )
        app = FastAPI()
        app.include_router(
            create_channels_router(
                manager=m,
                state_path=state_file,
            )
        )
        # Implementation note.
        assert m.has("slack")

    def test_env_key_takes_precedence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Implementation note."""
        import base64
        import secrets as _secrets

        from runtime.adapters.channels import ChannelManager
        from runtime.execution.agents import (
            AgentRegistry,
            make_general_agent,
        )

        state_file = tmp_path / "s.json"
        good_key = base64.b64encode(_secrets.token_bytes(32)).decode("ascii")
        other_key = base64.b64encode(_secrets.token_bytes(32)).decode("ascii")

        def _build():
            stack = _build_stack(tmp_path)
            reg = AgentRegistry()
            reg.register(make_general_agent(_rt()))
            mgr = ChannelManager(
                stack=stack,
                agent_registry=reg,
                default_agent_id="general",
            )
            app = FastAPI()
            app.include_router(
                create_channels_router(
                    manager=mgr,
                    state_path=state_file,
                )
            )
            return app, mgr

        # Implementation note.
        monkeypatch.setenv("ECHO_CREDENTIAL_KEY", good_key)
        app1, m1 = _build()
        TestClient(app1).post(
            "/api/channels/credentials/slack",
            json={"bot_token": "t1", "signing_secret": "s1"},
        )
        assert m1.has("slack")

        # Implementation note.
        monkeypatch.setenv("ECHO_CREDENTIAL_KEY", other_key)
        app2, m2 = _build()
        assert not m2.has("slack")  # Implementation note.

    def test_pairings_endpoint_lists_users_and_groups(
        self,
        tmp_path: Path,
    ):
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        c = TestClient(app)
        # Implementation note.
        for user in ("U_ALICE", "U_BOB"):
            body = json.dumps(
                {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "user": user,
                        "channel": "D_DM",
                        "text": "hi",
                        "ts": "1234.5678",
                    },
                }
            ).encode()
            ts = str(int(time.time()))
            sig = _slack_sig("sec", ts, body)
            c.post(
                "/api/channels/slack/inbound",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-slack-request-timestamp": ts,
                    "x-slack-signature": sig,
                },
            )
        r = c.get("/api/channels/slack/pairings")
        data = r.json()
        assert set(data["users"]) == {"U_ALICE", "U_BOB"}
        assert data["metrics"]["pairings_count"] == 2


# ═══════════════════════════════════════════════════════════
# POST /api/channels/{channel}/inbound
# ═══════════════════════════════════════════════════════════


class TestInboundSlack:
    def test_url_verification_challenge(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        body = json.dumps(
            {
                "type": "url_verification",
                "challenge": "abc123",
            }
        ).encode()
        ts = str(int(time.time()))
        sig = _slack_sig("sec", ts, body)
        r = TestClient(app).post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"challenge": "abc123"}

    def test_message_event_dispatches(self, tmp_path: Path):
        app, _, http = _build_app(tmp_path, signing_secret="sec")
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C_test",
                "ts": "1700000000.000100",
                "user": "U_user",
                "text": "list the files",
            },
        }
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = _slack_sig("sec", ts, body)

        r = TestClient(app).post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["dispatched"] is True
        assert data["agent_id"] == "general"
        assert data["conversation_id"]
        # Implementation note.
        assert len(http.calls) == 1
        assert http.calls[0]["json"]["channel"] == "C_test"

    def test_bot_message_ignored(self, tmp_path: Path):
        """Implementation note."""
        app, _, http = _build_app(tmp_path, signing_secret="sec")
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C",
                "ts": "1",
                "text": "from bot",
                "bot_id": "B01",
            },
        }
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = _slack_sig("sec", ts, body)
        r = TestClient(app).post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "dispatched": False}
        # Implementation note.
        assert http.calls == []

    def test_bad_signature_401(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path, signing_secret="right")
        body = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        # Implementation note.
        bad_sig = _slack_sig("wrong", ts, body)
        r = TestClient(app).post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": bad_sig,
            },
        )
        assert r.status_code == 401

    def test_expired_timestamp_401(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        body = b'{"type":"event_callback"}'
        old_ts = str(int(time.time()) - 3600)  # Implementation note.
        sig = _slack_sig("sec", old_ts, body)
        r = TestClient(app).post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": old_ts,
                "X-Slack-Signature": sig,
            },
        )
        assert r.status_code == 401

    def test_bad_json_400(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path, signing_secret="sec")
        body = b"{not json"
        ts = str(int(time.time()))
        sig = _slack_sig("sec", ts, body)
        r = TestClient(app).post(
            "/api/channels/slack/inbound",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )
        assert r.status_code == 400

    def test_unknown_channel_404(self, tmp_path: Path):
        app, _, _ = _build_app(tmp_path)
        r = TestClient(app).post(
            "/api/channels/ghost/inbound",
            content=b"{}",
        )
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _PollingOnlyChannel(Channel):
    """Implementation note."""

    channel_id = "polling_only"

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, msg: OutboundMessage):
        pass


class TestWebhookNotSupported:
    def test_polling_only_channel_returns_400(self, tmp_path: Path):
        stack = _build_stack(tmp_path)
        reg = AgentRegistry()
        reg.register(make_general_agent(_rt()))
        m = ChannelManager(
            stack=stack,
            agent_registry=reg,
            default_agent_id="general",
        )
        m.register(_PollingOnlyChannel())

        app = FastAPI()
        app.include_router(create_channels_router(manager=m))
        r = TestClient(app).post(
            "/api/channels/polling_only/inbound",
            content=b"{}",
        )
        assert r.status_code == 400
        assert "not accept webhook" in r.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAuth:
    def test_require_auth_blocks_anon_list(self, tmp_path: Path):
        store = IdentityStore()
        app, _, _ = _build_app(
            tmp_path,
            identity_store=store,
            require_auth=True,
        )
        r = TestClient(app).get("/api/channels")
        assert r.status_code == 401

    def test_api_key_lets_through(self, tmp_path: Path):
        store = IdentityStore()
        store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
        app, _, _ = _build_app(
            tmp_path,
            identity_store=store,
            require_auth=True,
        )
        r = TestClient(app).get(
            "/api/channels",
            headers={"Authorization": "Bearer sk-alice"},
        )
        assert r.status_code == 200
