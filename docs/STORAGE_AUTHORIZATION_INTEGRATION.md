# 外部 Storage 资源授权接入要求

状态：**未实施；尚未取得外部服务源码或可运行发行包；不是已证实的越权漏洞。**

本记录来自本轮对当前工作区、启动器、已安装包和前端消费者的只读核查。可以确认本仓代理没有传递可信的成员资源范围，但不能由此推断缺席的 echo-storage 服务已经发生越权，也不能宣称它已完成多用户隔离。用户提供的服务位置仍待确认。

本文没有定义任何已存在的身份 header、签名格式或 manifest 授权 schema。下文是双方必须落实和验证的行为要求；具体协议须取得服务实现后共同确定。

## 1. 实际定位结果与审查边界

已检查启动器明确使用的位置，没有搜索用户个人数据，也没有读取访问令牌或文档内容：

| 检查 | 本轮结果 |
| --- | --- |
| `appliance.storage_spawner._storage_executable()` | 返回 `None`。 |
| `runtime.sensing.gateway.storage_supervisor.resolve_storage_command()` | 返回 `None`。 |
| PATH 中的 `echo-storage`、兼容名 `octopus-storage` | 均未定位到。 |
| 当前项目 `.venv/Scripts/echo-storage.exe`、`octopus-storage.exe` | 均不存在。 |
| 当前 Python 的 `echo_storage`、`storage` 模块及已安装 distribution | 模块不可发现，未发现名称包含 Storage 的 distribution。此结论仅覆盖当前 Python 环境。 |
| 启动器对应的同级 `C:\飞牛os\echo-storage`、`C:\飞牛os\octopus-storage` | 均不存在。 |
| `ECHO_STORAGE_CMD`、`ECHO_STORAGE_URL` | 本轮进程环境中未配置。未读取任何 token 配置。 |
| 默认本地 TCP 8767 监听 | 本轮检查时没有监听；不代表其他位置不存在服务。 |

定位依据：[appliance 可执行解析](C:/飞牛os/octopus-os/appliance/storage_spawner.py:48)、[runtime 命令解析](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_supervisor.py:74)。

现有启动器持有全局子进程和服务地址。appliance 启动参数是 `serve --host --port`，可附加一个 `--data-dir`；runtime 启动器同样持有单个 `_proc`。没有看到按 actor 创建独立实例的装配代码，不能假设服务通过“一人一个进程”隔离。[appliance 启动参数](C:/飞牛os/octopus-os/appliance/storage_spawner.py:162)、[runtime 启动](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_supervisor.py:129)

## 2. 当前真实调用链

浏览器经 `/api/storage/v1/*` 进入 Agent 网关，由网关调用独立 Storage 服务。该代理确实被装配：[路由挂载](C:/飞牛os/octopus-os/runtime/platform/ui/_app_collab.py:62)。

代理的 `_auth` 调用 `_resolve_actor` 后未保留其返回身份；转发仅保留固定请求 header 白名单，再注入共享 `_storage_token`。query 和 body 原样传给上游，未套设备文件服务的 `DataAccessScope`。[身份校验](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_proxy_router.py:76)、[上游 header](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_proxy_router.py:45)、[请求转发](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_proxy_router.py:114)

Agent 的 `search_documents` 是另一消费者：它同样使用共享服务 token，`source_ids` 从工具参数转换后传给 `/v1/search`。工具参数中的来源选择不能被当作服务端授予的访问权限。[共享请求封装](C:/飞牛os/octopus-os/runtime/execution/suckers/storage_skills.py:60)、[检索参数](C:/飞牛os/octopus-os/runtime/execution/suckers/storage_skills.py:125)

当前设备文件和照片服务则显式绑定 `OmvDataAccessPolicy`；这一结论不能自动延伸到外部 Storage。[设备权限装配](C:/飞牛os/octopus-os/appliance/extension.py:325)、[照片服务权限传入](C:/飞牛os/octopus-os/appliance/extension.py:395)

`NASManifest` 的本仓客户端类型只有 service、version、role 和普通 capabilities 字符串数组，没有可据以验证资源授权语义的合同。工作台读取 manifest、policy、sources 判断知识服务可用，媒体和应用按独立结果处理；它没有证明上游逐资源授权。[manifest 类型](C:/飞牛os/octopus-os/frontend/src/core/storage/api.ts:6)、[工作台加载](C:/飞牛os/octopus-os/frontend/src/app/workspace/storage/page.tsx:434)

抽查的代理测试使用 `httpx.MockTransport`，默认未开启身份认证，验证了共享 token 注入、查询透传、Range、体积限制和不可用响应。这些测试不构成真实 Storage 多用户隔离证据。[测试装配](C:/飞牛os/octopus-os/tests/test_storage_proxy_router.py:19)

## 3. 待接入的 API 类别

下表列的是本仓消费者与代理所需的授权合同。未取得上游实现前，不能认定每个上游端点已经存在或存在漏洞。

| API 类别 | 当前消费者/代理能力 | 必须在真实服务中确认并实现的授权 |
| --- | --- | --- |
| 浏览、来源、文件列表、相册 | `GET /v1/browse?path=...`、sources、files、albums。 | 限定可浏览的目录和资源；路径、数量、相册名称和封面都属于可泄露的信息，必须使用同一授权范围。 |
| 文件内容与图标 | 通过资源 ID 请求 content/icon；代理转发 GET、HEAD 和 Range。 | 服务端解析资源 ID 后按真实资源授权；不能只过滤列表。内容读取还须防路径、链接或文件替换绕过。未授权对象的响应不能泄露其存在性或大小。 |
| 文档检索、问答 | `/v1/search`、`/v1/answer`，浏览器可发送空 `source_ids`；Agent 可选择来源。 | 授权候选必须在排序和上下文生成之前确定。客户端来源选择只能缩小授权范围；空选择只能表示“我的全部可见来源”。命中、引用、回答片段及云端导出均不得使用非授权内容。 |
| 来源新增、删除 | 传入 host path 或 source ID。 | 重新校验目录归属、可读树和来源所有者；确认“删除来源”究竟删除索引还是原件，不在未确认语义下代替用户操作。 |
| 索引任务及任务状态 | 创建索引 job，按 job ID 查询。 | 绑定任务 owner、来源和授权依据；执行、重试、状态及取消都要检查权限。全局任务计数和消息不得成为旁路。 |
| 全局 policy、模型操作 | 工作台实际调用 policy 修改、模型下载；客户端另有模型 enable/disable 导出方法。 | 单独区分设备管理权限与成员资源权限，不能让普通读取凭据改变全局隐私/云端导出策略或资源配置。导出方法存在不等于 UI 已有相应按钮。 |
| 应用 open/reveal | 工作台可调用宿主应用打开、定位。 | 这是宿主动作，须明确执行主体和管理/本地会话权限，不能按普通文档读取处理。 |
| 未知 `v1` 路径和方法 | 当前代理允许 GET、HEAD、POST、PUT、PATCH、DELETE。 | 按审查过的路由、方法和参数建立允许清单；不能自动代理未来新增的管理接口。 |

接口证据：[来源、浏览与索引](C:/飞牛os/octopus-os/frontend/src/core/storage/api.ts:235)、[搜索](C:/飞牛os/octopus-os/frontend/src/core/storage/api.ts:269)、[相册、应用与内容](C:/飞牛os/octopus-os/frontend/src/core/storage/api.ts:304)、[问答](C:/飞牛os/octopus-os/frontend/src/core/storage/api.ts:357)、[全部代理方法](C:/飞牛os/octopus-os/runtime/sensing/gateway/storage_proxy_router.py:152)。

Storage 启动入口已经有 operator 检查，而且明确不把 token 返回浏览器；需要保留这些现有行为，而不是另建一个可绕过的启动入口。[现有启动授权](C:/飞牛os/octopus-os/runtime/sensing/gateway/local_brain_router.py:77)

## 4. 双端必须共同满足的合同

1. **同一个可信身份和范围来源。** 浏览器与 Agent 都从服务器认证上下文取得 actor、tenant 和部署实例身份。设备部署复用现有目录权限策略；通用 runtime 通过部署方明确注入授权解析器。模型参数、浏览器 header 和 body 不得提供可信身份、角色或授权根。

2. **来源权限不能替代文件权限。** 一个 source 可能覆盖多人目录。服务必须能把索引项映射到受管理来源、授权根和根内路径，保留显式 deny 与更具体路径规则的优先级。不能只用 source ID 列表过滤一个覆盖整盘的来源。

3. **授权在上游执行。** 服务间认证仍然需要，但共享服务 token 不能表达成员资源范围。如果选用短时签名凭据，双方须共同确定签名、受众、实例、有效期、请求绑定和权限版本语义；Storage 必须验证并在查询/读取/执行时应用。仅添加一个身份 header、manifest 声明或回显字段不算完成。

4. **检索、聚合、文件读取和后台任务一致。** 授权必须先于 top-k、相册聚合、问答上下文和文件打开。后台任务保存明确归属，并在实际访问时重新核实权限。不能在已经从全库选出命中、产生回答或完成副作用后才过滤响应。

5. **撤权与重放使用当前状态。** 查询开始及结果发布前核实适用权限；job 执行、下载打开和重试使用当前授权。Agent 查询不能重放撤权前的缓存。长下载如何处理开始后的撤权须明确，不能承诺撤回已经交付的字节。

6. **管理动作与成员功能分开。** 明确来源管理、索引、全局配置和宿主动作各自需要的权限，以及适用的现有审批机制。普通成员应能浏览、检索和取得自己及共享资源；不能把“全部禁用成员”当作最终交付。

7. **能力协商反映真实服务行为。** 取得具体版本后，共同确定能验证资源授权语义的协议及兼容策略。没有相关支持时，明确返回授权接入未就绪；不能绕回共享 token 的无范围请求，也不能误报为模型未下载或要求反复启动服务。

## 5. 接入顺序及兼容期

- 取得真实服务源码或可运行发行包，记录版本/提交、执行入口、数据目录、认证实现、路由和资源映射。当前尚缺这一步。
- 对照上表确认双方接口，复用现有身份、目录权限、任务和审批能力；不新建重复索引引擎或第二套用户系统。
- 同时修改代理、Agent 查询适配及上游授权执行；前端只消费服务器确认的能力和结果。
- 在真实服务验收通过前，成员继续使用现有设备文件浏览、文件名搜索和设备相册。外部语义检索若不具备成员授权支持，应单独标记未完成，不应让整个设备数据功能显示不可用。
- 若最终选择每用户独立实例，也必须限制实例可索引的目录、宿主文件权限、凭据和数据目录，并处理共享资源与撤权。实例数量本身不是隔离证明，此方案也尚未实施。

## 6. 真实服务验收

使用完全合成的数据目录和真实 Storage 进程/数据库。至少准备 A 私有、B 私有、A/B 共享，以及共享目录内显式 deny 子目录；加入同名文件、跨来源同名资源和可观测但不应授权的索引项。不得用只返回预设成功结果的 MockTransport 代替这组验收。

| 验收场景 | 必须得到的结果 |
| --- | --- |
| A、B、共享与 deny 基本访问 | 双方仅看见自己的和允许共享的内容；deny 在文件、目录、检索、相册计数/封面和引用中一致生效。 |
| 全库高排名项都属于另一用户 | 本用户可见的低排名项仍能被检索；不能先全库 top-k 再丢弃。 |
| 伪造 actor、tenant、来源、资源或 job ID | 身份不可被参数覆盖；直接请求内容、HEAD/Range、任务状态、相册封面均不绕过授权。 |
| 同一 source 内混合权限 | source 级授权不导致拒绝子目录或其他用户文件被读出。 |
| 搜索、下载打开或后台索引前撤权 | 当前请求/任务按照已约定的撤权时点拒绝或停止；无旧权限缓存重放。 |
| 空来源列表、未知来源、重复参数 | 不能退回全局范围；参数解析和授权结果唯一明确。 |
| 普通成员修改全局策略、管理模型或触发宿主动作 | 在上游执行副作用前拒绝；数据库和宿主状态保持不变。成员正常读取不受影响。 |
| 目录链接、挂载或原件替换 | 索引与内容读取不能借路径变化跨出授权根。 |
| 身份过期、权限版本变化、跨实例重放 | 请求被拒绝或要求重新授权，不能降级成无范围服务调用。 |
| 重启、任务重试和前端账户切换 | 归属、共享授权和拒绝规则仍成立；缓存、任务计数及 object URL 不把前一账户内容展示给下一账户。 |

证据应记录实际服务版本、源文件摘要、合成数据布局、真实请求和主体、返回资源集合、数据库/原件前后校验，以及拒绝发生前未执行的副作用。不能把“代理返回 403”单独当作未读取非授权内容的证据。

完成标准是：**真实 Storage 对上述资源动作执行授权，浏览器和 Agent 使用同一合同，A/B/共享/deny 全链测试通过，成员仍能使用其获授权的功能。** 在此前，本事项保持“外部接入待完成”。
