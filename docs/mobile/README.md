# Echo Mobile · 移动触手

> **章鱼伸出去的物理触手 · 让 AI 真正"操控"你的手机与桌面**

Echo Mobile 是 [echo-agent](../README.md) 的**移动端与跨端编排层**。
它让章鱼的中枢（Cerebrum）能**真实操控** Android 设备、桌面设备，并
实现"**手机+电脑+多手机**"的混合编排任务。

---

## 🎯 解决的真实问题

| 场景 | 没有 Echo Mobile | 有 Echo Mobile |
|---|---|---|
| "帮我在淘宝抢个首发" | 需自己写爬虫 / 用 Puppeteer（被反爬） | 发微信 → 章鱼调小米 14 真机自动抢 |
| "用公司 3 台手机比价" | 手动切 3 个 App 来回截屏 | 一句话 → 3 台手机并行跑 + Excel 报表 |
| "在桌面写代码，手机上验收 UI" | 写代码 + 手动 `adb install` + 截屏 | 一个任务，跨端自动编排 |
| "手机上看不了的网页" | 切电脑 / 装模拟器 | 手机端的真浏览器，内核增强反爬 |
| "夜间手机跑测试，早上看报告" | 写脚本 + 部署 CI | IM 定时任务，章鱼自动跑 |

---

## 🌟 核心特性（与 2026 年竞品的差异化）

| 标签 | 价值 |
|---|---|
| **双向控制** | 不止"手机控电脑"，也支持"电脑控手机 + 手机间互控" |
| **真实浏览器** | 可选集成 Chromium 内核（Kiwi 思路）· 反爬免疫 |
| **多设备编排** | 1 个 Runtime 可同时管控 N 台手机 + M 台电脑 |
| **Apache-2.0** | 完全开源，可商用、可魔改 |
| **自托管** | 数据 100% 在自己服务器，零外传 |
| **国内 IM 全覆盖** | 钉钉/微信/飞书/QQ/企业微信 + 海外 15+ |
| **任意 LLM** | OpenAI / Anthropic / DeepSeek / Qwen / GLM 都能接 |
| **自进化** | 夜间 Regeneration 锻造新技能，第二天自动生效 |

---

## 🏗️ 架构一览

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Cerebrum 中枢（echo-agent 已有）              │
│   接收用户任务 → DAG 分解 → 任务分配到 Arm → 协调并发/依赖/失败重试  │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ Nerves 总线 / JSON-RPC 2.0 / WebSocket
                          │
   ┌──────────────────────┼──────────────────────────┐
   ▼                      ▼                          ▼
┌──────────┐         ┌──────────┐              ┌──────────┐
│ Desktop  │         │ Android  │              │  Code/   │
│Operator  │         │Operator  │              │  ...     │
│  Arm     │         │  Arm     │              │  Arms    │
└────┬─────┘         └────┬─────┘              └──────────┘
     │                    │
     │ WebSocket RPC      │ WebSocket RPC
     ▼                    ▼
┌──────────┐         ┌──────────────┐
│ Desktop  │         │  Android     │
│  客户端  │         │  Echo Mobile     │
│ (Electron)│        │  改造版      │
└──────────┘         └──────────────┘
   Tier 1              Tier 1
   (桌面端)             (移动端)
```

完整架构设计见 [architecture.md](architecture.md)。

---

## 📂 目录索引

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 三层三端架构、混合编排杀手锏场景 |
| [protocol.md](protocol.md) | JSON-RPC 2.0 协议详细规范（device/* namespace） |
| [skills.md](skills.md) | 30+ 移动技能的 SKILL.md 体系 |
| [browser-integration.md](browser-integration.md) | 浏览器内核集成方案（Kiwi 思路 + CDP） |
| [getting-started.md](getting-started.md) | 5 分钟跑通"Runtime 控制手机点一下" |

补充：
- 仿生学：[../biomimetic/tentacle/README.md](../biomimetic/tentacle/README.md) — "触手"器官的设计哲学
- 决策记录：[../adr/008-echo-mobile.md](../adr/008-echo-mobile.md)

---

## 🚀 快速状态（2026-06 快照）

| 模块 | 状态 | 说明 |
|---|---|---|
| `runtime/tentacle/` 触手器官 | ✅ Phase 0 骨架 | Device pool + Mobile/Desktop 抽象 |
| `runtime/execution/arms/presets.py` | ✅ 接入 | `make_mobile_operator_arm` 已添加 |
| `runtime/tentacle/mobile/skills/` | ✅ 30 个 | 30 个移动技能的 canonical SKILL.md，MCP/LLM 与 Android assets 由此对齐 |
| `../echo-mobile/` Android 端 | ⏳ Phase 0 概念验证 | RPC 客户端骨架已就位 |
| `docs/mobile/` 文档 | ✅ 完整 | 架构/协议/技能/浏览器集成 |
| 桌面端架构影响 | ✅ 零破坏 | 全 add-only，最坏情况撤掉不损失任何代码 |

---

## 🛣️ 路线图

| 阶段 | 周期 | 目标 |
|---|---|---|
| **Phase 0** ✅ | 3 天 | 概念验证：30+30 行代码，验证 add-only 假设 |
| **Phase 1** ⏳ | 2 周 | 设备注册 + 心跳 + 简单工具执行通路 |
| **Phase 2** ⏳ | 3 周 | 30 个移动技能完整接入 + Cerebrum 调度 |
| **Phase 3** ⏳ | 2 周 | 双写配置 + 离线降级 |
| **Phase 4** ⏳ | 2 周 | 屏幕状态增量上报 + 反爬基础 |
| **Phase 5** ⏳ | 3 周 | 混合编排杀手锏场景（手机+电脑+多手机）|
| **Phase 6** ⏳ | 2 周 | 自进化闭环（Regeneration 锻造新技能） |
| **Phase 7** 🔮 | 4 周 | 浏览器内核集成（Kiwi 思路 + CDP 协议） |

详见 [roadmap.md](../roadmap.md) 阶段 5「触手期 / 跨端期」。

---

## 🤝 贡献

Echo Mobile 接受以下类型的 PR：
1. **新设备类型**（iOS、IoT、嵌入式设备）—— 继承 `runtime/tentacle/base.py`
2. **新移动技能**（SKILL.md + 1 个 Android 实现）—— 参考 `runtime/tentacle/mobile/skills/`
3. **新 IM 触发源**（小众 IM 适配）—— 参考 `docs/channels/`
4. **新场景 demo**（电商多账号、内容爬虫、企业自动化）—— `demos/`

提交前请读 [CONTRIBUTING.md](../../CONTRIBUTING.md) 与 [standards.md](../standards.md)。

---

> 🐙 **章鱼有 8 腕，每一腕都是一个触手。**
> 你的每台手机、每台电脑、每个 IoT 设备，都可以是章鱼伸出去的一根触手。
