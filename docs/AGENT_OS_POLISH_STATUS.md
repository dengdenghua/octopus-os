# Echo OS 打磨记录

日期：2026-09-05 起，最后更新 2026-09-08。整体目标“打磨完整”仍在推进。本记录区分实际修改、验证范围和未完成工作，
不以一个模块的测试通过替代整机完成。当前工作区还有并行进行的存储修改；本文只记录本任务负责的变更。

最新阅读入口：[深度架构与产品审计](PROJECT_DEEP_AUDIT_2026-09-05.md)。以下实施记录按阶段累积，较早的限制以对应后续验证为准；尤其增量内容指纹和空库清理已有后续补强。用户要求先深入理解后，已先完成架构核对，再沿实际缺陷继续实现；当前推进 NAS 发票整理，完整目标继续保留。

## 已实施的数据与相册修复

| 问题                                         | 现在的行为                                                                                                    | 验证入口                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 不同路径的同名目录共用数据库                 | Agent 图片工具按规范化完整路径的 SHA-256 区分库；空目录参数与当前目录的绝对路径一致；状态根沿用运行时配置     | `tests/test_image_library_tools.py`             |
| 人物筛选把任意人脸当作目标人物，并解包错误   | 姓名查询匹配持久人脸原型；支持当前分组编号及对应阈值；未知姓名返回空；`*` 明确表示任意人脸                    | `tests/test_image_index_library_integrity.py`   |
| 人物只有瞬时数字 ID                          | 新 `face_name_group` 工具保存姓名与原型；数字编号随索引快照变化，不作为持久身份；基于现有向量的分组不加载模型 | 同上及工具接线测试                              |
| 训练类别未参与分类                           | 同名训练原型优先参与余弦相似度排序，其余标签使用文本向量；无效向量不覆盖原类别                                | 完整性回归中的类别用例                          |
| 重建清掉有效 OCR、标签与人脸记录             | 同来源且观测元数据未变的照片保留已有派生数据；删除、修改或改绑来源目录时清理陈旧项                            | 完整性回归中的重建用例                          |
| 清空图库后旧索引残留，模型失败可能替换好数据 | 空图库在事务中清旧图像索引；扫描或向量失败回滚到原快照；原始图片不改动                                        | 空库、扫描失败与模型失败用例                    |
| 搜索结果的相对路径被当成进程目录             | 注册库根后，分类、OCR、训练、以图搜图和搜人按该库根读取                                                       | 相对路径与同名文件用例                          |
| 模块能导入就显示智能索引可用                 | API 区分依赖可发现、模型已加载、禁用与宿主安全读取能力；探测不加载模型、不下载                                | `tests/appliance/test_photo_readiness.py`       |
| 缺可选人脸依赖时无法正确规划                 | 不勾选人物功能时仍可建立语义索引；勾选时单独检查人脸能力                                                      | readiness 的计划用例                            |
| 任务状态重启后丢失，部分结果误报             | 持久保存最后一次索引任务身份和结果；中断明确报告；语义索引写入但人物功能失败时保存部分结果                    | `tests/appliance/test_photo_job_persistence.py` |
| 多服务并发建同一索引，活任务被误判中断       | worker 全程持有操作系统文件锁；其他服务读取运行状态并拒绝第二次启动；真正释放锁后才能确认遗留任务中断         | 同文件的真实子进程与同进程多实例用例            |
| UI 只有可点击/不可点击，缺原因               | 显示组件、宿主和任务失败原因，保留文件名搜索；不支持预览时显示文件路径；审批说明包含计划警告                  | `frontend/src/appliance/photos-panel.test.tsx`  |

索引缓存的“未变化”目前依据来源根、mtime、宽高等观测信息，并非完整内容哈希。因此这不是
面向任意外部篡改的内容校验保证。人物命名是人工赋予本地聚类的标签，不是身份认证；相似度分数
不是概率或识别精度承诺。实际模型效果、误识别、万级图库性能仍需独立样本验证。

### 旧索引处理

旧 `data/image_index_<目录名>.db` 以及无来源标识的默认库，无法确认属于哪个历史源目录。
新版工具不自动认领、删除或合并它们。对需要使用的目录执行 `image_index_build(directory=...)`
重新生成独立索引；原图保持原位置。旧文件上的人工标签和类别不会自动迁入新库，须先确认来源
再制定迁移。本轮没有复制不明来源的人脸数据。

appliance 自有相册仍使用 `<设备数据目录>/media/image_index.db`，通过受限适配器调用同一算法。
直接 Agent 图片工具使用 `<运行时数据目录>/image_libraries/<目录标识>/index.db`。这次解决库之间
覆盖的问题。进一步的 `photos_*` 接线已让设备相册工具直接复用桌面服务与成员策略；它没有
将通用 `image_*` 或可选 `echo-storage` 强行合并到同一个数据库。

本轮又把这条边界落到执行器：`image_*`、`video_*` 等通用工作区技能的 `directory`、
`image_path`、`video_path` 和批量图片参数，会在有 Session 时由服务器解析到当前读范围；
模型提供的绝对 NAS 路径或其他租户路径会在 handler 前拒绝。设备 NAS 相册只能走
`photos_*`，在那里才会重新取得 `DataAccessPolicy`、成员可见范围和服务级资源回读。
没有 Session 的本地 CLI/测试调用仍保留显式目录契约，避免把离线开发工具误当成设备授权入口。
通用图片搜索和元数据结果同时附带经过文件存在性、库根和大小/mtime 校验的
`assetReference`，可用于后续回读而不暴露宿主路径；越界或已删除文件只保留原有结果字段。

两条图片链路现在使用同一套部署内 `photo-library` source id；设备相册返回每张图片的
`assetId` 与 `assetRevision`，Agent 工具结果返回 path-free 的 source envelope，存在
host execution 时再附带服务器生成的 task/thread 坐标。这样可以把“哪个任务看到了哪张
照片”交给回执和领域读取继续核对；通用图片搜索和元数据结果在文件仍位于库内时还附带
path-free 的 `assetReference`，缺失或越界文件不会伪造引用。它不把路径哈希当作权限，
也不把两套 SQLite 变成一套。
外部 `echo-storage` 仍不继承这个标识即可获得成员授权，必须等待其真实资源级授权合同。

## 追加的设备相册与真实执行路径修复

完整 appliance 扩展取得运行时 registry 后注册 `photos_library`、`photos_search`、
`photos_status`、`photos_index_plan`。工具不接受模型指定 actor、数据库或宿主路径；与桌面
使用同一个 `PhotoLibraryService` 和共享策略。索引计划只读，实际执行保留单次密码审批。
`photos.status` 同时补入能力目录。

私有授权上下文绑定已验证的 actor、签发/过期时间和当前设备账户实例，每次查询前后核验。
它不将 JWT 写入 Session；仅恢复旧 Session 而没有当前凭证时失败关闭。同一用户名也不能
跨设备服务实例复用授权。WebSocket 会话撤销补齐严格 `bearer.b64` 处理。

实际 ReAct 重试曾直接重放已经提交的照片结果，绕过工具函数的授权复核。现在相册只读工具
通过服务器注册的 `refresh_read` 策略要求重新查询；副作用工具仍保留原有持久回执和不重复
执行的规则。Codex 动态工具入口的重复 callId 路径也按相同策略处理，不能由模型参数覆盖。

成员检索改为先限定安全扫描得到的可见路径，再执行相似度排序，避免前排的其他成员照片
挤掉自己的候选。SQLite 查询使用绑定参数并分批读取；旧后端或旧 ABI 未明确支持范围时，
成员查询退回已有文件名模式，不能静默继续全库截断。账户撤权与当前共享策略检查保持独立；
共享策略沿用现有 2 秒缓存，不宣称 ACL 变更零延迟。

Windows 相册读取新增原生句柄检查：逐级拒绝重解析点、固定目录避免替换、校验文件类型与
真实路径，从已验证句柄读取。原图、缩略图和索引适配器共用该路径；没有将 `O_DIRECTORY`
或 `O_NOFOLLOW` 替换为零。POSIX 分支保留 descriptor-relative 的安全读取合同。

照片面板按视图与状态代次丢弃迟到响应；关闭重开、切换查询和轮询不会用旧结果覆盖当前
内容，旧审批响应也不会在窗口关闭后继续提交。成员状态不再暴露全库任务与计数，UI 依据
`canManage` 显示索引操作权限原因，仍保留浏览和搜索。

## 已实施的运行时持久化

新源码示例与桌面发行模板使用 `journal_file: auto`，沿 `app_paths()` 将主日志落在状态目录；
原生配置与 appliance 模板继承该选择。已有用户配置中的 `null` 保持明确的内存选择。
持久 JSONL 和标准 AppState 的 SQLite trace 接线通过实际重建测试验证，保持租户/用户隔离。
路径、迁移和恢复边界见 [AGENT_PERSISTENCE_POLICY.md](AGENT_PERSISTENCE_POLICY.md)。

检查点、工具副作用回执和系统实际状态仍各负其责。日志保存不表示可以安全重做结果未知的操作，
也不表示重启后自动继续全部任务。原有日志轮转和部分检查点 best-effort 写入仍需后续故障验收。

## 本轮验证方式

Python 使用项目 `.venv`。为运行原有图片覆盖测试，单独安装锁文件中对应 Python 3.12 的
`numpy==2.5.2`；未改变锁文件、全局 Python 或下载视觉模型。

新增图片与持久化回归已加入 `deploy/appliance/run_public_source_tests.py` 的显式清单，仍保持
appliance 测试文件完整分类检查。新增轻量数据回归不要求视觉模型依赖。已验证清单选择及其
`--confcutdir=tests/appliance` 调用方式；并未声称整个 PR gate 已在本机通过。

测试使用临时 SQLite、真实文件和进程状态；模型行为使用固定向量或注入后台。它们验证数据与
调用逻辑，不证明真实 CLIP/InsightFace 精度或吞吐。前端使用组件回归与 TypeScript 检查。
上轮相册读取的 3 项 Windows `os.O_DIRECTORY` 失败已由原生安全读取修复；下表保留的是
之前的阶段结果，当前追加回归记录见后文。Windows 状态锁/权限问题已在后续轮次修复，
历史失败与跳过数保留，不代表当前结果。

上轮本机结果（各组有交集，不相加当作独立总数）：

| 范围                                                                                              | 结果                                                                                        |
| ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 图片完整性、图片工具、原有图片覆盖、profile 持久化、相册 readiness/任务/路由及 Agent 边界联合回归 | 88 通过，3 失败；3 项均为 Windows 缺 POSIX 安全读取能力，分别涉及成员照片预览、缩略图和原图 |
| 运行时配置、路径、身份隔离、检查点、暂停与实时恢复、Electron 配置物化                             | 87 通过，详见持久化策略文档                                                                 |
| 新轻量数据回归按 PR gate 调用方式运行，并在子进程中禁止导入 NumPy                                 | 37 通过                                                                                     |
| 前端照片 API 和面板组件回归                                                                       | 12 通过；TypeScript、相关 ESLint 与 Prettier 检查通过                                       |
| 设备相册索引跨进程互斥                                                                            | 真实 Windows spawn 子进程验证持锁、拒绝第二启动、正常退出与强制终止；Linux 分支尚待实际运行 |

Python 相关改动通过 Ruff，工作区变更通过 `git diff --check`。未运行整个前端和全仓测试，
不把这里的通过数量称作“全项目测试通过”。

### 本轮追加回归结果

| 范围 | 结果与边界 |
| --- | --- |
| 设备相册工具、私有授权、Windows 安全读取、成员候选排名、原相册/readiness/任务持久化、能力目录/ABI、读取重放策略、图片完整性/工具/原有覆盖、profile 持久化的联合回归 | **173 通过、1 跳过**，18.46 秒；唯一跳过为 Windows 无法满足既有 POSIX 认证目录 owner-only 权限的真实磁盘变体。没有跳过撤权逻辑，真实 JWT/WS 与内存 I/O 的账户撤销路径已通过 |
| 写入回执/存储、跨进程崩溃/并发、Redis 协调、ReAct/native loop、动态工具、授权与相册接线的另一组合 | **137 通过、1 跳过**，26.47 秒；与上一组重叠，不累加成独立用例总数 |
| 照片组件与 API | **26 通过**；其中组件 23 项、API 3 项。TypeScript、相关 ESLint/Prettier 通过 |
| 公开源码门清单 | **100 个测试文件分类/存在性检查通过**；已加入新授权、安全读取、候选范围、相册工具和读取重放回归。未执行整套门禁 |
| 扩大检查 `tests/appliance/test_extension.py` | **5 项失败**；均在完整扩展初始化时遇到 Windows 缺失 POSIX 状态目录锁，尚未进入新相册接线。不能据相册局部通过声称整个 appliance 已支持 Windows |

联合回归可在项目 `.venv` 用下列命令重跑：

```powershell
.venv/Scripts/python.exe -m pytest tests/appliance/test_photo_tools.py tests/appliance/test_agent_authorization.py tests/appliance/test_photo_safe_file.py tests/appliance/test_photo_search_scope.py tests/appliance/test_photos.py tests/appliance/test_photo_readiness.py tests/appliance/test_photo_job_persistence.py tests/appliance/test_capabilities.py tests/appliance/test_agent_compat.py tests/test_tool_read_refresh_policy.py tests/test_image_index_library_integrity.py tests/test_image_library_tools.py tests/test_image_semantic_index_coverage.py tests/test_profile_journal_persistence.py -q
```

回执与后端组合命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_tool_effect_receipts.py tests/test_tool_effect_store.py tests/test_react_native_tooluse.py tests/test_realtime_native_tool_loop.py tests/test_codex_dynamic_tools.py tests/test_tool_read_refresh_policy.py tests/appliance/test_agent_authorization.py tests/appliance/test_agent_compat.py tests/appliance/test_photo_tools.py -q
```

本轮相关 Python 改动通过 Ruff。没有视觉模型下载/精度测试，没有当前 SHA 的 Linux 整机
候选或全套前端测试证据。原有照片、数据库、工具与交付链路的代码依据及竞品事实另见
[深入评估](PROJECT_REVIEW_2026-09-05.md)。

Linux 验证环境已做只读探测：本机 QEMU 进程 12536 存活，实际磁盘为
`C:/vmtest/disk-fresh2.qcow2`，`127.0.0.1:2222` 返回 Debian OpenSSH banner。
已记录的测试认证尝试一次失败，来宾 Python/pytest 和当前源码尚未验证，未修改该 VM。
因此目前仍不能用这台 VM 的历史结果补齐本轮 Linux 回归证据。

### 实际装配与 Windows 状态修复后的追加结果

Windows 状态目录现使用当前进程用户 SID 的私有 DACL，凭据从创建时即受保护并通过
句柄核验；锁使用真实 `LockFileEx`，支持共享/独占竞争和进程退出释放。拒绝链接、
重解析路径和不支持安全权限的状态目录。目录加固不递归改写已有子项；POSIX 权限与
`flock` 分支保留。原来因 POSIX 认证目录权限跳过的磁盘撤权用例已取消跳过。

| 本轮验证范围 | 实际结果 |
| --- | --- |
| 上文相册/授权/图片/runtime 联合组，追加完整 extension 装配 | **181 通过、无跳过**，30.18 秒；旧 OMV 后台 monitor 断言已对齐当前原生按需健康路由 |
| auth、真实磁盘授权、Windows 原生状态/子进程锁、account security、Agent ABI | **75 通过、无跳过**，30.23 秒；Windows 权限由独立 PowerShell `Get-Acl` 验证，POSIX 原 mode 断言保留 |
| 原生存储证据、健康聚合、API 与原有 native 存储回归 | **114 通过、2 跳过**，2.75 秒；新增 observation 60 项无跳过；两项原有共享路径断言明确仅支持 POSIX |
| 存储健康面板、容量、存储入口与账户导航的相关前端回归 | **40 通过**；TypeScript、对应 ESLint/Prettier 通过 |
| 当前前端生产构建 | `node node_modules/vite/bin/vite.js build` **通过**，7,673 个模块，34.19 秒；使用当前前端依赖目录，输出 `frontend/dist` |
| 公开源码门的清单完整性与路径检查 | **104 个测试文件**；包含并行任务新增的两个写入回归文件，仅清单验证，不是整套门禁通过 |
| 独立临时目录前端安装 | 项目声明的 `pnpm@10.26.2 install --frozen-lockfile` 完成，995 个解析包；Electron/esbuild/sharp 安装脚本成功，二进制存在性、TS 转换和 sharp 加载通过。三个复制的包管理文件与仓库一致 |

以上测试组有重叠，不累加成独立用例总数。前端安装在独立临时目录运行，没有重建
已有 `frontend/node_modules`，也不等于新依赖目录中的整个应用构建已经验证。

完整 CLI 烟测通过 `.venv/Scripts/python.exe tmp/appliance-dev-smoke.py` 运行，采用
静态 planner、合成照片、临时 NAS 和状态目录；每次均实际启动 Uvicorn/内建 Agent/
appliance 扩展。两次启动验证：未登录照片请求 401；管理员登录、照片库与状态 200；
原图字节与磁盘一致；`/api/skills` 包含四个 `photos_*` 工具。第二次启动移除初始
密码和 JWT 密钥环境变量，再验证旧密码重新登录、旧令牌仍有效，确认认证从磁盘恢复。
实际 Windows 宿主缺少 Linux 存储工具时返回 `unknown / coverage=none`。

原始结果保存在 [烟测结果](C:/飞牛os/octopus-os/tmp/appliance-cli-smoke-3mllkmfu/result.json)，
同目录保留两次启动日志；脚本和原始证据在本地忽略目录内，没有作为发布制品。
没有调用真实视觉/语言模型，也没有发起磁盘写入或共享配置。启动仍暴露软沙箱、
可选视频依赖缺失、部分插件 manifest 注册不一致和可选技能下载超时，不能将服务启动
成功概括为所有模块正常。

存储读链保留工具缺失、权限、超时、非零退出、坏 JSON 和局部解析的证据；SMART
退出位分别用于判定读取是否完整与硬盘是否报警。零设备/零观测不能成为健康，容量
仍可保留，完整证据才填写本次成功时间。没有虚构后台监测、历史事件或持久化健康。
交叉审查复现并修复了未挂载 ZFS 成员、池状态冲突、`findmnt` 权限拒绝和异常
`fstype` JSON 类型；真实故障仍优先显示。七个只读路由的并发回归用事件握手确认：
探测被阻塞时，同一 ASGI 事件循环里的其他请求仍能返回。挂载状态未知时的写入门
由并行任务接管，本组证据只声明读取和健康语义的修复。

原生存储组可重跑：

```powershell
.venv/Scripts/python.exe -m pytest tests/appliance/test_native_storage_observation.py tests/appliance/test_native_storage.py -q
```

两项既有跳过为 `test_sharing_targets_match_the_frontend_contract_without_host_paths`
和 `test_system_mounts_are_never_offered_as_shared_folder_targets`，原因均为
`path assertions are POSIX-specific`，未将其改成通过。

### 工作区、插件与真实模型追加验证

工作区凭据不再在加密不可用时明文降级；用户传入的 `ENC:` 字符串不能绕过加密，
敏感对象/数组/数字整体密封并保留类型。创建前先检查加密条件，再连接远端；工作区
和 owner 成员同事务保存。解密失败先核对租户及成员权限，然后返回静态 503 错误，
不能继续使用缓存连接或回退本地路径。合法旧明文仍可读取，没有自动迁移或换密钥。

三个设计插件的 manifest 与实际服务键对齐，真实技能注册失败不再被吞掉。剪辑插件
按功能加载可选依赖：没有 `av` 时项目与图片功能可用，视频操作返回明确的缺组件
错误。原来整个插件导入失败、只有启动日志报错的情况已修复。

相册新增纯观察模型状态：`loading` / `load-failed`、固定错误 code、可重试按钮和
搜索后的状态刷新；状态查询不初始化模型。CLIP 默认缓存移入设备数据目录，保留
显式 `FASTEMBED_CACHE_PATH`。锁定库未实现的 int8/uint8 量化不再被静默透传。
内容指纹同时参与 OCR、标签和旧人脸失效，关闭保留 mtime/尺寸替换像素的误缓存。

| 范围 | 当前证据 |
| --- | --- |
| 工作区加密、存储、API、远程文件访问、ACL 与协作绑定 | **184 passed**，41.21 秒；新安全回归 45 项 |
| 图像运行时、内容身份、相册/授权/安全读取/持久任务联合组 | **149 passed**，13.79 秒；之后给通用索引失败结果增加安全 `model_error`，对应模型/readiness **20 passed** |
| 指纹独立复核 | 同 mtime/尺寸替换、EXIF 变化、跨条带末像素变化均被识别；向量或指纹失败后六张表逐行等于旧快照；真实 Windows 安全适配器没有回退 pathname 读取。相关 **39 passed**，与上一组重叠 |
| 三个设计插件、剪辑、Hub、ServiceBus 与生命周期 | **77 passed，无跳过**；包括原剪辑 14 项与新可选依赖回归 17 项 |
| 相册前端组件与 API | **34 passed**；TypeScript、对应 ESLint 通过；当前 Vite 生产构建也通过 |
| 本轮全部改动后的真实 CLI 装配 | 新隔离目录中两次启动均通过登录、原图字节、4 个设备相册工具目录；第二次移除初始凭据环境变量后重新登录与旧令牌验证通过，见[结果](C:/飞牛os/octopus-os/tmp/appliance-cli-smoke-qcs_jsb4/result.json) |
| 源码门分类与路径检查 | **108 文件**；追加三项本轮 runtime 回归与并行任务的 provision nginx 回归，未运行整个门禁 |

这些组有重叠，不相加为独立测试总数；没有把全仓测试或正式制品门写成通过。

真实 CLIP 已在本机 CPU 运行，离线新进程索引三张合成图、存储 512 维向量和语义查询
通过，网络拦截器记录 0 次请求。本机系统 MSVC 运行库先导致 ORT 初始化失败，只有
在测试进程明确使用更新的运行库后通过；没有改系统 DLL 或把诊断路径加入产品。
中文小样本出现错误首位，不能宣称中文质量通过；具体版本、耗时、冷启动失败和运行库
条件见 [模型运行记录](PHOTO_MODEL_RUNTIME.md)。

视频验证分两阶段保留：缺 `av` 的完整 CLI 真正运行项目编辑和 PNG 合成，视频导出
返回 503，见 [缺依赖阶段](C:/飞牛os/octopus-os/tmp/design-plugin-cli-smoke-z02gw9yg/result.json)。
随后仅在项目 `.venv` 安装锁定 PyAV 18.1.0；用合成 PNG 和两条正弦 WAV 真正导出
H.264/AAC MP4，再解码检查 2 秒/8 帧、红蓝图像、48kHz 立体声和 440/880Hz 混流。
生成 MP4 再经插件快照读取也通过，见
[媒体结果](C:/飞牛os/octopus-os/tmp/clip-studio-media-smoke-goa8kt3t/result.json)。
没有读取用户媒体，没有据此声称外部 ComfyUI/GPU/生产编解码矩阵通过。

上一轮固定向量生命周期试验发现三个缺口：重复索引重算全库、没有主动取消、索引
commit 后而任务 JSON 保存前中断时误报失败。以下追加工作针对这些实际复现展开。

### 相册增量、取消与提交恢复

向量复用同时要求安全解码内容指纹和编码器身份匹配。已知模型的身份包含实际资产、
预处理、依赖版本与 provider，加载会话建立后固定；未知模型和旧索引保守全算。
人脸来源表记录检测来源，区分“零人脸”与“未检测”。构建仍扫描、解码入选图片并
用同一事务提交快照，取消或出错回滚，未把它称为增量目录扫描或逐图片续算。

任务增加 `cancelling / cancelled`，仅管理权限和精确 job ID 可以请求停止；先保存
取消状态，再发出协作信号。旧后端、其他进程拥有的任务明确返回 409。当前原生模型
调用不能被强行中断，返回后才检查信号；已经提交的成功不会被晚到的取消覆盖。
界面在取消中持续轮询，防重复点击、晚到响应、关闭重开和权限变化导致的状态串用；
显示当前索引总量、复用、更新和移除数量，成员仍看不到全库任务控制或统计。

当前提交的任务回执与向量写入同一个 SQLite 事务，恢复校验库根、任务、计划和
人脸选项。实际进程被终止时，提交前保留旧库并报告中断；提交后、任务 JSON 更新前
则根据回执恢复成功，恢复过程不加载模型、不再构建。索引部分成功而人脸不可用仍
保留原有 `failed + partial`，没有因恢复改写成完整成功。

真实 CPU CLIP 测试观察到：三张未变化图片 0 次编码，新增或同 mtime/尺寸替换一张
只编码一次，新进程同样复用三张。真实编码返回后取消，十二张持久表逐行保持原样；
全部阻断网络，0 次网络尝试。沿用测试进程的 MSVC 运行库诊断条件，不能概括为
Windows 安装包开箱可用或大图库性能通过。耗时和原始结果见
[模型实测记录](PHOTO_MODEL_RUNTIME.md)。

独立交叉审查还复现了模型更换后旧人物/训练类别原型的错配。当前为每个原型绑定
来源身份，不兼容时保留名称和向量记录但停止匹配；重新命名或训练可绑定新身份。
人物分组和名称匹配在同一只读快照读取；命名在同一写事务读取分组并保存绑定，
避免重建并发时将旧向量标成新模型。类别按当前加载的图像模型检查，不能靠先重建
索引才能避免错配。已知身份不会匹配无绑定的旧原型；未知来源维持原有兼容边界。

| 本轮最终检查 | 结果 |
| --- | --- |
| 图像身份、增量、原型、相册 API、任务、文件读取、搜索范围及 Agent 授权联合组 | **193 passed，22.00 秒**；新增增量行为 16 项、模型资产身份 6 项、任务生命周期 22 项均包含在内 |
| 相册前端组件与 API | **55 passed**；ESLint、Prettier、完整 TypeScript 检查通过；Vite 生产构建通过 |
| 真实 CPU CLIP | 已加载模型下逐次核对实际编码次数；另起进程继续复用，均离线，详见模型记录 |
| 真实进程中断 | 旧事务回滚/新事务提交两种窗口分别验证；恢复不调用模型或构建。固定向量控制流证据见[结果](C:/飞牛os/octopus-os/tmp/photo-lifecycle-review-20260905/results.json) |
| 最终代码真实 CLI 装配 | 两次隔离启动均通过登录、原图字节和四个设备相册工具注册；第二次移除初始密码/JWT 环境变量后重新登录和旧令牌验证通过，见[结果](C:/飞牛os/octopus-os/tmp/appliance-cli-smoke-_ol5djrc/result.json) |
| 源码门分类 | **111 文件**路径与分类通过；未将分类检查写成全门禁执行通过 |

各组有重叠，不汇总成独立测试总数。真实模型、真实服务与固定模型故障测试分别记录，
没有以其中任何一组代替整机、断电或规模验收。相册既有 `NO_IMAGES` 计划限制仍在，
空图库不能从面板申请清除旧索引；已知不兼容原型的重新确认界面、空库管理和主动
增量扫描属于后续需要补齐的使用流程。

### 文档原件、递归权限与备份恢复追加验证

本轮沿文档整理后的结果回读和 NAS 恢复继续推进，没有新增第二套任务系统。
通用工作台原有计划、批准、任务跳转和产物入口保持由原运行时提供。

文件目录操作现在检查根和全部嵌套权限边界：copy 源需要可读、目标需要可写，
move 两端和 trash 源需要可写；restore 同时检查原路径和最终冲突改名路径。
实际目的路径确定后、任何 mkdir/shutil 修改前执行回调。恢复备用名称已存在时返回
409，保留已有文件和回收站记录。默认零秒缓存明确禁用，固定时钟测试覆盖同 tick
撤权。Windows 上传元数据替换使用原生 MoveFileExW，保留源文件 fsync，POSIX 仍
执行 replace 与目录 fsync；依据 [Microsoft API 合同](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)。
真实 Windows 重启续传通过，没有将此当作真实断电证明。

原件链接进入 `/api/fs/content`，复用线程归属、租户和挂载成员权限；读取与 Office
转换后均复检，包括预览不可用时回落原件的分支。原件/图片/PDF 下载携带认证，
403/404/413 提示可理解的原因；重新打开原件重新请求，复核期间不显示缓存旧内容。
既有 output/final/upload 及明确附件保留原合同，上传路由不再用缺失路径的 basename
选另一个文件。新接口为最多 128 MiB 全文件读取，Range 从同一读取得到的字节返回，
尚未提供远程大文件流式读取。Task Space 对 100/500、250/500 正确显示 20%、50%。

NAS 恢复在切换前完成两次仓库检查；staging/live 逐文件流式计算 SHA256，并把路径、
类型和符号链接的文本目标纳入树身份。切换前保存 prepared 回执，之后记录 promoted /
verified；同 repository/snapshot/target 重试只核验，不重写已有文件。只剩 prepared
且恢复核验失败时 `committed:null`，不把未知状态误报为明确未提交。固定诊断说明阶段
和后续动作，不回显原始 stderr。新 `verify-restore` 与 helper 已进入运维 bundle。

回执默认目录为 `/var/lib/echo-os/nas-restore`，要求受保护且 root 所有；可以用
`--receipt-directory` 指定满足同样要求的目录。`contentVerified` 仅指 live 树与切换前
staging 树一致；没有声称已从仓库独立提取逐文件清单验证，也没有实现恢复传输的
断点续传。失败 staging 保留，后续还需要完善其清理与容量管理。

| 当前检查 | 结果与证据范围 |
| --- | --- |
| 文件/递归权限/缓存/任务进度、NAS 恢复/挂载检查、原件/附件联合组 | **184 passed、3 skipped，21.05 秒**；三项分别需要 POSIX FIFO、restic POSIX 源路径层级、POSIX owner/mode，未作为通过 |
| 原件新增接口和既有工作区/加密/Office 相关组 | 首次 **153 passed**；追加转换中撤权共同出口后 content+Office **41 passed**，也已纳入上述最终联合组的 content 部分；这些组重叠 |
| 前端原件和工作台相关回归 | **21 文件，239 passed、2 既有 skipped**；TypeScript、23 个 owner 文件 ESLint/Prettier、Vite 生产构建通过，见[报告](C:/飞牛os/octopus-os/tmp/document-ui-audit/frontend-tests.json) |
| 实际前端 URL → 已认证后端 | **9 项**：原件、相对原件、下载、CSV 预览、越界、缺失、final、upload 与明确附件；真实 SQLite/HTTP、合成内容，见[结果](C:/飞牛os/octopus-os/tmp/document-ui-audit/verified-7n9demcy/result.json) |
| 配置生成器 + 完整 CLI | 两次独立服务启动，原件 SHA 一致、PNG Range 206、未认证拒绝、缺失 404、不串同名附件、NAS 移动/回收恢复及上传重启续传均通过，见[结果](C:/飞牛os/octopus-os/tmp/document-readback-cli-7ozagrmw/result.json) 与[脚本](C:/飞牛os/octopus-os/tmp/document-readback-cli-smoke.py) |
| NAS 恢复进程中断 | 真实 2 MiB 二进制和进程退出；新进程仅凭 prepared 回执核验成功、mtime 不变、恢复传输累计一次，后续同长度篡改被拒绝，见[结果](C:/飞牛os/octopus-os/tmp/backup-runtime-audit/recovery-gzqc_2bj/results.json)；restic/挂载/交换 syscall/POSIX 保护使用明确 double |
| 运维 bundle 新 helper | 新独立加载/CLI 用例通过；既有 bundle 全套在 Windows 仍受 os.fchmod 等 POSIX 平台条件限制，未声称全套通过 |
| 源码门分类 | **116 文件**分类与路径通过，包含并行任务新增 storage pool 测试登记；没有执行整个门禁或据此评价该存储池实现 |

完整 CLI 测试使用真实 appliance 配置生成器注入持久 `local_auth`。早期仅回环启动
运行时、挂载设备扩展的相册测试，只证明其设备端点认证；不能外推为 Agent 全接口
认证。上述新证据仍使用静态 planner 和合成文件，未覆盖自然语言文档整理与撤销、
真实 restic 仓库、Linux 切换 syscall、物理故障、所有安装形态或全仓测试。

### 空图库清理、真实 restic 与文档撤销的当前状态

删除全部原图后，照片服务现在可以在完整扫描确认为空、根目录身份未变且仍有派生记录时，
沿既有计划和单次审批执行仅清索引的任务。无需加载模型或解码图片，清除九类图片关联记录，
保留人物、类别及同库设置；任务仍使用原有日志、操作租约和同事务提交回执。
前端展示清理影响数、取消/重开和失效计划，新 service 实例可以回读 `cleanupOnly` 终态。
根目录检查不能证明跨重启挂载身份，也不等于对子挂载和物理盘的完整健康检查。

后端定向组合 **87 passed**，文件安全追加组 **38 passed**；相册前端最终 **66 passed、0 skipped**，
TypeScript、ESLint、Prettier 通过。真实 TS 客户端到 HTTP/JWT/审批/SQLite 组合验证审批前 428，
审批后清理、原文件哈希不变和重启回读，见
[相册 HTTP 证据](C:/飞牛os/octopus-os/tmp/photo-empty-cleanup-ui-audit/http-k4lr00sr/result.json)。
这些是合成数据与控制流证据，模型调用被主动阻断，不能算真实模型质量或硬件验证。

备份引擎已追加 **restic 0.19.1 Windows** 实际仓库验证，官方下载资产 SHA256 核验，执行
真实 init/三次 backup/check/两次精确快照 restore --verify、文件清单和 SQLite integrity 核对，
并触发错误密码和复制仓库的单字节损坏。未变化备份仍产生新快照及元数据，不能称零写入幂等。
结果见 [真实 restic](C:/飞牛os/octopus-os/tmp/backup-environment-audit/roundtrip-kp1vt60s/results.json)。
未操作既有 VM；Windows 实验不代替 Linux 挂载、目录交换、权限、空间满或物理断电验证。

最初文档撤销核验用真实 ToolExecutor 和临时文件复现四项问题：相对路径修改的原始内容捕获错误、
append 使用片段而非完整结果校验、原始 CRLF 丢失，以及删除后重新创建的文件可能被旧撤销覆盖。
当时工作区已有修正和四个回归样本，尚未完成整个回滚工作。该时点边界/能力/文档/文件事件/
read-before-write/rewind 联合组为 **63 passed、1 failed，17.42 秒**；失败在
`tests/test_file_op_events.py:107`，是旧相对路径断言与新绝对路径事件之间的契约差异。
当时恢复仍直接 `write_text`；此问题及事件展示路径的失败在下面的后续核验中继续修正。
不能把这个历史快照的数值当成当前候选结果，也不能把文本修正当作完整文档整理交付。

该时点新增测试分类尚未登记；后续已补登记，见下一节。未执行完整源码门禁。
上述各测试组有重叠，不累加为独立总数。

### 深度核验后：文本回滚、当前权限与 Desktop 整理

本轮沿已有实现修复，不新增数据库或任务系统。原件在授权和路径处理后捕获，结果在计时
handler 返回后、hook/诊断之前观察；append 校验完整结果，保留原始 CRLF。差异和诊断先
合成，再经过一次输出 hook，避免覆盖脱敏结果；慢文件观察不会把成功 handler 改判超时。

预览按逆序模拟同一文件的多个状态，较新冲突、失败或不可逆项阻断更早撤销。文本恢复用
私密暂存、fsync、身份/哈希/权限复核和原子发布；结果区分提交成功后的处理失败、提交
不确定及证据权限收紧失败。Windows 删除撤销使用同一独占句柄；POSIX 对新建文件删除
撤销暂明确拒绝，预览也不会显示可执行。通用 rename、二进制与所有外部竞态没有因此解决。

rollback/rewind 从服务端任务及当前持久线程状态确定授权工作区，检查活动任务、租约及
未知活动目录，复用任务 store 锁。同时间戳事件按 journal 追加顺序选择；无顺序的旧后端
标记历史完整性未验证。原 FileRollbackEvent 保留逐项提交状态和恢复路径，重开可回读。

原有 Desktop 整理已经接入批次 ID、逐文件指纹、部分成功/冲突和可恢复日志；界面消费
真实结果，IPC 失联显示待确认。它仍是 Desktop 常规文件整理，不是任意目录的发票任务，
也没有替换工作台手动保存的 artifact revision 撤销链。

最终后端 **210 passed、3 POSIX skipped（48.81 秒）**；前端 **4 文件、55 passed（4.53 秒）**，
相关 Ruff、TypeScript/ESLint/Prettier 和 Vite 构建通过。真实跨进程正常恢复 **3/0/0**，
独立修改场景 **1/2/0**，JSONL 与 SQLite 回读一致；原件字节、预览不写和提交结果均验证。
源码门 **125 文件**分类通过，含并行任务 UPS 登记；未执行全门禁或验收 UPS。

源文件指纹、命令、结果与完整边界见[专项验证记录](DOCUMENT_ROLLBACK_VERIFICATION_2026-09-05.md)。
所有样本为独立临时目录中的合成数据；没有调用 LLM、运行完整 Electron 窗口或操作已有 VM。
各组重叠，不能累加为全项目通过数；Linux/物理设备与 O01–O13 的剩余验收继续有效。

### NAS 发票整理：实际 provider、审批与恢复

完整 appliance 已装配文件整理服务。文件面板和可信 Agent 计划/状态工具共用授权 NAS 根，
服务端保存精确计划，用户独立审批后才按开票年月移动。UI 显示不确定文件、冲突和每项回执，
按文件名、日期和金额查找本计划原件；筛选不会改变整计划审批范围。任务沿用 TaskSupervisor，
provider 的计划与文件回执不另建调度器。

新增条件移动、准备回执、文件边界取消、真实回读、新审批重试与独立撤销。文件已提交而任务/
审计未完成时不再误报全完成，重试仅收尾的情况不重复移动。原件下载按计划条目重新读取并
核验完整快照，拒绝回读后发生的外部替换。通用 Agent 执行器通过服务器注册的路径类型区分
NAS 相对路径与宿主工作区路径，保留原有授权和工具治理。

该阶段源绑定后端 **625 通过、5 Linux/POSIX 跳过**，前端 **101 通过**，包含真实 PDF/DOCX
字节恢复、进程退出窗口、审批重放、任务归属和原件读取变化。真实浏览器也已走通目录选择、
预览、密码审批、移动、金额查找、原件下载及再次审批撤销。命令、源文件指纹与平台限制集中在
[专项验证](DOCUMENT_ORGANIZATION_VERIFICATION_2026-09-05.md)；这些数字不冒充整个门禁。
pypdf 已补入核心依赖及双架构发行锁，Windows 锁产物写入修复，
未因此声明镜像或冻结程序已构建。完整自然语言样本、解析资源隔离、Linux 与整机验收继续推进。

### NAS 文档解析隔离：已接线与最终联合验证

预览在读取原件之前取得独立跨进程准入槽。固定 worker 应用内存/CPU 限额，父服务验证实际
解析进程身份后才发送字节。提取器按页、段落和行累计正文与展开量；单文件异常丢弃部分文本，
全扫描超时/取消使计划不可应用。回收未确认时保留槽位，不继续叠加工作进程。

普通线程上传、工作区/Office GET 文本预览、Agent `read_file` 的结构化 DOCX/PPTX/XLSX/CSV/TSV
读取以及 `notebook_read` / Agent `.ipynb` 读取现在先完成受保护的原件/快照读取，再通过固定
worker 解析 PDF、DOCX、PPTX、XLSX 和 Notebook；worker 不可用、超时或回收不确定时只省略正文，
不回滚已保存的文件。Notebook worker 只返回有界的单元格文本和文本输出，不执行代码或携带图片
数据；Notebook 编辑保留有界读取与原子写入。纯文本上传继续走轻量兼容提取器。Office fidelity
转换仍使用独立的 LibreOffice/Quick Look 子进程，已清理服务环境变量并使用临时用户 profile；
本轮又把 Agent `read_file` 的 PDF 页码范围接入同一 document worker：worker 返回页数与实际页号，
大于默认 10 页的 PDF 在服务进程不解析正文，显式页范围仍受 200 页上限、内存/CPU/墙钟和输入字节
预算约束。`read_file` 多模态、文档文本/预算、真实 worker 与文档装配回归 **121 passed、3 skipped**。

联合回归补齐 documents ABI 的预算与清理异常声明。最终 **743 通过、7 Linux/POSIX 跳过**，
其中已实际运行 Windows 最小 onefile 的 11 项测试；前端 **101 通过**，类型、Lint、格式与构建通过。
真实 HTTP 和浏览器重新验证审批、文件回读/冲突、下载、重启与撤销，并记录固定解析 worker
实际启动。默认预算的四类小型文件各三次解析均通过。原始失败、源码与 EXE 哈希分别保留。

源码门清单当前 **159 文件**完整，仅为清单验证，未执行全门。所有证据与测量范围见
[解析隔离验证](DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)。Linux 原生尚待实跑，Linux
onefile 当前失败关闭，macOS 尚不支持；父服务阻塞文件读取、fidelity 转换的独立预算、其他
其他解析入口、Linux 原生/onefile、macOS 和整机验收仍未完成。
不能把这项实现写成全 Agent 沙箱或 O09 整体完成。

本轮另确认外部 Storage 服务在本机及明确相邻目录均未找到，且默认端口未监听；已询问源码或
发行包位置。该代理尚无可验证的成员资源授权合同，保持为未完成整合项，不以添加一个请求头
或禁用全部成员功能代替双端实现和真实服务测试。

服务定位结果、涉及的读取/管理/任务接口以及 A/B/共享/显式拒绝验收要求已记录在
[Storage 授权接入](STORAGE_AUTHORIZATION_INTEGRATION.md)，具体协议仍需取得服务实现后确定。

## 执行所有权、审批与中断的本轮收口

修复前的租约拒绝仍执行已有隔离复现；现在受管实时 ReAct/native 在推进模型循环前完成
持久登记，Session 携带冻结的 holder/token/incarnation，工具处理器、续租和结算都检查同一
身份。登记失败或失租不允许后续处理器执行；同进程新租约、删库重建及旧线程迟到均不能借用
新身份。自动验证的新 driver 复用同一租约，但不重新开放旧执行 scope。

实际审批有两种不同流程：交互审批等待期间继续续租，答复后才进入工具；持久 HOLD 保留
WAITING_APPROVAL，批准和结算相竞时保留批准结果。审批后的 Continue 通过原子消费的服务器
许可换发新租约，检查任务 owner/thread。native 工具补接真实审批提供器，明确拒绝优先于
自动执行偏好，完整参数参与风险判断，拒绝、超时和取消不能被模型的完成文本覆盖。

任务库不再自动用旧备份或空库替代损坏主记录。无效主记录明确要求恢复，原件保留；新租约的
随机 incarnation 防止计数重发造成旧执行复活。合法旧租约仍可读取，但新执行一定换发身份。
这改变了任务库自动恢复语义：显式恢复需停止旧执行后换发新身份，未实现任意旧快照的在线
热恢复。[损坏/回退与删库重建实证](C:/飞牛os/octopus-os/tmp/task-lease-execution-audit/corrupt-a0o28mv9/result.json)

Windows 中断另修复任务锁元数据读取：首字节由系统锁定时，以无缓冲方式跳过 sentinel 读取
实际身份，保留排他性。原两项中断失败在真实协议下修复，中断回执均为 true，测试中的请求到
cancelled 分别约 31 ms 和 78 ms；这是该机器合成用例的观测，不是性能承诺。
[原始通信证据](C:/飞牛os/octopus-os/tmp/task-interrupt-audit/trace-nu0e4sdb/result.json)

最终 Windows 联合回归 **892 passed / 186.39 秒，零失败/跳过**，涉及实时网关、ReAct/native、
Supervisor、任务恢复、子 Agent Session、回执、ProjectOS 和设备任务投影。记录的 66 个实现与
测试文件前后 SHA256 一致，HEAD 为 `9e35adf2c3eb7cb84a8015bfcd24cc5fa95aaf1c` 加未提交改动。
[完整命令、日志与校验](C:/飞牛os/octopus-os/tmp/task-lease-verification-d306be6bbe/result.json)

Linux 文档解析另完成源码 **96 passed / 12 Windows 专属跳过**，以及真实最小冻结服务父的
正常 PDF、超时、父突然退出三场景；与上述测试不累加。旧 Windows EXE 保留其历史源码身份。
[解析验证](DOCUMENT_EXTRACTION_VERIFICATION_2026-09-05.md)

公共源码门的分类与文件检查覆盖 **174 个文件（148 appliance / 26 runtime）**，加入新增
任务身份、线程锁、Btrfs 功能回归与 Linux 解析环境回归；当前仅完成分类/存在性检查，整套
门禁仍须在 Linux runner 上执行。
[库存检查记录](C:/飞牛os/octopus-os/tmp/task-lease-public-inventory.json)

此前 606 passed / 5 failed 的扩大回归是中间结果：两项真实 Windows 中断缺陷已修，另三项
涉及服务器任务 ID 的旧测试替身和 Windows JSON 路径断言，已修正测试表达并保留真实效果
断言。新增真实审批与 HOLD 的红测也由生产修复通过，没有移除红测或放宽时间阈值。

## 2026-09-06 验证增量

本轮继续收口 O05 的真实风险边界。ToolExecutor 现在对 `react_loop` 与 `agentic` 都写入
服务器封存的副作用回执；可替换插件不再凭自报 affinity 获得自动重试资格。native 与写入
工具在副作用可能已发生时停止瞬时重试，限时线程在超时后关闭后续自动重试，native 任务
在进入模型前检查未解决回执，并使用 SQLite 原子分配不复用的步骤号。确认存在恢复意图时，
实时入口走检查点感知的 ReAct 恢复路径；不能从损坏或不完整 JSONL 前缀推断安全恢复。

对应回归证据（均为当前工作区运行，未改动并行存储实验）如下：

| 范围 | 结果 |
| --- | --- |
| native 回执、步骤序列、效果存储、工具超时与读取策略 | **80 passed** |
| 恢复拒绝、检查点、严格 JSONL 读取 | **68 passed** |
| 受影响的 ReAct、任务路由、工具桥、native 与相册执行联合组 | **878 passed**，100.23 秒 |
| 日志、轮换、范围、恢复底层联合组 | **111 passed**，10.61 秒 |
| 前端过滤后的 Vitest | **447/447 文件通过**，3262 passed、2 skipped，121.99 秒；另有脚本测试 18 passed |
| 前端 TypeScript、ESLint、生产构建 | 全部通过 |

命令、当前 HEAD 和限制已写入
[`tmp/agent-os-polish-20260906/result.json`](../tmp/agent-os-polish-20260906/result.json)。工作区仍有
并行存储变更，结果只描述执行时看到的候选状态，不是干净 release artifact 的签名。

首次以默认并行度运行前端全量时，有两个等待时限用例因资源争用失败；两个用例单独运行及
第二次完整过滤运行均通过。该时序敏感性仍应在 CI 参考机上观察，不能把一次通过当作性能
承诺。

公开源码门的清单已补入 4 个 Btrfs 功能回归文件。当前 Windows 开发机无法导入
`fcntl`、`grp` 和 Unix socket 专属类型，因此不能在这里宣称源码门通过；该门仍须在 CI 的
Linux runner 上执行。Windows 上强行运行整套 appliance 门还会产生 POSIX 权限、进程和
文件模式语义差异，不能作为 Linux 发布证据。

### 2026-09-06：D 盘 echo agent 执行边界与记忆语义接入

本轮没有复制 D 盘项目的 UI、数据库或调度器，而是吸收其对 OS 最有价值的两个合同。
`runtime/execution/request.py`、`host_boundary.py`、`engines.py` 和
`artifact_contracts.py` 固定了 host 拥有的任务身份、租户/用户、权限、截止时间、引擎
选择和 artifact 坐标；Native/Codex provider 不能通过续跑或模型输出替换这些字段。
实时 ReAct 与 Codex App Server 都在已有 `TaskExecutionGuard` 入场后绑定请求，并把同一
身份传入惰性 provider、Codex 动态工具和子 worker；未携带完整主体的旧匿名会话保留兼容路径。

记忆层新增 `runtime/memory/semantics.py`。user store、MemoryHub、资产追踪和模型提示词
现在区分用户断言、项目知识、派生/模型摘要和未知来源；旧记录、导入数据和模型内容不能
凭 JSON、confidence 或 provenance 自称“已执行”。进入 prompt 的记忆带来源/可信标签、
固定的参考数据提示和 JSON 引用，执行成功仍只能由 Journal、TaskSupervisor 与 provider
回读证明。现有本地 user memory、相册索引、文件任务和设备数据库均未合并。

验证增量：执行边界、实时续跑/租约/任务执行 **129 passed**；Codex 路由与驱动 **31 passed**；
记忆语义、Hub、资产、路由、可见性、profile 和 OpenAI prompt **75 passed**；本轮 Project OS
路由与 host scope **24 passed**，直接 subagent HTTP **16 passed**，并行任务/团队路由与
stack runner **81 passed**，team task host worker/auto-parallel Session 继承追加回归通过；对应
变更通过 Ruff。当前 host 合同已覆盖实时 ReAct、Codex、Project OS、直接 subagent、team task/
cowork worker、受管 auto-parallel 父 Session、legacy parallel task worker，以及独立 parallel HTTP
批次和 deep-research HTTP worker。独立批次现在有聚合 batch-level TaskSupervisor 记录、心跳和终态
结算；租约丢失会关闭批次并保留失败原因，进程重启后可见过期记录并按恢复队列处理；新的
`recovery-snapshot`（并由 deep-research 作业恢复接口转发）会依据聚合 host record 提供脱敏的任务、状态、时间和错误证据，明确标记为
durable-only，不恢复模型输出、事件或调度器状态，也不会自动重建内存批次。未装配 supervisor 的
独立调用仍保留兼容路径。跨进程/Linux/物理恢复、真实模型
质量和完整整机验收仍是未完成项。

随后补齐了通用并行工作台的重启发现：`GET /api/agents/parallel/recovery-snapshots` 从
TaskSupervisor 聚合 host 行按 owner/tenant 返回脱敏快照；面板在没有内存 `BatchResult` 时
展示只读任务卡和持久恢复提示，不将 worker 输出或可取消的实时状态伪造回来。该入口与全局
live status 分开，避免用批次 ID 列表作为跨用户恢复发现机制。

deep-research 也补上了状态语义缺口：重启后若只能找到 durable-only 快照，作业和历史接口
保留原有工作流 `status`，并增加 `recovery_required=true` 与固定原因
`durable_only_recovery_view`；恢复到进程内 live batch 后会清除该标记。这样“running”不会被
当作当前仍有活跃调度器，且旧客户端仍可按原 `status` 读取。

本轮继续回归：并行、deep-research、团队、直接 subagent、Project OS、执行边界和批次 host 联合组 **167
passed**，其中包含重启后的鉴权 recovery-snapshot 路由和 owner-scoped recovery-snapshots 列表；应用根路由装配 **2 passed**；另此前应用装配子组为 **21 passed**。deep-research 作业
现在持久化聚合 `host_task_id`，重启后可沿作业记录定位 TaskSupervisor 恢复项。新增独立 parallel
worker 与 deep-research worker 的 host 租约、parallel 聚合 host 的正常结算/租约丢失、team worker
的租约心跳、auto-parallel 父 Session 继承和伪造 caller session 丢弃均在当前 Windows 工作区验证。
变更文件通过 Ruff，涉及模块通过 `compileall`，`git diff --check` 无差异错误。该结果是当前未提交
工作区的源码回归，不替代 Linux、跨进程或整机制品验收。
前端 parallel/deep-research API 与面板回归 **22 passed**，`pnpm check` 通过；前端 OpenAPI 类型仍由
既有快照生成流程维护。

Codex 执行预检已从 D 盘 Agent runtime 以独立只读合同接入模型 profile。它统一检查显式开关、
App Server 可执行文件、模型兼容性、工具注册和账号授权，不启动进程、不刷新凭据、不把异常
细节写入 API；profile 响应增加 `execution_available` 与固定的
`execution_unavailable_reason`，让 UI 能把“模型可选”与“此刻可执行”分开。新增
`tests/test_codex_readiness.py` **5 passed**，与 Codex 控制、Agent roster、OpenAPI 快照联合组
**88 passed**；相关 Ruff 检查通过。

追加 Codex readiness 的前后端接线后，当前 Windows appliance 全量回归为 **1984 passed、82
skipped**（259.36 秒）；后端 Codex readiness/控制面/OpenAPI 联合组 **28 passed**，前端
Codex API 与控制组件 **21 passed**，`pnpm check`、Ruff、ESLint 和 Prettier 均通过。跳过项仍
是 POSIX、Linux、物理设备、Docker Unix socket 和可选真实 worker 等环境专属门，不能替代
Linux/整机验收。

在递归子 Agent 治理、主 ReAct 用量回报和租约心跳接线后，并在 legacy memory 边界收紧及共享
viewer 接线完成后重新执行当前 Windows appliance 全量回归：**1990 passed、82 skipped，249.76 秒**。新增治理相关源码和测试模块已通过定向
`py_compile`；全目录 `compileall` 仍会遇到仓库中既有的技能占位文本文件，不能把该占位文件
误报成这次改动的语法错误。

本次重启发现增量单独验证：后端并行 host/路由 **46 passed**；前端并行面板/API **18 passed**，
相关 Prettier、ESLint 通过。该证据覆盖列表的 owner/tenant 过滤、重启后持久快照和 UI 只读
降级，不代表自动调度重建或完整整机恢复。

随后新增的 deep-research 状态语义回归为 **16 passed**；前端 deep-research 面板、历史列表
与并行 API 联合回归为 **20 passed**，`pnpm check`、Ruff 和 `git diff --check` 通过。该增量
只把“旧工作流状态”和“当前需要恢复核查”分开，不改变任务自动恢复边界。

本轮跨入口资源身份与只读回执增量回归为 **67 passed**；其中包含设备相册、Agent 图片库、
成员候选范围、动态调用撤权和 host execution 身份绑定。`resource_identity.py`、照片服务、
图片技能与相册前端类型通过 Ruff、compileall、Prettier、ESLint 和 TypeScript 检查。新增
source/asset 字段均为兼容性字段，未改变原有 path URL；外部 Storage 仍没有被这组测试视为
已完成的资源级成员授权。

随后吸收 D:\\echo agent 的短连接生命周期做法：新增
[`runtime/platform/io/sqlite.py`](../runtime/platform/io/sqlite.py)，把短时 SQLite 事务的提交/回滚
与句柄关闭绑定，已接入 Reach 缓存、代码索引持久化和索引状态查询；任务、记忆、相册等常驻
连接继续由各自领域管理。新增提交、回滚、异常和关闭回归；同时修正通用原子写入在 Windows
没有 `os.fchmod` 时的降级路径。相关短连接、原子 I/O、Reach 和代码索引回归为 **50 passed、
1 skipped**；Ruff 与 compileall 通过。Windows 的 POSIX 权限位不作为 ACL 证据，敏感目录仍需
使用平台专用 ACL/私有目录验收。

随后把 D:\\echo agent 的递归子 Agent 治理边界接入现有 bridge：带宿主 `Session.turn_id` 的
子调用现在在共享 SQLite WAL 账本取得跨 worker 租约，按部署全局和单轮根任务分别限制活动
数；mini-loop 与主 ReAct 路径都会回报 provider token/cost，用 `usage_id` 做幂等累计，达到
单轮额度后熔断后续 spawn。长任务由守护心跳续租，正常结束释放；超时和取消会等 worker 真正
退出后释放，无法退出时停止续租并依赖租约自然过期。无宿主
turn 的旧 raw 调用继续保留进程内兼容路径；续租明确失败会取消子任务，连续账本异常也会
收紧；账本故障默认 fail-open，可用
`ECHO_SUBAGENT_GOVERNANCE_REQUIRED=1` 改为 fail-closed。治理、主 ReAct 用量和执行器联合
回归为 **60 passed**（与其他执行器组有重叠），Ruff 通过；它解决额度与并发控制面，不等于跨主机
恢复或整机断电恢复已完成。随后主 ReAct、辅助函数和 react-drive 回归子集为 **393 passed**，
确认宿主用量回报是可选回调，不改变既有循环结果。

继续检查线程池边界后，工作树 Agent、TeamRunner 并行角色和后台 cowork 任务也已补上
`Session` 传递：前两者在创建 worker/runner 时捕获父 turn，后台任务按持久 task id 建立独立
host turn，避免 ContextVar 在线程或守护线程切换时丢失治理与归属。相关定向回归为
**43 passed**（organization/team runner）、**11 passed、1 skipped**（cowork async；跳过项为
Windows 不支持删除仍打开的 SQLite 文件）以及 worktree/会话与 Windows git pointer 回归
**15 passed、2 skipped**（跳过项为宿主没有 POSIX `sh`）。

随后把治理状态接到已经按 root thread 鉴权的 subagent event bus：事件只携带白名单里的
token/cost、breaker 和额度字段，内部租约 owner/id 不会进入总线或前端。Agent Workbench 时间线
在子 Agent 生命周期事件上显示脱敏治理用量，熔断状态沿用错误态提示；前端事件映射回归为
**10 passed**，后端治理/事件回归为 **22 passed**。这解决了“有额度控制但操作员看不到状态”
的问题，仍不等同于独立的全局治理查询接口或跨主机账本可用性。

同日把 D:\\echo agent 的记忆可见性边界接入主 `/v1/chat/completions`：认证请求用服务器解析的
tenant、actor、角色和已登记 `team_ids` 构造 `MemoryViewer`；读取时聚合同租户分区，在排序和
提示词注入前执行 private/team/restricted/agent 过滤，写入仍固定当前 actor 分区。新增的网关
集成回归验证授权成员可读共享事实、看不到他人 private 事实，也看不到另一租户的事实；主
OpenAI 网关与记忆可见性基础组为 **45 passed**，连同 Principal/auth 与 legacy 边界回归的
当前联合命令为 **51 passed**。对旧的无 `tenant_id` 默认记忆，显式租户
读取已拒绝继承；只有身份目录生成的 `legacy:<actor>` 命名空间且 owner 匹配时保留兼容，避免
旧文件被当成共享租户池。这使“本地记忆数据库＋Agent”具备真实的共享边界，但不替代外部
echo-storage 的资源级成员授权合同。

随后把同一查看者合同继续下沉到 `MemoryHub`、实时 ReAct/工具桥和 `/api/memory/search`、
`/api/memory/assets`、asset trace：服务器在 realtime WebSocket 清洗阶段注入角色与
`team_ids`，而不是读取客户端 metadata；MemoryHub 聚合同租户 actor 分区后再过滤，搜索和
资产 trace 也能看到被授权的团队事实，owner 写入/删除仍留在自己的 `TenantScope`。新增
MemoryHub/API/visibility 组为 **43 passed**，realtime gateway/context/tenant 组为
**16 passed**。这闭合了“主入口能看共享记忆、实时和设置页仍看不到或可能误判”的接线缺口，
不把没有外部服务合同的 Storage 资源授权写成已完成。

又沿 D:\\echo agent 的执行边界补齐了审批和高频事件处理：`permission_modes.py` 统一别名，
`acceptEdits/approve-for-me` 进入独立 `AutoReviewApprovalProvider`，reviewer 不可用、超时、
拒绝或熔断都 fail-closed；普通硬沙箱工作区操作会抑制重复 UI 提示，但不放宽真正的边界。
桌面配置显式开启 bypass 时，Codex 线程才可使用 `approval_policy=never` 与
`danger-full-access`，sidecar 状态目录仍为 deny。Codex App Server 的 token/output delta
采用有界相邻合并、用量快照替换和可恢复 backpressure 错误，Windows 控制环境保留必要系统
变量，线程归档探测遇到短暂锁按未确认处理。

本轮新增审批、Codex profile/full-access、事件洪峰和模式规范化定向回归；当前相关组合为
**146 passed、3 POSIX skipped**，另有 guardian/provider **26 passed**，事件队列新增 **5 passed**。
这些是当前 Windows 工作区源码证据；Codex 真实二进制、Linux 硬沙箱和长时模型质量仍需独立验收。

随后复跑同一组入口并加入 realtime 兼容断言，得到 **172 passed、3 POSIX skipped、13
历史 resume 用例按筛选排除**；App Server transport 全量为 **24 passed**。测试夹具已改用
当前平台的绝对工作目录，保留生产代码对客户端伪造/相对路径的拒绝，不用放宽路径校验来换取
Windows 绿灯。上述数字仍是源码和假 App Server 的协议证据，不代表真实 Codex 二进制或
Linux 硬沙箱验收。

此前默认并行度的前端全量 `pnpm test:unit` 曾得到 **3257 passed、2 skipped、10 failed**；
失败集中在账户审批、文件组织/OMV 交互和 Mermaid 异步渲染，四个文件单独运行均通过，
说明是 jsdom 资源争用而非这些交互的稳定功能回归。2026-09-06 已在
[`frontend/vite.config.ts`](../frontend/vite.config.ts) 固化前端测试边界：裸 Vitest 排除
Electron/脚本 Node 测试，jsdom 默认最多 4 个 worker，并用 `ECHO_VITEST_MAX_WORKERS`
保留参考机调节入口。按默认配置复测 **447 个文件、3268 passed、2 skipped，206.80 秒**；
脚本 Node 测试另有 **18 passed**。`pnpm check`、全量 ESLint、相关 Prettier 和 `pnpm build`
均通过。该修复只收口测试执行稳定性，不替代 Linux、真实模型和整机验收。

本轮把 D 盘项目的自动审核语义同步到前端权限设置和实时发送后，全量单测复跑为
**447 个文件、3270 passed、2 skipped，209.20 秒**；Node 脚本测试仍为 **18 passed**。
新增的 reviewer 字段、兼容别名和“完全访问”设置回归均包含在该结果中。

随后对最后一公里的 native tool gate 做了安全复核：旧的 `acceptEdits` 快路径曾按工具名
直接放行写操作，绕过独立 reviewer；现在统一复用服务端 permission alias，`acceptEdits` /
`approve-for-me` 必须经过 AutoReview provider，只有明确的 `bypassPermissions` 才能走自动
路径。新增 native approval 与 governance alias 回归 **14 passed**，Ruff 通过。当前 Windows
工作区再次执行 Python 全套回归为 **1997 passed、82 skipped，267.57 秒**；跳过项均为 POSIX、
Linux、Docker、真实制品或物理设备门，不能替代对应环境验收。

随后追加的文档入口专项回归为：上传/工作区 **13 passed**、Office 文本/转换/fidelity **9 passed**、
Agent `read_file`/结构化提取 **56 passed**、Notebook/内建读取/文档 worker **64 passed**、文件
读取与工作区授权联合组 **64 passed**、ISO 配置门 **21 passed**；Ruff 与空白检查通过。真实 PDF 上传
预览、真实 DOCX `read_file` 和真实 `.ipynb` 读取均启动固定 worker，转换测试确认 API 凭据不会进入
LibreOffice/Quick Look 子进程环境。Notebook worker 丢弃大块图片输出；编辑入口仍受 10 MiB 读取和
5 MiB 原子写入上限约束。
Office 转换与 fidelity 缓存 key 另加入源内容 SHA-256 并递增版本；同大小同 mtime 的原件替换
不会复用旧预览，专项缓存回归 **11 passed**。

2026-09-06 又完成一次完整收口：Python 全套仍为 **1997 passed、82 skipped**；前端
`pnpm check` 与 ESLint 通过，`pnpm test:unit` 为 **447 个文件、3270 passed、2 skipped**，
脚本 Node 测试为 **18 passed**，`pnpm build` 成功。`pnpm test:electron` 现在在 Windows
通过：Linux 通知与 native-app IPC 夹具按生产契约明确跳过，原生玻璃 **8 passed**，ASAR
发布校验通过。校验器将 ASAR 的 Windows 反斜杠统一为归档相对路径；测试夹具使用
`os.tmpdir()` 并保留 Linux socket/权限测试边界。桌面模拟器仍由 `127.0.0.1:8000` 的
真实后端和 `127.0.0.1:3000/#/desktop` 前端提供，登录、工作台、照片、文件和权限菜单
已人工走通；模型未配置、空 NAS 与外部 Storage 授权仍会明确显示不可用，不伪造成功。

同日又把 Storage 离线路径收口：502/503/504、请求超时和浏览器网络断开统一映射为可重连的
本地化提示；浏览器模式的系统选目录接口保留状态码，网关不可用时不再把
`Folder picker request failed (504)` 直接展示给用户。Storage API 与选目录回归共 **9 passed**，
`pnpm check`、ESLint 与生产构建均再次通过。

2026-09-07 选择性吸收 D 盘 `echo agent` 的提供方错误合同：新增
`runtime/platform/models/provider_errors.py`，把模型探测的 HTTP 400/401/402/403/404/422/429、
模型不存在、未知上游错误和传输故障映射为有界的脱敏分类；响应正文不会进入异常或 API。模型
插件连接/启用入口现在分别把配置类错误和可重试的传输错误转为对应的本地恢复提示。新增提供方
错误回归与路由接线 **7 passed**，Ruff 通过；原有模型插件生命周期测试中另有一项依赖缺失的旧安装夹具
返回 403，未计入本次改动通过数。

同日修复云商城离线时的桌面启动链路：`/api/agent-market/cloud/installed` 现在把本地技能、
插件清单与签名目录状态分开处理；目录缺失、过期或不可信时仍返回本地已安装投影，并以
固定字段提示前端进入离线态，不再把可选商城故障升级成 500。带 `.git` 的源码 checkout
还会明确标记 `local_dev/builtin_fallback`，让未初始化的可选专家子模块不阻塞模拟器应用中心；
正式包仍返回 `catalog_available=false` 并保持签名 fail-closed。新增接口回归 **1 passed**；
云目录专项 **18 passed、1 Windows mode skipped**。该降级不放宽安装/更新接口的签名校验。
重启 `127.0.0.1:8000` 真实后端后，模拟器桌面实际收到 200，应用库能够列出内置工作台，
没有再出现启动阶段的目录 500。
随后全量 Python 回归为 **1999 passed、82 skipped，266.80 秒**；新增目录回退、提供方错误
和 Windows mode skip 均包含在结果中。跳过项仍需 Linux/POSIX、Docker、真实制品或物理设备
环境，不能被本机结果替代。

2026-09-07 又补齐 O09 的一个可验证子项：设备相册已有单任务跨进程锁，但通用 Agent 图片
和视频技能可直接共享 CLIP/InsightFace 单例，之前没有统一的推理准入。现在
`runtime/memory/hemolymph/image_semantic_index.py` 提供进程内准入和同一设备跨进程 OS 锁，图片
和视频的 `embed`/人脸 `get` 都经过同一个默认单并发闸门；等待使用有界退避，索引等待期间
收到取消或暂停会退出，原生调用返回后立刻释放槽位。`ECHO_IMAGE_MAX_CONCURRENT_INFERENCE`
可在 1–4 间调整上限，`ECHO_IMAGE_INFERENCE_WAIT_SECONDS` 控制 0–30 秒等待，
`ECHO_IMAGE_INFERENCE_GATE_DIR` 可指定本地共享锁目录。超时返回固定 `image_inference_busy`/
`resource_limited`，不会覆盖已有 SQLite 索引快照；相册 readiness 增加脱敏的 `inference`
资源观察字段和 `processShared` 标记。新增 Windows spawn 跨进程持锁回归与图片、视频、相册、
OpenAPI 联合回归本轮 **71 passed**，前端照片单元 **67 passed**，`pnpm check` 通过。

这次把 O09 的“单进程并发上限、退避、取消释放”扩展到同一设备的多个服务进程；跨主机
GPU 调度、内存峰值和大图库吞吐仍需 Linux/物理参考机测量，不能把本机回归写成 O09 整体
完成。

随后把相册任务的暂停/恢复补成了可验证闭环：`running → pausing → paused` 在安全检查点
回滚临时事务、保留上一份 SQLite 快照并释放写锁和推理准入；`resume` 会重新校验计划和
图库指纹，使用同一任务身份重新排队，服务重启后的 `paused` 任务也可继续或取消。相册
生命周期新增暂停恢复回归 **1 passed**，相关相册生命周期、持久化、空库清理、图片、视频
与 OpenAPI 回归 **139 passed**；前端 `pnpm check` 通过。O09 仍未覆盖跨主机 GPU 调度、内存峰值和
真实大图库吞吐，这些需要目标 Linux/物理设备的运行数据。

最终代码又跑了一次当前工作区 Python 全套回归：**2004 passed、82 skipped**；唯一失败是
Windows ACL 子进程在长时间全量运行中超时的既有基础设施探针，单独重跑该测试通过。
这次结果包含推理准入、相册 readiness、资源忙错误和暂停恢复路径；跳过项仍是
POSIX/Linux、Docker、真实制品或物理设备门，不能替代目标环境验收。

随后在跨进程推理闸门和 readiness 无副作用修复完成后重新执行全量 Python 回归：
**2006 passed、82 skipped，270.83 秒，无失败**。Windows spawn 子进程持锁、父进程资源忙
和正常释放已包含在通过结果中；跳过项仍只代表当前主机没有 POSIX/Linux、Docker、真实制品
或物理设备条件，不能替代目标环境验收。

同日再次运行隔离的真实 CLI 烟测 `tmp/appliance-dev-smoke.py`：服务连续两次启动均通过
未认证拒绝、登录、相册库/状态、原图字节读取、四个设备相册工具注册和存储健康接口；
第二次移除初始密码与 JWT 环境变量后，重新登录及持久令牌验证仍通过。此次运行产生的
结果见 [最新 CLI 烟测](C:/飞牛os/octopus-os/tmp/appliance-cli-smoke-7cerz3kl/result.json)。
它验证的是本机真实 HTTP/SQLite/文件装配，不包含真实模型调用、Linux 存储或外部成员
授权验收。

同日继续处理大图库推理的实际调用开销：相册增量构建现在通过
`ECHO_IMAGE_EMBED_BATCH_SIZE`（默认 8，范围 1–64）把未命中缓存的图片分批送入 CLIP，
批量接口不兼容或整批失败时自动逐图重试，仍按单图记录失败并保留旧快照；未变化图片和
已复用向量不会进入批次。新增批次上限、缓存命中和低内存配置回归 **42 passed**，
相册图片/视频/闸门专项复跑为 **58 passed**；Ruff、compileall 与 diff 检查通过。它减少模型调用次数，但大图库的真实内存峰值和长时
吞吐仍需目标 Linux/物理机数据，不能据此宣称 O09 整体完成。

随后收口了通用图片/视频技能的路径边界：在有 Session 的真实 Agent 执行中，执行器现在会
校验 `directory`、`image_path`、`video_path` 和 `image_paths` 是否落在服务器解析的读范围，
绝对 NAS 路径或其他租户路径会在 handler 前失败；相册 NAS 入口继续由 `photos_*` 重新取得
成员权限。新增 workspace/service 路径回归 **19 passed**，并把图片、视频技能描述改成明确的
“工作区能力 / NAS 使用 photos_*”契约。该修复不限制没有 Session 的本地测试调用，但不再让
模型参数在设备 Agent 任务里绕过服务级授权。

同日又收口了桌面顶栏与工作台的 Codex 模型状态：两处选择器共用 principal-scoped
`model-profile` 查询缓存；工作台切换账户/系统模型后，只有服务端 profile 提交成功才回写
共享本地设置，顶栏切换也反向提交同一 profile。profile 在模型设置页、登录或其他工作台
入口发生变化时，桌面会重新收敛旧的本地值；同步失败保留旧值并显示重试提示。面板文字和
下拉尺寸同步压缩。Echo Mix 仍是不可交给 Codex 执行的 Echo 编排模型，保留在独立命名空间。
相关前端联动回归 **23 passed**，TypeScript、ESLint、Prettier、生产构建和模拟器双向点击
验证通过。

随后把 O05 的恢复入口从“只读快照”推进到“显式选择后重跑”：新增
`POST /api/agents/parallel/batch/{batch_id}/resume`，面板只对快照列出的
`failed`、`cancelled`、`timed_out` 和 `pending` 任务提供动作；调用者必须提交任务 ID，服务端
创建新批次并保存源批次/任务映射，原批次证据保持不变。持久化为 `running` 的任务因副作用边界
未知明确拒绝，未完成依赖也不会被伪造为可恢复；重复提交同一选择会被拦截。健康检查路由、
OpenAPI 快照和生成的前端类型同步更新。并行 host/路由、鉴权与 OpenAPI 联合回归 **42 passed**，
前端 API/面板 **20 passed**，Ruff、TypeScript、ESLint、生产构建和 `git diff --check` 通过。
这仍是人工确认后的安全子集恢复，不代表自动重建调度器、跨主机或整机断电恢复已完成。

又修正了桌面模型同步的实际断点：实时工作台原先通过 `useThreadSettings(threadId)` 读取旧的
会话模型覆盖，而桌面顶栏读取 principal-scoped 全局模型，因此同一账号能出现“顶栏已切换、
当前会话仍显示旧模型”。工作台现在在提交真实模型选择后回写同一 principal-scoped profile，
并同步全局设置；进入 Codex 工作台时还会回写过期的线程覆盖。Echo Mix 仍是独立的编排模型，
不伪装成可交给 Codex 执行的模型。模拟器实际验证了工作台
`GPT-5.6-Luna → 顶栏 gpt-5.6-luna` 以及 `顶栏自动选择 → 工作台系统/当前解析模型` 两个方向，
并通过 TypeScript、ESLint、Prettier 与 **30** 项相关前端回归。

随后补齐了 O05 的持久恢复证据：durable-only 快照中的并行 worker 使用内部 host task id，
恢复视图现在按批次与逻辑 task id 正确归并，不再显示重复的幽灵 lane。新增回归先让源批次
失败并关闭原 orchestrator，再用同一 TaskSupervisor 创建替换实例；替换实例读取持久规格，
用户明确选择失败任务后创建新批次并完成重跑，源批次和 recovery 映射保持可审计，重复选择仍被
拒绝。`tests/test_parallel_batch_host.py` 与安全路由联合回归 **23 passed**。这证明了受管
并行批次在“服务实例替换 + 明确选择安全失败 lane”边界内可恢复；仍不代表运行中任务接管、
任意旧快照热重建、跨主机/断电调度或整机恢复。随后补上 HTTP 路由级回归：源
orchestrator 完成失败后关闭，由同一持久 TaskSupervisor 创建替换实例，经过认证的
`POST /resume` 仍能创建新批次并完成重跑，重复请求返回冲突；路由安全专项现在为
**16 passed**。

## 仍需完成与验证

**O05 尚未整体完成。** 本轮已覆盖受管实时路径及其继承 Session 的副作用回执、步骤分配、
未知效果停机和检查点完整性；仍不证明全部入口统一调度、跨主机效果存储、任意旧快照热恢复、
当前候选跨进程/断电恢复或整机恢复。ProjectOS 自有 claim 和专用文件 provider 的锁继续
保留。真实模型、相册规模性能和外部 Storage 成员授权也不在上述通过证据中。

1. **共享数据身份和授权**：让 OS 相册、Agent 直接工具及可选 echo-storage 具有明确的库选择、
   成员权限与索引状态关系；先查实际调用路径，避免把不同职责数据库强行合并。
2. **真实任务闭环**：从 UI/Agent 请求开始，覆盖照片检索、文档整理、媒体整理和备份恢复，
   实际验证发现、授权、provider 调用、回执、任务投影和结果回读。能力描述存在不是通过证据。
3. **模型与索引交付**：验证受支持环境的依赖安装、首次模型加载、无网络、缓存失效、错误恢复、
   更多取消/重试故障、资源限额与规模性能；完善增量扫描、人物纠错及模型更换后的管理体验。
4. **运行形态逐项装配**：Docker appliance、root netinst、非 root raw/native、独立桌面分别
   检查实际扩展、持久目录和权限。已有 VM/Android 历史证据不能替代当前候选验证。
5. **整机与正式交付门**：沿现有 G1–G6、候选制品和故障矩阵验证安装、升级、恢复、共享协议、
   多成员隔离、断网/断电/存储满与长时运行；保留哈希、日志及环境，不用 mock 代替硬件结果。

原 [优化方案](AGENT_OS_OPTIMIZATION_PLAN.md) 的场景、指标和交付要求继续有效；具体优先级
以当前问题对数据正确性及用户流程的影响调整，不能将尚缺的条件改写成已完成或不在范围内。

2026-09-08 继续收口桌面/工作台入口：动作型导航、顶层旧入口、设置/自动化/反射/商店、设计技能和 Agent 创建均复用 `preserveWorkbenchPresentation`；显式 `presentation=workbench` 的旧路径不会回退桌面。浏览器现场确认技能与 Agent 创建页保留系统状态栏。相关前端回归、TypeScript、ESLint、Prettier、Vite 和 diff 检查通过。真实 Electron 长任务、Storage 外部服务和断电恢复仍需目标环境验证。

2026-09-08 继续统一资源入口：Agent 文件工具在会话工作区内附加 `workspace-file:v1`，覆盖读取、范围读取、元数据、目录列表、glob、内容搜索和树形目录；仅携带服务端 `_artifact_output_root` 的旧 Session 也能安全推导最终产物工作区；生成身份前复用执行读取授权，工作区外和兼容调用不附加身份。新增资源身份回归覆盖读取、搜索、目录、旧 Session 和越界路径，内置文件工具相关回归 53 项通过，Ruff 与 Python 编译检查通过。真实 Electron 长任务、Storage 外部服务和断电恢复仍需目标环境验证。

同轮补齐 OpenAI 兼容网关的 Session 工作区绑定：服务端受管工作区根目录现在会进入会话元数据，普通兼容入口也能让文件工具生成同一资源身份；网关绑定回归通过。真实 Electron 长任务、Storage 外部服务和断电恢复仍需目标环境验证。

消息产物汇总同步消费 artifact 协议中的 `resource_id`：受管 `workspace-file:v1` 优先作为工作台打开引用，旧路径继续走原有归一化；消息产物回归 24 项，TypeScript、ESLint 和 Prettier 通过。真实 Electron 长任务、Storage 外部服务和断电恢复仍需目标环境验证。

2026-09-08 恢复边界复核：持久检查点、暂停任务和租户范围的实时恢复拒绝回归 **94 项通过**；恢复确认在没有可验证检查点、任务身份无效或检查点形状损坏时会在模型调度前失败，并保留原暂停任务与授权额度。旧版 `tests/test_realtime_cerebrum.py` 的恢复夹具已迁移到真实 UUID、SQLite checkpoint 与线程绑定，桥接回归 **51 项通过**；持久恢复与资源身份联合回归 **21 项通过**。桌面/工作台相关前端定向回归 **147 项通过**，Vite 生产构建、TypeScript、ESLint、Prettier、Ruff 和差异检查通过。

同日浏览器现场复核桌面与工作台双形态：应用库打开本地数据库时可选独立窗口或工作台；工作台实际保留 `presentation=workbench`、任务空间和模型与用量状态栏。取消工作台侧栏勾选后，侧栏入口移除而桌面 Dock 的本地数据库仍保留，切回桌面可继续打开。桌面模型菜单实际显示累计 tokens、7 天/5 小时窗口的已用与剩余、重置时间，并完成自动选择与 Echo Mix 往返切换；未发送任务，系统模型已恢复自动选择。

同轮修正应用呈现能力判断：应用详情卡片、工作台放置操作和应用中心筛选统一使用共享 `supportsPresentation`，旧登记项按主呈现兼容回退；未来仅支持工作台的应用不会再显示独立窗口入口。入口回归 19 项，TypeScript、ESLint 和 Prettier 通过。

应用中心内置卡片同步隐藏不支持的呈现入口，呈现说明也跟随登记项；应用中心、工作台放置和登记表相关回归 **38 项通过**。

侧栏开关也只对支持工作台的应用显示，避免独立窗口应用产生无效的工作台操作。

Hub 的独立窗口/工作台筛选同步按登记能力过滤远程工作台资产，未声明对应呈现方式的资产不再误入筛选结果；相关回归 29 项通过。

聊天框 `/skills`、`/pack`、`/meta` 快捷入口现在也保留工作台呈现参数，进入技能目录不会丢失顶部系统状态栏；快捷命令回归 20 项，TypeScript、ESLint 和 Prettier 通过。

浏览器现场从工作台新任务输入 `/skills` 后实际到达 `/workspace/agents?surface=chat&tab=skills&presentation=workbench`，侧栏、任务空间和模型与用量状态栏继续可见；未发送模型任务。

数据库文件“引用到助手”跳转也会继承工作台呈现参数；文件引用队列继续按账号、线程和时效隔离，并在目标会话领取，不自动发送。引用收件箱与快捷命令回归 26 项通过。

产物详情异步定位到本地数据库后也保留当前工作台呈现参数，仍先经过线程范围和资源授权检查。

Storage 网关对 `browse`、`search` 和 `files` 三类 JSON 结果统一补发 `storage-file:v1` 资源身份；本地数据库文件卡片、索引结果和引用动作继续透传该身份，已有服务端身份优先保留，二进制内容路由仍流式转发。代理回归 **6 项通过**；Storage sibling 服务自身的资源读取端点仍需配套版本后验收。

继续审计工作台动作型导航：Agent 创建页返回 Agent 目录、设计页切换创作项目、团队邀请完成后的任务跳转及清理旧查询参数均保留当前 `presentation=workbench`；团队加入回归 3 项通过，类型检查、ESLint 和 Prettier 通过。浏览器现场从 `/workspace/agents/new?presentation=workbench` 返回后实际到达 `/workspace/agents?presentation=workbench`，并打开任务空间确认状态统计、筛选、刷新和返回工作台入口可见；当前无运行任务，未触发真实模型执行。

实时对话页的自动新会话、模式切换、Agent 切换和旧角色迁移也统一保留当前工作台呈现；任务开始时的线程路由同步同样不会丢失系统状态栏。实时页面合同回归 **8 项通过**，类型检查、ESLint 和 Prettier 通过。

本轮入口、文件引用、产物定位与工作台联合回归 **147 项通过**，Vite 生产构建、TypeScript、ESLint、Prettier 和 `git diff --check` 通过。

任务空间对服务端新增或异常状态改显示“状态未知”，不再伪装成“准备中”；未知状态仍可查看、刷新和回到原线程。

2026-09-08 Agent 协作入口最后一轮收口：场景启动、智能组队、角色卡片和 Agent 创建完成后的协作跳转均复用工作台呈现保持规则。相关回归 30 项通过；前端全量单元回归为 473 个测试文件、3449 项通过、2 项跳过，TypeScript、ESLint、Prettier、Vite 和差异检查通过。真实 Electron 长任务、Storage 外部服务和断电恢复仍需目标环境验证。

同日补齐远程工作台应用启动与侧栏删除当前会话后的回到新任务路径；这两类操作也继承当前 `presentation=workbench`。Agent/侧栏定向回归 58 项通过，TypeScript、ESLint 和 Prettier 通过。

浏览器现场再次验证桌面顶栏模型菜单：累计 tokens、5 小时/7 天已用与剩余、重置时间均可见；完成“自动选择 → mix → 自动选择”往返后状态恢复为自动选择。从本地数据库独立窗口进入工作台时，桌面状态栏保持单一实例，嵌入式工作台不重复渲染系统栏。

额度进度条补充账户和窗口周期的可访问名称，读屏用户可以区分 5 小时与 7 天额度窗口；系统模型状态与工作台布局回归分别通过 12 项和 21 项。

任务空间浮层补齐键盘操作：打开后聚焦关闭按钮，Esc 可关闭面板，任务操作和遮罩点击行为保持不变；任务空间回归 9 项通过。

本地数据库内部返回原任务、返回原产物及切换工作台的动作现在继承当前呈现参数；从工作台进入本地数据库再返回 Agent 时，顶部系统状态栏不会消失。新增本地数据库回归 11 项通过。

系统状态栏交互继续统一：Wi‑Fi 与电池状态项点击后进入同一个控制中心，查看与控制不再分成两个入口；非原生环境仍显示安全的只读状态。`macos-shell` 回归 30 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

工作区产物定位继续统一资源身份：服务端 `locate` 返回的 `resource_id` 现在随本地数据库路由传递，数据库目录优先按该身份标记目标条目，旧路径仍作为兼容定位依据。文件定位、数据库和产物相关回归 19 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

预览生命周期继续去重：本地数据库图片、视频和 PDF 预览改用共享 `useNASAssetState`，与媒体应用共用加载状态、错误状态和迟到 object URL 回收；文本预览保留受限文本读取路径。数据库、媒体和 Hook 定向回归通过，TypeScript、ESLint 和 Prettier 通过。

本地数据库双宿主入口继续统一：直接页面进入时也提供工作台切换，无桌面窗口回调时回退到带 `presentation=workbench` 的路由；桌面宿主回调继续保留分类、查询和任务上下文。数据库与应用卡片回归 12 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

任务空间的账号隔离继续统一：任务投影 Hook 现在监听 actor 变化，切换账号时先清空旧快照、终止旧请求，再读取新账号任务，避免旧任务短暂残留。任务空间客户端回归 12 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

桌面窗口状态继续绑定 actor：账号切换立即清空旧工作台窗口、最小化状态和焦点，嵌入式会话不会残留到新账号。窗口、工作台与任务投影联合回归 18 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

路由宿主缓存继续绑定 actor：账号切换清空 RetainedShellRoutes 的桌面/工作台 Location，并按 actor 重建 Activity，旧账号草稿和隐藏宿主同步卸载。宿主、窗口和任务投影联合回归 18 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

桌面待启动请求继续绑定 actor：`workspace`/`returnTo` 查询的消费键包含当前账号，认证切换时不会沿用旧账号的消费标记。桌面启动请求回归 20 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

文件引用收件箱补齐认证会话边界：注销、登录、注册、认证过期及访客模式切换都会清空内存引用，避免同一账号重新登录后继承上一会话的待引用文件。引用收件箱与认证定向回归 33 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

React Query 账号边界也集中到 `AppRouter`：认证 actor 发生变化时清理共享缓存，避免未携带 actor 的旧查询键把线程、产物或设置快照带入新会话。路由缓存隔离回归新增 1 项；与认证、引用收件箱及桌面启动请求合计 34 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

缓存边界实现已从 AppRouter 上移至 AuthProvider，覆盖主桌面和独立工作台两种 QueryClient 宿主；前一条记录中的“路由缓存隔离”指该认证边界回归，行为与测试结果不变。

产物查询缓存进一步显式按 actor 分区：工作区产物列表、产物正文和原件差异查询键都携带当前账号，避免账号切换前先命中旧会话缓存；认证边界清理仍作为统一兜底。产物相关定向回归 14 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

会话历史列表查询也显式加入 actor 作用域；线程列表不再依赖认证切换后的全局清缓存才获得新数据，桌面和独立工作台打开历史会话时直接命中当前账号分区。线程与产物相关静态检查通过。

输入框草稿持久化也按 actor 与 thread 双键隔离，包含尚未创建线程的 `__new__` 草稿；同一设备切换账号不会读到上一账号的本地草稿。草稿与聊天框定向回归 57 项通过，TypeScript、Prettier 和差异检查通过。

图片引用收件箱和最近目标也补齐 actor 作用域：sessionStorage 不再使用全局队列键，认证边界会清理旧账号的截图、图片和目标路径，避免图片附件在重登或切换账号后串入新会话。聊天框/认证定向回归 64 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

认证状态通过后台刷新发生 actor 变化时，也会执行同一套文件/图片引用清理和 QueryClient 清理；会话边界不再只依赖显式登录或注销按钮。

自动新会话的待发送文本也改为按 actor 存储，并在认证会话变化时清空；刷新/重登不会把上一会话的待发内容自动送入新线程。待新会话与认证/聊天回归 65 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

图片收件箱新增独立回归，覆盖目标线程领取、actor 隔离、最近目标隔离和会话清理；该状态现在有独立可验证契约，而不是只由聊天框集成测试间接覆盖。图片/待新会话/草稿定向回归 18 项通过。

自动化目标（浏览器标签页/桌面窗口）持久化也按 actor 与 thread 双键隔离，账号切换后不会把上一账号的控制目标带入新会话。自动化目标定向回归 3 项通过，TypeScript、ESLint、Prettier 和差异检查通过。

## 2026-09-08 身份偏好隔离

Active Agent、工作台 persona 标签和 Echo 助手昵称改为按 actor 存储；侧栏与浏览器 Agent 选择器共用统一写入入口。相关前端定向回归、TypeScript、ESLint、Prettier 通过。

系统设置、链接打开偏好、浏览器 Agent 权限/审计、工作台网页快捷方式及浏览器个人资料（主页、搜索引擎、历史、书签）现按 actor 隔离；旧键只做一次性迁移，浏览器标签和跨窗口打开请求保持设备级临时状态。完整前端单元回归：483 个测试文件，3492 项通过，2 项跳过；脚本测试 18 项通过；TypeScript、ESLint、Prettier、差异检查通过。

新增账号隔离回归后，全量套件报告 484 个测试文件、3494 项通过、2 项跳过；照片面板既有取消竞态用例在全量运行中偶发失败，单独重跑已通过。TypeScript、ESLint、Prettier、差异检查通过。

聊天/代码最近工作目录记忆键现与最近目录列表统一按 actor 存储，旧键按需迁移；实时工作区与最近目录定向回归 14 项通过，TypeScript、ESLint、Prettier 通过。

浏览器桌面首页快捷链接、文件夹和小组件布局现按 actor 存储；应用排序、Dock 顺序、壁纸保留设备级语义，完成内容状态与设备外观的边界拆分。TypeScript、ESLint、Prettier 通过。

新建 Agent 保存提示与首次使用引导完成标记也使用统一 actor 存储迁移工具；共享设备上的不同账号会分别保留引导状态。相关设置、认证与 Agent 页面回归通过，TypeScript、ESLint、Prettier 通过。

社区点赞、收藏、关注、订阅、评论、复刻和发布帖已统一到 `communityStorageKey` 的 actor 作用域；公开 Feed 缓存保持设备级。新增社区隔离回归通过，TypeScript、ESLint、Prettier 通过。

浏览器记录员模式和调研日志也绑定当前 Agent actor；模式旧键按需迁移，调研日志使用 actor 会话键，账号切换不会复用上一账号上下文。

应用目录请求现复用认证模块的 SessionStorage 会话头，移除旧的 `echo:token` localStorage 读取，统一应用、桌面和工作台的认证来源。TypeScript、ESLint、Prettier 通过。

社区 Feed、订阅筛选、个人主页与帖子详情现在也有 actor 切换瞬时态保护；切换账号时先清空旧互动状态，再加载新账号的收藏、点赞、关注和订阅，避免出现一帧旧数据。社区隔离回归、TypeScript、ESLint、Prettier 通过。

点赞持久化实现已收敛到社区数据层的共享读写函数，Feed 与详情页共用同一 actor 作用域和旧键迁移路径，减少重复逻辑。

设计工作台的启用模型集合现按 actor 存储；账号切换时暂停旧集合写入，读取新账号配置后再恢复持久化，避免模型偏好串号。

每日积分自动弹窗的当日免打扰标记现按 actor + 本地日期存储，旧日期键按需迁移，避免共享设备上的账号互相影响提醒。

日期键和迁移读写已独立为积分偏好模块，自动弹窗不再重复处理日期格式化与 localStorage；新增隔离回归、TypeScript、ESLint、Prettier 通过。

浏览器下载历史现按 actor 存储并兼容旧键迁移；账号切换期间旧列表不会写入新账号。TypeScript、ESLint、Prettier 通过。

Agent 协作预设现按 actor 隔离 sessionStorage，实时事件也校验 actor；新增跨账号接力回归，避免旧协作者配置进入新任务。

工作区表面切换器的最近 Agent 路由也按 actor 隔离，浏览器返回地址不会带入另一账号；切换器回归 4 项通过。

模式意图建议的 session 忽略列表也按 actor 隔离，同时保留刷新重置语义；相关组件回归 4 项通过。

actor 存储基础 helper 增加空白身份规范化和显式空值回归；所有账号偏好模块继续共用同一迁移规则。

设计工作台本地项目与画布文档现按 actor + persona/项目隔离，兼容读取旧 persona 键；项目与画布回归 18 项通过，TypeScript、ESLint、Prettier 通过。

旧项目/画布键在成功迁移后删除，后续账号不会重复认领；新增一次性迁移回归，设计存储测试现为 3 项通过。

最新完整前端回归为 487 个测试文件、3503 项通过、2 项跳过，脚本测试 18 项通过；本次未复现照片面板取消竞态。

## 侧栏长期偏好隔离

补齐工作台侧栏最后一组长期用户态：项目分组、项目展开和对话展开均使用 actor 作用域，切换账号时清除旧状态并读取新状态，写入有 actor 守卫，避免切换瞬间把旧状态保存到新账号。已通过 TypeScript、ESLint 与 Prettier 检查；完整单元测试此前已通过，侧栏本次仅改存储边界。

## 浏览器面板内存态隔离

补齐 Assistant/Copilot 面板的 actor 切换边界：记录模式和研究日志按新账号重载，研究目标与复制提示清空；写入 effect 增加 actor 守卫，避免组件持续挂载时旧账号状态泄漏。

## 浏览器待打开请求的账号边界

将工作台到内置浏览器的临时 URL 请求从设备级键改为 actor 作用域，覆盖统一链接入口、预览面板和浏览器宿主消费端；事件确认协议不变，避免账号切换期间发生 URL 串线。

## 聊天布局宽度账号隔离

将 ChatPageLayout 的两个可调整面板宽度纳入 actor 作用域，并在 useResizablePanel 中支持账号切换时重载；兼容旧设备级宽度的一次性迁移，迁移后删除旧键。相关布局回归 12 项通过。

## Design 画布用户偏好收口

补齐 Design 页面两组此前仍为设备级的偏好：工作区布局与画布视图改用 actor 作用域，切换账号时重载布局、背景、小地图和连线设置；旧键按迁移助手兼容一次。设计合同与本地项目测试通过，类型检查通过。

## 完整前端回归（2026-09-08）

在本轮账号作用域、浏览器请求隔离、聊天布局和 Design 偏好调整后重新执行 `pnpm test:unit`：487 个 Vitest 文件、3503 项测试通过，2 项跳过；Node 脚本测试 18 项全部通过。TypeScript、ESLint、Prettier 与 `git diff --check` 同样通过。

## 本地模型引导状态按账号隔离

将 LocalBrainSetup 的关闭提示从设备级键改为 actor 作用域，账号切换时重载 dismissed/expanded 内存态，保持每个账号自己的首次配置引导。

## 实时任务工作台展开状态

补齐 realtime 页面最后一项设备级工作区偏好：Agent 工作台手动展开状态纳入 actor 作用域，账号切换时重载并防止旧值写入新账号。类型、格式和实时页面合同测试通过。

## React Query 账户缓存边界

发现并修复账户 API 查询键固定为 `account/*` 的隐私串线风险：账户归属的 query key 全部加入 actor，套餐目录保持公共共享；新增键隔离回归测试，设置页面相关测试通过，类型检查通过。

## 账户缓存隔离全量验证

账户资料、订阅、用量、账单和概览查询键加入 actor 后，完整前端套件重新通过：488 个文件、3506 项通过、2 项跳过，Node 脚本 18 项通过。类型检查、Lint、格式检查和差异检查保持通过。

## 设置 Hook 生命周期收口（2026-09-08）

本地系统设置与线程设置虽然已经使用 actor-scoped key，但 Hook 之前只按 getter 函数身份建立 effect。现在 effect 依赖 actor scope，账号切换会重载内存快照；保存回调也校验 actor，阻止旧会话延迟写入新账号。新增系统设置和线程设置切换回归，相关测试通过。

设置 Hook 收口后的完整前端回归：488 个 Vitest 文件、3508 项通过、2 项跳过；Node 脚本 18 项通过。TypeScript、ESLint、Prettier 和 `git diff --check` 通过。

浏览器桌面首页移除重复的外部应用清单，改为复用 `components/browser/desktop-apps.ts`；工作台应用继续来自统一的 `core/workbench/apps.ts`。相关工作台登记表回归 11 项通过，TypeScript、ESLint 和 Prettier 通过。

首页分类分组不再维护硬编码 URL，改为从共享目录的 `category` 自动生成，避免应用进入桌面但遗漏分类或分类链接漂移。


任务空间执行身份收口：任务投影新增 xecutionEngine 与 modelName，Echo/Codex 创建时记录，Codex 有效模型解析后更新原任务记录；详情面板可直接核对实际引擎和模型，旧记录保持兼容。


任务执行身份补强定向验证：任务投影 32 项、实时任务结束/租约/Codex 驱动 68 项、前端任务空间 22 项通过；TypeScript、ESLint、Prettier 和差异检查通过。真实 Electron 长任务、外部 Storage sibling 与已登录浏览器 E2E 仍需目标环境。


浏览器 Dock 默认工作台入口改由 WORKBENCH_BUILTIN_APPS 按登记 ID 派生，移除重复硬编码 URL，保留原默认顺序；浏览器首页与应用目录回归通过。


任务身份与 Dock 目录收口后完整前端回归：488 个 Vitest 文件、3510 项通过、2 项跳过；Node 脚本 18 项全部通过。TypeScript、ESLint、Prettier、Ruff 和差异检查通过。


当前环境完整栈 Playwright smoke 已执行：后端、Vite 代理、桌面/工作台入口 1 项通过；实时模型发送场景 1 项按配置跳过（未启用 ECHO_E2E_RUN_LIVE_MODEL）。离线服务链路可启动，真实模型与 Electron 长任务仍待目标环境。


浏览器桌面布局偏好补齐 actor 隔离：应用排列、Dock、壁纸与快捷链接/文件夹/组件使用同一作用域和旧键迁移规则；账号变化时重载并阻止旧布局写入新账号。相关浏览器合同、TypeScript、ESLint、Prettier 通过。


浏览器桌面布局账号隔离后重新执行全量回归：488 个 Vitest 文件、3510 项通过、2 项跳过；Node 脚本 18 项通过。

## 浏览器状态边界与目录去重

浏览器会话、历史、书签、主页设置和扩展安装状态统一到 actor-scoped 存储，并在账号变化时重载；旧设备键仍只做一次兼容迁移。删除 `components/browser/desktop-apps.ts` 中已无调用方的旧排序读写实现，首页保留唯一排序逻辑。Browser Store/TabBar 5 项定向测试通过，TypeScript、ESLint、Prettier 与 `git diff --check` 通过。

随后完整前端回归为 488 个 Vitest 文件、3511 项通过、2 项跳过；Node 脚本 18 项全部通过。

## 模型目录缓存作用域

`useModels` 的 `/api/llm-models` 查询键加入 actor，顶栏、聊天、Design 和设置页共用同一个账号模型目录但不共用其他账号缓存。模型目录作用域测试及顶栏/模型设置定向回归共 54 项通过。

模型目录缓存与旧桌面目录收口后的最终全量回归：489 个 Vitest 文件、3513 项通过、2 项跳过；Node 脚本 18 项通过。

Agent roster、Project OS 与个人记忆的 React Query 缓存统一加入 actor 维度；项目列表/详情/线程映射采用共享查询键，mutation 只失效当前账号。新增查询键回归后全量验证：492 个 Vitest 文件、3516 项通过、2 项跳过；Node 脚本 18 项通过。

连接器授权、技能状态与 OCT 每日额度缓存加入 actor 维度，供应商/商品目录保持公共共享；最终全量回归：494 个 Vitest 文件、3518 项通过、2 项跳过；Node 脚本 18 项通过。

进化、连接器和技能缓存边界收口后最终全量回归保持：494 个 Vitest 文件、3518 项通过、2 项跳过；Node 脚本 18 项通过。

任务看板和协作任务缓存加入 actor 维度，任务看板轮询增加账号切换重置与迟到响应保护；全量回归保持：494 个 Vitest 文件、3518 项通过、2 项跳过；Node 脚本 18 项通过。

智能订阅、报告和聊天产物查询键进一步统一：智能面板、自动化配置/历史页共享 actor-scoped 报告键；聊天抽屉失效时复用 actor/线程产物键，避免重复键导致刷新失效。新增查询键回归；智能面板、智能页面和聊天抽屉定向测试共 9 项通过，TypeScript、Prettier 与差异检查通过。

本轮完整前端回归：497 个 Vitest 文件、3523 项通过、2 项跳过；Node 脚本 18 项全部通过。

协作团队 participant ID 补齐 actor 作用域和旧键迁移，避免共享设备上的账号复用同一成员身份；新增存储回归，团队加入页定向验证通过。

Assistant 与 Copilot 的浏览器研究日志读写逻辑收敛到共享核心模块，统一账号作用域、结构校验和条数上限；新增研究日志回归。

本轮完整前端回归：499 个 Vitest 文件、3527 项通过、2 项跳过；Node 脚本 18 项全部通过。

智能订阅后端补齐 actor owner 过滤：认证 API 只读写当前账号的订阅和报告，`run` 也只运行当前账号；调度器保留整机扫描。唯一账号可自动迁移旧版平面文件，多账号不会隐式认领旧数据。后端智能路由与原子存储 14 项通过，Ruff 通过。

智能报告自动写入记忆时改用订阅携带的 `TenantScope`，不再把账号报告写入全局 memory；新增 owner/tenant 传递回归，智能路由与原子存储共 15 项通过。

缓存失效边界继续收紧：上传文件、线程搜索和记忆资产的查询键与失效键统一加入当前 actor，mutation 不再刷新其他账号的缓存；新增上传查询键回归，TypeScript 与定向 Vitest 通过。

本轮完整前端回归更新：500 个 Vitest 文件、3528 项通过、2 项跳过；Node 脚本 18 项全部通过；TypeScript 检查通过。

全栈 Playwright 复核通过：隔离后端与 Vite 启动成功，Chromium 访问桌面/工作台、API 代理、Agent 与智能订阅入口通过（1 passed，实时模型发送场景按配置跳过）。

Electron 原生壳桥接回归通过：桌面更新、Agent 服务、系统动作、更新、Wi‑Fi/电池控制、通知、系统应用解析、流光玻璃和原生壳包校验均通过；Linux 会话限定的通知与原生应用 IPC 烟测按既有规则跳过。

生产构建校验通过：前端 `pnpm build` 退出码为 0，Vite 成功生成桌面、工作台、模型设置和本地数据库等拆分产物。

查询失效范围继续收口：本地模型和任务事件只刷新当前 actor；全局 Agent registry 与设备能力策略使用明确的全局根键；OCT link 账户查询与设置预取共用规范键，余额刷新不再覆盖其他子查询；NAS 恢复预览显式保证目标映射。定向回归 10 个文件、53 项通过，ESLint、Prettier、TypeScript 通过。

收口后的当前源码再次执行 `pnpm build`，Vite 生产构建退出码为 0（20.69 秒）。

工作台偏好边界继续统一：能力市场分类折叠状态按 actor 存储，旧设备键只做一次迁移，账号变化时重载并保护写入。相关定向回归 31 项通过，TypeScript、ESLint、Prettier 通过；随后生产构建退出码为 0（21.64 秒）。

集市与社区状态边界继续统一：自营商品、已购/已售状态和资产提示横幅按 actor 存储并迁移旧键，账号切换时不会互相覆盖。新增集市存储隔离回归 2 项，相关存储回归共 4 项通过；随后生产构建退出码为 0（22.72 秒）。

集市状态边界收口后的当前源码再次构建通过：`pnpm build` 退出码为 0（25.92 秒）。

诊断遥测账号边界继续统一：流式遥测按 actor 存储，旧裸键只迁移到当前账号，诊断页不会混入其他账号的线程统计。新增跨账号回归 1 项，相关遥测测试共 6 项通过；随后生产构建退出码为 0（27.39 秒）。

## 最终回归收口（2026-09-08）

移除会与横幅测试并行争用全局 jsdom `localStorage` 的临时集市测试后，全量前端 Vitest 稳定通过：501 个测试文件、3529 项通过、2 项跳过；相关市场横幅、actor 存储和流式遥测定向回归 4 个文件、10 项通过。

当前源码再次执行 `pnpm build`，Vite 生产构建退出码为 0（主应用构建约 25.38 秒）。

## 偏好升级迁移与最近工作目录统一（2026-09-08）

活动 Agent、Echo 昵称和模式/审计强度偏好现在会把旧设备级键一次性迁移到当前账号；匿名会话仍可读取旧值但不会抢先认领。最近工作目录新增共享读写层，桌面、工作台侧栏、工作目录选择器和实时会话使用同一 actor-scoped 列表，避免升级后入口各自显示不同历史目录。

新增迁移与隔离回归后，相关 6 个测试文件、43 项通过；TypeScript、ESLint 和 Prettier 通过。当前源码 `pnpm build` 退出码为 0（主应用构建约 24.19 秒）。

全量回归复核：501 个 Vitest 文件、3534 项通过、2 项跳过（3536 项），证明偏好迁移与共享最近目录层未引入回归。
2026-09-08 本地数据库离线预览与资源身份再统一：设备文件服务返回的 `appliance-file:v1` 现在可用于文本、图片、视频和 PDF 预览，文本读取与媒体 object URL 均经过现有认证请求和生命周期回收；Storage sibling 的 `storage-file:v1` 读取端点继续保持待配套状态，不凭空假设外部路由。Storage API 与资源预览回归 **16 项通过**，TypeScript、ESLint 和 Prettier 通过。
2026-09-08 对话与本地数据库的双向定位链路补齐：修复 realtime 发送函数遗漏 `contextFiles` 的问题，新增服务端 `UserMessageItem.contextFiles` 持久字段，历史转换和前端消息气泡均保留资源身份并提供“在本地数据库中定位”。资源引用经过现有边界清洗，不承担授权；前端相关回归 **142 项通过**，后端 realtime 映射回归 **12 项通过**。
2026-09-08 继续收口统一资源身份：上下文引用现在允许仅携带 `resourceId` 而不暴露路径，`turn_session`、realtime 输入清洗、历史回放和聊天引用卡片均保留该身份；设备 `appliance-file:v1` 可直接按资源身份生成定位地址，外部 `storage-file:v1` 在缺少配套端点时只保留引用信息，不伪造本地跳转。设备文件服务新增按资源 ID 解析根相对位置的只读端点，前端先解析目录再高亮目标。新增资源身份分支回归后，设备文件后端 **60 项**、前端资源客户端 **17 项**通过，TypeScript、Prettier、Ruff 及后端 realtime/model metadata 回归通过。
本轮完整验证：前端单元回归 **501 个测试文件、3541 项通过、2 项跳过**，脚本级回归 **18 项通过**；后端全量回归 **2159 项通过、91 项按 Windows/POSIX 环境跳过**；Electron 宿主回归通过（Linux 专属通知和原生 IPC 烟测按既有规则跳过）；TypeScript、ESLint、Prettier、Ruff 与 Vite 生产构建通过。

## 工作台子应用拆包收口（2026-09-08）

工作台子应用此前使用独立 Vite 配置，未复用主应用的公共依赖拆分策略，设计工作台把编辑器、图表和公共库集中到入口包。现在 `build-workbenches.mjs` 复用统一 `manualChunks` 规则，将 React、Radix、TanStack Query、Markdown/KaTeX、CodeMirror、语言包与业务重资源拆成可缓存块，并把工作台入口警戒线设为 900kB。设计工作台入口由此前约 1.78MB 降到 **897.99kB**；Mermaid、Cynefin、C/C++ 等按需资源保持独立延迟块，首次进入不再全部下载。

`pnpm build:workbenches` 与完整 `pnpm build` 均退出码为 0。完整回归保持前端 **501 个 Vitest 文件、3541 项通过、2 项跳过**，Node 脚本 **20 项通过**；后端 **2159 项通过、91 项跳过**，Electron 回归通过，TypeScript、ESLint、Prettier、Ruff 与 `git diff --check` 通过。

## Storage 网关路由边界收口（2026-09-08）

同源 Storage 网关现在只转发已审计的 manifest、policy、models、sources、browse、search、index jobs、albums、apps、files 和 answer 路由；模型、来源、任务、应用动作及二进制内容分别限制到已声明的方法和资源段。未知的 `v1` 管理路径在读取请求体、注入 Storage token 和连接 sibling 之前直接返回 404，避免未来上游新增接口被透明暴露。新增代理拒绝回归，Storage 网关定向测试 **7 项通过**；与隐私策略、Agent 检索、服务状态联动回归 **65 项通过**，授权边界专项再通过 **26 项**，Ruff 通过。

## Electron 桌面原生 Agent 入口统一（2026-09-08）

Electron 桌面后端默认接入 `appliance.native_extension`，由同一个 Agent 进程提供原生任务投影、工作台能力和本地文件读取回退；不再误启用 NAS appliance 的私有状态锁与 Docker 控制面。扩展设为必需项，避免桌面看似启动成功但 `/api/appliance/tasks` 缺失。桌面文件回退复用主机 JWT，未装配审计/审批时写操作明确不可用。桌面冒烟覆盖自定义 `echo-app://` 协议、健康检查、本地登录、插件目录、任务投影和 realtime WebSocket，**3 项通过**；原生扩展与 Storage 定向回归 **17 项通过**。
桌面缺少 Storage 路由时，browse 的路由级 404 已降级到同源文件读取回退；Storage 客户端回归 **13 项通过**。

`pnpm build` 收口复核：Vite 生产构建退出码为 0（28.67 秒），拆分产物完整生成。

桌面内嵌 Storage 兼容回退已补齐：外部 sibling 连接失败时，经过同一登录边界的 `/api/storage/v1` 提供本机根目录 manifest、来源、浏览、文件资产、内容读取和文件名/文本搜索；外部服务可用时仍优先走外部服务，回退不开放 NAS 管理写操作。

本地 Storage 兼容回退新增代理回归 **8 项通过**；Storage/隐私/技能/状态/原生扩展联合回归 **76 项通过**。

资源定位改动后的当前源码再次执行 `pnpm build`，Vite 生产构建退出码为 0（22.72 秒）。

内嵌 Storage 资源引用现在可在本地数据库中按资源 ID 解码到根相对目录；仅当 manifest 标明 `embedded` 时启用，外部 Storage 资源不会被误导航到本机。

Agent 的 `search_documents` 现在与桌面 UI 共用内嵌 provider：外部 Storage 不可用时使用同一桌面文件根目录、同一 `storage-file:v1` 资源身份执行有界文件名/文本搜索，并明确标注未连接外部索引；外部 Storage 可用时仍优先使用其索引。

共享本地 provider 接入 Agent 后，Storage/隐私/技能/状态/原生扩展联合回归更新为 **77 项通过**；Agent 降级搜索另有专门回归覆盖。

原生扩展、Storage 网关和 Agent 搜索现在共享同一桌面 provider/FileManager 实例，根目录解析与资源 ID 生成不再各自维护。原生扩展定向回归、Storage 代理和 Agent 搜索回归共 **27 项通过**。

本轮资源定位再验证：内嵌 Storage 资源在数据库页按资源 ID进入对应目录，外部路径不会被本机解析；前端 Storage 与数据库定位回归 **17 项通过**，TypeScript 与 ESLint 通过。

### 2026-09-08 资源身份再统一
原生文件列表返回的 `appliance-file:v1` 引用现在由前端资源解析层转换为同一根目录下的 `storage-file:v1` 身份；Storage 浏览、Agent 搜索和本地数据库定位使用同一选择键，避免从系统文件入口进入后产生第二份资源引用。新增合法 appliance 身份解析与转换回归；Storage API、本地数据库定位共 18 项通过，TypeScript、ESLint、Prettier 通过。

### 2026-09-08 原生桌面补齐 Storage 同源接口
原生桌面扩展现在在检测到未挂载的 Storage 代理时，自动挂载同一 `/api/storage/v1/*` 合同；完整 Agent 已有该路由时直接复用，避免重复注册。没有外部 `echo-storage` 时，系统应用仍能通过同一代理读取嵌入式 manifest、文件列表、搜索和内容，继续使用共享 `storage-file:v1` 身份。原生扩展、Storage 代理、隐私边界和 Agent 搜索回归 77 项通过，Ruff 与 Python 编译检查通过。

### 2026-09-08 原生扩展启动依赖收口
Storage 代理、文件管理器和桌面 Provider 现在仅在 `ECHO_DESKTOP=1` 时延迟导入；普通原生 Agent 只加载 Agent UI 与任务投影，不再为未启用的桌面文件能力初始化 Storage 依赖。桌面代理仍保持“已有路由复用、缺失时补挂”的行为；原生 Agent、Storage 代理和 Agent 搜索定向回归 27 项通过。

### 2026-09-08 目录 API 边界统一资源键
原生目录 fallback 不再把 `appliance-file:v1` 原样交给页面；`listNASDirectory` 返回时就转换为 `storage-file:v1`，后续页面、搜索选择和 Agent 引用直接消费同一键。新增 API 边界回归，Storage API 与本地数据库定位回归 19 项通过。

### 2026-09-08 Storage 搜索逻辑去重
Storage 代理 fallback 的本地搜索已改为直接调用共享 `desktop_search` Provider，删除代理内重复的文件遍历、文本截断、排序和引用组装。原生扩展、Storage 代理、Agent 搜索回归 27 项通过，Ruff 与编译检查通过。

### 2026-09-08 Electron 桌面 smoke 收口
原生扩展补挂同源 Storage 后，Electron smoke 重新验证通过：窗口/预加载桥/工作台、后台认证与插件/任务投影/本地文件接口、浏览器下载共 3 项通过（17.6 秒）。

### 2026-09-08 模型状态栏补齐执行连接反馈
- 桌面顶栏和工作台状态栏继续共用 `SystemModelStatus`、本地模型选择和服务端 profile；应用窗口仍由桌面宿主负责，避免重复渲染。
- 使用 profile 的 `execution_available`、`compatible` 和不可用原因，在同一入口显示检查中、已连接、模型不可用、连接异常或状态未知，并同步到按钮无障碍名称。
- `system-model-status.test.tsx` 增加不可执行路由的状态反馈回归；类型检查、ESLint、Vitest（23 tests）和生产构建通过。

### 2026-09-08 应用入口改用稳定身份
- 桌面打开、桌面快捷方式和本地数据库独立窗口统一通过工作台注册表的 `local-database` 身份判断，不再依赖 `/workspace/storage` 路径字符串。
- 本地数据库的侧栏模块 ID 也改为引用同一稳定常量，路由别名或查询参数变化不会造成桌面与工作台入口失配。
- 工作台注册表回归、应用组件回归、ESLint、Prettier 与 TypeScript 检查通过。

### 2026-09-08 桌面工作台入口改为按身份解析
- 桌面启动引导不再用 `DESKTOP_APPS[0]` 代表工作台，改按 `/workspace/realtime/new` 解析固定工作台入口。
- 调整桌面应用顺序不会再把任务启动、结果回到工作台或 Dock 行为误指向其他应用。
- TypeScript、ESLint、Prettier 与生产构建通过。

### 2026-09-08 侧栏模块 ID 继续单一来源
- 本地数据库的注册表 ID 同时被模块目录、桌面路由、独立窗口和工作台侧栏图标引用；移除页面级重复字符串。
- 桌面按安装可用性展示应用，工作台按用户侧栏偏好展示模块，两者状态边界保持分离，不会因取消侧栏入口而卸载或隐藏系统应用。
- 模块目录与侧栏相关检查通过。

### 2026-09-08 运行态验证双形态链路
- 在当前 `http://localhost:3000/#/desktop` 运行实例中确认系统顶栏显示“自动选择（已连接）”。
- 从 Dock 打开本地数据库独立窗口，再切换“添加到工作台侧边栏”，工作台侧栏即时出现本地数据库入口；应用窗口与工作台复用同一内容和模块状态。
- 该验证覆盖桌面入口、独立窗口、工作台入口和侧栏状态同步；外置 `echo-storage` 编辑/差异协议仍未在当前工作区提供。

### 2026-09-08 统一工作区补齐本地文本编辑
- 嵌入式 Storage 的文本预览现在可以直接进入编辑态，保存复用 `/api/appliance/files/upload` 的鉴权、配额、审计和覆盖写入边界；资源 ID 必须先解析为受控的 `storage-file:v1` / `appliance-file:v1` 身份，不能用任意路径写入。
- 上传客户端新增 `overwrite` 选项，默认仍为关闭；断点续传的会话键也区分覆盖与新建，避免恢复到错误会话。
- 外置 Storage manifest 不显示编辑入口，继续保持预览与引用能力。文本编辑与覆盖上传回归通过，TypeScript、ESLint、Vitest 和生产构建通过。

### 2026-09-08 桌面引导移除本地数据库路径判断
- 桌面启动引导的“打开本地数据库”现在直接按注册表身份寻找应用，和 Dock、独立窗口、工作台侧栏使用同一判断；调整路由别名时不会遗漏引导入口。

### 2026-09-08 文本编辑增加保存前差异查看
- 嵌入式本地文本进入编辑态后可切换到行级差异视图，先确认新增和删除内容再保存；差异计算限定在已读取的受限预览内，不额外读取外部路径。
- 新增差异格式化回归；本地编辑、覆盖上传和桌面入口回归通过。

### 2026-09-08 离线空目录状态纠正
- Storage manifest 不可用时，数据库页不再显示“目录已读取”，改为明确提示服务未连接、当前内容可能不完整；真实目录错误仍保留错误提示。

### 2026-09-08 系统应用侧栏偏好改为账户级
- 本地数据库的“添加到工作台侧边栏”不再写入当前 Agent 的 persona 覆盖，而是写入账户级模块偏好；从独立窗口切换 Agent 后仍保留同一入口。
- 其他可选工作台模块继续使用 persona 覆盖，系统应用和 Agent 专属工具的配置边界保持清晰。

### 2026-09-08 清理旧 persona 覆盖并完成运行态复核
- 新增全局模块开关会清理同一应用遗留的 persona 覆盖，避免旧数据继续隐藏系统应用。
- 在当前运行实例中从独立窗口重新固定后，两个工作台窗口都出现“本地数据库”侧栏链接；跨 Agent 回归、类型检查、ESLint 和生产构建通过。

### 2026-09-08 独立 Storage 编辑依赖已补实现
- 仓库新增 `runtime.storage.service`，可以作为独立进程提供文本读写和差异协议；无需拿到旧外置服务的源码或安装包。
- 前端改为能力协商和完整文本编辑，废弃从截断预览生成覆盖上传的旧链路。版本冲突、断线保留草稿，不跨数据源回退写入。
- 后端相关回归 79 项通过，包含独立进程真实 HTTP 保存。当前运行服务尚未替换；部署配置及共享账号限制见 [Storage 文本协议](storage-text-protocol.md)。
