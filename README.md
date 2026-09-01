# Echo OS

Echo OS 是个人设备上的 Agent 操作系统（桌面 / 启动器 / 文件 / 系统能力）。Echo
Agent 运行时已经直接内建在本仓库和同一个 `echo-os` 安装包中；用户可见工作台也由
OS 唯一前端提供，不依赖同级项目、不加载第二套 Agent WebUI。

English readers: [README.en.md](README.en.md)

## 家族结构

| 项目                 | 定位                                                  |
| -------------------- | ----------------------------------------------------- |
| `echo-agent`      | Echo OS 内建的 Agent 能力与兼容命令                    |
| `echo-os`         | 唯一发行单元：Agent + 桌面 + 文件 + 系统能力           |
| `echo-mobile`     | Android 自动化客户端                                  |
| `echo-enterprise` | 企业版 AI 项目管理                                    |
| `echo-storage`    | 本地安全数据小脑（File Agent）                        |

## 当前结构

| 路径               | 作用                                                      |
| ------------------ | --------------------------------------------------------- |
| `appliance/`       | OS 设备层：应用注册器、认证、扩展管理、文件管理、技能管理 |
| `frontend/`        | 桌面 UI 壳                                                |
| `tests/appliance/` | appliance 层测试                                          |
| `deploy/`          | K8s / Docker 部署配置                                     |
| `docs/`            | 架构、入门和参考文档                                      |

## 与 echo-storage(File Agent)集成

Echo OS 不重复建设文档索引，而是通过窄 HTTP API 调用可选的
`echo-storage` 服务：

- **桌面入口**:Dock/启动器新增"文件管家",打开 OS 内建的 `/workspace/storage`。
- **文件管理器桥接**:NAS 文件管理器(`appliance/files/`)顶部新增"AI 问答"面板,
  可直接调用 storage 的 `/v1/search` 与 `/v1/answer`。
- **基础文件闭环**:文件管理器支持多文件选择/拖放上传、带进度下载、文件与目录
  复制、移动、建目录和回收站；上传先写临时文件再原子提交，重名默认拒绝覆盖。
- **自动启动**:设置 `ECHO_STORAGE_AUTOSTART=1`,OS 启动时会后台拉起
  `echo-storage serve`(默认 127.0.0.1:8767);未安装或启动失败只记录日志,
  不阻塞其余功能。
- **地址可配**:通过 `ECHO_STORAGE_URL` / `ECHO_STORAGE_HOST` /
  `ECHO_STORAGE_PORT` 覆盖默认地址;`/api/appliance/config` 会把地址暴露给前端。

## 快速开始

```bash
# 安装当前仓库的统一开发环境
make install

# 从当前仓库生成同源 wheel/resources/Codex bundle，再启动 Docker 栈
make agent-bundle
make up

# appliance 测试会验证 OS 与 Agent 的公开扩展/认证契约
make test
```

本地 QA 快照和真机部署步骤见 [NAS 部署说明](deploy/appliance/README.md)。

本地开发内建的 Echo 工作台：

```bash
cd frontend
pnpm format
pnpm lint
pnpm typecheck
pnpm test
pnpm build

# 启动联调桌面
pnpm dev:with-agent
```

这会启动 `127.0.0.1:8000` 的 Agent 后端与 `3000` 的唯一 OS 前端；
工作台是 OS 内建页面。完整配置见
[Echo Agent 工作台接入](docs/ECHO_AGENT_INTEGRATION.md)。

文档入口与历史资料归属：[docs/README.md](docs/README.md)。

当前架构：[docs/architecture.md](docs/architecture.md)

OS 与内建 Agent 的依赖规则：[Echo OS ↔ Echo Agent 工程边界](docs/AGENT_OS_BOUNDARY.md)。

NAS 产品交付边界与下一阶段门槛：
[NAS_DELIVERY_STATUS.md](docs/NAS_DELIVERY_STATUS.md)。
