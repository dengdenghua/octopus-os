# CI 门禁阻塞分析（2026-09-20）

基准：`p3-provision` @ `7342d137`（含桌面重构 + jsdom Blob.stream 修复）。全部结论基于
实证：GitHub Actions API 查询 + 本地复现，不做推测。

## 一、首跑总览

主 CI run `35512100296` 共 10 个 job，推送后实测：

| Job | 结论 | 说明 |
|---|---|---|
| Frontend quality and production build | **failure** | 见 §2 |
| Security scan | **failure** | Bandit ✓ → pip-audit ✗（见 §2） |
| GitHub Actions workflow contract | **failure** | 见 §3，**结构性阻塞** |
| Untracked source guard | success | 上轮修复生效 |
| Cross-platform pytest (macos-15) | success | |
| OMV host artifacts | success | |
| Cross-platform pytest (windows-2025) | 运行中 | |
| Cross-platform pytest (Python 3.12 observation) | 运行中 | |
| Public source contract (3.11.9 / 3.12.11) | 运行中 | |

此外 `behavioral-evidence.yml` 与 `engine-comparison-evidence.yml` 在每次 push 时都会
各产生一个 **零 job、秒失败** 的 run（`created` 与 `completed` 同一秒）。对照实验确认
API 权限足够（主 CI 的 10 个 job 均可见），所以"无 job"是真实状态。

## 二、前两个 job 与本次修复的关系（重要）

**Frontend job 的失败与代码质量无关，且导致本轮修复未被验证。**

精确失败步骤：

```
1. Set up job                        -> success
2. checkout / action-setup / node    -> success
5. Install dependencies              -> success
6. Frontend production dependency audit -> FAILURE  <<<
7. Verify frontend source quality    -> skipped
8. Run frontend and Electron contracts -> skipped   <<< pnpm test 在这里
9. Build production frontend         -> skipped
```

`pnpm audit --prod` 位于 `pnpm format / lint / typecheck / test / build` 之前，
它一挂，**后面四道门禁全部 skipped**。所以 `7342d137` 里修的那 3 条 jsdom
`Blob.stream` 用例，在 CI 上**根本没被执行**——不是修复失败，是没跑到。

本地等价验证（已做）：`vitest` 全量在该修复下这 3 条已转绿；相关 4 个文件 41 passed。

Security scan 方面 Bandit 已是 success，**pip-audit 是唯一失败步骤**。

处理建议：`pnpm audit --prod` 与 `pip-audit` 属依赖漏洞披露，需要定策略
（升级依赖 / 加豁免清单 / 拆成独立非阻塞 job）。**建议把 audit 从阻塞链里拆出去**，
否则任何一次依赖披露都会连带屏蔽掉 format/lint/typecheck/test/build 五道门禁。

## 三、workflow contract：一个结构性矛盾（需要决策）

### 3.1 本地复现

CI 该 job 唯一步骤是 `actionlint -shellcheck= -pyflakes=`（版本钉在 1.7.12）。
本地跑同一版本，**10 个错误**：

| 类型 | 数量 | 位置 |
|---|---|---|
| `[runner-label]` | 2 | `behavioral-evidence.yml:16`（`behavioral-evidence`）、`engine-comparison-evidence.yml:26`（`hardened-verifier`） |
| `[expression]` | 8 | 两个 evidence workflow 的 **job 级 `env`** 里使用 `${{ runner.temp }}` |

### 3.2 两类错误都成立

**`[runner-label]`**：actionlint 只在 `.github/actionlint.yaml` 登记了 `echo-os-image`，
而这两个 workflow 用了另外两个自托管标签。

**`[expression]`**：GitHub 的上下文可用性规则里，`jobs.<job_id>.env` 只放行
`github / inputs / matrix / needs / secrets / strategy / vars`，**不含 `runner`**。
在 job 级 env 写 `${{ runner.temp }}` 会被求值为**空串**，路径退化成
`/echo-behavioral-<run_id>-<attempt>` 这类**文件系统根目录绝对路径**，
后续 `mkdir -p "${ECHO_DATA_DIR}"` 必然失败。这不是风格问题，是真实缺陷。

### 3.3 但我修不了——修好会撞项目自己的测试契约

按上述两点修复后，actionlint **exit=0、10 个错误全清零**。代价是破坏 2 个测试：

```
FAILED tests/appliance/test_delivery_workflow_policy.py::
       test_workflow_linter_is_checksum_pinned_and_knows_the_dedicated_runner
   断言 actionlint 配置精确等于 {"self-hosted-runner":{"labels":["echo-os-image"]}}

FAILED tests/test_engine_comparison_evidence_workflow.py::
       test_protected_runtime_inputs_are_rechecked_without_publishing_auth_identity
   断言 job["env"]["ECHO_PROTECTED_IDENTITY_BASELINE"]
        .startswith("${{ runner.temp }}/echo-protected-identities-")
```

回退基线已验证：改前 **1 failed / 26 passed**（唯一失败是
`test_launcher_validate_cli_runs_as_an_isolated_absolute_script`，持续存在，
伴 `UnicodeDecodeError` 子进程解码告警，属既有环境项，与本次无关）。

**矛盾本质**：项目测试把两件与 actionlint/GitHub 规则冲突的事实**固化成了契约**——
①actionlint 配置只能登记一个 runner 标签；②`${{ runner.temp }}` 必须留在 job 级 env。
而 actionlint 要想通过，恰恰要求这两点不成立。**二者不可能同时满足**，
所以 workflow-contract job 是**结构性必红**，与代码质量无关。

### 3.4 三个候选方向（请择一）

| 方案 | 动作 | 代价 |
|---|---|---|
| **A. 承认 evidence runner 合法**（推荐） | 更新 `.github/actionlint.yaml` 登记两个标签；把 8 处 `runner.temp` 改为 step 内解析后写 `GITHUB_ENV`；同步更新上述 2 个测试的断言 | 需确认这两个自托管 runner 确实存在并受控 |
| **B. 让 evidence workflow 改用托管 runner** | 换 `runs-on`，并解决 `/Applications/ChatGPT.app/...` 与固化签名身份的依赖 | 语义上这些流程需要专用机，改动面大 |
| **C. 把 evidence workflow 移出 lint 范围** | actionlint 加忽略规则（如按文件排除） | 掩盖真实缺陷，不推荐；`[expression]` 那条是真 bug |

方案 A 的改动我已实现并验证过（actionlint exit=0），随后为不越权决定契约而回退，
备份留在 `.workbuddy/wf-backup/`（3 个文件），需要时可直接取回。

### 3.5 附带发现：evidence workflow 为何每次 push 都秒挂

两个 workflow 都声明 `on: workflow_dispatch`（仅手动），但实测 run 的 `event` 是 `push`，
且零 job、秒失败、API 返回的 workflow `name` 是**文件路径**而非文件内的 `name:` 字段
（`engine-comparison-evidence.yml` 的 name 却是正常的）。结合 3.2 的真实缺陷，
怀疑这批 workflow 从未在任何 runner 上成功执行过。建议在方案 A 落地后，
用 `workflow_dispatch` 手动各触发一次确认。

## 四、结论

- 本轮代码修复（`7342d137`）本身有效，但被 `pnpm audit --prod` 挡在 CI 验证之外。
- CI 有两道**与代码质量无关**的阻塞：依赖审计策略未定、workflow contract 契约自相矛盾。
- 建议优先级：先拆开 audit 阻塞链（低风险、立刻恢复 5 道门禁）→ 再定 workflow
  contract 方向（§3.4）→ 最后手动验证 evidence workflow 能否真正跑起来。
