# Octopus OS 总体改造方案

> 版本:v0.1(2026-06-13)
> 基线:fork 自 octopus-agent @ `8fe3352`,分支 `os-main`,remote `mother` 指向本地母体仓库
> 状态:规划阶段,P1 未开工

---

## 1. 定位(一句话)

**一台设备的操作系统,不是所有电脑的操作系统。**

Octopus OS 是面向家庭服务器盒子(NAS 硬件形态)的 Appliance OS:开机即进 Agent 桌面,
浏览器即窗口系统,Docker 即应用生态,存储底座来自上游开源组件。对标进场方式是
Android/ChromeOS/SteamOS——通过受控硬件定义新品类,绝不走"装在任意 PC 上替代
Windows"的死路。

核心论点(别人抄不动的部分):**Agent 是会话本身,不是装在系统上的应用。**
- 母体的 approval/audit/budget_breaker 升格为 OS 级权限与审计模型;
- Agent 管理系统自身:磁盘报警自处理、应用崩溃自修复、更新失败自回滚(自愈家电);
- 桌面三原语:对话(Agent)/ 窗口(应用 Web UI)/ 文件。

## 2. 北极星场景(P2 结束时必须能演示)

1. **自然语言找文件**:"把 2023 年的发票全找出来按月归档"——语义索引 + 文件技能 + 审批门;
2. **看门狗自动化**:下载完成 → 识别剧集 → 重命名入库 → 通知 Jellyfin 刷新媒体库(跨应用编排);
3. **本地文档问答**:对私人合同/笔记 RAG,敏感内容强制走 ollama 本地推理,不出网。

## 3. 架构分层

```
显示器(HDMI)                       ← P2.5
  ↑ 全屏渲染
kiosk(cage+Chromium 或 Electron)    ← 宿主进程,需 GPU;远程访问时此层=用户自己的浏览器
  ↑
桌面 Shell(Next.js,改造自 frontend/) ← 对话 / 窗口管理 / 文件管理 / 应用启动器
  ↑ JSON-RPC WebSocket
母体 Runtime(runtime/)              ← Agent OS:技能/审批/记忆/模型路由(含 ollama)
  ↑ docker.sock + 反向代理
Docker 容器运行时                    ← 第三方应用生态(兼容 CasaOS 应用商店模板格式)
  ↑
NAS 基座(Debian + OMV 存储包)        ← P3;P1/P2 阶段寄宿在现成 NAS 系统上
```

两条铁律:
- **你的代码跑宿主,别人的代码进容器**;
- **WebUI 和 HDMI 桌面是同一个 Web 桌面的两块屏**,不存在两套 UI 代码。

## 4. 与母体(octopus-agent)的 fork 管理策略

- 母体继续作为通用 Agent OS 独立演进;本仓库定期 `git fetch mother && git merge mother/main`;
- **OS 专属新代码一律放顶层新目录 `appliance/`**(后端)和 `frontend/src/appliance/`(前端),
  尽量不改 runtime 内核文件 → 把未来合并冲突压到最小;
- 对 runtime 的必要改动优先做成 **配置开关 / 插件挂载点**,并考虑反向贡献给母体;
- 裁剪 = profile 开关,**不删代码**(删了合并必疼);
- ROOT_LAYOUT.md 与 CI 白名单需在 P1 第一周更新以容纳 `appliance/`。

基线注意:fork 时带入了母体 25 个未提交 WIP 文件(react_loop / skill_forge /
realtime_cerebrum 等)。**P1 开工前需决断:等母体提交后重新同步,或就地冻结。**
data/ 运行时状态(330M)未带入,OS 以空白记忆出生。

## 5. Appliance Profile:裁剪清单(默认关闭,不删除)

| 模块 | 路径 | 处置 |
|---|---|---|
| 多 Agent 集群 | `runtime/execution/swarm/` | 默认关 |
| HA 心跳/选举 | `runtime/core/hearts/` | 默认关(单机设备) |
| K8s/SSH 沙箱后端 | `runtime/safety/sandboxing/` 内对应后端 | 默认关,保留 local/docker |
| 自演化/技能锻造 | `runtime/safety/evolution/`、`runtime/safety/recovery/`(skill_forge) | 默认关;开启时强制过审批门 |
| company/research/tentacle | `runtime/company/` 等 | P1 评估后决定 |

实现方式:`config.yaml` 增加 `profile: appliance` 预设,一键收敛;内存目标:
runtime 常驻 < 1.5GB(不含 ollama)。

## 6. 分阶段工程计划

### P1 —— NAS 上的 Docker 应用(最小可信产品)

寄宿目标:CasaOS / OMV / 飞牛(以 Docker 应用形式分发,挂载 docker.sock 与存储卷)。

| 工单 | 说明 | 依托 |
|---|---|---|
| appliance profile | 配置预设 + 裁剪开关 | `runtime/platform/config/` |
| 应用注册器 | 读 docker.sock:容器清单/图标/端口 → 启动器数据源 | 新建 `appliance/app_registry/` |
| 桌面 Shell v0 | 三原语:对话+文件+启动器;砍掉专业面板(evolution/swarm/diagnostics 等) | `frontend/`(workspace 改造) |
| 文件管理器 | 对接 `runtime/sensing/gateway/fs_router.py` | 现成 API |
| 本地模型默认开 | ollama 预设模型清单 + 安装引导 | `runtime/sensing/model_router/ollama_router.py`(已存在) |
| 打包分发 | docker-compose 一键 + CasaOS 商店模板 | `deploy/` |

验收:在一台现成 NAS 上一条命令装好,浏览器打开即是桌面,能聊、能管文件、能看到并打开已装应用(新标签页方式)。

### P2 —— 窗口化 + 应用技能 + 语义索引

| 工单 | 说明 |
|---|---|
| 反向代理 + SSO | 内嵌 Caddy:剥 X-Frame-Options/CSP、路径重写、票据注入(本阶段工作量大头) |
| 窗口管理器 | 第三方应用 Web UI iframe 窗口化 |
| 头部应用技能 ×5 | qBittorrent / Jellyfin / Immich / PhotoPrism / Transmission,**API 优先**,Playwright 兜底(每应用一个 SKILL.md) |
| 语义文件索引 | embedding 索引挂载 MemoryHub(`runtime/memory/hub.py`);现有向量仅服务技能匹配,需扩展到用户文件内容 |
| 自动化规则 | `runtime/sensing/normalize/sensors/file_watcher.py` + 规则 UI("下载完成→整理") |
| **回收站语义(硬约束)** | 所有删除改为移入回收站,物理删除仅限人工确认 |

验收:北极星场景 1/2/3 全部可演示。

### P2.5 —— HDMI 本地桌面

- kiosk 会话:cage + Chromium kiosk(或复用 `frontend/electron/`)指向 localhost Shell;
- mpv 硬解播放通道(QSV/VAAPI),Shell/Agent 控制播放——mobile 项目的 mpv 经验平移;
- 键鼠 + 遥控(CEC 后置);第一版只做桌面模式,电视 10-foot 海报墙模式后置(参考飞牛的克制)。

### P3 —— 整机镜像(此时才挂"OS"的牌子)

- Debian stable + OMV 存储包(apt 融合,不 fork);开机默认进 kiosk 会话;
- **不可变系统 + A/B 原子更新**(更新失败自动回滚,Agent 可自主管理更新);
- 受控硬件白名单:首发 2~3 个目标(N100 迷你主机 / 常见 NAS 准系统;16GB 内存基线);
- WebUI 降级为"远程投影"。

## 7. 安全模型(从第一行代码就是硬约束)

1. **第一攻击面 = 网页内容→Agent 输入的提示注入**:桌面即浏览器 + Agent 有文件权限,
   任何来自应用窗口/网页/下载元数据的文本都是不可信输入;"看完内容→动文件"链路
   强制过 `runtime/safety/approval/`;
2. 删除即回收站(见 P2 硬约束);
3. `runtime/safety/` 规则全面配置化(母体遗留问题,OS 场景下从"建议"升级为"必须");
4. 审计:journal 作为系统级操作审计,UI 可回放"AI 对这台机器做过什么";
5. budget_breaker 管本地+云端推理成本配额。

## 8. 工程纪律

- 永远是 Debian 薄 remix:不 fork 内核、不 fork 上游组件、存储栈不自研;
- 复用母体 CI 体系(tools/lint/ 全套 + invariants),`appliance/` 纳入检查范围;
- 死亡陷阱清单:支持任意 PC 硬件 / fork 上游 / 自研存储 / 跳过 P1、P2 直接憋镜像。

## 9. 待决事项

| 事项 | 选项 | 截止 |
|---|---|---|
| 母体 WIP 处置 | 等母体提交后 merge / 就地冻结 | P1 开工前 |
| 寄宿首选平台 | CasaOS(商店生态) vs OMV(存储扎实) vs 飞牛(国内用户多) | P1 第一周 |
| 品牌命名 | octopus-os 为工作名 | P3 前 |
| company/research/tentacle 去留 | 评估后定 | P1 内 |

## 10. 本仓库当前状态

- 由 `rsync` 自母体复制(排除 node_modules / .venv / data / 各类缓存),152M;
- 开发环境需重建:`uv sync` + `cd frontend && pnpm install`;
- 分支:`os-main`;remote:`mother`(本地路径);母体与本仓库均无远程托管,**建议尽快推 GitHub/Gitea 备份**。
