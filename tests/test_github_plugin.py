"""Tests for the bundled ``github`` plugin (基于 gh CLI 重写自 codex github 插件)。

覆盖:
  1. 插件可发现、可加载(bundled)
  2. 注册 4 个技能进 SkillRegistry
  3. pr_summary / list_prs / issue_triage 的 gh JSON 解析(网络用 monkeypatch 桩掉)
  4. create_pr 写操作安全门:无 confirm 拒绝,有 confirm 才真正构建命令
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from runtime.platform.plugins.bundled.github import GitHubPlugin
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub

PLUGIN_ID = "github"


def test_bundled_github_is_discoverable_and_loadable() -> None:
    hub = PluginHub()
    matches = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(matches) == 1
    assert matches[0]["bundled"] is True
    assert hub.load(PLUGIN_ID) is not None


def test_github_registers_four_skills() -> None:
    plugin = GitHubPlugin()
    registered: list[str] = []
    plugin.ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir="",
        manifest=None,
        skill_registry=MagicMock(register=lambda s, verify_tests=False: registered.append(s.name)),
    )
    plugin.register_skills()

    assert set(registered) == {
        "github.pr_summary",
        "github.list_prs",
        "github.issue_triage",
        "github.create_pr",
    }


def test_pr_summary_parses_gh_json(monkeypatch) -> None:
    canned = {
        "title": "Add feature X",
        "state": "OPEN",
        "author": {"login": "alice"},
        "additions": 12,
        "deletions": 3,
        "comments": [{"body": "x"}],
        "reviews": [],
        "files": [{"path": "a.py"}, {"path": "b.py"}],
        "baseRefName": "main",
        "headRefName": "feat/x",
        "url": "https://github.com/o/r/pull/7",
        "createdAt": "2026-01-01",
        "updatedAt": "2026-01-02",
        "body": "long body",
    }
    monkeypatch.setattr(
        "runtime.platform.plugins.bundled.github._gh",
        lambda args, timeout=30: {"ok": True, "raw": json.dumps(canned)},
    )
    out = GitHubPlugin()._pr_summary(pr_number=7, repo="o/r")
    assert out["ok"]
    s = out["summary"]
    assert s["title"] == "Add feature X"
    assert s["author"] == "alice"
    assert s["num_files"] == 2
    assert s["num_comments"] == 1


def test_list_prs_parses_gh_json(monkeypatch) -> None:
    canned = [
        {
            "number": 1,
            "title": "t1",
            "state": "OPEN",
            "author": {"login": "a"},
            "url": "u1",
            "createdAt": "c",
            "baseRefName": "main",
            "headRefName": "h",
        },
        {
            "number": 2,
            "title": "t2",
            "state": "OPEN",
            "author": {"login": "b"},
            "url": "u2",
            "createdAt": "c",
            "baseRefName": "main",
            "headRefName": "h",
        },
    ]
    monkeypatch.setattr(
        "runtime.platform.plugins.bundled.github._gh",
        lambda args, timeout=30: {"ok": True, "raw": json.dumps(canned)},
    )
    out = GitHubPlugin()._list_prs(repo="o/r", state="open")
    assert out["ok"]
    assert len(out["prs"]) == 2


def test_issue_triage_parses_gh_json(monkeypatch) -> None:
    canned = {
        "title": "Bug in Y",
        "state": "OPEN",
        "author": {"login": "bob"},
        "body": "repro",
        "comments": [{"body": "1"}],
        "url": "https://github.com/o/r/issues/5",
        "createdAt": "c",
        "updatedAt": "u",
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "carol"}],
    }
    monkeypatch.setattr(
        "runtime.platform.plugins.bundled.github._gh",
        lambda args, timeout=30: {"ok": True, "raw": json.dumps(canned)},
    )
    out = GitHubPlugin()._issue_triage(issue_number=5, repo="o/r")
    assert out["ok"]
    assert out["issue"]["title"] == "Bug in Y"
    assert out["issue"]["labels"] == ["bug"]
    assert out["issue"]["assignees"] == ["carol"]


def test_create_pr_requires_confirm() -> None:
    out = GitHubPlugin()._create_pr(title="t", head="h")
    assert out["ok"] is False
    assert "confirm" in out["error"]


def test_create_pr_with_confirm_builds_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_gh(args: list[str], timeout: int = 30) -> dict:
        captured["args"] = args
        return {"ok": True, "raw": "https://github.com/o/r/pull/9"}

    monkeypatch.setattr("runtime.platform.plugins.bundled.github._gh", fake_gh)
    out = GitHubPlugin()._create_pr(title="t", head="h", base="main", repo="o/r", confirm=True)
    assert out["ok"]
    assert captured["args"][:2] == ["pr", "create"]
    assert "t" in captured["args"] and "h" in captured["args"]

