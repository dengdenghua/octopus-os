# NAS 发票整理实现与验证记录

后续解析隔离已经实施，当前联合组、冻结程序及重新运行的 HTTP/浏览器证据见
[解析隔离验证](DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)。下文保留该阶段的实现与历史记录。

日期：2026-09-05。本文记录未提交工作树中的 O07 实施，基线 HEAD 为 `5d84708`；
并行存储任务的提交不作为本任务验证成果。完整目标及 O01–O13 仍有效，尚未完成整机交付。

后续验证期间并行 HEAD 经 `cbd4bd4` 推进至 `0ea048f`。以下最新结果绑定各自记录中的实际源文件 SHA256，
不能仅凭 HEAD 相同或不同判定未提交工作树的实现一致。

## 当前实现

文件管理器可在当前授权目录打开“整理此目录”，创建只读预览，查看确定票据、待确认文件和
冲突，再通过原有单次密码审批执行。服务按开票日期归入当前目录的 `YYYY/MM` 子目录，
保留原文件名和内容。支持 PDF、DOCX、TXT、MD、CSV、TSV；扫描型 PDF 不自动 OCR，
无法取得完整、明确开票日期时保留原件。XLSX 尚未纳入自动归档格式。

计划绑定创建者、NAS 目录身份、逐文件内容与权限快照、精确目标和有效期；审批只消费服务端
保存的计划，不接受客户端重新提交文件清单。初次执行须在 15 分钟内，恢复已开始的计划仍须
重新审批。扫描上限为 250 个目录项、8 层、单文件 16 MiB、总读取 64 MiB；不完整扫描不能
批准执行。这些是读取预算，不能当作解析 CPU/内存强制上限。

`FileOrganizationService` 装配在完整 appliance 扩展中，复用当前 `FileManager`、共享目录
策略、审计、审批和 `TaskSupervisor`。任务生命周期仍由 Supervisor 管理；私有计划与逐文件
回执是 provider 的事实记录，没有增加平行调度器或业务数据库。缺少 documents ABI 时明确
不装配整理功能，原有文件与相册服务继续按各自能力装配。

可信 Agent 工具 `files_organize_plan`、`files_organize_status` 与 UI 使用同一服务，工具
不能指定身份或数据库、批准或移动文件。服务器注册的 `path_resolution="service"` 保留
NAS 相对路径，避免通用执行器将它改成工作区绝对路径；其他工具默认仍为 workspace 路径。
读取重放重新授权并查询，不复用已经失效的历史内容。Agent 生成的计划可在同目录面板按
最近计划或精确计划 ID 选择，不能把“计划就绪”当成执行完成。

## 文件、任务和恢复合同

- 条件移动只在同一文件系统进行，拒绝链接、重解析点、硬链接、来源变化、目标占用及当前
  权限变化；没有覆盖或跨卷复制删除降级。Windows 用独占文件句柄，Linux 用目录描述符及
  `renameat2(NOREPLACE)`。新年月目录继承目录权限，撤销保留空年月目录。
- 每项移动前保存准备记录；每项结果保存后才推进任务。文件已提交但审计、任务或最终回执
  未完成时，明确显示部分完成/待核实，不能显示整个任务完成。
- GET 只回读和补齐已可证明的文件事实，不移动文件或接管活租约。新的独立审批才允许恢复；
  已确认提交的文件不会再次移动。仅剩记录收尾时，重试只补任务/审计记录。
- 取消在文件边界停止后续移动；已完成项保持可见。撤销生成独立反向计划并重新审批，只处理
  本次已确认移动且尚未撤销的条目。原位置被占用或归档文件被外部修改时报告冲突，保留后来修改。
- 任务投影按创建者或管理员权限显示整理任务；任务接管使用现有 Supervisor 租约和服务端
  关联标记，客户端不能伪造接管 token。

关键源码：[装配](../appliance/extension.py)、[服务](../appliance/files/organization.py)、
[路由](../appliance/files/organization_router.py)、[计划](../appliance/files/organization_plan.py)、
[条件 IO](../appliance/files/organization_io.py)、[任务适配](../appliance/agent_api/file_tasks.py)、
[Agent 工具](../appliance/file_organization_tools.py)、[文件面板](../frontend/src/appliance/file-organization-panel.tsx)。

## 已取得的阶段证据

最新实现还包含本计划查找与原件下载加固：查找支持文件名、开票日期和金额，只影响当前
计划的显示内容，审批仍针对完整计划。下载请求绑定 `planId + entryId`，由服务端确定路径；
当前目录身份、文件身份/内容/权限与保存快照一致且当前账户仍可读取，才返回已捕获的字节。
响应不再把路径交给 HTTP 服务稍后重开；文件在结果回读后被替换时返回 409。
浏览器的 cookie 登录也使用可反馈错误的下载方式，不能将 409 页面保存为“原件”。

所有文件、账号、密码和故障样本均为独立临时目录中的合成数据。没有调用 LLM 或接触用户原件。
相关组有交集，不相加成项目总测试数。日志中的跳过不视为对应平台通过。

| 验证范围 | 结果与适用边界 |
| --- | --- |
| 本轮首组后端联合回归 | **606 passed、5 skipped，73.43 秒**；611 项包含分类、真实文档、provider、审批、任务、重启、工具路径治理及依赖锁。源文件在运行前后哈希一致；早于后续原件下载加固，保留为阶段证据 |
| 真实 PDF/DOCX | 文字 PDF 与分段表格 DOCX 经真实提取→计划→移动→重开→撤销，逐字节及 SHA256 一致；图片、空白、损坏、加密、日期冲突和截断 PDF，以及不支持的 XLSX 留在原位。上述联合组已包含这 10 项，不另累加 |
| 真实进程中断 | 子进程在来源捕获前、捕获为 pending 后、目标发布后 `os._exit`；GET 前后文件树不变，已提交事实与未收尾任务分别呈现，活租约拒绝重试。不是物理断电或 Linux 实测 |
| TypeScript→HTTP 阶段审计 | 当前真实 TS 客户端经 Node 环境适配访问两个独立 Uvicorn 进程；真实审批、部分冲突、新审批重试、成功项 inode/mtime/SHA 不变、重启重新认证、旧审批拒绝与撤销通过。此阶段使用通用文件下载；后续原件接口需要新证据 |
| PDF 发行依赖 | 核心依赖加入 pypdf，锁定 6.16.2；原有 32 个运行依赖版本保留，amd64/arm64 解析均为 33 个运行包、6 个构建包。uv 0.11.25 验证；本机按锁定 wheel SHA 离线安装。没有由此证明镜像/冻结发行物已构建 |
| Windows 依赖锁写入 | 使用真实文件 fsync 和同目录 MoveFileExW 原子发布；占用导致失败时保留旧文件，独立 ACL 检查和暂存清理通过。POSIX mode/fsync 测试明确跳过；三份锁仍按每个文件原子发布，不是多文件事务 |

原始证据位于仓库 `tmp/document-organization-audit/`：

- [后端命令、源哈希、611 项结果](../tmp/document-organization-audit/final-backend-3mzg8my0/result.json)
  与同目录 `pytest.log`、`pytest.xml`；复跑脚本为 `verify_current_backend.py`。
- [带脚本和源码指纹的 HTTP 阶段结果](../tmp/document-organization-audit/http-o9fv12_m/result.json)。
  `http-rmpyi6z9` 保留早期脚本对 Windows launcher PID 的错误断言，没有因此修改服务行为或覆盖失败证据。
- [PDF 锁定和安装记录](../tmp/document-organization-audit/pdf-runtime-akx42o7v/result.json)。

首组 5 项跳过分别为依赖锁的 POSIX 权限/目录 fsync、Linux 目录创建，以及 Linux 条件移动
的元数据与两种路径竞态。Windows 的真实文件锁、子进程和 ACL 证据不能替代这些 Linux 分支。

## 原件加固后的最新验证

| 范围 | 当前结果 |
| --- | --- |
| 后端联合回归 | **625 passed、5 skipped，82.08 秒**；630 项去重收集，运行前后源文件 SHA 一致。原件相关新增 19 项已包含，不额外累加；跳过仍为上述 5 个 Linux/POSIX 分支 |
| 前端联合回归 | **7 文件、101 passed、无跳过**，源文件前后 SHA 一致；TypeScript、相关 ESLint、最终 Prettier 检查和 Vite 生产构建通过（构建 25.75 秒）。一个新增测试文件的格式检查初次失败，格式修正后仅重跑受影响的测试/格式检查，不隐去初次失败 |
| 最新真实 HTTP | 精确原件下载、回读后替代文件拒绝 409 且不写出替代内容、冲突重试不重复移动、重启重新认证、已使用及未使用的旧审批拒绝、独立撤销与原件 SHA 恢复均通过 |
| 真实浏览器 | Chromium 中装配当前真实 FileManager、整理面板、审批组件与样式，实际连临时后端。选择目录→预览不移动→待确认文件保留→独立密码审批→移动→按金额查找→cookie/XHR/Blob 原件下载→新审批撤销全部通过；无控制台或页面错误 |
| 持久任务与清理 | 真实 TaskSupervisor 中整理和撤销两项任务均 completed，owner、kind、plan_id 匹配；HTTP/浏览器源文件及脚本前后稳定。独立进程与端口查验确认本次临时服务全部退出 |

最新证据：[后端结果](../tmp/document-organization-audit/final-backend-e2xrnzd_/result.json)、
[HTTP 结果](../tmp/document-organization-audit/http-e3baeuig/result.json)、
[浏览器结果](../tmp/document-organization-audit/http-browser-C42POv/result.json)、
[浏览器操作轨迹](../tmp/document-organization-audit/http-browser-C42POv/trace.zip)、
[进程清理](../tmp/document-organization-audit/http-browser-C42POv/cleanup.json)。
浏览器目录另保存 9 张截图，其中预览与按金额查找画面已人工检查。

前端[101 项与最终格式结果](../tmp/document-organization-audit/final-frontend-3pa326oe/result.json)
及[TypeScript/ESLint/构建和初次格式失败](../tmp/document-organization-audit/final-frontend-_mb9dqj2/result.json)
分别保留。后续只格式化 `files.test.ts`，未修改生产逻辑。

最终[当前源文件与证据比对](../tmp/document-organization-audit/final-quality-wrgoftfb/result.json)
确认后端、HTTP、浏览器及最新前端记录仍匹配实际生产/测试文件；uv 锁与双架构运行依赖验证通过。
公开源码清单 **129 个 appliance 文件、总计 147 个文件** 分类与存在性通过；包含并行存储任务
新增的 mdraid 测试登记，**没有执行整个公开源码门，也没有据登记声称这些并行测试已通过**。
相关 Python Ruff 检查与工作区 `git diff --check` 通过。

浏览器测试采用临时 Vite 页面装配真实产品组件，没有重写面板或 mock HTTP/provider/文件 IO；
它证明这些真实组件与后端的操作流程，**没有运行完整桌面壳、Electron IPC 或自然语言 planner**。
早期 `http-browser-EIktfj` 因临时页面漏载实际桌面样式导致遮罩拦截，保留失败证据；补入现有
产品样式与宿主 class 后通过，没有为通过测试修改产品行为。

## 继续验收的边界

自然语言 planner 发起、固定 20 个任务各 3 次的独立结果判定、完整桌面壳与实际用户操作，
仍需按优化计划验收。真实 HTTP、组件或模拟故障中的一项通过不能替代全部三类产品任务。

目前文档文本输出在完整提取后截断；既有 ZIP 和 pypdf 单流限制没有形成应用级总解压、
可强制终止的解析耗时、预览并发及父进程退出清理保证。需要实施并实测隔离解析流程，
不能用“bounded”字样宣称已具备进程级资源保护。后续实施入口为
[解析资源控制方案](DOCUMENT_EXTRACTION_RESOURCE_PLAN.md)，当前明确是尚未实施的设计。

当前 Linux 分支、同一候选镜像的安装升级恢复、物理存储故障、长时性能及试用仍缺完整证据。
O07 是当前持续推进的一条具体链路，不是对 O01–O13 完整目标的替代。
