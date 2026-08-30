---
name: github
description: >-
  GitHub 操作能力(参考重写自 OpenAI codex github 插件,MIT)。用于查看/摘要
  PR、分诊 issue、列出 PR、创建 PR。优先使用本插件的 gh CLI 工具;仅在需要
  当前分支 PR 发现、分支创建、commit/push、gh auth status、Actions 日志时才
  回退到本地 git / gh 命令。
license: MIT
upstream: https://github.com/openai/plugins (codex github plugin)
---

# GitHub (Echo port)

> 本文件为「参考重写」的溯源与操作指南。技能本身由 `plugin.yaml` +
> `__init__.py` 中的 `gh` CLI 工具实现,不依赖 OpenAI 托管 connector。

## 能力清单

| 技能 | 作用 | 写操作? |
|---|---|---|
| `github.pr_summary` | 摘要某 PR(标题/状态/增删/评论/评审/文件/链接) | 否 |
| `github.list_prs` | 列出 PR(open/closed/merged,可按 repo) | 否 |
| `github.issue_triage` | 查看并分诊某 issue(标题/状态/正文/评论/标签) | 否 |
| `github.create_pr` | 创建 PR(需 `confirm=true`,否则仅预览) | **是,需确认** |

## 使用约定(移植自原插件路由规则)

1. 先解析上下文:用户给了 `owner/repo`、PR/issue 编号或 URL 就直接用;
   说「这个分支 / 当前 PR」就先 `git` 解析本地仓库与分支,再用 `gh` 发现分支 PR。
2. 读操作优先走本插件工具;本地 checkout / push / Actions 日志等本插件未覆盖的,
   回退到本地 `git` 与 `gh`。
3. 写操作(`create_pr`)默认拒绝,必须显式 `confirm=true` 才提交;可先不带
   `confirm` 看预览。
4. 仓库无法从请求或本地 git 上下文识别时,**问用户要仓库标识**,不要假装存在
   搜索流程。

## 与原 codex 插件的差异

- 原插件:`.app.json` 指向 OpenAI 托管 connector(`connector_7686...`),本地无实现。
- 本插件:`gh` CLI 本地实现,`trusted_source=plugin://github`,完全自包含。
- 许可证:沿用 MIT + OpenAI 署名(见 LICENSE.txt);实现为独立重写。
