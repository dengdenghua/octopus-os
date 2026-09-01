"""GitHub 插件 — 基于本地 gh CLI 重写自 OpenAI codex github 插件(MIT)。

参考重写说明(详见 SKILL.md / LICENSE.txt):
- 原 codex github 插件依赖 OpenAI 托管的 connector(.app.json 中的
  connector_7686...),包内无本地实现。本插件用本机 ``gh`` CLI 重新实现其
  PR 摘要 / issue 分诊 / PR 列表 / 创建 PR 等核心能力,包装为 Echo 的
  ModulePlugin。
- MIT 许可证沿用 OpenAI 原作者署名;connector 实现由 Echo 完全重写,
  不依赖任何外部托管服务。
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
from typing import Any

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.plugin_base import ModulePlugin

_LOGGER = logging.getLogger(__name__)

PLUGIN_NAME = "github"
_TRUSTED_SOURCE = "plugin://github"

# gh 调用统一超时(秒)
_GH_TIMEOUT = 30


def _gh(args: list[str], timeout: int = _GH_TIMEOUT) -> dict[str, Any]:
    """运行 ``gh`` CLI,返回结构化结果。

    成功:  {"ok": True,  "raw": <stdout 文本>}
    失败:  {"ok": False, "error": <原因>}
    """
    if shutil.which("gh") is None:
        return {"ok": False, "error": "未找到 gh CLI,请先安装并登录(gh auth login)"}
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"gh 命令超时({timeout}s)"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip() or "gh 返回非零退出码"}
    return {"ok": True, "raw": proc.stdout}


def _parse(raw: str) -> dict[str, Any]:
    try:
        return {"ok": True, "data": json.loads(raw)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "gh 输出不是合法 JSON", "raw": raw[:500]}


class GitHubPlugin(ModulePlugin):
    name = PLUGIN_NAME
    display_name = "GitHub"
    version = "0.1.0"
    description = (
        "基于 gh CLI 的 GitHub 操作插件(PR 摘要 / issue 分诊 / PR 列表 / 创建 PR)。"
        "参考重写自 OpenAI codex github 插件(MIT)。"
    )
    author = "Echo (ported from OpenAI codex github plugin, MIT)"

    # ── 技能注册 ────────────────────────────────────────────────
    def register_skills(self) -> None:
        if self.ctx is None:
            return
        skills = [
            Skill(
                name="github.pr_summary",
                description=(
                    "查看并摘要某个 GitHub PR 的元信息:标题 / 状态 / 增删行数 / 评论数 / "
                    "评审状态 / 改动文件数 / 作者 / 链接 / 创建与更新时间。参数:pr_number 必填"
                    "(整数或 '#123' 形式),repo 可选('owner/name',缺省用当前仓库)。适用于"
                    "'看下这个 PR'、'PR 123 改了啥'、'这个 PR 评审过了吗'。"
                ),
                summary="摘要 GitHub PR(pr_number 必填)",
                affinity=["github", "pr", "code-review", "git"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._pr_summary,
            ),
            Skill(
                name="github.list_prs",
                description=(
                    "列出 GitHub 仓库的 PR。参数:state 可选(open/closed/merged/merged,默认 open),"
                    "repo 可选('owner/name',缺省当前仓库)。返回编号/标题/状态/作者/链接/分支。"
                    "适用于'最近有哪些 PR'、'列一下 open 的 PR'。"
                ),
                summary="列出 GitHub PR(state/repo 可选)",
                affinity=["github", "pr", "git"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._list_prs,
            ),
            Skill(
                name="github.issue_triage",
                description=(
                    "查看并分诊某个 GitHub issue:标题 / 状态 / 正文 / 评论 / 标签 / 负责人 / 链接。"
                    "参数:issue_number 必填,repo 可选。适用于'这个 issue 啥情况'、'分诊一下 #45'。"
                ),
                summary="分诊 GitHub issue(issue_number 必填)",
                affinity=["github", "issue", "triage", "git"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._issue_triage,
            ),
            Skill(
                name="github.create_pr",
                description=(
                    "创建 GitHub PR(写操作)。参数:title 必填,head 必填(源分支),body 可选,"
                    "base 可选(目标分支,默认仓库默认分支),repo 可选。**默认拒绝**——必须显式传 "
                    "confirm=true 才会真正提交;不带 confirm 时仅做参数校验与预览,不改动任何东西。"
                    "适用于'帮我把 feat/xxx 提个 PR'。"
                ),
                summary="创建 GitHub PR(需 confirm=true)",
                affinity=["github", "pr", "git", "write"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._create_pr,
            ),
        ]
        for skill in skills:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(skill)

    # ── 工具实现(gh CLI) ───────────────────────────────────────
    def _pr_summary(self, **kwargs: Any) -> dict[str, Any]:
        pr = str(kwargs.get("pr_number", "")).lstrip("#").strip()
        repo = (kwargs.get("repo") or "").strip()
        if not pr:
            return {"ok": False, "error": "需要 pr_number 参数(整数或 '#123')"}
        args = [
            "pr",
            "view",
            pr,
            "--json",
            "title,state,additions,deletions,comments,reviews,files,"
            "author,url,createdAt,updatedAt,baseRefName,headRefName,body",
        ]
        if repo:
            args += ["--repo", repo]
        res = _gh(args)
        if not res["ok"]:
            return res
        parsed = _parse(res["raw"])
        if not parsed["ok"]:
            return parsed
        d = parsed["data"]
        return {
            "ok": True,
            "summary": {
                "title": d.get("title"),
                "state": d.get("state"),
                "author": (d.get("author") or {}).get("login"),
                "additions": d.get("additions"),
                "deletions": d.get("deletions"),
                "num_comments": len(d.get("comments") or []),
                "num_reviews": len(d.get("reviews") or []),
                "num_files": len(d.get("files") or []),
                "base": d.get("baseRefName"),
                "head": d.get("headRefName"),
                "url": d.get("url"),
                "created_at": d.get("createdAt"),
                "updated_at": d.get("updatedAt"),
                "body": (d.get("body") or "")[:2000],
            },
        }

    def _list_prs(self, **kwargs: Any) -> dict[str, Any]:
        state = (kwargs.get("state") or "open").strip()
        repo = (kwargs.get("repo") or "").strip()
        args = [
            "pr",
            "list",
            "--state",
            state,
            "--json",
            "number,title,state,author,url,createdAt,baseRefName,headRefName",
        ]
        if repo:
            args += ["--repo", repo]
        res = _gh(args)
        if not res["ok"]:
            return res
        parsed = _parse(res["raw"])
        if not parsed["ok"]:
            return parsed
        return {"ok": True, "state": state, "prs": parsed["data"]}

    def _issue_triage(self, **kwargs: Any) -> dict[str, Any]:
        issue = str(kwargs.get("issue_number", "")).lstrip("#").strip()
        repo = (kwargs.get("repo") or "").strip()
        if not issue:
            return {"ok": False, "error": "需要 issue_number 参数"}
        args = [
            "issue",
            "view",
            issue,
            "--json",
            "title,state,body,comments,author,url,createdAt,updatedAt,labels,assignees",
        ]
        if repo:
            args += ["--repo", repo]
        res = _gh(args)
        if not res["ok"]:
            return res
        parsed = _parse(res["raw"])
        if not parsed["ok"]:
            return parsed
        d = parsed["data"]
        return {
            "ok": True,
            "issue": {
                "title": d.get("title"),
                "state": d.get("state"),
                "author": (d.get("author") or {}).get("login"),
                "labels": [label.get("name") for label in (d.get("labels") or [])],
                "assignees": [a.get("login") for a in (d.get("assignees") or [])],
                "num_comments": len(d.get("comments") or []),
                "url": d.get("url"),
                "created_at": d.get("createdAt"),
                "updated_at": d.get("updatedAt"),
                "body": (d.get("body") or "")[:2000],
            },
        }

    def _create_pr(self, **kwargs: Any) -> dict[str, Any]:
        if not kwargs.get("confirm"):
            return {
                "ok": False,
                "error": "创建 PR 是写操作,需传 confirm=true 才会真正提交(可先不传看预览)",
                "preview": {
                    "title": kwargs.get("title"),
                    "head": kwargs.get("head"),
                    "base": kwargs.get("base"),
                    "repo": kwargs.get("repo"),
                },
            }
        title = (kwargs.get("title") or "").strip()
        head = (kwargs.get("head") or "").strip()
        body = kwargs.get("body") or ""
        base = (kwargs.get("base") or "").strip()
        repo = (kwargs.get("repo") or "").strip()
        if not (title and head):
            return {"ok": False, "error": "创建 PR 需要 title 与 head(源分支名)"}
        args = ["pr", "create", "--title", title, "--body", body, "--head", head]
        if base:
            args += ["--base", base]
        if repo:
            args += ["--repo", repo]
        res = _gh(args)
        if not res["ok"]:
            return res
        return {"ok": True, "url": res["raw"].strip()}


__all__ = ["GitHubPlugin"]
