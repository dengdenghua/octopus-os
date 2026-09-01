"""SQLite schema for the agent trace store."""

from __future__ import annotations

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    tenant_id   TEXT,
    owner_actor_id TEXT,
    thread_id   TEXT NOT NULL,
    turn_id     TEXT,
    agent_id    TEXT,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trace_messages_thread ON messages(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_messages_turn ON messages(turn_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_messages_agent ON messages(agent_id, id);

CREATE TABLE IF NOT EXISTS agui_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    tenant_id   TEXT,
    owner_actor_id TEXT,
    thread_id   TEXT,
    turn_id     TEXT,
    task_id     TEXT,
    agent_id    TEXT,
    item_id     TEXT,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trace_events_thread ON agui_events(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_events_turn ON agui_events(turn_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_events_task ON agui_events(task_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_events_agent ON agui_events(agent_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_events_type ON agui_events(event_type, id);

CREATE TABLE IF NOT EXISTS approvals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at   TEXT NOT NULL,
    decided_at     TEXT,
    tenant_id      TEXT,
    owner_actor_id  TEXT,
    thread_id      TEXT,
    turn_id        TEXT,
    task_id        TEXT,
    agent_id       TEXT,
    tool_name      TEXT NOT NULL,
    tool_call_id   TEXT NOT NULL,
    args_preview   TEXT NOT NULL DEFAULT '',
    decision       TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    metadata       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trace_approvals_thread ON approvals(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_approvals_tool_call ON approvals(tool_call_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_approvals_decision ON approvals(decision, id);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    tenant_id         TEXT,
    owner_actor_id    TEXT,
    task_id           TEXT NOT NULL,
    thread_id         TEXT,
    turn_id           TEXT,
    agent_id          TEXT,
    checkpoint_type   TEXT NOT NULL,
    iteration         INTEGER NOT NULL DEFAULT 0,
    summary           TEXT NOT NULL DEFAULT '',
    state             TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trace_checkpoints_task ON agent_checkpoints(task_id, iteration, id);
CREATE INDEX IF NOT EXISTS idx_trace_checkpoints_thread ON agent_checkpoints(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_checkpoints_type ON agent_checkpoints(checkpoint_type, id);

CREATE TABLE IF NOT EXISTS llm_token_usage (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    tenant_id        TEXT,
    owner_actor_id    TEXT,
    task_id          TEXT,
    thread_id        TEXT,
    turn_id          TEXT,
    agent_id         TEXT,
    iteration        INTEGER NOT NULL DEFAULT 0,
    model            TEXT NOT NULL DEFAULT '',
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    thinking_tokens  INTEGER NOT NULL DEFAULT 0,
    cached_tokens    INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0,
    is_local         INTEGER NOT NULL DEFAULT 0,
    metadata         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trace_tokens_task ON llm_token_usage(task_id, iteration, id);
CREATE INDEX IF NOT EXISTS idx_trace_tokens_thread ON llm_token_usage(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_tokens_agent ON llm_token_usage(agent_id, id);

CREATE TABLE IF NOT EXISTS resume_requests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    tenant_id         TEXT,
    owner_actor_id    TEXT,
    thread_id         TEXT NOT NULL,
    checkpoint_id     INTEGER NOT NULL,
    task_id           TEXT,
    status            TEXT NOT NULL,
    intent            TEXT NOT NULL DEFAULT '{}',
    confirmed_at      TEXT,
    consumed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_trace_resume_requests_thread ON resume_requests(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_resume_requests_checkpoint ON resume_requests(checkpoint_id, id);
CREATE INDEX IF NOT EXISTS idx_trace_resume_requests_status ON resume_requests(status, id);
"""
