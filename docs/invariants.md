# INVARIANTS · 不变量金本位

> 整套 echo-agent 的**必须-成立**性质清单。
> 协议里的 "Ixx" 散落在 8 份文档里；本文是**中央索引 + 交叉链路 + 执行矩阵**。
>
> **所有 PR review / CI 静态检查 / 运行时断言 / 人工审批门都以本文为准**。违反 = 工程事故。

---

## 1. ID 命名规范

稳定 ID 格式：`<PROTO>-I<N>` 或 `<PROTO>-C<N>`（C = Cross-cutting，本文引入）。

| Protocol | 前缀 | 协议文档 |
|---|---|---|
| Digestion | `DIG` | [protocols/digestion.md](protocols/digestion.md) |
| Reflex | `REF` | [protocols/reflex.md](protocols/reflex.md) |
| Swarm | `SWM` | [protocols/swarm.md](protocols/swarm.md) |
| Immunity | `IMM` | [protocols/immunity.md](protocols/immunity.md) |
| Evolution | `EVO` | [protocols/evolution.md](protocols/evolution.md) |
| Distribution | `DIS` | [protocols/distribution.md](protocols/distribution.md) |
| Budget | `BDG` | [protocols/budget.md](protocols/budget.md) |
| Genome | `GEN` | [protocols/genome.md](protocols/genome.md) |
| Cross-cutting | `CC` | 本文 §3 |

**ID 永不改号、永不复用。** 被废弃的 ID 标 `@deprecated` 保留占位。

---

## 2. 按协议分列的不变量

### DIG · Digestion（7 阶段流水线）

| ID | 内容 | 执行层级 |
|---|---|---|
| DIG-I1 | 阶段契约严格：某阶段 Out 严格匹配下阶段 In，boundary 必校验 | **lint** + runtime |
| DIG-I2 | 阶段可回放：给定 In 重跑得语义等价 Out（除 LLM 随机性外）| runtime test |
| DIG-I3 | 反射短路不跳免疫：REFLEX 路径的 EXECUTE 仍必须过 `immunity.check` | **lint** |
| DIG-I4 | 用户响应不被 STORE 阻塞：SYNTHESIZE 后可返回，STORE 异步 | runtime assert |
| DIG-I5 | 预算单向递减：流水线内 `ink.budget` 只减不增 | runtime assert |
| DIG-I6 | 每阶段必发 OTel span：无 span 的阶段视为未实现 | **lint** |

### REF · Reflex（快路径）

| ID | 内容 | 执行 |
|---|---|---|
| REF-I1 | 反射不绕免疫：命中后仍进 EXECUTE 让 `immunity.check` 跑 | **lint** |
| REF-I2 | 反射必入 Journal：反射响应也是 trajectory，Evolver 需要 | runtime assert |
| REF-I3 | 规则顺序即优先级：不得乱序命中 | runtime assert |
| REF-I4 | Cache key 稳定性：同语义 intent 必须算出同一 key | test |
| REF-I5 | SLM 只分类不生成 | **lint**（禁止 edge_slm.generate 调用）|
| REF-I6 | Reflex 冷启动空库：禁止为凑命中率塞垃圾规则 | 人工 review |

### SWM · Swarm（Boids）

| ID | 内容 | 执行 |
|---|---|---|
| SWM-I1 | 纯函数 + 无全局锁：Boids 决策只读取 Blackboard 当前快照 | **lint**（禁止 lock.acquire 于 boids 模块）|
| SWM-I2 | Separation 最高优先：永远优于 Alignment/Cohesion | runtime assert |
| SWM-I3 | Alignment 必同 tick：分片后等 `sync_tick` 一起启动 | runtime assert |
| SWM-I4 | Cohesion 软约束：helper/learner 失败不影响原簇 | runtime assert |
| SWM-I5 | Claim 必设 TTL：幽灵 claim 会阻塞后续腕 | **lint** |
| SWM-I6 | 决策必经 Blackboard：不得腕内本地私决策 | code review |

### IMM · Immunity

| ID | 内容 | 执行 |
|---|---|---|
| IMM-I1 | 每次 Beak.bite 前必过 `immunity.check` | **lint**（核心规则）|
| IMM-I2 | Tolerance 白名单是最短路径：自己人不走 Memory/Adaptive | runtime assert |
| IMM-I3 | Memory 只追加：攻击模式只增 + LRU，不可手动删 | runtime assert |
| IMM-I4 | Adaptive 冷启动保守：无基线返回 0.5 | runtime test |
| IMM-I5 | 攻击识别需双信号：`is_attack_like ≥ 2 signals` | runtime assert |
| IMM-I6 | 免疫事件必入 Journal：供 Evolution 和审计 | runtime assert |

### EVO · Evolution（行为层）

| ID | 内容 | 执行 |
|---|---|---|
| EVO-I1 | 评分只用 Batch API：实时 API 评分 = 成本失控 | **lint** |
| EVO-I2 | 新 Skill 必 shadow 后上线：未通过不得进 canary | runtime gate |
| EVO-I3 | Canary 限 5% 流量：失败立即 retire | runtime assert |
| EVO-I4 | Prompt 段注入后必 flush prompt cache | runtime assert + telemetry |
| EVO-I5 | Memory × Evolution 双写隔离：免疫事件和 trajectory 分表 | schema enforce |
| EVO-I6 | Rule 数量上限 + LRU：注入 prompt 的规则不得无限增长 | runtime assert |
| EVO-I7 | Evolver 不直接改 LLM 权重：只改 prompt | **lint** |

### DIS · Distribution（端云）

| ID | 内容 | 执行 |
|---|---|---|
| DIS-I1 | Personal 绝不出 Edge：最硬约束 | **lint** + runtime gate |
| DIS-I2 | Edge 必须能独立活：Cloud 全挂时仍响应基础任务 | runtime test（故障注入）|
| DIS-I3 | 路由必有 fallback_plan | runtime assert |
| DIS-I4 | Evolution 不过边：进化流水线只在 Cloud 跑 | **lint** |
| DIS-I5 | 敏感字段端加密：跨 tier 传输前在发送方加密 | **lint** |
| DIS-I6 | Offline 模式必广播：降级必通知用户 | runtime assert |

### BDG · Budget（成本治理）

| ID | 内容 | 执行 |
|---|---|---|
| BDG-I1 | 预算单向：`tokens_spent` / `usd_spent` 严格单调递增 | runtime assert |
| BDG-I2 | Reserve 必须原子：并发调用不得绕过 reserve | **lint** + runtime assert |
| BDG-I3 | Reserve/Commit 成对：未 commit 30s 视丢失、归还预留 | runtime assert |
| BDG-I4 | 熔断不自愈：状态只能 open → half_open → closed | runtime assert |
| BDG-I5 | 熔断触发必广播：不得静默熔断 | runtime assert |
| BDG-I6 | Cost profile 防抖：同 sucker 24h 内最多 1 次告警 | runtime assert |
| BDG-I7 | 熔断后写 Journal：每次 squirt 是进化素材 | runtime assert |

### WFR · Workflow Rewrite（工作流自改写）

| ID | 内容 | 执行层级 |
|---|---|---|
| WFR-I1 | Workflow 外部 I/O schema 不可单方改 | Schema enforce + gene_lock QUORUM |
| WFR-I2 | 单次失败不改写 —— 聚类 ≥ 5 次 | Runtime Assert |
| WFR-I3 | 改写必双目标：修失败 + 不退成功 | **Runtime Gate** |
| WFR-I4 | 运行中工作流不可被改写（in-flight immunity）| Runtime Assert |
| WFR-I5 | Gap Fault 改写必标 nuclear | Runtime Gate + Human Gate |
| WFR-I6 | Skill 升级热替换必校验 schema 兼容 | Runtime Gate |
| WFR-I7 | 改写历史链不可循环 | Runtime Assert |
| WFR-I8 | Rewrite rationale 必入 Journal | Runtime Assert |
| WFR-I9 | Subgraph Replace 强制 Shadow ≥ 200 条 | Runtime Gate |
| WFR-I10 | Remove 动作必附回滚预案（30 天保留）| Schema enforce |

### RCP · Recipe（上下文配方进化）

| ID | 内容 | 执行层级 |
|---|---|---|
| RCP-I1 | Recipe 绑 task_type，不可跨类型直接用 | Runtime Assert |
| RCP-I2 | 同 arm 在同 task_type 下 10 次内不切配方（sticky）| Runtime Assert |
| RCP-I3 | F-Recipe 样本数 < 阈值时不得参与 Thompson | Runtime Assert |
| RCP-I4 | Shadow 评估 ≥ 100 条 trajectory 回放 | **Runtime Gate** |
| RCP-I5 | 高方差配方不得晋升 active（即使均值高）| Runtime Gate |
| RCP-I6 | Active 晋升时老 active 保留为 backup | Runtime Assert |
| RCP-I7 | 跨 task_type crossover 限白名单 | Schema enforce |
| RCP-I8 | Recipe 变更必入 Journal | Runtime Assert |
| RCP-I9 | Quota 字段总和 = 1.0 | Schema enforce |

### CFR · Conflict Resolution（冲突消解底座）

| ID | 内容 | 执行 |
|---|---|---|
| CFR-I1 | 所有 assertion 必带 source + confidence + ts | Schema enforce |
| CFR-I2 | 策略顺序固定：Temporal → Evidence → Trust → Confidence → Recency → Escalate | Runtime Assert |
| CFR-I3 | Recency 不得单独定胜负 | Runtime Assert |
| CFR-I4 | 被驳回的 assertion 归档不删除 | Schema enforce |
| CFR-I5 | 推理产出 trust 上限 0.5 | Runtime Assert |
| CFR-I6 | Source trust 用 EMA，不允许大幅跳变 | Runtime Assert |
| CFR-I7 | 无法消解的 conflict 必广播 + Journal | Runtime Assert |
| CFR-I8 | 消解历史不可改写 | Schema enforce |

### KG · Knowledge Graph（知识图谱）

| ID | 内容 | 执行 |
|---|---|---|
| KG-I1 | Triple 写入必经 conflict_resolver | Lint + Runtime Gate |
| KG-I2 | 推理深度硬顶 3 跳 | Runtime Assert |
| KG-I3 | 推理 triple 默认不落盘，≥ 2 路径支持才持久化 | Runtime Assert |
| KG-I4 | Ontology 修改必 QUORUM | gene_lock |
| KG-I5 | Entity 合并必审计 | Runtime Assert |
| KG-I6 | 删除必走 archive 路径 | Schema enforce |
| KG-I7 | Personal 实体 triple 不出 Edge（复用 DIS-I1）| Lint |
| KG-I8 | Bulk ingest 必去重 | Runtime Assert |

### MEM · Memory Consolidation（记忆巩固）

| ID | 内容 | 执行 |
|---|---|---|
| MEM-I1 | Episodic 永不修改，只 append | Schema enforce |
| MEM-I2 | Semantic 写入必经 conflict_resolver | Runtime Gate |
| MEM-I3 | Decay 只降 priority 不删除 | Runtime Assert |
| MEM-I4 | REM 产出 trust 上限 0.4 | Runtime Assert |
| MEM-I5 | Procedural 规则上限 30 条（复用 EVO-I6）| Runtime Assert |
| MEM-I6 | Procedural 规则变更必 flush prompt cache（复用 CC-3）| Runtime Assert |
| MEM-I7 | Semantic ↔ KG 同步双写必绑定 id | Schema enforce |
| MEM-I8 | 召回必更新 last_accessed | Runtime Assert |
| MEM-I9 | Personal 记忆不出 Edge（复用 DIS-I1）| Lint |

### SKT · Skill Testing（自进化保险丝）

| ID | 内容 | 执行层级 |
|---|---|---|
| SKT-I1 | Golden 测试 IMMUTABLE，系统无权改写 | Schema enforce + gene_locks |
| SKT-I2 | Regression 测试 append-only | Schema enforce |
| SKT-I3 | 写盘前必过全层测试 | **Runtime Gate** |
| SKT-I4 | Canary → Public 必再跑 full suite | Runtime Gate |
| SKT-I5 | 测试结果必入 Journal | Runtime Assert |
| SKT-I6 | Critic LLM 必比被测 skill 降档或换供应商 | **Lint** |
| SKT-I7 | 合成边界用例进库前必须"区分有效" | Runtime Assert |
| SKT-I8 | 依赖变更必触发受影响 skill 的测试 | Runtime Assert |

### GEN · Genome（架构自进化）

| ID | 内容 | 执行 |
|---|---|---|
| GEN-I1 | 三门必过：Schema → Shadow → Canary 缺一不可 | runtime gate |
| GEN-I2 | Nuclear 不自动：schema_version 升级必须人工批准 | **human gate** |
| GEN-I3 | 回滚永远可行：上一代 production Genome 不得被删 | runtime assert |
| GEN-I4 | CRDT 合并后必 Schema 校验：避免畸形 DNA | runtime assert |
| GEN-I5 | 变异一次一字段：多字段变异禁用 | runtime assert |
| GEN-I6 | Genome 变更必入 Journal：完整可审计 | runtime assert |
| GEN-I7 | Patch 不可逆操作必标 nuclear：`arm_registry.remove` = nuclear | schema enforce |
| GEN-I8 | Production 至少保留 N 代（默认 10）防雪崩 | runtime assert |

---

## 3. Cross-cutting · 跨协议链路不变量

单协议不变量只守住**本协议内部一致性**；真正事故往往来自**协议交汇处**。这一节是本文的核心价值。

### CC-1 · "反射命中 → 免疫 → Journal" 完整链

**参与方**：DIG-I3 + REF-I1 + REF-I2 + IMM-I1 + IMM-I6 + EVO-I5
**描述**：反射路径即便命中 cache，EXECUTE 阶段仍必须过 `immunity.check`，且事件入 Journal。
**违反后果**：cache 中毒攻击绕过免疫，直接污染生产。
**静态 lint 规则**：
```
RULE NO_BYPASS_IMMUNITY
  For any call graph reaching `beak.bite(*)`,
  there must be a reachable `immunity.check(*)` call
  on all execution paths (including reflex).
```

### CC-2 · "熔断触发级联广播" 链

**参与方**：BDG-I5 + BDG-I7 + SWM-C1 + IMM-I6
**描述**：`ink.squirt` 必须同时做三件事：
1. `chromatophores.publish("alert.budget" / "alert.loop")` (BDG-I5)
2. `genome.journal.write_squirt_event` (BDG-I7)
3. 若原因是 loop/fail，同步触发 `immunity.adaptive.update`（行为可疑）

**违反后果**：静默熔断 → 其他腕不知道 → 继续撞同一堵墙。
**Runtime 断言**：`squirt()` 返回前校验三处调用 trace。

### CC-3 · "Prompt 前缀稳定性" 链

**参与方**：EVO-I4 + GEN-I1 + REF-I4 + Prompt caching 本身
**描述**：凡触发以下任一动作的，必 flush prompt cache 并重新 warm：
- Evolution 注入新 rule 到 Cerebrum prompt（EVO-I4）
- Genome DNA patch 改动了 `cortex_policy.system_prompt`（GEN-I1）
- Reflex 规则变更影响到 cache key 语义（REF-I4）

**违反后果**：cache hit rate 断崖；一天烧几百刀的典型姿势。
**Runtime 监控**：`cache_hit_rate < baseline * 0.7` 立即告警。

### CC-4 · "敏感数据端到端路径" 链

**参与方**：DIS-I1 + DIS-I5 + IMM-Innate（signature_check）+ GEN-I?（DNA patch 加密）
**描述**：Personal 级数据从产生到销毁全路径必在 Edge 内；任何跨节点传输在发源地加密、接收方解密；DNA patch 若含 personal 字段视同 personal。
**违反后果**：data leak 事故，法律风险。
**静态 lint**：
```
RULE PERSONAL_DATA_NO_EGRESS
  Any function tagged @privacy(personal) must not
  reach code in distribution.bus, eyes.cloud_providers,
  genome.dna.patch.cross_tier_sync.
```

### CC-5 · "只追加、不删除" 族

**参与方**：IMM-I3 + BDG-I1 + EVO journal + GEN-I3 + GEN-I6
**描述**：以下存储只允许 append + LRU（永不手工 delete）：
- Immunity 攻击模式库（IMM-I3）
- Budget 花费账本（BDG-I1）
- Evolution trajectory journal
- Genome Registry 的 production 历史（GEN-I3）
- Genome 变更事件流（GEN-I6）

**违反后果**：审计断链、回滚失败、攻击记忆丢失。
**Schema enforce**：对应表无 DELETE 权限；清理走 LRU/TTL 后台任务。

### CC-6 · "变异必单字段 + 必归因" 链

**参与方**：GEN-I5 + EVO-I5 + GEN-I6
**描述**：任何 mutation（无论 Regeneration 还是 Genome Evolution）必须：
1. 一次只动一个字段（GEN-I5）
2. 带 parent_id / source_trajectory_id（EVO-I5）
3. 入 Journal 包含完整 diff（GEN-I6）

**违反后果**：fitness 下降无法归因 → 进化盲调 → 整体性能退化。
**Runtime 断言**：mutation diff 字段数 > 1 → 抛异常。

### CC-7 · "人工门" 族

**参与方**：GEN-I2 + DIS-I?（纳入未来）+ IMM mitigation
**描述**：下列操作**绝不可自动执行**，必须人工审批：
- Genome schema_version 升级（GEN-I2 · nuclear）
- 关闭 Immunity 对某 sucker 的隔离
- 向 `trusted_sources` 白名单加条目
- 删除 Attack Pattern（IMM-I3 的唯一例外）
- Production Genome 大规模回滚跨 ≥ 3 代

**执行层级**：审批工作流（Slack/Email/PR approval）+ 操作日志。

### CC-C1..C2 · 冲突消解链

- **CC-C1** · 所有知识写入（KG/Memory）必经 conflict_resolver（CFR-I1..I2 + KG-I1 + MEM-I2）（Lint + Runtime Gate）
- **CC-C2** · Trust score 漂移守卫：一周漂移 > 0.3 告警（CFR-I6 + FITNESS drift）

### CC-M1..M2 · 记忆链

- **CC-M1** · Consolidation 只在业务低峰跑（默认 02:00-06:00），不占 Branchial Heart（MEM + GEN nuclear-time）
- **CC-M2** · 记忆召回 ↔ Recipe 绑定产生可对照统计（MEM + RCP-I1）

### CC-W1..W4 · Workflow 改写链

- **CC-W1** · Schema 契约铁律：workflow 外部 I/O 改动需 QUORUM（WFR-I1 + DIG-I1 + GEN-I7）
- **CC-W2** · 改写必双证据：只看 fix_rate 会学会"跳过难题"（WFR-I3 + FITNESS coverage）
- **CC-W3** · In-flight 不改写：运行时 patch 必先 drain（WFR-I4 + GEN warm blast）
- **CC-W4** · 改写链不可循环：检测 A→B→C→A 即停 7 天（WFR-I7 + EVO-I5 + GEN-I5）

### CC-R1..R3 · Recipe 链

- **CC-R1** · Recipe 插入 Fitness 五层对齐链：`F-Skill ≤ F-Trajectory ≤ F-Recipe ≤ F-Arm ≤ F-Genome`（Runtime Assert）
- **CC-R2** · Task_type 分类器必须独立于 Recipe，不得让 recipe 反向影响分类（**Lint**）
- **CC-R3** · 方差是配方的一票否决（高均值 + 高方差 ≠ 晋升）（Runtime Gate）

### CC-S1..S3 · 技能测试保险丝链

- **CC-S1** · "无测试不写盘"：SKT-I3 + GEN-I1 + EVO-I2（Lint + Runtime Gate）
- **CC-S2** · "Golden 测试是宪法"：SKT-I1 + IMMUTABLE gene_lock
- **CC-S3** · "测试失败降信任"：SKT-I5 + IMM-I4 + BDG cost profile 联动

### CC-G1..G5 · 基因锁链

详见 [gene-locks.md](gene-locks.md) §9。核心五条：
- **CC-G1**：锁门在 Schema/Shadow/Canary 三门之前执行（Runtime Gate）
- **CC-G2**：IMMUTABLE 字段永不可变，回滚时亦然（Schema enforce）
- **CC-G3**：成熟度等级降级自动、回升必须重新证明（Runtime Assert）
- **CC-G4**：Monotonic 字段自主只能朝严，放松必人工（Runtime Gate）
- **CC-G5**：Panic override 使用后系统自动降到 Level 1 + 30d 冷却（Runtime Assert）

### CC-8 · "预算语义环" 链

**参与方**：BDG-I1 + BDG-I2 + BDG-I3 + DIG-I5
**描述**：预算系统在流水线内的**完整半环**：
```
create_budget → reserve → execute → commit → (optional) refund_reserved
              ↑_____________________________________|
```
任何调用都不能跳步。commit 之后不得 refund；reserve 之后 30s 未 commit 自动 refund。
**Runtime 断言**：budget state machine 转移表严格校验。

---

## 4. 执行矩阵

每条不变量归入一种或多种执行层级：

| 层级 | 触发时机 | 失败处理 | 覆盖哪些 I |
|---|---|---|---|
| **Lint** | commit / PR review / pre-push hook | 阻断合并 | DIG-I3, DIG-I6, REF-I1, REF-I5, SWM-I1, SWM-I5, IMM-I1, EVO-I1, EVO-I7, DIS-I1, DIS-I4, DIS-I5, BDG-I2, CC-1, CC-4 |
| **Runtime Assert** | dev/test 环境函数入口 | 抛异常停流 | DIG-I4, DIG-I5, REF-I2, REF-I3, SWM-I2, SWM-I3, SWM-I4, IMM-I2, IMM-I3, IMM-I5, IMM-I6, EVO-I3, EVO-I4, EVO-I6, BDG-I1, BDG-I3, BDG-I4, BDG-I5, BDG-I6, BDG-I7, GEN-I3, GEN-I4, GEN-I5, GEN-I6, GEN-I8, CC-2, CC-6, CC-8 |
| **Runtime Gate** | 生产环境阻断路径 | 拒绝请求 + 告警 | EVO-I2, DIS-I1（二次验证）, GEN-I1, GEN-I7 |
| **Runtime Test** | CI 集成测试 | CI 红灯 | DIG-I2, IMM-I4, DIS-I2 |
| **Human Gate** | 审批工作流 | 人工确认前不得执行 | GEN-I2, REF-I6, CC-7 |
| **Schema Enforce** | 数据库/Registry schema | 写入失败 | EVO-I5, GEN-I7, CC-5 |
| **Telemetry Alert** | 监控告警 | 人工介入 | CC-3（cache hit rate）|
| **Code Review** | PR 人工阅读 | 退回修改 | REF-I6, SWM-I6 |

---

## 5. 静态 Lint 规则（可直接实现）

以下是可以用 `ast` / regex / custom pyright plugin 直接落地的规则。每条给出名称、检测方法、违规样例。

### LINT-01 · NO_BYPASS_IMMUNITY

- **守护**：IMM-I1 + DIG-I3 + REF-I1 + CC-1
- **检测**：AST 全局扫描。任何调用 `beak.bite(*)` 或 `ToolExecutor.execute(*)` 的函数，必须在调用方路径上能到达 `immunity.check(*)`。
- **违规**：
  ```python
  def run(sucker, args):
      result = beak.bite(sucker, args)    # ❌ 未过免疫
  ```
- **正确**：
  ```python
  def run(sucker, args):
      if immunity.check(call=...).allowed:
          result = beak.bite(sucker, args)
  ```

### LINT-02 · NO_MAGIC_ORGAN_COUNT

- **守护**：ARCHITECTURE "数字是诗意不是契约" + I GEN-I7
- **检测**：regex `range\((\s*)8(\s*)\)` 或 `\[0\]\s*\*\s*8`，在 `arms/` 路径附近触发。
- **违规**：
  ```python
  for i in range(8):
      spawn_arm(i)                        # ❌ magic 8
  ```
- **正确**：
  ```python
  for arm_spec in config.arms.types:
      spawn_arm(arm_spec)
  ```

### LINT-03 · BIO_NAME_IN_CODE

- **守护**：naming.md 双轨契约
- **检测**：类名 / 函数名 regex 命中生物词（`Cerebrum` `Ganglia` `Chromatophore` `Siphon` `Beak` 等）即违规 —— 只允许在 import 别名里。
- **违规**：`class Cerebrum:` / `def chromatophore_publish():`
- **正确**：`class Planner:` / `def signal_publish():`；`Cerebrum = Planner` 仅作 re-export。

### LINT-04 · NO_RAW_LLM_CALL

- **守护**：EVO-I1 (成本控制) + Prompt cache 规范 + CC-3
- **检测**：禁止直接 import `anthropic` / `openai` / `google.genai`（除 `eyes/models/` 内部）；外部必过 `eyes.ModelRouter`。
- **违规**：`from anthropic import Anthropic` 在 `arms/code_arm.py`
- **正确**：`from runtime.sensing.model_router import ModelRouter`

### LINT-05 · TASK_NEEDS_BUDGET

- **守护**：BDG-I2 + DIG-I5
- **检测**：`Task(...)` 构造器必须传入 `max_tokens` 且 `max_cost_usd`。AST kwargs 检查。
- **违规**：`Task(goal="...")  # ❌ 无预算`
- **正确**：`Task(goal="...", max_tokens=50_000, max_cost_usd=0.50)`

### LINT-06 · PERSONAL_NO_EGRESS

- **守护**：DIS-I1 + DIS-I5 + CC-4
- **检测**：装饰器 `@privacy(personal)` 标注的函数，其调用图不得到达 `distribution.bus.send_to_cloud` / `eyes.cloud_providers.*`。
- **违规**：personal-tagged function writes to cloud bus
- **正确**：写入 Edge 本地 Blackboard 或经端加密

### LINT-07 · NO_GENESTUDIO_SHORTCUT

- **守护**：GEN-I1 + GEN-I5 + CC-6
- **检测**：`registry.commit(...)` 的上游必须穿过 `schema_gate` → `shadow_gate` → `canary_gate`。
- **违规**：`registry.commit(new_genome)` 在未经 shadow 的路径上调用。
- **正确**：通过 `evolver.propose(...)` 入口，它会串联三门。

### LINT-08 · MUTATION_SINGLE_FIELD

- **守护**：GEN-I5 + CC-6
- **检测**：`mutate()` 返回值与输入 diff 后字段数必 = 1。AST 无法完全静态检查 —— 改为 runtime-only 的断言。（本 lint 仅作提醒，真正防线在 runtime）

### LINT-09 · REGEX_REFLEX_NO_GENERATE

- **守护**：REF-I5
- **检测**：`reflex/` 模块下禁止 import LLM 客户端、禁止 call `.generate()`。
- **违规**：reflex 规则 handler 里 `return llm.generate(...)`
- **正确**：reflex handler 必须是确定性 `response` 或 emit 事件

### LINT-10 · CRDT_NOT_LWW

- **守护**：GEN-I4 + CRDT 语义
- **检测**：Genome field 操作禁止直接 `dict.update` / `list.append` / `=`；必须通过对应 CRDT 类型方法。
- **违规**：`genome["scheduler_policy"] = "x"`
- **正确**：`genome.scheduler_policy.set(value, vclock=...)`

---

## 6. Runtime 断言标准位置

哪些函数必须埋 assert：

| 函数 | 守护 I |
|---|---|
| `beak.bite()` 入口 | IMM-I1, DIG-I3, CC-1 |
| `ink.reserve()` / `ink.commit()` | BDG-I1, BDG-I2, BDG-I3, CC-8 |
| `ink.squirt()` | BDG-I5, BDG-I7, CC-2 |
| `immunity.check()` | IMM-I2, IMM-I4 |
| `regeneration.forge_skill()` | EVO-I2 |
| `regeneration.inject_into_planner()` | EVO-I4, CC-3 |
| `genome.registry.commit()` | GEN-I1, GEN-I3, GEN-I6 |
| `genome.mutate()` | GEN-I5, CC-6 |
| `genome.crdt.merge()` | GEN-I4 |
| `hearts.enter_offline_mode()` | DIS-I6 |
| `chromatophores.separation()` / `alignment()` / `cohesion()` | SWM-I1, SWM-I2, SWM-I3 |

---

## 7. 失败模式分类（当不变量被违反）

把不变量违反按**后果严重度**分级：

| 级别 | 后果 | 例 |
|---|---|---|
| **P0 · 事故** | 数据泄漏 / 全系统瘫痪 / 成本爆炸 | DIS-I1（personal 外泄）· BDG-I1（预算穿透）· GEN-I3（回滚失败）|
| **P1 · 严重退化** | 核心功能失效 / 大量错误 | DIG-I3（免疫绕过）· CC-2（静默熔断）· CC-3（cache 雪崩）|
| **P2 · 质量下降** | 用户感知明显变差 | REF-I4（cache 失效）· EVO-I4（prompt cache miss）· SWM-I3（thundering herd）|
| **P3 · 运维困扰** | 可诊断性降低 | DIG-I6（无 span）· IMM-I6（无 journal）· GEN-I6（无 diff 记录）|
| **P4 · 风格违规** | 代码质量问题 | LINT-03（命名越界）|

---

## 8. 本文的元不变量（Meta-Invariants）

本文自己也遵守几条规矩，否则它会慢慢烂掉：

- **MI-1 · ID 不复用**：删除一条 I 就标 `@deprecated`，新的用新号
- **MI-2 · 每条 I 必有执行层级**：没有落地方案的 I 是愿望，不是不变量
- **MI-3 · 新增 I 必同步三处**：协议文档 + 本文 §2 + 执行矩阵（§4）
- **MI-4 · Cross-cutting 不重复**：CC 条目只写"交汇"；单协议内部的不复抄
- **MI-5 · 本文也进 PR review**：任何不变量的新增 / 修改 / 删除必专门 review

---

## 附录 · 快速索引

所有 I 的扁平列表：

```
DIG-I1..I6     · 7 阶段流水线     · 6 条
REF-I1..I6     · 反射快路径       · 6 条
SWM-I1..I6     · Boids 群涌       · 6 条
IMM-I1..I6     · 免疫系统         · 6 条
EVO-I1..I7     · 行为进化         · 7 条
DIS-I1..I6     · 端云分布         · 6 条
BDG-I1..I7     · 成本治理         · 7 条
GEN-I1..I8     · 架构自进化       · 8 条
SKT-I1..I8     · 技能测试保险丝   · 8 条
RCP-I1..I9     · 配方进化         · 9 条
WFR-I1..I10    · 工作流自改写     · 10 条
CFR-I1..I8     · 冲突消解底座     · 8 条
KG-I1..I8      · 知识图谱         · 8 条
MEM-I1..I9     · 记忆巩固         · 9 条
CC-1..CC-8     · 跨协议链路       · 8 条
CC-F1..CC-F4   · Fitness 链路     · 4 条
CC-G1..CC-G5   · 基因锁链路       · 5 条
CC-S1..CC-S3   · 技能测试链路     · 3 条
CC-R1..CC-R3   · Recipe 链路      · 3 条
CC-W1..CC-W4   · Workflow 链路    · 4 条
CC-C1..CC-C2   · 冲突消解链路     · 2 条
CC-M1..CC-M2   · 记忆链路         · 2 条
MI-1..MI-5     · 元规则           · 5 条
─────────────────────────────────────
合计                              · 139 条
```
