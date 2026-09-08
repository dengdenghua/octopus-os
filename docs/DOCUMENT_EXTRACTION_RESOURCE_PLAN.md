# 文档解析资源控制：当前实现与验证边界

状态：NAS 文档整理已接入独立解析进程、OS 限额、增量提取和跨进程预览准入。实现存在于当前未提交工作区；Windows 源码/冻结 worker、Linux 源码及最小 onefile 服务父的实测证据见 [专项验证](C:/飞牛os/octopus-os/docs/DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)。Linux 最新源码组为 96 passed、12 Windows 专属 skipped；最小冻结服务父的 PDF、超时、父退出 3 个场景通过。此文不表示完整 OS 制品或全 Agent 资源隔离已经验收。

## 当前调用链

`FileOrganizationService.create_plan` → 当前权限检查 → 设备预览槽 → 有截止时间的目录扫描/原件快照 → `agent_api.documents.extract_invoice_document` → `extract_document_isolated` → 固定 worker → 既有 `extract_document_text` → 既有票据分类器。

NAS 专用整理仍支持 PDF、DOCX、TXT、MD、CSV、TSV。通用提取器还支持 PPTX/XLSX，但这不改变 NAS 票据格式边界，也不新增 OCR。原件读取、计划保存、审批、移动及撤销继续由原有 provider 负责。解析进程只拿到已经授权读取的不可变字节、格式和服务端预算，不获得 NAS 路径或用户身份参数。

## 已执行的预算

| 预算 | 默认值 | 配置与含义 |
| --- | --- | --- |
| 单文件输入 | 16 MiB | 父进程及 worker 协议均有限制 |
| 输出正文 | 64,000 字符 | 达到限制明确标记截断，另有有限截断标记空间；截断文本不成为可执行票据 |
| 页数 | 200 | `ECHO_DOCUMENT_MAX_PAGES`；通用提取器用于 PDF 页/PPTX 幻灯片 |
| 累计直接展开量 | 32 MiB | `ECHO_DOCUMENT_EXPANDED_MIB`；读取到的 XML、文本、PDF 页流等 |
| 工作进程内存 | 256 MiB | `ECHO_DOCUMENT_MEMORY_MIB`；Windows 提交内存，Linux 单进程地址空间 |
| CPU 时间 | 10 秒 | `ECHO_DOCUMENT_CPU_SECONDS`；Windows Job 用户态 CPU，Linux RLIMIT_CPU |
| 单文件墙钟截止时间 | 15 秒 | `ECHO_DOCUMENT_WALL_SECONDS`；包含启动/握手/传输/解析/等待退出 |
| 全扫描截止时间 | 60 秒 | `ECHO_DOCUMENT_SCAN_SECONDS`；单文件只能使用剩余时间 |
| 预览准入 | 每设备状态目录 1 个 | 不排无限队列；读取原件前取得实际 OS lease |

环境预算在服务启动时校验为正且有限；无效配置不能被模型或请求参数覆盖，也不静默回落为无限制。默认值是当前工程配置，代表性样本测量与大文件/目标硬件容量结论必须分开。

`max_expanded_bytes` 不是所有解析内部对象的精确总和：字体、xref/CMap、单页内部临时分配等仍依赖进程内存限额约束。Windows 与 Linux 的内存统计口径不同，不能把此值写成统一 RSS 保证。

## 协议、进程与清理

- 普通 Python 使用受信解释器、`-I` 和固定 worker 文件；冻结程序使用同一可执行文件的专用参数，并在普通 CLI 导入之前分流。
- 工作目录固定，环境仅保留必要系统/临时目录/语言项及冻结启动字段；不继承模型 API token、PATH 或 PYTHONPATH。
- Linux 冻结短命 worker 保留 bootloader 的 `_PYI_*`，不设置 `PYINSTALLER_RESET_ENVIRONMENT=1`。`LD_LIBRARY_PATH` 只使用经过绝对路径、真实目录、所有者及写权限核验的 `sys._MEIPASS`；不继承任意库路径尾部、`LD_PRELOAD`、`LD_AUDIT` 或 `LD_LIBRARY_PATH_ORIG`。Windows 冻结分支保持原有环境行为。
- 配置帧最多 16 KiB，正文直接按块传输，不做整文 base64/JSON 包装。stdout 只接受有限的 ready/result 帧，stderr 不进入用户错误。
- worker 先施加 OS 限制；父进程再验证实际 worker 的 PID、创建身份和 Job/进程组归属，然后才放行文档字节。不能用 Popen 的 launcher PID 替代实际解析者身份。
- 取消、超时、协议损坏、结果后不退出都会进入终止和回收。Windows 检查 Job 成员句柄退出和活动成员数，避免 launcher 提前结束导致误判。
- 若进程树或管道线程无法确认回收，抛出清理异常，NAS 服务保留跨进程槽位。不能在旧解析进程仍可能存活时释放容量继续启动。

墙钟预算是解析截止时间；清理还会追加终止等待和线程 join。文件扫描/原件读取发生在父服务，截止时间在操作边界检查，无法中断已经阻塞的故障挂载 I/O。两者都不是整个 HTTP 请求的硬实时上限。

## 提取和计划处理

提取器已改为按页、段落、表格行累计正文；OOXML 使用流式 XML 读取并累计实际读取量。触发限制和 MemoryError 不再通过另一个 PDF 解析器绕开限制继续解析。

| outcome | 文本与计划处理 |
| --- | --- |
| `ok` | 有界正文；如 truncated 则待确认 |
| `no_text` | 无可用正文；待确认，不猜测是图片、加密还是损坏 |
| `timed_out` | 不使用部分正文，条目待确认 |
| `resource_limited` | 只在明确输入/资源证据下返回，不把任意异常退出猜成 OOM |
| `worker_failed` | 解析异常、异常退出或协议损坏；不使用部分正文 |
| `unavailable` | 依赖或必要保护不可用；不回退到主服务无保护解析 |
| `cancelled` | 服务内部取消；全扫描取消时计划不可执行 |

准入占用由服务返回 409 `operation_busy`，不是 worker 的解析结果。单文件失败可以保留其他确定条目；整个扫描超时或取消必须 `scanComplete=false`，禁止应用不完整计划。shutdown 触发当前解析取消；HTTP 客户端断开没有因此自动变成取消协议。

## 平台边界

**Windows：** Job Object 对单进程及整组提交内存、用户态 CPU 设限，并采用关闭即终止。launcher 与实际解析进程均须加入受管 Job。保护设置被系统拒绝时报告不可用，不绕开父 Job。普通源码回归包含实际资源压力、父退出及子孙进程清理；冻结验证另有自己的实际进程证据。

**Linux：** worker 设置 RLIMIT_AS/RLIMIT_CPU、独立进程组、prctl 父死亡信号并检查父身份。普通源码及最小 onefile 服务父已在独立 Debian 13.6 guest 实跑。冻结 worker 复用同 archive 的提取环境，实际 worker 与 Popen PID 相同，仍必须是服务的直接子进程及自身 session/group leader；没有通过放宽身份条件兼容额外 launcher。必要环境或身份不成立时仍失败关闭。[原始 Linux 证据](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/result.json)

Linux 的这些限制是单进程 RLIMIT，不是 cgroup 整树预算；PDEATHSIG 不会自动继承到 fork 后代。本轮冻结测试没有生成孙进程，也未证明后代通过 setsid/setpgid 逃离进程组后仍受控。普通进程组后代清理测试不能扩大为任意进程树或代码执行沙箱保证。

**macOS：** 当前 OS 限额适配不支持，返回不可用。spec 包含模块不等于平台运行验收完成。

当前隔离消费者为 NAS 票据整理、普通线程上传的 PDF/DOCX/PPTX/XLSX 可选预览、工作区/Office GET 的文本预览、Agent `read_file` 的结构化 DOCX/PPTX/XLSX/CSV/TSV 读取，以及 `notebook_read` / Agent `.ipynb` 读取。上传和授权路径先完成受保护的原件/快照读取，再把正文交给同一固定 worker；worker 不可用、超时或回收不确定时只省略可选正文，不回滚已保存的上传。纯文本上传仍使用轻量兼容提取器。Notebook worker 只返回受限的单元格文本和文本输出，丢弃图片等二进制输出，不执行单元格代码；Notebook 编辑仍使用 10 MiB 有界读取和 5 MiB 原子写入。Office fidelity 转换继续使用独立的 LibreOffice/Quick Look 子进程，现已清理环境变量并使用临时用户 profile，但它有自己的转换预算，不等同于 document worker。Agent `read_file` 的 PDF 页码范围及其他解析入口仍可能在进程内执行，只共享增量提取改进。本实现是文档解析资源与生命周期控制，不是完整文件系统/网络安全沙箱。

Office 文本与 fidelity 预览缓存同时绑定源内容 SHA-256（并递增缓存版本）；路径、大小和 mtime
相同但内容被替换时不会返回旧的解析或渲染结果。哈希读取与转换一样仍需在授权快照/目标文件
生命周期内完成，不能替代跨请求撤权或物理存储一致性验收。

## 工程登记与剩余验收

预算类型、清理异常及隔离函数进入 `appliance/agent_api/contract.py` 的 documents 域；设备层继续通过该边界调用 runtime。三种 PyInstaller spec 均包含 worker、限额 helper、提取器及 PDF 依赖。相关新测试已进入公开源码门清单，但清单完整不等于全门运行通过。

剩余工作包括完整 Linux 冻结后端及目标 OS 制品验收、macOS 平台实现、目标硬件/复杂文档/持续负载测量、阻塞文件系统 I/O 的生命周期方案，以及其他解析入口的逐项接入。当前 Linux 最小 ELF 使用父 transport SHA-256 `1d631f59a94f1a49ad5fd88efa6af21dd22bb4ddd2e8384373c0c2bacc28918f`；此前 Windows EXE 仍绑定修改前的 transport，只能作为其对应源码的历史证据，不能称为包含最新 Linux 分支。完整自然语言任务、媒体与备份场景和 G1–G6 交付要求保持原范围，不因本项实现缩减。
