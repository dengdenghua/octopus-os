# Echo OS ↔ Echo Agent 工程边界

Echo Agent 已作为 `runtime/` 迁入 Echo OS，并随统一 `echo-os` wheel 发布。单仓不等于
取消模块边界：设备层对 Agent 的 Python 调用仍必须封装在 `appliance/agent_api/`，不能
散落到 OS 功能模块。这样运行时实现可以继续演进，而桌面、文件和系统能力只依赖稳定领域接口。

## 允许的依赖方向

```text
appliance feature
  → appliance.agent_api.<domain>
  → runtime（内建 Agent 实现）
```

领域边界如下：

| 模块 | 暂时适配的 Agent 能力 |
| --- | --- |
| `auth` | 本地认证配置、JWT、密码哈希、会话 Cookie |
| `audit` | 防篡改审计链基础类型 |
| `tasks` | 任务租约、检查点恢复元数据 |
| `skills` | Skill 注册与 golden test 类型 |
| `catalog` | 插件和技能目录投影 |
| `capabilities` | Agent 能力安装、逐主体授权、连接、停用、卸载与回滚生命周期 |
| `devices` | Tentacle 协调器和活跃设备桥 |
| `images` | 本地语义图片索引 |

`agent_api/__init__.py` 禁止聚合导出。每个功能只能加载需要的领域，避免 Agent 的一个
可选子系统不兼容时阻断整个 OS 启动。离线备份、状态 schema、安装器和发布工具必须在
Agent 运行时不导入时继续工作。

`agent_api/contract.py` 定义 `echo.agent_api_contract.v1`。NAS appliance 启动前强制探测
认证、审计和任务三个核心必需领域；原生回环服务只强制探测任务领域。任一必需
符号缺失都会按领域失败关闭，系统不会带着半兼容 Agent 继续启动。目录、设备传输、技能、图片索引和能力
生命周期作为可选领域报告状态；缺失时对应入口明确不可用，但不能阻断文件、OMV、桌面等其余 OS 功能。
只读 `/api/appliance/config` 的 `agent_api` 字段
公开版本、领域兼容状态和有界缺失码，绝不返回导入路径或原始异常。

契约门禁还会逐一比对 `agent_api` 适配器中的静态 `runtime.*` 导入，并对目录、设备协调器、
能力生命周期等对象继续检查实际调用的方法；照片安全索引依赖的三个私有 seam 也全部显式列入。
因此不能出现“启动探针通过，功能首次调用才因漏探测符号崩溃”的半兼容状态。未来切换正式 SDK 时，
这份逐符号和逐方法清单也是唯一迁移面。

可选集成必须在确认用户配置后才能加载对应 Agent 领域。例如 PM skill extension 在未设置
`ECHO_PM_URL` 时只返回 no-op，连 Agent skill 模块都不会导入；一旦配置，则把 skills
领域提升为本次集成的必需契约并给出明确兼容错误。PM 工具只接受有界项目标识、角色、
优先级和正文，返回稳定错误码与重试提示，不把上游响应正文、网络地址或异常原文暴露给
Agent。

## 禁止事项

- `appliance/agent_api/` 之外不得 `import runtime` 或 `from runtime...`。
- 不得在其他 OS 模块中保存 Agent 私有模块路径并动态导入。
- 不得读取 Agent 私有 SQLite、checkpoint 或配置文件；优先走已认证 API。
- 不得把 Agent 的规划、记忆、技能或设备传输实现复制进 `appliance/`。
- 新增兼容调用必须进入最窄领域模块，并具备失败关闭或明确不可用语义。

Agent 能力生命周期是高权限边界：OS 只把精确的 `local:admin` 主体映射成 Agent 管理员，
其他已认证主体不得因“已登录”被提升权限。安装、权限授权、卸载和回滚都绑定经过复核的
计划 ID，并消费一次性管理员审批。计划、连接说明和操作结果必须经过 OS 字段白名单投影；
Agent 的未知字段、原始异常、凭据、内部路径和调试信息不得进入浏览器或审计日志。

## 验证门

`tests/appliance/test_agent_compat.py` 对整个 `appliance/` AST 扫描，阻止边界外私有导入和
动态模块路径回流。`deploy/appliance/run_public_source_tests.py` 对设备层与内嵌
runtime 的测试做显式分类，但两类都从同一 checkout 和同一 distribution 运行；新测试
未分类时 CI 会失败，避免单仓后的交付覆盖漂移。

`agent_api` 现在是单仓内的稳定适配层。八个领域的符号与方法探针在测试和启动时验证；发布
bundle 则把统一 wheel、资源和 Codex 绑定到同一个 OS revision。正式构建要求干净、已推送的
交付分支，任何领域缺口、混版或 dirty source 都会在生成发布制品前失败关闭。

## 认证密钥边界

`appliance.extension` 只在启动装配阶段读取一次设备 JWT secret，并构造
`ApplianceAuthenticator`。十四个 HTTP 路由工厂只接收这个验证器，可验证请求、取得 actor
和判断生产认证是否必需，但不能取得签名材料。原始 secret 只允许进入确实需要派生独立
密钥或签名的审计链、单次审批、设备凭据和加密后台账本构造点。AST 契约测试会阻止扩展
重新向路由传递 `jwt_secret`。
