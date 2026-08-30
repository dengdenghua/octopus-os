"""Implementation note."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime.memory.journal import (
    InMemoryJournal,
    JSONLJournal,
    ReflexHitEvent,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReflexHitEvent:
    def test_write_and_read(self):
        j = InMemoryJournal()
        j.write_reflex_hit(
            rule_id="ping",
            kind="regex",
            latency_ms=0.5,
            intent_goal="ping",
            response={"reply": "pong"},
        )
        events = j.read_all()
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, ReflexHitEvent)
        assert ev.rule_id == "ping"
        assert ev.kind == "regex"
        assert ev.response == {"reply": "pong"}

    def test_jsonl_roundtrip(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        j1 = JSONLJournal(path)
        j1.write_reflex_hit(
            rule_id="version",
            kind="regex",
            latency_ms=0.3,
            intent_goal="version",
            response={"version": "0.1"},
        )

        # Implementation note.
        j2 = JSONLJournal(path)
        events = j2.read_all()
        assert len(events) == 1
        assert isinstance(events[0], ReflexHitEvent)
        assert events[0].rule_id == "version"

    def test_read_by_type_filters(self):
        j = InMemoryJournal()
        j.write_reflex_hit(
            rule_id="x",
            kind="regex",
            latency_ms=0.1,
            intent_goal="x",
            response={"reply": "y"},
        )
        hits = j.read_by_type("reflex_hit")
        assert len(hits) == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunGoalReflex:
    def test_ping_skips_planner_and_journals_reflex_hit(self, tmp_path: Path, capsys):
        """Implementation note."""
        from runtime.cli import run_goal

        journal_path = tmp_path / "events.jsonl"
        rc = run_goal(
            "ping",
            intent_type="task",
            journal_file=str(journal_path),
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "[REFLEX]" in out
        assert "rule=ping" in out

        # Implementation note.
        j = JSONLJournal(journal_path)
        event_types = {e.event_type for e in j.read_all()}
        assert "reflex_hit" in event_types
        assert "step" not in event_types
        assert "trajectory" not in event_types


# ═══════════════════════════════════════════════════════════
# CLI · run_goal_from_config
# ═══════════════════════════════════════════════════════════


def _write_cfg(tmp_path: Path) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(
        "planner:\n"
        "  type: llm\n"
        "  model: mock/r\n"
        '  mock_response: \'{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{"path":"."}}]}\'\n'
        "budget:\n"
        "  max_tokens: 5000\n"
        "  max_usd: 0.05\n",
        encoding="utf-8",
    )
    return path


class TestConfigDrivenReflex:
    def test_ping_via_config_hits_reflex(self, tmp_path: Path, capsys):
        cfg = _write_cfg(tmp_path)
        from runtime.cli import run_goal_from_config

        rc = run_goal_from_config(
            goal="ping",
            config_path=cfg,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "[REFLEX]" in out
        # Implementation note.
        assert "[PLAN]" not in out


# ═══════════════════════════════════════════════════════════
# CLI · loop
# ═══════════════════════════════════════════════════════════


class TestLoopReflex:
    def test_ping_loop_counts_as_success(self, tmp_path: Path, capsys):
        cfg = _write_cfg(tmp_path)
        journal = tmp_path / "events.jsonl"

        from runtime.cli import run_loop

        rc = run_loop(
            goal="ping",
            config_path=cfg,
            journal_path=journal,
            iterations=2,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        # Implementation note.
        assert out.count("reflex") >= 2
        assert "2/2 succeeded" in out

        # Implementation note.
        j = JSONLJournal(journal)
        types = [e.event_type for e in j.read_all()]
        assert types.count("reflex_hit") == 2
        assert "trajectory" not in types


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config  # noqa: E402
from runtime.sensing.gateway import create_openai_router  # noqa: E402


@pytest.fixture
def gateway_with_reflex():
    from runtime.cli import _build_reflex_router

    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/g",
            mock_response=json.dumps(
                {
                    "reasoning": "plan",
                    "nodes": [{"skill": "list_cwd", "args": {}}],
                }
            ),
        ),
    )
    stack = build_from_config(cfg)
    app = FastAPI()
    app.include_router(
        create_openai_router(
            stack,
            reflex_router=_build_reflex_router(),
        )
    )
    return stack, TestClient(app)


class TestGatewayReflex:
    def test_ping_returns_reflex_completion(self, gateway_with_reflex):
        stack, client = gateway_with_reflex
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "echo-agent",
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        assert r.status_code == 200
        data = r.json()
        # Implementation note.
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "pong" in data["choices"][0]["message"]["content"]
        # Implementation note.
        assert data["usage"]["total_tokens"] == 0
        # Implementation note.
        assert data["echo"]["reflex"] is True
        assert data["echo"]["rule_id"] == "ping_diagnostic"

        # Implementation note.
        hits = stack.journal.read_by_type("reflex_hit")
        assert len(hits) == 1

    def test_non_ping_goes_to_planner(self, gateway_with_reflex):
        stack, client = gateway_with_reflex
        r = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "list current directory"}],
            },
        )
        assert r.status_code == 200
        data = r.json()
        # Implementation note.
        assert data["echo"].get("reflex") is not True
        # Implementation note.
        assert stack.journal.read_by_type("trajectory")

    def test_reflex_stream_frames(self, gateway_with_reflex):
        stack, client = gateway_with_reflex
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "stream": True,
                "messages": [{"role": "user", "content": "ping"}],
            },
        ) as r:
            assert r.status_code == 200
            body = ""
            for chunk in r.iter_text():
                body += chunk
                if "[DONE]" in chunk:
                    break
        # Implementation note.
        assert "[DONE]" in body
        assert "pong" in body
        # Implementation note.
        for line in body.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                parsed = json.loads(line[len("data: ") :])
                assert parsed["object"] == "chat.completion.chunk"
