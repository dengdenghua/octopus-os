from __future__ import annotations

import json

from runtime.adapters.mcp_client.trust import reset_trust_store_for_tests
from runtime.cli import main


def test_mcp_add_list_remove(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    reset_trust_store_for_tests()

    assert (
        main(
            [
                "--no-color",
                "mcp",
                "add",
                "fs",
                "--env",
                "ROOT=.",
                "--",
                "npx",
                "-y",
                "@modelcontextprotocol/server-filesystem",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--no-color", "mcp", "list", "--output-format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mcpServers"][0]["name"] == "fs"
    assert payload["mcpServers"][0]["command"] == "npx"
    assert payload["mcpServers"][0]["trusted"] is False

    assert main(["--no-color", "mcp", "remove", "fs"]) == 0
    assert "Removed MCP server fs." in capsys.readouterr().out


def test_mcp_trust_and_revoke(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    reset_trust_store_for_tests()

    assert main(["--no-color", "mcp", "trust", "fs", "--tool", "read_file"]) == 0
    capsys.readouterr()
    assert main(["--no-color", "mcp", "list", "--output-format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["mcpServers"] == []

    assert main(["--no-color", "mcp", "add", "fs", "--", "node", "server.js"]) == 0
    capsys.readouterr()
    assert main(["--no-color", "mcp", "list", "--output-format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mcpServers"][0]["trusted"] is True

    assert main(["--no-color", "mcp", "revoke", "fs"]) == 0
    capsys.readouterr()
    assert main(["--no-color", "mcp", "list", "--output-format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mcpServers"][0]["trusted"] is False
