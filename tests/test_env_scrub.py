"""Credential scrubbing for unconfined subprocess environments.

``scrub_credential_env`` is the single source of truth shared by the
model-driven exec path (execution.suckers.write_skills) and the
user-facing terminal WebSocket (sensing.gateway.terminal_router). These
tests lock the guarantee that a spawned child never inherits secrets
while still keeping the benign vars real commands need.
"""

from __future__ import annotations

import pytest

from runtime.safety.env_scrub import scrub_credential_env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Deterministic environment: strip anything the host set that would
    # confuse the assertions, then plant a known mix.
    for name in list(__import__("os").environ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/tester")  # lint: allow-user-path
    monkeypatch.setenv("LANG", "en_US.UTF-8")


def test_drops_credential_named_vars(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "abc/def")
    env = scrub_credential_env()
    for leaked in ("ANTHROPIC_API_KEY", "GH_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
        assert leaked not in env


def test_keeps_benign_vars():
    env = scrub_credential_env()
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/tester"  # lint: allow-user-path
    assert env["LANG"] == "en_US.UTF-8"


def test_ssh_auth_sock_is_kept(monkeypatch):
    # Contains "SOCK" but no hint substring; the KEEP allowlist protects
    # it because ssh-agent forwarding needs it and it carries no secret.
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-XXXX/agent.123")
    env = scrub_credential_env()
    assert env["SSH_AUTH_SOCK"] == "/tmp/ssh-XXXX/agent.123"


def test_overlay_wins_over_heuristics(monkeypatch):
    # An explicit overlay is applied verbatim even if its name looks like
    # a credential — explicit caller intent beats the name heuristic.
    env = scrub_credential_env({"TERM": "xterm-256color", "MY_TOKEN": "explicit"})
    assert env["TERM"] == "xterm-256color"
    assert env["MY_TOKEN"] == "explicit"


def test_drops_secret_valued_var(monkeypatch):
    # Benign-looking NAME but the VALUE is a detectable secret (JWT) →
    # dropped by the Redactor value check.
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abc123signature456"
    monkeypatch.setenv("HARMLESS_CONFIG", jwt)
    env = scrub_credential_env()
    assert "HARMLESS_CONFIG" not in env

