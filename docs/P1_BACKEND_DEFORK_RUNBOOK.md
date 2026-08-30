# Echo 后端单仓运行手册

> 旧的跨仓依赖方案已经退役。本页只保留当前单仓方式，避免历史步骤继续误导开发与交付。

## 当前边界

Echo 的 Agent 运行时位于本仓库 `runtime/`，设备能力位于 `appliance/`，两者由同一个
`echo-os` Python distribution 发布。`frontend/` 是唯一工作台，不加载第二套 Agent WebUI。

```text
frontend/ → appliance/agent_api/ → runtime/
```

`appliance/agent_api/` 是稳定领域边界；设备功能不应直接依赖运行时的私有实现。详细规则见
[Echo OS ↔ Echo Agent 工程边界](AGENT_OS_BOUNDARY.md)。

## 本地开发

```bash
make install
cd frontend
pnpm dev:with-agent
```

这会从当前 checkout 启动内建后端和唯一前端，不需要另一个源码目录或额外 Python 包。

## 构建与发布

```bash
make agent-bundle
```

构建从同一个干净 Git revision 生成统一 wheel、运行资源、Codex 包和完整性清单。开发中的
未提交内容只能使用 `make agent-bundle-local` 生成带 `dirty: true` 的 QA 快照，不能作为正式发布。

升级 Agent 能力就是修改本仓库的 `runtime/` 并通过统一测试与发布门，不再维护跨仓版本钉。
