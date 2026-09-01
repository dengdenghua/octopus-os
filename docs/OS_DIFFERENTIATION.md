# Echo OS 定位与差异化

> 回答一个战略问题:**echo-os 如何与 echo-agent 拉开差距?**
> 当前差距很窄(os ≈ agent 全套 ~22 万行 + 一层薄 appliance/desktop),
> 本文确立差异化方向 + "fork→依赖"的结构性迁移路线,让差距复利增长。

## 1. 核心定位:engine vs device

不是"同一个东西的两个版本",而是**两个品类**(类比 Chrome vs ChromeOS):

| | echo-agent | echo-os |
|---|---|---|
| 是什么 | **引擎**:嵌入的运行时 / 调用的服务 / 浏览器里的工作台 | **设备**:拥有整台机器,开机进桌面,是系统本身 |
| 买家 | 开发者 / 集成商(要个 Agent 引擎) | 家庭服务器/NAS 主人(要私有 AI 设备) |
| 关系 | 皇冠上的宝石(大脑) | 把宝石装进一台能卖给普通人的设备 |

差距不来自"功能更多",来自 **os 能做、agent 做不了也不该做的事**。

## 2. 四条差异化轴(agent 不能/不该有)

1. **设备与硬件主权** — agent 是进程/客人;os 是房主:开机引导、HDMI/电视
   模式、真实存储(OMV 的 RAID/SMB)、系统级自动化、A/B 原子更新。**0 重叠的纯增量。**
2. **桌面即产品(不是工作台)** — agent 的 UI 是 chat 工作台;os 的 UI 是
   **桌面**:窗口管理器 + 启动器 + 文件管理器 + 第三方应用开在窗口里。
   (已有:`appliance/app_registry` 启动器、`frontend/src/appliance/app-window` 窗口、
   文件管理器+回收站。)agent 不该长桌面(臃肿),os 该把它做成一流产品。
3. **Agent 即系统的神经(最深)** — 在 agent 里 Agent 是你打开的一个应用;在
   os 里 **Agent 是系统本身**:桌面/文件/应用/屏幕全可被 Agent 操作,配一套
   **OS 级权限模型**(agent 的 approval/audit 升格为系统权限,像 App 申请摄像头)。
   杀手体验:**对着设备说话,它跨你所有应用/文件/存储替你办事。**
4. **本地主权** — agent 是通用、可上云、多租户味;os 是**单用户、本地优先、
   数据不出门**(ollama 本地模型、你的 NAS、你的文件)。一个隐私/所有权产品。

## 3. 结构性迁移:从"被 fork"到"被消费"(最重要的一招)

当前 os 是 agent 的 git 复制 + 分叉 → 每改 agent 都要 merge,且 os 的身份被
22 万行继承代码淹没。和我们对**企业版改服务化调 agent**、**os 服务化调企业版
PM** 同一个原则:

> **os 不该是 agent 的 fork,而该把 agent 当一个 pinned 版本的依赖/服务来消费。**
> 那么 os 自己的代码就 = 它的差异化(设备/桌面/本地层),复利增长不漂移。

### 前置条件:agent 长出干净的扩展 API(对所有消费者都有利)

迁移的真正前提是 agent 提供**插件/扩展点**,让 os / 企业版 / mobile 不 fork 也能扩展:
- **挂自定义路由**:`create_app(..., extra_routers=[...])` 或注册钩子。现在 os 的
  appliance 块是直接改在 agent 的 `app.py` 里(fork),应抽成 agent 的官方扩展点。
- **注册自定义技能**:`register_all` 现在被 os 改了(挂 PM 技能);应变成 agent
  暴露的 `register_external_skills(hook)`。
- **前端扩展点**:agent 工作台支持外部面板/路由注入。

这套"agent 插件 SDK"是整个生态(企业版/os/mobile 消费 agent)的公共地基。

### 迁移阶段

- **P0 · agent 插件化**:把 os 现在 fork 改的两处(app.py 的 appliance 块、
  builtins 的 PM 技能钩子)反向贡献成 agent 的官方扩展 API。
- **P1 · os 后端去 fork**:os 删掉继承的 `runtime/`,把 agent 作为 pinned pip
  依赖装入;`appliance/` 通过扩展 API 挂载。os 后端 = 纯 appliance 层 + 版本钉。
- **P2 · 前端解耦(较难,但有妙招)**:**用 os 自己的窗口管理器把 agent 工作台
  当一个应用开在窗口里**——agent 作为服务跑、其工作台 UI 在 os 桌面窗口中加载。
  这样 os 不必 fork agent 的前端,还顺手 dogfood 了窗口系统。
- **P3 · os 仓库 = 纯设备/桌面/本地层 + 一个 agent 版本钉**。差距成为结构性。

## 4. 近期就能拉开差距的动作(建在已有基础上)

1. **反向代理 + 窗口化**(P2)→ 第三方应用真正开在桌面窗口 = agent 没有的桌面体验。
2. **HDMI/原生路线**(P2.5/P3)→ 从"浏览器里的网页桌面"变成"开机即用的设备"。
3. **OS 级 Agent 权限模型** → safety/approval 升格为"设备权限",Agent 动文件/
   装应用要授权 = "你的设备,Agent 操作,有权限闸"。
4. **本地优先叙事落地** → ollama 默认、首启纯本地可跑、"数据不出门"做成卖点。

## 5. 反向提醒:别在哪儿投

- **别让 os 长 agent 该长的东西**(更强 ReAct、更多技能、自演化)——那是 agent
  的活,os 投了就是重复 + 窄差距。
- **别把 os 做成"也能当通用 agent 服务"**——会和 agent 撞。os 要狠狠对准
  "家庭服务器盒子的主人",砍掉一切"开发者引擎"味的东西(删 PM、单用户认证、
  appliance profile 关 swarm/hearts —— 方向已对)。

---

**一句话:os 与 agent 拉开差距,不是比 agent 多做功能,而是变成不同品类——一台
你拥有的、以 agent 为神经的私有 AI 设备;并在架构上把 agent 从"被 fork 的代码"
变成"被消费的引擎",让 os 的设备/桌面/本地层成为护城河。两者互相成就,不是竞争。**
