# Echo OS（echo-os）项目分析

> 分析日期：2026-08-28 · 分支 `codex/echo-wayland-gate` · 版本 0.2.0
> 本文基于实际代码与实测运行，非文档转述。

## 2026-08-29 整改更新

本文的原始风险快照已完成第一轮系统性整改，历史段落保留用于解释改动来源，不能再当作
当前状态：

| 原问题 | 当前状态 |
| --- | --- |
| 20 处业务模块直接 `runtime.*` 依赖 | 已集中到分领域 `appliance/agent_api/`，AST 门禁禁止回流；版本化启动探针验证必需符号 |
| Device Link 固定共享哨兵 | 已改为每次启动随机 fallback；真实配对继续使用逐设备、可撤销凭据摘要 |
| 生产缺 JWT 时开发身份全放行 | 已失败关闭；`ECHO_APPLIANCE=1` 且缺 secret 返回 401 |
| Docker proxy 默认无鉴权 | 非回环监听强制 32–512 字符密钥；主服务与健康检查均认证 |
| 容器 ID 双向前缀 | 已改为唯一、单向短 ID → 完整 64 位 ID；缺失或歧义返回 409 |
| JWT secret 向路由广泛透传 | 14 个路由改用不可导出签名材料的单一 `ApplianceAuthenticator` |
| OMV 路由单函数 960 行 | 已拆为协议模型、只读状态、账户、共享、配额和统一安全上下文；84 行工厂只负责装配，AST 门禁锁定 24 个接口的归属 |
| OMV client 协议与通信混合 1900 行 | 已拆为 539 行受限通信客户端、408 行请求协议和 1051 行响应规范化；兼容门禁保证旧导入面不变 |
| OMV bridge 同时承载业务与 HTTP 进程边界 | Unix Socket HTTP、请求限流和状态映射已独立为 352 行传输层，错误分类独立共享；原模块继续提供相同 CLI 与导入入口 |
| OMV bridge 协议与账户控制耦合 | 668 行主机协议契约和 406 行账户控制器已独立；服务外观继续提供相同方法，并保留计划绑定、陈旧计划拒绝与回滚语义 |
| OMV bridge 共享/配额控制集中 | 共享文件夹、权限、SMB/NFS 已独立为无状态组合控制器，配额控制另行隔离；主桥接模块由 2216 行降到 1025 行 |
| OMV bridge 库存与命令执行混合 | 受限 RPC/secret/lsblk/mdstat 执行器和只读库存解析均已独立；主模块最终降到 131 行，只保留共享状态、组合与 CLI |
| 容器 ID 规则重复 | 已集中到 `appliance.identifiers`，审批和应用控制共享同一全匹配规则，结构门禁禁止私有副本回流 |
| 凭据目录权限与符号链接 | 显式数据目录和认证存储分别强制 `0700/0600`，文件或直接父目录为符号链接时失败关闭 |
| 英文首页/旧 Wiki 仍描述 Agent 单体 | 英文首页已改为真实 OS 结构；文档入口、Wiki 警告和测试归属页明确区分当前 OS 与历史 Agent 资料 |
| Agent 探针可能漏掉适配器符号 | 探针已覆盖认证会话清理和 SkillExpect 等全部静态导入；AST 门禁逐符号比对实际适配器与版本化领域清单 |
| PM 工具返回原始异常且项目 ID 可拼接路径 | 改为有界参数、受限 HTTP(S) 配置、稳定错误码/重试语义；不再返回上游正文或底层异常原文 |
| Docker/Hub 公开接口透传底层异常 | Docker 传输层保留异常 cause 供本地诊断，应用中心、Hub 控制和后台队列只返回稳定的有界错误，不暴露 socket、代理地址或策略路径 |
| Agent 能力接口默认管理员且透传上游结果 | 仅精确本机管理员可执行变更；权限授权绑定一次性复核，权限与凭据输入有界，计划/连接说明/结果按白名单投影，未知错误和秘密字段失败关闭 |
| 正式运行验收未覆盖 Agent 能力接口 | 运行验收器现验证未登录拒绝、可选服务有界状态、授权二次复核、未知权限与超长凭据拒绝，并检查秘密不回显 |
| 前端格式和 lint 未成为可靠门禁 | 排除生成物后统一格式化维护源码，修复无效审计 JSON，lint 改为零警告；主 CI 现执行格式、lint、类型、浏览器/Electron 测试和生产构建 |
| 生产构建容忍失效懒加载且浏览器首页有两套实现 | 消除静态/动态混合导入并把同类 Rollup 警告升级为构建失败；删除不可达旧桌面和专用模型，结构门锁定唯一 `BrowserHome` |
| 家庭账户强度只在前端校验 | 服务端统一执行字符类别、控制字符和 bcrypt 72 字节边界；直接 API 调用也失败关闭且不回显凭据 |
| 家庭成员登录后仍共享同一文件/照片视图 | 已按 OMV 公开成员、组、共享目录和逐共享权限生成失败关闭的数据路径范围；列表、容量、读写、上传会话、回收站、照片库/搜索/缩略图/原图均执行成员级授权，并新增 Alice/Bob 真实登录端到端隔离测试 |
| 家庭成员只有开通、没有生命周期 | 已增加停用/启用、独立 Echo 密码重置及“先停用再移除映射”的 API/UI；全部绑定计划、管理员审批和脱敏审计，精确撤销该成员的 HTTP/WS 会话，不删除 OMV 身份或 NAS 数据 |
| 可选 Agent 子系统故障会阻断 OS 启动 | 启动必需域收紧为认证、审计、任务；目录、设备、技能、图片和能力生命周期按领域明确降级，照片私有 seam 与对象方法面全部进入版本探针 |
| 家庭数据隔离只有本机模拟 | x86/ARM 耐久门现强制绑定 mode-0400 双成员 fixture，每阶段真实登录并交叉验证目录、文件、照片和越权拒绝；证据不保存用户名、路径或密码，仍待实体机产物 |
| Agent 与 OS 跨仓漂移 | Agent 运行时已迁入统一 `echo-os` distribution；bundle 从同一 OS revision 冻结 wheel、资源和 Codex，不再需要 sibling 来源锁 |

Agent 单仓迁入、冻结打包与协议合同收口后，完整 appliance 回归为 1181 项通过，公开源码门为 1181 项通过，均无跳过。
前端格式、零警告 lint、TypeScript、66 个测试文件
518 项、Electron 合同和生产构建也全部通过。在无 `dpkg-deb` 的 macOS 上使用系统 libarchive 独立验证 Debian 包结构，Linux CI 仍使用 `dpkg-deb`。OMV 控制面、家庭数据隔离、前端质量门和文档认知边界已完成整改；根目录历史 Agent 测试仍需按现行能力清册归档或迁入正式门。

## 历史原始结论（已失效）

这是一个**「去 fork 中途态」仓库**：目录层已经彻底摆脱 echo-agent，但代码依赖层没有。
工程质量本身相当扎实（883 测试全绿 / 覆盖率 79% / 零 TODO），主要债务集中在
**20 处反向私有依赖** 与 **2 个 P0 级安全隐患**。

---

## 1. 真实分层：OS 是 agent 的扩展插件，不是独立应用

`appliance/entrypoint.py:273` 并不组装 FastAPI app，它只做凭据引导与降权，然后
`os.execvp("echo-agent", ...)`。真正的挂载点是 agent 官方扩展 API：

- `appliance/extension.py:47` `register_app(app, context)`，由环境变量
  `ECHO_APP_EXTENSIONS=appliance.extension` 触发（`extension.py:3`）
- 原生镜像走 `appliance/native_entrypoint.py:52` + `native_extension.py:17`
- 中间件四层：`desktop_root` → `local_auth` → `session_revocation` → `web_security`

| 路由前缀 | 模块 | 端点数 |
|---|---|---|
| `/api/appliance/omv` | `omv_router.py:322` | 24 |
| `/api/appliance/files` | `files/router.py:98` | 17 |
| `/api/appliance/audit` | `audit.py:603` | 5 |
| `/api/appliance/device-link` | `device_link.py:479` | 5 |
| `/api/appliance/hub` | `hub/router.py:63` | 4 |
| `/api/appliance/tasks` | `task_projection.py:355` | 4 |
| `/api/appliance/photos` | `photos/router.py:72` | 7 |
| 其余（apps/approvals/capabilities/config） | 分散 | ~10 |

---

## 2. 历史去 fork 状态（已由上方整改表取代）

| 层面 | 状态 | 证据 |
|---|---|---|
| 目录删除 | 完成 | `runtime/`、`tools/` 无残留；commit `60e835e` 删 2216 文件 |
| 打包隔离 | 完成 | `pyproject.toml:160-162` wheel 只含 `packages = ["appliance"]` |
| **代码依赖** | **未完成** | **20 处 `import runtime.*` 私有模块** |

高风险依赖清单（agent 小版本升级即可能让 OS 崩溃）：

| 位置 | 依赖的私有模块 |
|---|---|
| `task_projection.py:184` | `runtime.sensing.gateway._realtime_turn_lifecycle_resume`（**下划线私有**） |
| `audit.py:26` | `runtime.safety.audit.audit_chain` |
| `pm_skills.py:21-22` | `runtime.execution.suckers.registry/testing` |
| `security.py:33,47` | `runtime.safety.auth.principal` / `.identity` |
| `auth.py:101`、`approval.py:268`、`state_recovery.py:130` | `runtime.adapters.integrations.local_auth` |
| `device_link.py:285,316` | `runtime.tentacle.team_bridge` |

---

## 3. 实测工程质量（好消息）

在 `.venv`（已装 `echo-agent 0.2.0` + `echo-agent-runtime 0.2.0`）实测：

```
pytest tests/appliance  →  883 passed, 1 skipped in 80s
覆盖率                  →  79%（CI 门槛仅 --cov-fail-under=40）
TODO/FIXME/HACK         →  0 处
大段注释掉的代码         →  0 处
shell=True              →  0 处
```

CI（`.github/workflows/ci.yml`）门禁齐全：ruff check + format、pytest + 覆盖率门槛、
bandit SAST、pip-audit、私有源码边界校验。

**测试集的一个精确判断**：`tests/` 根目录 407 个文件 / 12.4 万行 import `runtime`，
表面像死代码，但实测**全部能跑通**——它们在测 venv 里的 agent wheel，不是 OS 代码。
`pyproject.toml:171` `testpaths=["tests/appliance"]` 已将其绕过。
所以性质是「**越权测试**」而非「死测试」：能跑、但不该由本仓库维护。

---

## 4. 风险清单

### P0
| 问题 | 位置 | 说明 |
|---|---|---|
| 硬编码共享 token | `extension.py:171`、`device_link.py:201` | `"echo-device-link-managed"` 写死，LAN 内任意主机可用且不可轮换 |
| 认证静默降级 | `security.py:42-43` | `jwt_secret` 为空时返回 `DEVELOPMENT_ACTOR` 全放行；与 `extension.py:110-111` 的 fail-closed 不一致 |

### P1
| 问题 | 位置 | 说明 |
|---|---|---|
| Docker 代理鉴权默认关 | `docker_proxy.py:137-139` | 未设 `ECHO_DOCKER_PROXY_TOKEN` 时 `_check_auth` 直接 `return True` |
| 超长函数 | `omv_router.py:311` | 单函数 960 行 |
| 复杂度集中 | OMV 四件套 7375 行 | 占 appliance 总量 36%（`omv_bridge.py` 3481、`omv_client.py` 1900） |
| `jwt_secret` 透传面过大 | `extension.py` 12 处 | 任一 router 泄露即全控制面失守 |

### P2
| 问题 | 位置 |
|---|---|
| 容器 id 双向前缀匹配过宽 | `docker_proxy.py:62` |
| 凭据目录未指定 `mode=0o700`（依赖 umask） | `auth.py:56`（对比 `entrypoint.py:70` 正确） |
| 重复定义 `_CONTAINER_ID` 正则 | `app_registry/router.py:37` 与 `approval.py:38` |
| 错误处理风格分裂（dict vs HTTPException） | `pm_skills.py` 全部返回 dict |

### 做得好的部分（别在重构中破坏）
- Docker 三层隔离：`docker_client.py:73-77` 生产禁直连 socket → `docker_proxy.py:35-36` 正则白名单
  （`DELETE/PUT/PATCH` 全 405）→ `docker_proxy.py:308-333` 代理进程 drop root
- 镜像名不可由用户指定，全部由服务端 catalog 解析（`docker_installer.py:41-50`）
- 并发保护两层：`state_lock.py:63-65` `fcntl.flock` 跨进程 + `audit.py:129` `RLock` 进程内
- 审计链 HMAC + `hmac.compare_digest`（`audit.py:220,378`）

---

## 5. 文档与仓库卫生

| 项 | 事实 |
|---|---|
| `CODE_WIKI.md` | 1994 行，引用 `runtime/` 44 次，**100% 描述 echo-agent，与本仓库无关**。且称前端为 Next.js 14 + React 18 |
| 前端真实栈 | **Vite 7 + React 19 + Electron 34 + Tailwind 4 + Vitest 4，无 Next.js**；但目录沿用 `src/app` 约定，易误导 |
| 仓库体积 | 5.3G（frontend 2.9G = release 1.5G + node_modules 1.4G；packaging 967M；.git 375M） |
| git 跟踪卫生 | 干净：`.pak`、`node_modules`、`dist/` 均未被跟踪 |
| 工作区未提交 | 1087 files changed, +30,256 / −151,594（净删 12 万行） |
| 前端测试 | 稀疏，仅 15 个文件含测试 |

---

## 6. 建议动作（按投入产出排序）

1. **要求 agent 暴露稳定 API**，替换 20 处 `runtime.*`，优先处理
   `task_projection.py:184` 的下划线私有模块依赖 —— 这是唯一会随 agent 小版本升级炸掉的点。
2. **消除 2 个 P0**：device-link token 改为启动时随机生成并落盘；`security.py` 降级策略
   改为 fail-closed，与 `extension.py:110` 对齐。
3. **删掉 `CODE_WIKI.md`**（或移到 `docs/archive/` 并标注属于 echo-agent）。
   它现在是仓库里最大的认知污染源。
4. **归档 `tests/` 根目录 407 个越权测试**：迁到 echo-agent 仓库，或从本仓库删除。
   12.4 万行的归属混乱会持续误导新人和 AI 助手。
5. **拆分 `omv_router.py:311` 的 960 行函数**，并为 `docker_proxy.py` 设置默认 token。
