# P2 · OS 前端去 fork 计划与可删清单(待评审,先不删)

> 目标(见 [OS_DIFFERENTIATION.md](OS_DIFFERENTIATION.md) §3 P2):os 不再 fork/打包
> agent 的工作台前端,而用 os 自己的窗口管理器把它当一个应用**开在窗口里加载**
> (URL 指向独立运行的 agent 服务)。os 前端 = 纯桌面 + appliance 层。
>
> **本文是测绘 + 清单,不执行删除**。前端体量大、纠缠深、且有部署前置,需按下方
> 顺序分步做,每步可验证、可回滚。已落地的非破坏性接缝见 §5。

## 1. 现状体量(`frontend/src`,共 708 个 .ts/.tsx)

绝大多数是 agent 工作台;os 自有的桌面/appliance 很小。

| 区块 | 文件数 | 归属 |
|---|---|---|
| `components/workspace/` | 314 | **agent 工作台**(删除候选) |
| `core/`(threads/realtime/agents/tasks/artifacts/team-tasks/…) | ~192 | 多为**工作台逻辑**(删除候选);少数共享(见 §3) |
| `components/ui/` | 54 | 共享 UI 原语 · **留** |
| `app/workspace/` | 33 | **工作台路由**(删除候选) |
| `components/ai-elements/` | 29 | 工作台 AI 对话元件(删除候选) |
| `components/landing/` | 10 | 落地页(os 有自己的桌面主页 → 删除候选) |
| `components/browser/` | 9 | 浏览器应用 · **纠缠**(见 §3) |
| `appliance/` | 8 | **os 自有**(桌面/dock/窗口/文件/认证)· 留 |
| `components/store/` | 5 | 应用商店 · **纠缠** |
| `components/realtime/` `auth/` | 4 / 2 | 工作台实时(删候选)/ 认证(留) |
| `hooks/` `lib/` `providers/` | 17 / 8 / 1 | 共享基建 · **留** |
| `app/desktop/` | 1 | **os 桌面**(留) |

粗略:删除候选 ≈ 650(工作台 + 多数 core),保留 ≈ 50(桌面/appliance/共享原语/基建)。

## 2. 两个硬问题(决定可行性,先于删除)

### 问题 A · 外部 agent 工作台 UI 由谁 serve(部署模型,需你定)
去 fork 后,工作台 UI 不再由 os 前端打包,必须由**别处**提供给 iframe 加载。候选:
- **(推荐)同机 agent 自带 webui**:os 后端已装 agent(P1)。让 os 的镜像顺带构建
  agent 的 `frontend/`(类似 P1 的 wheel 投喂,做个 webui 投喂),后端在子路径
  serve 它;`window.__OCTOPUS_AGENT_WORKSPACE_URL__` 指该子路径。**注**:agent 的
  前端不在 pip 包里(包只含 runtime*/tools*),需单独构建产物。
- 独立 agent 容器:另起一个 agent 服务容器,os 桌面 iframe 指它。更"消费",但单盒
  部署多一个容器。

### 问题 B · 桌面的 dock 应用现在就是 workspace 路由
`app/desktop/page.tsx` 的 6 个 dock 应用,5 个 `navigate` 到工作台路由:
`/workspace/realtime/new`(对话)、`/workspace/knowledge`(本地文件)、
`/workspace/observability`(终端日志)、`/workspace`(设置)、`/workspace/store`。
删掉工作台前端 → 这些路由不存在 → 必须**逐个改成嵌入窗口**(指外部 agent 对应
路由)或砍掉。这是和 §5 接缝同款改造,但要对每个应用做一次,并定哪些保留。

## 3. 纠缠点(删除前必须解开)

桌面/appliance 层对"删除候选"区的依赖(grep 实测):
- `app/desktop/page.tsx` → `components/workspace/workspace-sidebar`(1)、
  `components/store/unified-store`(1)、`components/browser/*`(5)。
- `app/browser/`(AI 浏览器,os 想保留的桌面应用)→ `components/browser/`,
  而 `components/browser/` 又依赖 **core/ 24 处**(log/i18n/clipboard/config/auth/
  threads/api/agents)。→ 保留浏览器应用 = 必须保留这部分 core/(不能整删 core/)。
- `components/store/` → workspace/core **15 处**。
- 共享小件:`core/utils/log`、`core/auth/api`、`core/i18n`、`lib/utils`、`hooks` —
  桌面层在用,**留**。

结论:`core/` 不能整目录删——它混着"工作台逻辑"(删)和"共享基建/浏览器依赖"
(留)。需按子目录甚至文件级甄别。

## 4. 建议顺序(每步可验证、可回滚;不一次性大删)

1. **先解决问题 A**:让 os 能 serve(或指向)一个独立 agent 工作台 UI,把 §5 的
   `__OCTOPUS_AGENT_WORKSPACE_URL__` 真正指过去,验证窗口里加载的是"外部" agent。
2. **桌面应用逐个改造**(问题 B):把 5 个 navigate-工作台 的 dock 应用改成嵌入
   窗口(或砍),每改一个验证一次。改完桌面不再直接依赖工作台路由。
3. **甄别 core/**:把"共享基建/浏览器要用"的子目录(api/utils/i18n/auth/config/
   clipboard/agents 等)与"纯工作台"(threads/realtime/tasks/artifacts/team-tasks/
   company 等)分开;后者列为删除集。
4. **删工作台前端**:`components/workspace`(314)、`ai-elements`、`realtime`、
   `landing`、`app/workspace`(33)+ §3 甄别出的纯工作台 core/。同步 router 摘除
   对应路由。每删一批 `pnpm build` + 桌面冒烟。
5. **决定浏览器/商店去留**:若保留 os 桌面浏览器,保 `components/browser` + 其
   core 依赖;否则一并删。
6. **收尾**:os 前端 = 桌面 + appliance + 共享原语/基建 + (可选)浏览器。

## 5. 已落地(非破坏性接缝,os b5573da)

- `frontend/src/appliance/agent-workspace.ts`:`resolveAgentWorkspaceUrl()` —
  默认同源 `/workspace/realtime/new`,可经 `window.__OCTOPUS_AGENT_WORKSPACE_URL__`
  注入指向外部 agent 服务。
- `app/desktop/page.tsx`:Dock 加 "Agent 工作台" 项,`openWindow` 开 iframe 窗口
  (dogfood 窗口管理器)。预览实测:开出桌面窗口、iframe 加载工作台全 UI、无递归。

**这层是开关**:问题 A 解决后,把 URL 指向外部,即可在"自带 vs 外部"间切换,
为 §4 的逐步删除提供随时可回退的过渡态。

## 6. 风险与原则

- **不一次性大删**:650 文件牵连深(尤其 core/ 混用),分步删 + 每步 build/冒烟。
- **保留 os 身份层**:appliance/、app/desktop、components/ui、共享 hooks/lib —— 留。
- **浏览器/商店按产品决策**:它们是"os 桌面应用"还是"工作台一部分",由你定。
- 与 P1 一致:能本地 `pnpm build` + 预览验证;镜像层面待 NAS 验证。
