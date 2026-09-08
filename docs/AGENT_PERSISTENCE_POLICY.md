# Agent 日志与恢复持久化策略

开发示例和新安装桌面配置显式使用 `journal_file: auto`。原生 Agent 配置通过
`extends` 继承开发示例，appliance 入口使用同一示例生成带设备认证的配置。
`auto` 由后端在构建执行内核时解析为运行时数据目录下的绝对 `events.jsonl` 路径。

| 运行方式                   | 数据目录来源                                                      | 默认日志位置                                        |
| -------------------------- | ----------------------------------------------------------------- | --------------------------------------------------- |
| 源码直接运行 CLI           | 从当前目录向上查找项目根目录                                      | `<项目根目录>/data/events.jsonl`                    |
| `pnpm dev:with-agent` 联调 | launcher 设置专用 `ECHO_DATA_DIR`，默认 `data/echo-appliance-dev` | `<项目根目录>/data/echo-appliance-dev/events.jsonl` |
| Electron 桌面              | launcher 设置 `ECHO_DATA_DIR=<userData>/data`                     | `<userData>/data/events.jsonl`                      |
| 原生 OS Agent              | systemd 设置 `ECHO_DATA_DIR=/var/lib/echo-agent`                  | `/var/lib/echo-agent/events.jsonl`                  |
| appliance                  | 容器/服务设置 `ECHO_DATA_DIR=/data`                               | `/data/events.jsonl`                                |
| 自定义运行时 home          | 未设置 `ECHO_DATA_DIR`，设置 `ECHO_HOME`                          | `<ECHO_HOME>/data/events.jsonl`                     |

优先级沿用已有 `app_paths()` 合同：`ECHO_DATA_DIR`、`ECHO_HOME/data`、项目根目录下
`data`。配置文件所在目录不决定状态目录。安装目录与状态目录分离；从项目内不同子目录
启动时，日志不会额外落到 `frontend/data`。跨项目或安装目录启动独立服务时，应设置绝对的
`ECHO_DATA_DIR` 或 `ECHO_HOME`；没有环境变量且不在项目树内时，现有合同以当前目录为根。

`AgentConfig()` 的库调用默认仍为内存日志。用户显式设置 `journal_file: null` 仍表示
只保留进程内主日志；其他字符串继续表示用户指定的文件路径。发行模板更新不会覆盖已有
`config.local.yaml` 或 Electron 已生成的用户配置。已有安装若仍设置 `null`，需把实际使用
的配置改为 `auto` 后重启。过去仅在内存里的记录无法补回。

## 这项选择实际保证什么

正常服务装配会把 JSONL 日志附加到同一数据目录的 `agent_trace.sqlite`，使已写出的
检查点和细粒度轨迹可以在后端重启后读取。线程记录、任务监督记录及
`tool_effects.sqlite3` 延续各自已有的存储方式；这次不合并它们的 schema，也不改写历史数据。
事件继续使用服务端传入的 tenant、owner、thread 标识和既有脱敏逻辑，恢复读取继续受原来的
身份与线程权限检查约束。日志落盘不授予其他用户读取权限。

恢复对象是已经保存的任务状态。当前自动检查点默认每 10 次迭代写入，暂停和完成也有各自
检查点路径；可以用 `ECHO_CHECKPOINT_EVERY_N` 调整间隔。进程被终止时，最近检查点之后的
推理和未持久化进度可能丢失。任务需要通过现有继续/恢复流程重建执行，不会在重启后擅自
自动继续所有操作。对于已经进入执行但结果不确定的副作用，已有 receipt 状态机会阻止自动
重复执行；持久化主日志不改变这一约束。

写入失败仍按现有调用点处理，部分检查点写入是 best-effort；本项不承诺断电时每条记录零丢失。
默认 JSONL 上限为 50 MB，轮转会移除旧事件，因此这不是无限期审计保留或整机备份策略。
跨机器检查点镜像仍需显式配置。仅调用 `AgentKernel.from_config` 的嵌入式宿主会得到持久
JSONL，但 SQLite trace 由标准服务 `AppState` 装配；自定义宿主应自行接入对应适配器。

## 验证

`tests/test_profile_journal_persistence.py` 覆盖发行 profile 的路径解析、嵌套源码工作目录、
`ECHO_HOME` fallback、显式内存选择、用户文件路径保留，以及更换工作目录后重建内核和
AppState 的实际检查点读取、SQLite trace 接线及同租户不同用户隔离。
测试不调用外部模型。这不替代磁盘满、突然断电、真实长任务或镜像升级的整机验证。

配套的既有脱敏回归中，chunk packing 用例改用显式递增时间戳。打包协议要求严格递增的
事件时间；Windows 上连续创建事件可能得到相同墙钟时间，因此原测试偶发或持续退化为
逐条存储。该调整只固定输入时序，保留原有压缩格式、API key 脱敏和事件 ID 还原断言。

2026-09-05 Windows 本机回归：87 项通过，包括本文件对应的 9 项新测试、配置构建、
运行时路径合同、租户/用户日志脱敏隔离、实时恢复、自动检查点、暂停恢复，以及 Electron
模板物化后可被 Python 加载和不同安装使用独立认证秘密。涉及的 4 个 Python 文件通过
Ruff 检查与格式检查，改动通过 `git diff --check`。
