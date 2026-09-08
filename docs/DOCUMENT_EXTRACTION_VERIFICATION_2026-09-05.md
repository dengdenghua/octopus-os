# NAS 文档解析隔离验证

核验日期：2026-09-05。按轮次保留证据：早期 Windows 联合组基于 HEAD `f1feccd12586f3e9b13fdddf931e07044bad43ad` 及当时工作区；新增 Linux 源码/冻结验证的快照 HEAD 为 `72b0d6591f2f1677a74f2b32ca8c3925d2678124`，同样包含未提交改动。各轮实际文件哈希见各自结果，不能把早期制品或测试视为覆盖后续所有改动。不是干净 checkout 发布、完整公开源码门或 Linux 整机验收。[Linux 源码快照](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/source-manifest.json)

## 实现与本次收口

NAS 文档预览已从主服务直接解析改为固定独立 worker。父服务先获取当前成员权限和跨进程预览槽，再读取原件快照；worker 施加 OS 限额，父服务验证实际进程身份和归属后才发送文档字节。增量解析的字符、页数与展开量限制和进程内存/CPU/截止时间共同生效。

超时、取消、协议损坏或进程退出异常不返回可执行的部分文本。整个扫描超时或取消阻止应用计划；单文件解析失败保留其他确定条目的处理语义。清理不能确认时保留准入槽，避免孤儿解析者与新任务叠加。

本次联合组发现 documents ABI 漏声明预算类型和清理异常，已补齐 `DocumentExtractionBudget`、`DocumentWorkerCleanupError`，沿用原契约测试而没有删除失败断言。此前父进程传输边界的非截断正文长度、实际 worker 身份验证及全树清理修正也纳入最终组。

生产入口与参数详见 [资源控制说明](C:/飞牛os/octopus-os/docs/DOCUMENT_EXTRACTION_RESOURCE_PLAN.md)。核心源文件：

- [父进程协议与预算](C:/飞牛os/octopus-os/runtime/execution/misc/document_extraction.py)
- [OS 限额与进程回收](C:/飞牛os/octopus-os/runtime/execution/misc/document_process_limits.py)
- [固定 worker](C:/飞牛os/octopus-os/runtime/execution/misc/document_worker.py)
- [增量提取器](C:/飞牛os/octopus-os/runtime/execution/misc/document_text_extractor.py)
- [设备准入](C:/飞牛os/octopus-os/appliance/files/organization_preview.py)
- [设备/Agent 契约](C:/飞牛os/octopus-os/appliance/agent_api/contract.py)

## 按轮次保留的结果

| 验证 | 结果 | 原始证据 |
| --- | --- | --- |
| 早期后端联合 32 文件 | 743 passed、7 Linux/POSIX skipped；132.15 秒 pytest，136 秒外部测量 | [result.json](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/integrated-backend-xguvdlg7/result.json)、[日志](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/integrated-backend-xguvdlg7/pytest.log)、[JUnit](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/integrated-backend-xguvdlg7/pytest.xml) |
| 早期 Windows 最小 onefile | 11 passed、0 skipped；实际 PDF、身份、Job、超时和父退出清理；仍绑定修改前 transport | [result.json](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/frozen-worker-01vyt7kx/result.json)、[cleanup.json](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/frozen-worker-01vyt7kx/cleanup.json) |
| 新增 Linux 源码 6 文件 | 96 passed、12 Windows Job 专属 skipped；7.78 秒，退出码 0 | [result.json](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/result.json)、[日志](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/source-tests.log)、[JUnit](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/source-tests.xml) |
| 新增 Linux 最小 onefile 服务父 | 正常 PDF、超时、父突然退出 3 个真实场景通过；worker 直接父/session/身份均核验 | [frozen-result.json](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/frozen-result.json) |
| Linux 环境修复后的 Windows 源码回归 | 27 passed、9 Linux 专属 skipped；不等于重新构建 Windows EXE | [记录](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/result.json) |
| 相关前端 7 文件 | 101 passed；TypeScript、ESLint、Prettier、Vite 构建通过 | [result.json](C:/飞牛os/octopus-os/tmp/document-organization-audit/final-frontend-4zcrsome/result.json) |
| 真实 HTTP | 审批、冲突重试、原件、撤销、重启及旧票据拒重放通过 | [result.json](C:/飞牛os/octopus-os/tmp/document-organization-audit/http-iet3bwx4/result.json) |
| 真实 Chromium | 当前文件组件经真实 HTTP 完成预览、批准、整理、金额查找、原件下载及撤销 | [result.json](C:/飞牛os/octopus-os/tmp/document-organization-audit/http-browser-QYO7UU/result.json)、[trace.zip](C:/飞牛os/octopus-os/tmp/document-organization-audit/http-browser-QYO7UU/trace.zip) |
| 默认预算源入口 | 4 类小型合成文件各 3 次，12 次全部通过内容/限额/退出检查 | [result.json](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/source-default-budget-5d073e/result.json) |
| 早期质量与证据检查 | 当时相关 Ruff/格式通过，6 组结果源哈希匹配；当时源码门清单 159 文件完整；后续 Linux 分支改动另有证据 | [result.json](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/final-quality/result.json) |

各测试组重叠，尤其冻结 11 项已包含于后端联合组，不能相加成独立测试总数。源码门清单包含并行存储工作的测试登记，仅验证清单完整，未运行全门或验收其硬件功能。

首次联合组为 738 passed、1 failed、10 skipped，唯一失败是上述 ABI 声明遗漏；3 个冻结 opt-in 测试当时未传制品路径。修复后保留该失败记录，最终组传入真实 EXE，仅剩 7 个平台跳过。[首次结果](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/integrated-backend-1ts08cxk/result.json)

早期联合组的 7 个跳过分别为：POSIX 权限/目录 fsync、Linux dirfd 目录创建、3 个 Linux 条件移动/竞态测试，以及 2 个 Linux RLIMIT/prctl/进程组测试。新增 Linux 组实际运行了解析进程相关用例；不能据此把其它目录/移动用例也记为已验证。

## 冻结制品与完整性

[最小 EXE](C:/飞牛os/octopus-os/tmp/doc-extraction-audit/frozen-worker-01vyt7kx/dist/echo-document-worker-smoke.exe) 使用真实共享入口和生产 worker 模块，在独立 build venv 中重建；沿用已锁定安装依赖，没有修改项目共享 `.venv`。

EXE SHA-256：`97e2c0b432f8f2d43f968b4d9ec5da6760d85284727e858875c43d7376e84f8d`。

helper SHA-256：`df470622390facacdc669ddb1d770cd246f303cbeab6e7b652e74b87edd8a4cd`。

冻结验证确认 launcher 与实际 worker 在返回时持有的进程句柄均已退出，父进程异常结束也能回收等待正文的 worker。该组没有生成孙进程；源码 helper 组中的后代进程测试应另行解释。最小 EXE 排除了普通 CLI，不等于完整 Echo OS 冻结包、镜像或安装器已经构建。

该 Windows EXE 的父 transport SHA-256 是 `d1411b86ceef9916eb6d38e6bb462453add8dcbf49de2bbfb89497114be0933b`。后续修改只调整 Linux frozen 环境，Windows 源码分支保持原逻辑并回归通过，但旧 EXE 没有因此自动包含新源码；它仍是对应历史字节的制品。

## 新增 Linux 源码与冻结父进程实测

使用独立 Debian 13.6、Linux `6.12.107+deb13-cloud-amd64`、Python 3.13.5 guest，2 GiB / 2 vCPU。固定官方 cloud 镜像 `20260831-2587` 完整下载 339,214,336 字节并通过固定 SHA512 校验，独立 overlay、端口和临时密钥；没有复用既有 VM 磁盘。安装依赖仅发生在 guest，PyInstaller 固定为 6.16.0，其依赖闭包和 pypdf/defusedxml 按 `uv.lock` 版本与 wheel 哈希校验。[环境记录](C:/飞牛os/octopus-os/tmp/linux-document-audit/env-fn6vrd0d/environment.json)、[构建依赖](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/build-packages.txt)、[构建日志](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/build.log)

源码组使用原始 6 个测试文件和生产模块，`-c /dev/null --noconftest` 隔离运行，没有载入完整 app 的全局 fixture。新增 Linux 专属用例实际验证父退出后无管道 EOF 依赖的 worker 清理、错误创建身份/外部 session 拒绝，以及继承硬限额不足时失败关闭；新环境测试验证真实目录/权限和库路径剔除。96 项通过和 12 个 Windows 专属跳过不能相加为 108 项通过。

此前冻结环境设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，会重新进入顶层 onefile 解包流程。当前 Linux 短命 worker 保留真实 bootloader `_PYI_*`，不设置 RESET，并仅继承经核验的 `_MEIPASS` 库路径。直接父进程/session leader/PDEATHSIG 条件未放宽；这与 PyInstaller 对同一可执行文件短命 worker 的机制相符。[PyInstaller 6.16 官方说明](https://pyinstaller.org/en/v6.16.0/advanced-topics.html)、[精确修改](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/worker-environment.diff)

最小 ELF 中的服务父实际调用生产 `extract_document_isolated`，由生产命令工厂启动同一个 ELF 的专用 worker 参数，沿真实共享入口分流；外部测试驱动没有代替冻结服务父创建解析 worker。三种场景分别为：

| 场景 | 实际核验 |
| --- | --- |
| 小型文字 PDF | 真实 pypdf 提取预期文字；服务 PID 1890、worker PID 1891 |
| 解析超时 | 服务 PID 1895、worker PID 1896；0.8 秒墙钟截止时间触发 `timed_out` 并回收 |
| 父进程突然退出 | 服务 PID 1900 执行 `os._exit`；worker PID 1902 已读完全部输入并进入固定 30 秒休眠，不再读取 stdin 或写 stdout，仍被父死亡信号清理 |

超时/父退出场景的休眠是专用于生命周期验证的解析夹具，不是复杂 PDF 性能测量。每次都核验实际 worker PID 与 Popen PID、SID、PGID 一致，PPID 是冻结服务父，创建身份有效；父/worker 使用同一 `_MEIPASS`，且 worker 不带 RESET、任意 LD_* 注入和合成 API secret。测试后未发现遗留解析者。[逐场景原始事实](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/frozen-result.json)、[进程表](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/processes-after.txt)

被测 Linux 源码在宿主快照、guest 和测试结束后逐项一致。关键 SHA-256：

| 对象 | SHA-256 |
| --- | --- |
| 父 transport | `1d631f59a94f1a49ad5fd88efa6af21dd22bb4ddd2e8384373c0c2bacc28918f` |
| OS helper | `df470622390facacdc669ddb1d770cd246f303cbeab6e7b652e74b87edd8a4cd` |
| worker | `3cdd4fc222a40a1ac84070c32403afa8cfeac0807305531949a77161915a19f6` |
| 提取器 | `818265ea685881f8c109c9ceeeaa82c177a59c3d35e65f4a9338bd663a1755bf` |
| Linux 最小 ELF，10,402,128 字节 | `0dcb837a43a94a27bff36bc041c93d6b94335ce029f6970d166912a59077437e` |

[ELF 制品](C:/飞牛os/octopus-os/tmp/linux-document-audit/frozen-linux-4wp2nqj1/echo-document-service-smoke) 是最小冻结服务父及 worker 的验证产物，排除了普通 runtime CLI，没有装配完整桌面、NAS 服务或 OS 镜像。Linux frozen 支持已经有实测依据，但完整发行制品仍须单独构建验收。

## HTTP 和浏览器实际执行范围

HTTP 测试使用实际 TypeScript 客户端、独立 Uvicorn 进程、真实鉴权/审批/TaskSupervisor/provider 和合成文本发票。它记录了 3 次固定 `-I document_worker.py` 启动；检查冲突重试未重复移动已提交文件，外部替换后原件读取拒绝，重启后旧审批失效及撤销后 SHA 恢复。

浏览器使用真实 Chromium，在临时 Vite 宿主加载实际 FileManager、FileOrganizationPanel 和 HighRiskApprovalDialog，没有 mock HTTP 响应。下载走 Cookie/XHR/Blob 分支；记录 3 次解析 worker 启动。保存 9 张截图并检查页面错误。人工查看了预览与金额搜索截图。[预览](C:/飞牛os/octopus-os/tmp/document-organization-audit/http-browser-QYO7UU/02-preview-no-moves.png)、[金额查找](C:/飞牛os/octopus-os/tmp/document-organization-audit/http-browser-QYO7UU/05b-find-original-by-amount.png)

两条实验均确认私有服务进程与监听端口已清理。没有访问用户文件、调用 LLM、操作现有 VM 或进行外部网络请求。临时宿主不是完整桌面壳，也没有验证自然语言规划器的成功率。

## 默认预算样本

Windows、Python 3.12.14，源码入口、默认 256 MiB/10 CPU 秒/15 秒墙钟截止时间，每份输入各启动 3 个新 worker：

| 输入 | 耗时范围 | 中位数 |
| --- | --- | --- |
| 单页文字 PDF | 0.263–0.280 秒 | 0.266 秒 |
| 三页 PDF | 0.254–0.261 秒 | 0.260 秒 |
| DOCX | 0.147–0.153 秒 | 0.152 秒 |
| TXT | 0.151–0.157 秒 | 0.155 秒 |

每次都检查完整预期文字、未截断、实际限额、worker 身份及调用返回后两进程句柄退出。小样本只证明该配置能运行这些输入，不是大文档容量、并发吞吐、冷磁盘或冻结启动成本结论。

## 未完成边界

Linux 普通源码和最小 onefile 服务父已真实实跑，完整 Linux 冻结后端/OS 制品及 macOS 限额适配仍未完成。本轮没有验证 cgroup 整树预算，也没有证明通过 setsid/setpgid 逃逸的孙进程仍被回收；冻结场景未生成孙进程。Linux 单进程 RLIMIT/PDEATHSIG 和普通进程组清理不能扩大为任意后代预算/父退出保证，更不是文件系统或网络安全沙箱。

扫描和原件读取仍在父服务，阻塞文件系统 I/O 不受解析 worker 的超时中断；解析截止时间之后还需清理等待。HTTP 断开没有自动取消协议。
本轮将通用 Agent `read_file` 的 PDF 分支接入同一 `extract_document_isolated` worker：页数由 worker
回报，默认超过 10 页仍拒绝无范围读取，显式范围最多 200 页并在 worker 内做边界校验。其他上传/工作区
解析入口仍未全部接入隔离。

大库/复杂文档/持续负载、完整自然语言样本、媒体与备份场景、统一发行和 G1–G6 整机验收仍需推进，本项结果不缩减原目标。
