# 项目概述 · Project Overview

> 自动从仓库结构提取。Echo · The Open-Source Multi-Agent AI Workspace.

Echo OS 是个人设备上的 Agent 操作系统（桌面 / 启动器 / 文件 / 系统能力）。Echo
Agent 运行时已经直接内建在本仓库和同一个 `echo-os` 安装包中；用户可见工作台也由
OS 唯一前端提供，不依赖同级项目、不加载第二套 Agent WebUI。

## 仓库结构

| Directory | Purpose |
| --- | --- |
| `runtime/` | Python runtime (agents / planner / executor / safety / memory) |
| `frontend/` | React + Vite SPA for the webui |
| `agents/` | Per-agent profile + memory + workspace directories |
| `docs/` | Human-written architecture docs, ADRs, invariants |
| `docs/auto/` | ← you are here · auto-generated |
| `tests/` | Pytest suite (backend) |
| `scripts/` | Tooling (this generator + OpenAPI snapshot) |
| `protocols/` | 8 protocol specs (digestion / immunity / swarm / …) |

## 规模

- Python 模块：**1476** 个（runtime/）
- TSX 组件：**721** 个（frontend/src）
- 后端测试：**1101** 个

