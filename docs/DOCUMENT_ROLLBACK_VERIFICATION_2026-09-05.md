# 文档回滚与桌面整理：实现和验证边界

本记录承接 [项目深度核验](PROJECT_DEEP_AUDIT_2026-09-05.md)。检查对象是当前未提交工作区，
不是已发布制品。项目已有文件工具、回滚 journal、checkpoint rewind 和 Electron 桌面整理；
本轮沿这些实现修正数据正确性，没有新建任务系统或数据库。

## 实际修改

**文本工具与回滚。** ToolExecutor 在作用域、参数 hook、文件权限和租约检查之后捕获原件，
在计时 handler 返回后、任何后置 hook 或诊断之前读取结果。相对路径的事件展示合同保持原样，
回滚另外记录实际授权的绝对目标。append 校验整个结果文件，原件保留 UTF-8 字节及 CRLF；
读取不到完整内容时不声明可逆。现有生产者的内容快照上限仍为 100,000 字节。

差异预览、诊断和回归建议先合成，再经过一次公开 PostToolUse 输出 hook；之后不再用原始
输出覆盖脱敏结果。文件观察不计入 handler 超时，避免成功写入被慢读取误报为工具超时。
原始差异和恢复内容仍属于受授权的 journal 数据，输出脱敏没有被当成清除历史原件的功能。

同一文件多次修改时，预览按撤销顺序模拟中间内容。最近一次撤销若冲突、失败或不可逆，
阻断该文件更早的撤销；其他独立文件可以继续。逐项结果区分 `would_apply / applied /
skipped / failed / uncertain`，另有 `committed`、恢复证据路径和证据权限状态。
恢复已发生但后续处理失败，不会被误报成“完全没有写入”。

**文件 IO。** 恢复先写私密临时文件并 flush/fsync，再核对目标内容、身份及权限后发布。
缺失目标使用不覆盖发布；已有目标使用原子替换。Windows 使用原生 API，原 DACL 恢复和
核验是内容提交之后的操作，失败保留恢复证据并明确提交状态。POSIX 有 mode/owner/xattr
保留实现，当前主机没有实际运行这些平台检查。

撤销新建文件时，Windows 通过同一个独占句柄读取、核验和标记删除，不再按路径执行
`unlink`。已有读者也可能令打开失败，接口会报告失败并保留文件。**POSIX 的这项删除撤销
目前明确不支持**，预览和执行均返回 `guarded_delete_unsupported`；不能把普通路径删除
包装成已经排除对象替换竞态的实现。

**API 权限和历史。** rollback/rewind 从已有 TaskSupervisor、当前线程归属及工作区绑定
推导目录，请求的 `project_root` 只用于一致性检查。检查当前持久线程状态、活动任务、
工作区重叠和有效租约，并复用任务 store 的写锁序列化检查与回滚。逐文件提交状态和恢复
路径写回原 `FileRollbackEvent`，支持在重新打开持久 journal 后回读。审计保存失败会在
响应中说明，不能抹掉已经发生的写入。

checkpoint rewind 改用已授权 journal 的实际追加顺序，避免系统时钟粒度令 checkpoint
和随后文件操作具有相同时间戳时漏掉撤销。旧后端若只有按类型读取、无法证明事件顺序，
结果明确标记历史完整性未验证；非文件副作用仍列为不可逆事项。

**现有桌面整理。** `/desktop` 页面经 preload、Electron IPC 调用原有 Desktop 整理功能。
JSON journal 保存操作 ID、逐文件状态、内容哈希和对象身份。移动以硬链接不覆盖发布，
再处理来源；中断记录可由新进程恢复。撤销只处理选定批次，后续修改、原位置被占用及较新
批次尚未完成时保留冲突。界面依据逐文件结果报告部分成功，不再把批量调用完成等同于
全部文件成功。

## 仍然不代表的能力

- 这不是任意文件系统的 compare-and-swap 事务。原子替换内容不等于内容和元数据同一事务；
  外部进程不参加任务锁，Windows 内容替换后的权限处理仍有独立窗口。
- Desktop 移动需要硬链接，不提供跨文件系统复制删除回退。隐藏恢复路径不是隔离句柄，
  没有宣称消除了同用户恶意进程的所有路径竞态。
- 通用文本回滚没有支持 rename；二进制、大文件及 handler 抛错或超时后的全部副作用恢复
  仍未形成统一合同。没有把失败或未知的操作自动当成可以安全重试。
- 通用 rollback API 目前没有新的前端面板。工作台“撤销上次保存”是另一个既有 artifact
  revision 流程，不能与该 API 混为一谈。
- Desktop 按扩展名整理不等于 O07 的任意授权目录、发票内容识别、按年月预览、审批执行及
  原件逐一回读。O07 和 Linux/整机交付验收仍需继续完成。

## 验证证据

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| 后端最终联合组 | **210 passed、3 skipped，48.81 秒** | 12 个文件，包含真实 ToolExecutor、IO、API 权限、rewind、输出 hook、读前写和副作用回执；3 个跳过分别要求 POSIX 权限/xattr、POSIX 链接及实际 POSIX 的删除拒绝合同 |
| 桌面整理与 React 联合组 | **4 文件、55 passed，4.53 秒** | 真实 Node 临时文件操作、子进程中断与恢复、React 逐项结果；没有运行完整 Electron 窗口 |
| 持久日志跨进程：正常 | writer 28248 → reader 36044；**3 applied、0 skipped、0 failed** | 真实 builtin 读写/追加、JSONL 和重开 SQLite 的事件一致；预览不改字节/mtime/journal；原 CRLF 精确恢复 |
| 持久日志跨进程：外部修改 | writer 8556 → reader 37064；**1 applied、2 skipped、0 failed** | 父进程在 writer 退出后独立修改目标；最近冲突阻断该文件旧撤销，独立文件仍恢复；outcomes 再次持久回读一致 |
| 静态检查与构建 | 相关 Python Ruff、前端 TypeScript/ESLint/Prettier、Vite 生产构建通过 | 构建 7680 modules，41.47 秒；没有把构建等同于开机或硬件验收 |
| 源码门登记 | **125 文件**分类及路径通过 | 包含并行存储任务新增的两个 UPS 测试登记；本轮未执行完整源码门，也未据此验收 UPS |

联合组执行前后，记录的 35 个源文件/测试/门脚本 SHA256 一致，见
[后端结果](C:/飞牛os/octopus-os/tmp/document-rollback-audit/final-validation-dtrhl3l1/backend-validation.json)、
[JUnit](C:/飞牛os/octopus-os/tmp/document-rollback-audit/final-validation-dtrhl3l1/backend.xml)、
[前端结果](C:/飞牛os/octopus-os/tmp/document-rollback-audit/final-validation-dtrhl3l1/frontend-validation.json)。
联合组结束后仅补登记并行任务新增的 UPS 测试，并单独检查分类；这不是再跑全部门禁。

跨进程结果与 11 个产品源文件指纹见
[完整结果](C:/飞牛os/octopus-os/tmp/document-rollback-audit/cross-process-final-1opoombc/result.json) 和
[独立脚本](C:/飞牛os/octopus-os/tmp/document-rollback-audit/cross-process-final-1opoombc/audit.py)。
执行前后及结束后指纹一致；子进程启用网络阻断，实际网络尝试为零，没有模型调用。
此前绑定旧 IO 指纹的实验另行保留，没有覆盖或冒充最终候选结果。

所有文件操作样本使用独立临时目录与合成内容；没有操作用户 Desktop、真实文档或照片。
正常退出后的跨进程回读和故障注入不等于物理断电。以上组存在重叠，不累加成全项目测试数。
