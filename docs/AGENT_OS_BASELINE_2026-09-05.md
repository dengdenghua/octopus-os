# Agent OS 第一阶段本机基线

日期：2026-09-05。环境：Windows，Node.js v24.19.0，当前工作区（含未提交优化）。
本记录不是干净 checkout、Linux 构建或物理 NAS 的交付证据。

本页保留早期基线。后续已修复 Windows 状态锁/认证 ACL，完成真实 CLI 两次启动与
重启认证，以及隔离目录中的锁定前端依赖安装；当前结果见
[打磨记录](AGENT_OS_POLISH_STATUS.md#实际装配与-windows-状态修复后的追加结果)。

## 第二轮更新（2026-09-05）

以下结果覆盖后文对应的初次记录，未列出的限制仍然存在。

- Windows 模型配置复制已改为：创建空暂存目录 → 用当前用户 SID 设置独占 DACL 并回读 →
  新建文件写入并 fsync → 用不覆盖目标的硬链接发布 → 清理暂存路径。不会修改已有配置，
  ACL 失败或文件系统不支持硬链接时不回退到不安全复制。
- Linux/macOS 保留私有暂存目录与 `0600` 文件模式；本轮实跑在 Windows，POSIX 分支仍需 Linux CI。
- Windows 测试直接读取最终文件 ACL，确认唯一授权项为当前用户 FullControl；另验证特殊字符
  路径、ACL 工具失败、源读取失败、清理及既有配置保留。开发脚本集合现为 **18/18 通过**。
- Python `.venv` 已使用 Python 3.12 和 uv 0.11.25 按 `uv.lock` 安装 dev/serve/web/local-auth
  依赖，未修改锁文件。真实配置预检已选中该虚拟环境和仓库示例。
- 开发启动器为 Python 子进程设置 UTF-8；启动合同测试也显式使用 UTF-8 读取源文件，
  Windows GBK 解码失败已修复。
- 后端定向测试命令：`.venv/Scripts/python.exe -X utf8 -m pytest
  tests/appliance/test_entrypoint.py tests/appliance/test_agent_compat.py
  tests/appliance/test_task_projection.py tests/appliance/test_capabilities.py -q --tb=short`。
  结果为 **46 通过、7 失败**，没有将失败项跳过。
- 7 项失败中，3 项来自 `appliance/auth.py` 对私有目录的 POSIX 模式检查，在 Windows 上
  拒绝认证状态目录；4 项依赖 Windows 没有的 `os.geteuid`，属于 Linux 容器入口测试。
  模型配置 ACL 修复不等于 Python 认证状态存储已支持 Windows。
- 修改的 JavaScript lint、Python Ruff 和差异空白检查通过。主机没有已配置的 WSL Linux
  环境，尚无 Linux 构建/完整后端启动/真实模型任务证据。

下一步优先修复并验证 Python 认证状态的 Windows 权限边界；Linux 容器入口测试仍应在
Linux 执行，不能以禁用权限检查来获得绿色结果。

## 已修复

- 开发启动器无条件读取未跟踪的 Codex 发布清单，新克隆会 ENOENT。现在允许源码开发缺少
  清单；存在但损坏的清单仍报错，正式入口的制品验证保持独立。
- 默认依赖未跟踪的 `config.local.yaml`。现在优先显式配置及本地配置，否则使用仓库示例。
- 显式 Python 路径错误时可能回退到另一环境。现在明确失败并提供安装说明。
- 新增 `--check`：只检查配置发现、解释器文件路径及端口，不创建运行数据或读取认证秘密。
- 当前架构入口与仿生历史材料分离，开发说明补齐虚拟环境和前端依赖步骤。

## 实测

以下命令直接调用已安装工具，避免当前宿主 pnpm 包装器触发自动安装。

| 检查 | 命令（仓库根目录执行，除注明外） | 结果 |
| --- | --- | --- |
| TypeScript | `node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json` | 通过 |
| 启动回归 | `node --test frontend/scripts/dev-appliance-config.test.mjs` | 6 项通过；不启动真实 Python 后端 |
| 开发脚本集合 | 在 frontend 执行 `node --test scripts/*.test.mjs` | 16 项中 15 项通过；原有模型复制权限用例失败 |
| 任务及能力 UI | 在 frontend 执行 `node node_modules/vitest/vitest.mjs run src/appliance/task-space.test.ts src/appliance/task-space-panel.test.tsx src/core/plugins/use-capability-surface.test.tsx` | 3 文件、12 项通过 |
| 修改的启动脚本 lint | 在 frontend 执行 `node node_modules/eslint/bin/eslint.js scripts/dev-appliance-config.mjs scripts/dev-appliance-config.test.mjs scripts/dev-appliance-backend.mjs --max-warnings=0` | 通过 |

## 初次记录的基线限制（更新见上方）

1. `dev-appliance-model-seed.test.mjs` 在 Windows 上将 POSIX mode 与 `0600` 比较，实际
   返回 `0666`。没有删掉断言或将该失败记作通过；需分别验证 POSIX 权限与 Windows ACL，
   再决定模型配置复制是否满足各平台私密性要求。
2. 工作区无 `.venv`，可用的宿主捆绑 Python 缺少 pytest。Python appliance 测试尚未重跑，
   也没有证明后端端到端启动。下一步应在隔离环境安装仓库锁定依赖并执行现有测试。
3. 本机 `pnpm typecheck` 的包装器尝试安装依赖，并被 `electron-winstaller` 构建策略阻断；
   工具自动加入的占位配置已撤回。类型检查改为直接执行已安装的 tsc，因此不能据此声称
   冻结依赖安装已验证。应在仓库指定 pnpm 版本下复核依赖安装与构建策略。
4. 尚未执行 Linux CI、完整镜像构建、模型任务、真实磁盘故障或物理机升级恢复。

## 下一步

先建立锁定的 Python/Linux 基线，再沿现有能力契约手动走通文档整理任务，收集正常、冲突、
权限和中断样本。当前优化完成的是开发启动发现逻辑及文档基线，尚未完成
[优化方案](AGENT_OS_OPTIMIZATION_PLAN.md) 的阶段 A 全部门槛。
