# 真机验证清单(P1/P2 在 Docker/NAS 上的闭环)

> 目的:本机(开发用 Mac)无 Docker,以下事项只能在**有 Docker 的机器或真实 NAS**
> 上验证;在此之前的所有功能均已用 pytest + 浏览器预览验证过(见各 commit)。
> 跑完把结果按"反馈给开发"一节回填即可。

适用环境:任意装了 Docker 的 Linux 主机 / 群晖 / 飞牛 / CasaOS / ZimaOS / 绿联,
或 Mac/Windows 上的 Docker Desktop。

---

## 0. 准备（Echo 单仓）

Agent runtime、设备能力和工作台都在 Echo 仓库中。构建前从同一个源码身份生成
**wheel + resources + Codex + bundle manifest**：

```bash
git clone https://github.com/dengdenghua/echo-os.git
cd echo-os

# ⓪ 发布/远端重型门必须先得到 ready:true；--offline 永远不能批准发布
python3 deploy/appliance/delivery_source_preflight.py \
  > echo-delivery-source-preflight.json

# ① 一次生成并校验四项构建输入
./deploy/appliance/prepare-agent-bundle.sh
# 开发中的脏工作树只用于 QA：ECHO_AGENT_ALLOW_DIRTY=1 ./.../prepare-agent-bundle.sh

# ② 可选：管理员密码与 NAS 存储目录
export ECHO_ADMIN_PASSWORD=改成你的密码
export NAS_STORAGE=/path/to/your/nas/share    # 群晖如 /volume1/share
export PUID=$(id -u)                          # 或 NAS 共享目录所属用户的数字 ID
export PGID=$(id -g)
# 仅反向代理/FQDN 部署需要；直接私网 IP、localhost、*.local、单标签 LAN 名免配
export ECHO_APPLIANCE_TRUSTED_HOSTS=echo.home.example
export ECHO_APPLIANCE_TRUSTED_ORIGINS=https://echo.home.example
export ECHO_TRUSTED_PROXY_IPS=172.20.0.10  # 反代与 Echo 直连时看到的源 IP/CIDR，禁止 *
```

四条发布证据齐全后，还必须在 `os-main` 执行 `delivery-release-candidate.yml`（完整参数见
`deploy/appliance/README.md`）。它会核对四个成功 run 并在线验证十份 GitHub OIDC provenance，
再复验 GPG、OMV 字节和 appliance 不可变摘要。其结果里的 `ciReleaseCandidateReady:true` 只说明
raw、A/B、真实 OMV x86 与 appliance 标签发布属于同一 OS/Agent 来源；在本清单的物理项全部回填
前，`nasProductDeliveryReady` 必须保持 `false`，不得手改索引代替验收。

下载候选 artifact 后先联网执行
`gh attestation verify echo-delivery-release-candidate.sha256 -R <owner>/<repository>`，再在隔离环境执行
`./verify-release-candidate-bundle.sh`。必须得到 `ECHO_DELIVERY_CANDIDATE_OFFLINE_OK`；缺少原始
raw/A-B/OMV/appliance 输入、public keyring 审计器、出现额外文件或回放索引不同均不得进入真机门。

离线回放后立即执行 `physical_acceptance_capture.py plan`，在 `physical-evidence/` 外生成
`echo-physical-acceptance-lab-plan.json`。核对计划中的候选 indexId、OS/Agent SHA、标签、六门顺序、
架构与最低设备数；保存 `planId` 到实验记录。相同候选的计划必须逐字节一致，且两个 ready 字段都
必须为 false；顶层 `deliveryRequirements` 必须精确为 G1–G6，每门的映射及逐项检查必须和
`deploy/appliance/README.md` 一致。开始、换班交接和最终签名前都执行
`physical_acceptance_capture.py verify-plan`；自改
内容后重算 planId 或拿另一候选的计划都会失败。该计划只防止现场串候选或漏门，不是通过证据，
不能签名后冒充实验结果。

六项真机实验的日志和脱敏附件必须按 `deploy/appliance/README.md` 的固定目录保存，使用
`physical_acceptance_capture.py profile/result/marker/build` 绑定同一个候选，再由验收负责人用专用 OpenPGP
key 分别签名。每门先用 `profile` 生成固定只读 `hardware-profile.json`；它只允许当前 gate、门对应
的非唯一 profile class、架构、设备数和已脱敏声明，摘要与内容都必须和 manifest 一致，不能手写
厂商、型号、序列号或扩展字段。画像模板本身不证明真机实验成功。每门实验全部完成后必须按计划
逐项提交全部 `--pass-check`，生成固定只读 `gate-result.json`；缺项、重复项、其他门检查项、任一
`false` 或额外字段都不能进入 manifest。断电/状态恢复门还必须证明五个运维 unit 的真实安装、
安装/启用失败后的完整回滚、备份与审计两个 timer 实际触发、两类挂载分别丢失时 fail-closed、
移除失败后恢复原状态、受管移除后没有 unit/timer 残留且凭据与数据保留；Debian parser CI 不能
代替这些结果。G5 必须再用 `physical_acceptance_capture.py operations-result` 生成只读
`operations-systemd-lifecycle.json`：九项 systemd 生命周期检查各绑定同一 gate 目录中实际日志或
附件的名称、大小和 SHA-256，并把 JSON 与所有被引用文件一并传给 `build --artifact`。缺引用、
目录外/链接文件、错摘要、其他 gate 检查或只提交一组 `true` 都必须失败。优先使用运维包内
`operations_systemd_lab.py plan/run` 的八个顺序阶段生成日志，再用
`operations-result --candidate-index ... --lab-plan ... --lab-directory ...` 消费；plan 必须使用同一
候选索引，并复核当前解包运维包及实验脚本字节，不能把旧候选日志挂到新候选。随后必须运行
`power_state_recovery_lab.py seed/plan/run/verify`：先用候选与当前双容器摘要绑定的精确确认语，把新
候选包的可变 release 选择安全对齐到旧 digest；再在候选 digest 选择已 fsync、Compose 尚未执行的固定
边界人工断电，恢复供电后证明 boot ID 改变、前一 boot 没有正常关机标记、启动恢复 service 自动
回到旧 digest；再完成正常 digest 升级、Compose 故障回滚、无 volume 删除的受管卸载/重装，以及
外部加密备份的只读预检与确认恢复。七份固定日志必须通过 `power-result` 生成
`power-state-lifecycle.json`，并与 `operations-systemd-lifecycle.json` 一起进入 G5 manifest；任一份
都不能代替另一份。必须在专用
Debian 13 + OMV 8 实验机和具备其他副本的
测试介质上执行，禁止
在家庭生产 NAS 上做卸载/挂载故障注入。裸机恢复门必须证明从
异机备份恢复，并使用 `bare_metal_recovery_lab.py` 的 source-backup 加七个跨启动阶段生成固定日志，
再由 `bare-metal-result` 生成 `bare-metal-recovery-lifecycle.json`；设备状态、Agent 和 NAS 恢复金丝雀
分别固定为 1 MiB、1 MiB 和 1 GiB。候选索引、运维包、安装包、恢复密钥、备份、私有计划目录与
公开证据目录必须分开但均位于已验证异机挂载之下，避免整盘安装把后续输入或恢复状态本身清除；
禁止把私有计划或恢复密钥放入证据目录。G6 恢复的是
appliance 管理员认证、会话撤销边界与审计签名身份；替换机本地管理员必须重新创建，严禁克隆原机
`/etc/shadow`、machine-id 或磁盘身份。x86/ARM 门必须证明文件/Agent/1 GiB/连续运行与
普通断电恢复；x86 还必须证明 TLS、Secure cookie、Origin/WebSocket、会话、审批、审计、docker.sock
隔离与第三方 CSP；存储门必须覆盖拔盘、只读、写满、重启、回收站和阵列重建；协议门必须覆盖
Windows/macOS/Linux SMB、macOS/Linux NFS、用户/ACL、跨协议配额和大文件。开始/结束时间必须是包含秒的
规范 UTC；x86/ARM 冷启动门至少持续 24 小时，其他门至少 1 秒，全部不超过 7 天。不要把序列号、
WWN、by-id、密码或 token 写入附件；二进制附件必须
人工脱敏；六门必须分别使用六个不同的 UUIDv4 `labRunId`。最终只有
`physical_acceptance.py` 对六门、同一 public keyring 和同一完整 signer fingerprint 全部复验后生成的
`echo-nas-product-delivery-release.json` 且 `deliveryRequirementsVerified` 精确为 G1–G6，才能把
`nasProductDeliveryReady` 置为 `true`；清单勾选、
截图或未签名日志均不能代替该证据包。

G2 存储门必须使用运维包内的 `storage_recovery_lab.py plan/run`。只允许在 Debian 13 + OMV 8 的
专用双盘 RAID1 实验机运行；目标卷必须是 4–64 GiB、除 root 所有 mode-0444 授权标记外为空、可丢弃
且不含用户数据。八个阶段必须按 plan 顺序分别确认和执行，人工拔下/接回的必须是计划中固定的
牺牲盘。完成后使用 `physical_acceptance_capture.py storage-result --candidate-index ... --lab-plan ...
--lab-directory ...` 生成只读 `storage-recovery-lifecycle.json`。采集器会校验八份固定日志、64 MiB
种子摘要、SMART/降级/只读/ENOSPC/同盘重连/重建/重启，以及 Echo API 的 1 GiB 回收站删除—恢复—
再下载摘要；九项 G2 结论逐项绑定实际日志字节。缺少重连日志、跨候选计划、设备身份漂移、手写
`true`、非 ENOSPC 写失败或仅做 API 冒烟都不能进入签名 manifest。

G3 协议门必须使用候选运维包内的 `protocol_interoperability_lab.py`。计划绑定候选、运维包、服务器
名称和专用共享 UUID；每个 SMB/NFS 挂载根在探针开始前只能含同一候选授权标记。分别在真实 Windows
SMB、macOS SMB/NFS、Linux SMB/NFS 客户端完成 8 MiB 写入、读回、重命名和删除；再由 Linux 专用
身份完成 SMB/NFS 允许/拒绝权限、同一账户跨协议硬配额拒绝和 SMB 写入—NFS 读回/删除的 1 GiB
非稀疏文件。收齐八份 mode-0444 日志后执行 `verify --candidate-index ... --bundle-root ...`，重新绑定
候选索引、运维包清单和执行器字节并生成 `protocol-interoperability-lifecycle.json`。容器内回环挂载、
伪造系统名、只做服务端配置、已有用户文件的共享或手写八项 `true` 都不能满足 G3。

- [ ] 使用完整候选 artifact、六门目录和 public-only acceptance keyring 执行
      `product_delivery_bundle.py build`，不能只给它一份候选索引
- [ ] 在另一台安装 `python3`/`gpgv`/`sha256sum` 的隔离 Linux 机执行包内
      `python3 tools/product_delivery_bundle.py verify "$PWD"`
- [ ] 保存输出的 `bundleId`、`productReportId`、候选 `indexId` 与完整 signer fingerprint；四者
      必须和验收记录一致，最终目录不得出现验收私钥或任何额外文件

> prepare 脚本要在能跑 Node 20/pnpm + Python 3.11+ 和固定 `uv 0.11.25` 的机器上跑。若 NAS 本身不便装
> 构建工具，可在开发机跑完后，把 `agent-dist/`、`agent-resources/`、
> `agent-codex/` 和 `agent-bundle.json` 连同 OS 仓库一起拷到 NAS 再 compose。

---

## 0b. 正式版本与供应链证据

正式候选必须来自 `echo-appliance-v<semver>` 标签触发的 `Echo appliance release`，不能把本地
镜像、普通分支构建或 `latest` 当成发行版。先下载该次 run 的 `echo-appliance-<标签>` 证据包。

- [ ] `sha256sum -c echo-appliance-release.json.sha256` 同时验证发布清单、`echo-release.env`、
      OCI 索引原文、两份平台 SPDX、构建/运行依赖锁和锁元数据，以及运维包、其外层校验和、
      SPDX 与验证器，全部成功
- [ ] 清单中的 OS 与内建 Agent source commit 都等于版本标签指向的同一个干净提交，且不是
      开发机的 dirty 快照
- [ ] 清单只列 `linux/amd64`、`linux/arm64` 两个真实平台及各自摘要，并为二者各列一份非空
      SPDX 软件包清单；索引另含指向这两个平台摘要的 BuildKit attestation manifest
- [ ] 清单的 `pythonDependencies` 固定 Python 3.12、uv 0.11.25、`onlyBinary:true` 和同样两个
      平台；构建锁、运行锁、元数据的文件名、包数和 SHA-256 与证据包完全一致
- [ ] `echo-release.env` 只含 `ghcr.io/<owner>/echo-os@sha256:<64 hex>`；没有普通 tag 或 `latest`
- [ ] 运行 `python3 operations_bundle.py verify echo-appliance-operations.tar.gz` 成功；清单中的
      `operationsBundle.imageReference` 与 OCI 不可变引用逐字一致，旁附验证器 SHA-256 与包内
      验证器相同，包内只有固定 Compose/TLS、安装/升级/备份/恢复/审计和 systemd 文件
- [ ] 用验证器 `extract --destination` 解包后，`echo-release.env` 为 0600、脚本为 0755；复制
      `appliance.env.example` 为 `appliance.env` 后，首次安装以 `--no-build --wait` 健康启动，
      主容器与 docker-control 均回读为清单中的同一摘要
- [ ] 登录 GHCR 后运行 `gh attestation verify "oci://$ECHO_OS_IMAGE" -R <owner>/<repository>`，
      验证到预期仓库、版本提交和 GitHub Actions OIDC 身份
- [ ] `docker buildx imagetools inspect "$ECHO_OS_IMAGE"` 显示同一 OCI 索引下同时存在 amd64 和
      arm64；下载清单里的索引摘要等于实际 registry 摘要

这节全部通过只证明“产物和来源可信”，不能替代第 4d 节两台真实架构机器的运行验收。

---

## 1. 镜像构建闭环(去 fork 后的新构建流程)⭐ 最高优先级

> ⚠️ 必须先跑完 §0 的统一 prepare 脚本。缺任一目录/清单、哈希变化或来源混用，
> Dockerfile 都会硬失败。

```bash
cd deploy/appliance
docker compose up -d --build
```

**预期**:多阶段构建成功——pnpm 锁文件构建 OS 前端 → bundle-verifier 复算三类制品和依赖锁
哈希 → py-builder 先按 `--require-hashes --only-binary` 安装完整构建/运行闭包，再关闭依赖
求解和 build isolation 安装实际 distribution，并验证版本与
`echo-agent` 兼容入口 → runtime 只复制同源 runtime/resources/Codex/manifest。
启动时再核对安装版本和两份子清单；`docker compose ps` 显示 healthy。

**检查点**:

- [ ] `docker compose build` 无错；日志出现 bundle `verify` 与 `verify-installed` 成功
- [ ] 构建日志中的 Python 依赖只来自 `build-requirements.lock` / `runtime-requirements.lock`；
      不出现未固定版本、源码包回退或本地项目安装时重新求解传递依赖
- [ ] `docker compose ps` → `echo-os` healthy
- [ ] `curl http://localhost:8000/api/health` → 200
- [ ] `/api/appliance/config` 的 `agent_bundle.verified` 为 `true`，且 `dirty` 为 `false`
- [ ] 用 §0 的管理员密码登录返回 200；`data/echo-agent-config.yaml` 仅含
      `$ECHO_APPLIANCE_*` 凭据引用，不直接落 bcrypt/JWT 原值
- [ ] `/api/appliance/config` 中的 `agent_ui_base` 与 `agent_workspace_url` 均为 `null`
- [ ] `docker compose logs | grep "appliance admin password"` → 未设密码时看初始密码
- [ ] 执行 `ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python
      verify-running-appliance.py --require-clean-bundle`，输出确认主容器无 socket、代理无宿主
      端口、两个 PID 1 均非特权身份、`CapEff=0`、`NoNewPrivs=1`，并包含
      `origin_guard: 403`、`host_guard: 400`、`login_rate_limit: 429`

> **若 COPY 报 no source files**：统一 prepare 未完成，或三项 Agent 产物拷贝不完整。
> **若提示 source/hash mismatch**：产物来自不同 Agent 快照或准备后又被改过；重新跑统一
> prepare，禁止手改产物。**若提示 dirty**：正式构建必须提交 Agent；仅本地 QA 可显式设
> `ECHO_AGENT_ALLOW_DIRTY=1`，脚本会冻结快照且清单仍标脏。

---

## 1b. 单前端 Agent 工作台专项验证⭐⭐

确认 Agent 工作台已由 Echo OS 唯一前端内建，不存在第二个 UI 服务或 iframe 桥接。

```bash
curl -s http://localhost:8000/api/appliance/config
#   预期 agent_bundle:{verified:true, dirty:false, source_id, version}
#   且 agent_ui_base:null, agent_workspace_url:null
```

**检查点**:

- [ ] `/api/appliance/config` 的两个退役 UI 字段均为 `null`
- [ ] 桌面点 Dock “工作台”直接渲染 OS 内建 React 内容，DOM 中无 Agent iframe
- [ ] 对话、任务、能力和终端日志都通过同源 Agent API 正常工作
- [ ] 窗口只有 Echo OS 一排红黄绿控件，关闭、最小化与拖动正常
- [ ] 运行进程和端口中没有第二套 Agent WebUI

---

## 2. 桌面 + 登录 + 启动器(P1 端到端)

浏览器打开 `http://<本机或NAS_IP>:8000/#/desktop`

**检查点**:

- [ ] 出现原生登录屏(极光壁纸 + 毛玻璃卡),输入管理员密码进入
- [ ] 进入后是极光壁纸桌面 + Dock(图标有邻近放大效果)
- [ ] Dock "本地应用"段列出**宿主上真实运行的 Docker 容器**(运行中带绿点)
- [ ] 九个 Hub 应用全部安装后，启动台和 Spotlight 能找到并打开九个应用；Dock 最多保留前六项，
      第七至第九项不能因 Dock 限额而从应用库和搜索中消失
- [ ] 应用图标/名称正确(从容器 label 读;用 CasaOS 装的应用图标应能复用)
- [ ] 容器 Web UI label 的路径、查询和锚点可保留，但最终主机名必须绑定当前浏览器访问的 NAS；
      `javascript:`、`data:`、带账号密码、畸形端口或公共外部站点 label 均被拒绝，并仅在有安全
      发布端口时回退为本机端口入口
- [ ] Hub 已安装卡以“打开”为主操作，不再把“卸载”作为醒目的默认动作；已停止应用显示“启动”，
      运行中应用可“安全重启”或“停止”。对九个应用逐项确认启动按目录依赖顺序、停止按反向顺序，
      不是只控制公开 Web 容器；三种动作均要求管理员密码、planId 绑定的单次审批并进入持久后台任务，
      失败时恢复操作前运行集合，配置与 NAS 数据保留
- [ ] 九个目录条目均展示明确版本；安装后显示的当前版本来自受管容器标签，升级前显示“当前 → 目标”
      两个版本。删除版本标签或注入控制字符、超长值后只能显示“版本待识别”，不能把不可信 label
      直接投影到 Hub
- [ ] Hub 顶部“已安装 N”和“可更新 N”与当前 Docker/目录投影一致；筛选后页脚显示可见数/总数，
      安装、卸载、更新、启动、停止和安全重启进入 Echo 自有后台任务账本；关闭或刷新商城后仍能恢复真实状态并刷新正确计数，
      账本不得打开 Agent 私有数据库，也不得把一次性凭据明文写盘或在领取后再次返回
- [ ] 清空某应用镜像后安装，商城只显示 Docker 实际发现/完成的镜像层数和多镜像序号，不显示伪造百分比；
      任务账本与 API 不得出现镜像名、layer ID、registry 原始状态或原始错误。断开浏览器后 sidecar 仍完成
      已批准操作，重新打开商城能读到最后阶段或最终结果
- [ ] 逐个打开九个应用的“详情”：设备架构、对外端口、隔离网络/局域网发现、每个 NAS/配置目录的
      只读或读写范围、受管服务数量、卸载保留和更新回退说明均与同一次已认证详情响应一致；Home
      Assistant 明确显示 mDNS/SSDP 与无 USB/Bluetooth/Zigbee 直通，非公开数据库/缓存服务不显示宿主端口
- [ ] 九个应用安装后详情均显示“运行健康”，服务运行数/总数与目录服务图一致，CPU、内存、进程与
      重启次数来自 `echo.hub.runtime.v1`；任一服务 unhealthy/OOM/非零退出时降级为“需要处理”。伪造
      标签、重复服务或固定容器名不符时只能显示“暂不可读”，不能继续读取原始 Docker 数据
- [ ] 对 OOM、健康检查失败、重启循环、非零退出和部分服务停止逐项注入故障；详情只显示
      `echo.hub.diagnostics.v1` 固定故障码对应的中文说明、服务 ID 和恢复动作，不能出现 Docker 原始错误、
      日志、环境变量或宿主路径；可恢复故障的“安全重启”必须整组执行并恢复健康
- [ ] 详情接口 401、503、畸形 envelope 与应用 ID 不匹配时只在单层详情浮板内报错并可重试；Hub
      目录、筛选和卡片仍可使用，Esc 先关闭详情而不是连同 Hub 一起关闭
- [ ] 安装前资源预检逐端口显示“可用/当前应用使用中/已被占用”，冲突时点名具体宿主端口；服务数、
      内存/进程/共享内存上限与实际容器 HostConfig 一致，Jellyfin 为 3072 MiB、Navidrome 为 1024 MiB
- [ ] 使用当前 NAS 挂载点的真实 `statvfs/disk_usage` 数字展示总容量和剩余容量，响应不得包含宿主路径；
      挂载不可读时明确显示无法读取且不伪造数字。另核对当前架构的目录签名 OCI 去重分层下载量、
      blob 数及 Docker 数据根真实余量；数据根观察挂载必须与 Engine 自报路径一致，响应不得暴露路径
- [ ] 将 Docker 数据根可用空间分别压到保守预留量上下：足够时计划可确认；不足、挂载缺失或路径
      不匹配时计划必须在拉取前阻断。修改目录中的任一 downloadBytes/blobCount 后，发布 OCI 校验失败
- [ ] 点击安装后的管理员确认页仍显示刚生成计划中的内存上限、固定端口数、Docker 预留/余量和 NAS 剩余容量；在确认
      前制造端口冲突后，apply 重新计算计划并拒绝旧 planId，不能消费旧摘要继续创建容器
- [ ] 已停止的应用点击 → 先弹管理员密码复核；取消/错误密码均不启动，正确密码后才启动
      并转绿点；同一次审批令牌不能重放
- [ ] 打开 Echo Hub，Jellyfin、Navidrome、Syncthing、Nextcloud、Immich、Open WebUI、qBittorrent、Paperless-ngx 与 Home Assistant 显示“可安装”，其余
      未完成条目明确显示“接入中”
- [ ] x86 与 ARM 的 `hub-lifecycle-result.json` 都包含九应用首装和重装两轮的
      `publicEndpoint` 机器证据；端口必须来自同一候选目录，九个入口均返回 200–499，且附件只保存
      有界响应摘要而不保存登录页、正文或一次性凭据
- [ ] 同一 schema 9 生命周期证据的每个首装记录都必须通过后台任务依次完成整组停止、启动和重启；
      停止后全部目录服务保留原容器 ID 且 runningServices 为 0，诊断状态为 stopped；启动和重启后
      服务顺序、容器 ID、卷指纹、公开入口与健康状态必须恢复且三个操作均声明数据保留和失败恢复
- [ ] 同一 schema 9 生命周期证据的每个首装/重装记录都包含 `runtimeHealth`：状态为 healthy，
      running/healthy/serviceCount 三者等于目录服务数，CPU/内存/进程为真实非空聚合；证据中不得出现
      容器日志、环境变量、挂载路径、网络地址、镜像名或服务原始 stats
- [ ] 安装计划只显示固定镜像、端口和卷；浏览器请求无法注入其他镜像、特权模式、宿主目录或
      Compose 配置
- [ ] Navidrome 安装后 4533 页面可打开；其 `/data` 使用独立持久卷，NAS 仅以 `/music` 只读挂载
- [ ] Nextcloud 安装计划明确展示 PostgreSQL/Redis/App/Cron、8081 端口、双数据卷、两个生成式
      密钥和私有后端网络；安装后一次性凭据页只显示管理员密码，不显示数据库密码
- [ ] 用一次性密码登录 Nextcloud，确认数据库和 Redis 不发布宿主端口，辅助容器不出现在 Dock；
      卸载再重装后原账号、文件与数据库仍存在，且不再次回显管理员密码
- [ ] 用测试目录升级 Nextcloud：数据库与应用卷均生成同一计划的回滚快照，候选四服务在新隔离
      网络全部健康后才切换；分别注入 App 启动失败与双卷恢复失败，核对完整回滚和保留快照边界
- [ ] 安装 Immich 后确认只有 Server 发布 2283，数据库、缓存和机器学习服务不出现在 Dock；照片只
      写入 NAS 的 `photos/immich` 专属目录，数据库与模型缓存使用独立卷，路径逃逸和伪造 NAS
      提供者均被拒绝
- [ ] 升级/失败回滚/卸载重装 Immich：数据库与模型缓存卷进入同一计划快照，NAS 照片目录不被
      更新事务复制或删除；重装后原照片、账号与时间线仍可读
- [ ] 安装 Open WebUI 后确认只有 App 发布 3005，内部 Valkey 不发布宿主端口；应用数据卷和持久
      密钥卷在卸载重装后身份不变，升级失败时数据卷恢复，Docker 配置与日志不出现密钥明文
- [ ] 安装 qBittorrent 后确认 Web 管理端只发布 3006、BT 监听为 6881/TCP+UDP，下载只能写入
      `downloads/qbittorrent`；首次密码只显示一次且可直接登录，重启和卸载重装后密码、任务与配置
      保持不变，Docker 配置与证据文件不出现密码明文
- [ ] 安装 Syncthing 后确认 Web 管理端只发布 3007、同步监听为 22000/TCP+UDP，数据只能写入
      `sync/syncthing`；应用容器没有 host 网络且不发布 21027。首装密码只显示一次，重启和卸载重装后
      设备 ID、索引、密码与共享配置保持不变
- [ ] 在同一物理 LAN 接入第二台 Syncthing 设备，确认双方无需手工填写 IP 即可发现；关闭
      `echo-lan-discovery` 后商城阻止新安装，恢复健康后重新放行。代理必须保持 nobody 用户、零能力、
      只读根文件系统、无卷挂载且只处理 Syncthing 发现报文
- [ ] 在 x86 与 ARM 各自设备门运行候选包内 `lan_discovery_functional_lab.py`。两端 Syncthing 地址
      必须严格保持 `dynamic`，两份 mode-0444 探针必须证明本地发现缓存命中、私网 `isLocal:true`
      TCP/QUIC 非 relay 直连、已有流量、设备摘要互相交叉匹配且机器摘要不同
- [ ] NAS 与伴机先启用 NTP；每次计划、凭据生成、探针和汇总前，schema 2 工具都必须自证当前 mode-0755
      执行文件与计划绑定的候选 SHA-256/大小一致。三份探针均在 `verify` 前一小时内、首尾不超过十分钟，
      且没有超过五分钟的未来偏差；改写工具、传输损坏、旧探针或跨轮次拼接均被拒绝
- [ ] 安装 Paperless-ngx 后确认只有 App 发布 3008；PostgreSQL、Valkey、Gotenberg、Tika 不发布宿主
      端口且只在私有后端网络。首次管理员密码只显示一次，Docker 配置、日志和证据不出现管理员密码、
      数据库密码或应用签名密钥
- [ ] Paperless 的数据库、缓存与应用数据卷进入同一升级快照；原始文档、消费入口与导出结果只能写入
      `documents/paperless/{media,consume,export}`。卸载重装后账号、文档、索引、标签和密码保持，
      不再次回显密码；从 3.0.5 升级到 3.1.0 及候选失败回滚后内容摘要不变
- [ ] 用扫描 PDF 验证简体中文与英文 OCR 均可检索；分别导入 DOCX、XLSX、PPTX，确认 Tika/Gotenberg
      转换任务完成、预置检索词命中并能导出原文件。记录测试 ID、任务结果、检索命中和 SHA-256，
      不把密码、正文或宿主绝对路径写入验收附件
- [ ] 在 x86 与 ARM 各自设备门使用候选包内 `paperless_functional_lab.py plan|run|verify`；私有夹具目录
      为 root:root mode-0700、清单和五份夹具均为 mode-0400。公开门目录只放 mode-0400
      `paperless-functional-plan.json` 与 mode-0444 `paperless-functional-result.json`，并确认计划/结果
      均绑定本门候选、架构、五服务安装证据、五份输入摘要、搜索命中、原文件下载摘要和 204 清理
- [ ] Hub 生命周期 `run` 使用 `--private-paperless-secret-output` 把首装密码写到证据目录之外的
      root:root mode-0700 私有目录；交接 JSON 为 mode-0400，绑定同一候选与 Hub planId。Hub 最终卸载后
      从商城重装 Paperless，再用 `paperless_functional_lab.py run --password-file` 消费该文件；确认密码
      没有出现在 Hub/Paperless 公开结果、标准输出、gate manifest 或签名附件
- [ ] 安装 Home Assistant 后确认 8123 可打开，配置卷在卸载重装后身份不变，从官方 2026.8.2 升级
      到 2026.8.3 成功；候选启动失败时恢复配置卷、旧容器名和原运行状态
- [ ] Home Assistant 容器必须是 host 网络但 `Privileged:false`、`CapDrop:[ALL]`、禁止提权，且没有
      Docker socket、`/dev`、`/run/dbus` 或其他宿主 bind。用真实 LAN 的 mDNS 与 SSDP/UPnP 设备各
      自动发现至少一个并完成可逆状态读取/控制；手工 IP 不算自动发现。USB/Bluetooth/Zigbee 直通
      明确标为当前安全模式不支持
- [ ] Home Assistant 探针必须从公开 WebSocket API 取得 loaded `zeroconf` 与 `ssdp` 配置项，将一个
      `switch.*` 或 `light.*` 实体绑定到其中一项，通过 REST 切换一次并恢复初态。门目录保留固定
      mode-0400 `lan-discovery-functional-plan.json`、mode-0444 汇总结果和三份原始探针；私有凭据、
      设备 ID、IP、实体 ID、密码与 token 不进入 gate manifest 或签名附件
- [ ] NAS 与伴机都使用候选工具的 `credentials` 命令从秘密管理器环境生成固定名称 mode-0400 凭据；
      父目录为对应执行用户所有的 mode-0700 且与公开计划目录分离。确认重复生成、错 role、伴机夹带
      Home Assistant 字段或输出到 gate 目录均被拒绝，stdout/stderr 不出现密码、token 或实体 ID
- [ ] 卸载前再次展示管理员密码确认和“保留配置卷/NAS 文件”；卸载后容器消失，重新安装仍能
      读回原配置和媒体库，NAS 文件摘要不变
- [ ] 用测试目录把一个受管应用指向新固定镜像：更新计划显示旧/新镜像和“保留数据、失败回滚”；
      正常更新后容器 ID 改变、配置与运行/停止状态不变，临时回滚卷被清理
- [ ] 在候选容器启动阶段注入失败：旧容器恢复原名和原运行状态，应用配置卷逐文件摘要与更新前
      快照一致；再注入卷恢复失败时，旧容器保持停止且回滚卷仍在，不允许带迁移后的数据强启旧版
- [ ] `docker-control` 的卷复制临时容器只使用自身受保护镜像 ID，无网络、源卷只读、目标卷可写，
      无宿主 bind mount；浏览器请求不能指定复制镜像、卷名、命令或安全参数
- [ ] Hub 的“Agent 能力”只投影 Agent 公开插件/技能目录；关闭 Agent 目录服务时安全降级，Echo
      不读取或迁移 Agent 的私有 SQLite。向 Agent 目录夹带私有路径、配置对象、未知数据库字段、
      控制字符、超长标识和重复项，确认它们不会进入 Echo 响应；插件卡跳到 Agent 插件页，技能卡
      跳到 Agent 技能页，不能全部误导到同一个管理入口
- [ ] Agent 发现的 Echo 能力包含 Hub 整组 start/stop/restart 的 plan 与 queue 合同；决策阶段以
      planId 为 target 返回管理员复核，执行仍进入 Echo 自有任务账本。整个过程不得查询、附加、复制
      或迁移 Agent 私有 SQLite

> 若 Dock 不显示任何应用:先看 `docker compose ps docker-control` 是否 healthy；确认只有
> `docker-control` 挂了 `/var/run/docker.sock`，且宿主上确有带发布端口的容器
> (`docker ps` 能看到 PORTS 列)。Echo 主容器不应出现 socket mount。

---

## 3. 窗口化第三方应用(P2 窗口管理器 + 反向代理的关键反馈)⭐

点击 Dock 里一个运行中的应用 → 应在桌面内开成窗口。

**逐个应用记录**(这是反向代理要不要做、怎么做的依据):

| 应用           | iframe 直接显示? | 备注 |
| -------------- | ---------------- | ---- |
| 例:Jellyfin    | ☐ 正常 / ☐ 空白  |      |
| 例:qBittorrent | ☐ 正常 / ☐ 空白  |      |
| 例:Immich      | ☐ 正常 / ☐ 空白  |      |
| …              |                  |      |

- [ ] 窗口能拖拽(标题栏)、缩放(右下角)、最小化(黄)、关闭(红)
- [ ] **空白的应用**:点标题栏"新标签打开"能正常访问 → 说明是 X-Frame-Options
      拦截了内嵌,需要反向代理剥头(P2 后续)
- [ ] 浏览器 DevTools Console 里记录空白应用的报错(通常是
      `Refused to display ... in a frame because it set 'X-Frame-Options'`
      或 CSP `frame-ancestors`)

> 这一张表直接决定下一步:哪些应用开箱即用、哪些必须走反向代理。

---

## 4. NAS 文件传输 + 回收站(P2,已本机验证,真机复核)

点 Dock 文件夹图标 → 文件管理器。

**检查点**:

- [ ] 列出 `NAS_STORAGE` 挂载的真实目录与文件
- [ ] 进入子文件夹(面包屑可回退)
- [ ] 工具栏选择多个文件上传；把文件拖入内容区也能上传，期间显示进度
- [ ] 上传同名文件返回冲突提示，宿主原文件内容不变（默认绝不静默覆盖）
- [ ] 文件行的“下载”能保存原始内容；“复制副本”生成同级副本
- [ ] Chromium/Electron 下载出现保存位置选择后边读边写；模拟网络中断时第二次请求携带从已写
      字节开始的 `Range`，最终内容一致；点击取消不提交部分文件
- [ ] 在已登录的正式 Cookie 会话中禁用 File System Access API 后点击下载：浏览器原生下载
      管理器收到原始文件，页面不创建 Blob URL，下载 URL 不含 token、Authorization 或其他凭据
- [ ] 大于 1 MB 的文件上传后内容与源文件哈希一致；下载支持 HTTP Range
- [ ] 前端先调用 `/upload/preflight`；超过单文件上限返回 413，越过磁盘保留水位返回 507，
      两种情况均不产生目标文件或遗留临时文件
- [ ] 设置 `ECHO_SHARE_QUOTAS_JSON` 后，预检返回父级/子级的已用、已预留和可用字节；越过
      任一规则返回 507，普通上传、分块会话、复制、跨共享移动和回收站恢复均不能绕过
- [ ] 创建未完成分块会话后重启 Echo OS：会话最终大小仍占共享配额预留；取消/过期回收后释放；
      配额已满时同共享内重命名仍成功，不重复计算原文件
- [ ] 上传进行中不能整体移动或回收其父目录；复制该目录时不带走 `.echo-upload-*.part`，原上传
      会话继续可恢复且最终哈希一致
- [ ] 若该共享还通过 SMB/NFS 暴露，直接从另一台机器写入也必须被 OMV/XFS/ZFS 原生硬配额
      限制；Echo 应用层配额单独通过不能作为跨入口配额验收证据
- [ ] 上传成功响应带服务端 SHA-256；API 提供期望摘要时不一致返回 422、声明大小不一致返回
      400，均不原子提交；完整摘要不进入长期审计链
- [ ] 选择大于等于 16 MiB 的文件后走 `/upload/sessions`：每块不超过 8 MiB 且携带块摘要；暂停在
      当前分块结束后生效，继续后从原偏移上传，取消后会话元数据和 `.echo-upload-*.part` 都消失
- [ ] 上传中断或重启 Echo OS 服务后，重新选择同一文件会从服务端已提交偏移继续；模拟“数据
      fsync 成功、元数据更新前崩溃”后，服务以临时文件真实长度恢复且最终哈希一致
- [ ] 模拟崩溃遗留超过 24 小时的 `.echo-upload-*.part`，再次浏览该目录后被清理；当前进程的
      活跃上传即使超过窗口也不能被误删，内部临时路径不能直接下载
- [ ] 删除一个文件 → 进"回收站"能看到它,原位置消失
- [ ] 回收站"恢复" → 文件回到原位置
- [ ] 在宿主上确认:删除的文件进了 `<NAS_STORAGE>/.echo-trash/`,
      **不是被物理删除**(`ls -la` 看得到)
- [ ] "清空回收站"先要求再次输入设备管理员密码；取消/错误密码时文件仍可恢复，正确密码
      后宿主上 `.echo-trash/` 内容才真正消失
- [ ] 直接请求 `.echo-trash/manifest.json` 等内部路径返回 400，不能绕过回收站 API

> 上传端已经具备**可恢复分块传输闭环**；Chromium/Electron 下载端也已边读边写并支持 Range
> 重试。正式 appliance 的 HttpOnly Cookie 会话在旧浏览器中交由同源原生下载管理器落盘，
> 不占用与文件大小接近的页面 Blob 内存，也不生成携带凭据的 URL；下载进度和取消由浏览器
> 界面负责。仅非正式设备的 Bearer-only 旧开发客户端保留 XHR Blob 兼容。面向多 GB 正式
> 交付还需要真实 1 GiB、多并发、网络抖动、服务重启/断电和双架构压力证据。详见
> [NAS 交付状态](NAS_DELIVERY_STATUS.md)。

---

### 4a-1. 自动化 1 GiB 传输门

先在 NAS 中创建一个专用空目录（下面示例为 `verification`）。第一次只预览，不创建上传会话、
文件或回收站条目：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --nas-transfer-test-bytes 1073741824 \
  --nas-transfer-test-path verification --nas-transfer-restart-main
```

确认输出中的 `writeExecuted:false`、目录、1 GiB 字节数和设备 origin 都正确，再原样复制
`confirmationRequired` 执行第二次。默认本机 origin 的示例是：

```bash
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --nas-transfer-test-bytes 1073741824 \
  --nas-transfer-test-path verification --require-nas-transfer \
  --nas-transfer-restart-main \
  --nas-transfer-write-confirm \
    'VERIFY ECHO NAS TRANSFER 1073741824 verification ON http://127.0.0.1:8000 AND RESTART echo-os'
```

该门会在首块 fsync 后重启 `echo-os`，等待 bundle 健康门，再验证同一会话偏移；随后检查
8 MiB 分块摘要、重复偏移 409、整文件摘要、完整下载、最后 1 MiB Range、数据型会话取消和
回收站可恢复性。成功结果必须包含 `writeExecuted:true`、`restartVerified:true`、
`offsetRecovery:8388608`、`fullDownload:1073741824`、`cancelVerified:true`、
`physicallyDeleted:false`。该选项会造成一次短暂服务中断，但不会重建或更换容器。测试文件会
保留在回收站并继续占用约 1 GiB；检查结果后由管理员在 UI
中明确清空。脚本绝不调用“清空回收站”，因此不会连带物理删除用户已有回收站内容。

- [ ] 第一次预览前后，文件列表、上传会话和回收站完全不变
- [ ] 错误字节数、目录、origin 或缺少 `--require-nas-transfer` 时在任何文件请求前拒绝
- [ ] 第二次完成后输出摘要与本地确定性数据一致，主目录没有测试文件，回收站只有该测试产物
- [ ] 用网络整形补充脚本尚不能制造的真实链路中断，重新运行仍通过且没有孤儿会话

---

## 4b. 本地智能照片（只读首版）

点启动台或 Dock 的“照片”。准备普通 JPG/PNG、回收站图片、`.echo-*` 内部图片和一个指向
NAS 外部的图片符号链接。

- [ ] 照片网格只显示普通图片；回收站、内部目录和符号链接均不出现，响应中没有宿主绝对路径
- [ ] 缩略图为有界 WebP，原图像素和摘要不变；`../`、内部路径、链接路径均被拒绝
- [ ] 未建索引时搜索明确显示“文件名”；建立后“海边的家人”等自然语言返回本地语义结果
- [ ] “建立智能索引”先显示照片数、最多索引数和人物聚类选择；取消或密码错误时不创建/改写 DB
- [ ] 正确密码只消费一次，后台状态依次为 running → succeeded；审计含 attempted/succeeded，
      不含密码、绝对路径、图片内容或向量
- [ ] 建库期间新增/修改照片会使旧 planId 409，需重新检查；并发第二个建库任务被拒绝
- [ ] 开启人物聚类后只显示记录数量；第一版没有自动删除、自动移动、重复清理或敏感内容写操作
- [ ] 断网后已缓存的模型仍能重建索引；记录首次模型准备时间、纯 CPU 与可用 GPU 的每千张耗时、
      峰值内存、索引 DB 大小，并分别在 amd64/arm64 真机验证
- [ ] 1 万张以上图库保持桌面可交互，静止时无永久动画渲染；扫描上限和 4000 张首版索引上限如实显示

---

### 4b-1. 桌面存储中心（所有机器）

点启动台“存储中心”，先在 NAS 根目录准备照片、视频、音频、文档、压缩包、普通文件、一个回收站
条目和一个指向根目录外的符号链接；如配置了共享配额，再保留一个未完成的可恢复上传。

- [ ] “设备总容量”、已用、可用和上传可用空间与目标机 `df`/实际保留量一致，页面不显示宿主
      NAS 根目录、mountpoint、devicefile、序列号或 by-id
- [ ] 六类内容字节数、文件数和顶层大目录排名与准备数据一致；回收站不混入普通分类，上传临时文件
      不混入文件库，但回收站占用、上传预留和共享配额分别可见
- [ ] 符号链接被跳过且页面出现提示；构造超过 20 万目录项的可丢弃测试树时页面明确标记安全上限，
      API 不跟随链接、不读取文件内容，其他登录、文件和 Agent 请求仍能响应
- [ ] 新增/删除/恢复文件后点“重新分析”能看到新容量；普通打开走 10 秒缓存，不持续扫描磁盘
- [ ] “磁盘健康”直接显示现有 OMV SMART/RAID/LVM 页，“共享与用户”直接显示现有真实 plan →
      审批 → apply 页面；未接 OMV 时容量概览仍可用，且不能伪报磁盘健康

---

### 4b-2. 设备连接与局域网配对（所有机器）

点启动台“设备连接”。正式原生镜像初始应为关闭状态；开发直连配置若 Agent 已开启 Tentacle，页面
必须明确显示“Agent 开发连接 / 共享凭据”兼容提示，不能伪报单设备撤销。正式 appliance 入口会
强制关闭 personal preset 的共享 Tentacle，并由 Echo 管理一机一凭据监听器。

- [ ] 未登录 `GET /api/appliance/device-link` 返回 401；登录后的状态响应不含 token、secret、
      credential digest、UDID、序列号或宿主路径；未启用远程 overlay 时显示“尚未配置”
- [ ] 原生模式不输入管理员密码不能开启 LAN listener；错误密码、重复审批票据均拒绝，正确审批后
      `listenerActive:true`，端口为配置的 `ECHO_DEVICE_LINK_PORT`（默认 8765），审计含
      attempted/succeeded 且不含配对链接
- [ ] Docker/OMV 的 Compose 同号发布该端口，但 Device Link 关闭时没有监听器；运行配置中的上游
      Tentacle 固定为 disabled，不能因 personal preset 回退到共享凭据。容器不得把 172.x bridge IP
      写进二维码
- [ ] 创建配对邀请需再次复核；邀请 5 分钟后失效，首次连接后绑定该设备 ID，同一邀请不能再绑定
      第二台设备；磁盘只落 keyed digest，不出现明文链接或 token。通过 NAS 的 RFC1918 IP 打开桌面
      时，深链 `ws`/`sync` 使用同一可达主机；公网域名/localhost 场景未配置 LAN host 时明确失败
- [ ] 真实 Echo Mobile 在同一 Wi-Fi 粘贴邀请后上线，页面显示真实平台、型号、电量与能力数量；
      断网后显示离线，恢复网络后使用同一设备凭据重连
- [ ] 单独移除设备需绑定该设备 ID 的审批；在线连接立即断开，旧凭据重连失败，其他已配对设备仍可
      正常重连。关闭入口会断开全部连接并清除未使用邀请，但不静默删除已配对设备记录
- [ ] 页面“远程访问”明确说明当前 LAN WebSocket 未加密；未接 Tailscale overlay 或受控中继前，
      外网不可达且不能出现“已开启远程访问”的产品文案

---

### 4b-3. Tailscale 私网远程网页（可选，Docker/OMV 机器）

先在 Tailnet 开启 HTTPS，创建一次性、预授权、非可复用的设备 auth key，并用 0400/0600 普通文件
保存。用 `start-remote-access.sh` 启动可选 overlay；不要把 key 写进 `.env`、命令行或聊天记录。

- [ ] 启动前错误 DNS、非 `*.ts.net`、符号链接 key、0644 key、带换行/控制字符或错误前缀均失败，
      且 Docker 尚未执行；成功路径输出、Compose config、容器环境和 Echo 日志均不出现 key 原文
- [ ] `echo-tailscale` 使用锁定 index 与 amd64/arm64 子摘要、userspace networking、只读根文件系统、
      `cap_drop: ALL`、`no-new-privileges`，没有宿主 ports、Docker socket、host network 或 privileged
- [ ] Tailnet 未授权/断网时页面为“正在连接”且远程网页不可用；授权成功后健康状态变为 connected，
      精确 HTTPS 地址可打开并通过 Echo 登录，其他 Tailnet 成员之外的网络不能直连该入口
- [ ] HTTPS 入口的 Host/Origin、HttpOnly + Secure Cookie、WebSocket、管理员审批与审计验证全部通过；
      Tailscale Serve 只反代 Echo 8000，不暴露 Docker 控制网、OMV socket、Tentacle 8765 或其他应用端口
- [ ] 页面在远程网页成功后仍显示“设备配对限局域网”，`features.deviceLink` 为 false；只有
      `/api/appliance/sync` 安全挂载后 `fileSync/photoSync` 才为 true，不得把浏览器手工上传冒充设备备份

### 4b-4. 手机照片/文件自动备份（原生 Echo + Echo Mobile）

API 36 模拟器烟测记录见 `docs/mobile/ANDROID_EMULATOR_ACCEPTANCE.md`：现已覆盖正式 App 深链、权限、
SAF、WorkManager、真实受管凭据上传、同内容幂等跳过、内容变化 keep-both，以及 512 MiB 文件在
8 MiB 偏移强制结束 App 后的续传；并实跑修复 Android 36 MediaStore 查询兼容问题。它仍不能勾选
下列任何需要实体手机、真实 NAS、断电/弱网或 Tailnet 的项目。

- [ ] Android 点击/扫描 `echo://join` 后先展示 Runtime 与同步地址；点取消时原连接、凭据和同步基址
      均不变，确认后才加密保存。重复 token、未知参数、公共明文 Runtime 和外部 HTTPS 接收主机必须拒绝
- [ ] Runtime 设置页能分别开启照片/文件备份；Android 13+ 只在开启照片时申请图片权限，文件通过系统
      SAF 多选并在重启后保有读取权。仅 Wi-Fi、仅充电和“立即同步”实际反映到 WorkManager 约束与状态
- [ ] 已配对但未单独开启 scope 的设备预检返回 403；管理员分别为该设备开启照片/文件后才可写入，
      Agent 共享兼容模式固定拒绝；错误密码、跨 action/设备复用审批票据均失败
- [ ] 设备请求只使用 `EchoDevice` 头与设备 ID，不使用浏览器 Cookie；第二台合法配对设备查询、追加、
      完成或取消第一台的 session 均返回 404，撤销第一台后旧凭据立即返回 401
- [ ] 每个请求携带 `X-Echo-Sync-Version: 1`；缺失或不支持版本在凭据校验后返回 426 和服务端版本，
      Mobile 收到后暂停 Worker 并提示升级，不能继续猜测协议
- [ ] 上传一个大于 8 MiB 的真机照片/文件，中途断 Wi-Fi、杀 App、重启 Echo 后，从服务端
      `uploadedBytes` 续传；最终 SHA-256、大小和内容一致，失败临时文件不会出现在普通文件列表
- [ ] 同一 asset + 同一 SHA 重报为 skip；同一 asset 内容变化、同目录同名异内容都保留两份，冲突副本
      有稳定短摘要且旧文件未改写。另一台设备的 changes 不返回该文件名、摘要或路径
- [ ] 关闭某 scope 会取消该设备该类未提交会话但不删除已提交内容；照片完成后无需重启即可出现在照片库，
      Agent 语义索引仍需由照片应用受控重建
- [ ] 通过真实 Tailnet HTTPS 完成后台上传；省电、锁屏、弱网、跨运营商、低容量/配额 507、海量相册、
      HEIC 兼容与 Android 后台权限均有真机证据。iOS 若后续进入产品范围，必须另做原生后台任务与权限验收，
      不能用 Android 结果代替

---

## 4c. 企业版 ↔ agent 服务化联调(PM 归并 D②,可选)

把企业版作为 PM 插件部署后,可让它把 AI 调用走 agent(而非自带 LLM key):

- 企业版 backend 设 `ECHO_AGENT_URL=http://<agent_IP>:8000`(agent 服务地址);
- 在企业版里触发一次需 AI 的操作(如 PRD 导入 / 风险扫描);
- [ ] 看 agent 侧日志收到 `/v1/chat/completions` 请求 → 证明走的是 agent 网关;
- [ ] 不配 `ECHO_AGENT_URL` 时仍能用自带 `LLM_BASE_URL` 直连(回退正常)。

> 本机已用单测验证路由逻辑 + 进程内耦合解除;此项是真实双服务的联调确认。

## 4c. OpenMediaVault 存储接入（OMV 机器）

正式候选优先按 `deploy/omv/README.md` 安装经过摘要、SBOM 和来源证明校验的
`openmediavault-echo-os` 原生 `.deb`，再叠加 `docker-compose.omv.yml`。受管宿主包只用于兼容或
迁移：先用 `echo_omv_host.py plan` 做只读预检，再用与本次源码哈希绑定的精确确认语安装。
两种形态不能混装，也不要手工把 root 服务指向仓库目录。

```bash
export ECHO_OMV_ADMIN_URL=https://nas.example.com
export OMV_BRIDGE_GID="$(getent group echo-omv | cut -d: -f3)"
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py \
  --require-clean-bundle --require-omv --expected-gid "$OMV_BRIDGE_GID"
```

脚本会重新确认宿主是 Debian 13 + OMV 8，并同时检查宿主插口 `0660`/数字组、未登录 `401`、
主容器只读挂载、docker-control 无挂载、注入参数 `422`，递归扫描响应中是否出现序列号、by-id、
密码、SSH 公钥、绝对共享路径或额外协议选项。它还确认基础共享文件夹、SMB、私网 NFS 与
用户/组配额能力均存在，未登录计划请求为 401，额外共享目录路径/配额路径字段为 422；真正
写入仍需另按下面矩阵验收，其他未允许
POST 应为 405。`omv.host_install.support_matrix` 必须为 `debian-13+omv-8`。

自动化基线已在 ARM64 Debian 13 VM 用真实 `dpkg`/systemd 验证首装、升级、失败升级回装、
remove/purge、unit 安全属性、`0660 root:echo-omv` socket 和 `/health`；CI 也有固定摘要 Debian 13
离线包生命周期门。2026-08-26 又在隔离 ARM64 VM 从官方 Synchrony 仓库实装
`openmediavault 8.5.6-1`，通过了真实 engined、Workbench 生成配置、只读 RPC、专用 ext4 盘、
SMB 创建/445 监听、64 MiB 用户硬配额、purge 数据保留和重装回读。下面仍保留正式制品证明、
x86_64/物理设备、浏览器 Workbench 视觉、物理 SMART/阵列及 SMB/NFS 客户端写满验收项。

- [ ] `Real OMV 8 / x86_64` CI 在一次性 Debian 13 systemd 容器从官方签名 Synchrony 仓库安装
      真实 OMV 8 和当前 `.deb` 后绿色；下载同一 `echo-real-omv-x86-evidence` artifact，先执行
      `sha256sum -c echo-real-omv-x86-artifact-set.sha256`，确认 `.deb`、`.deb.sha256`、SPDX、原始
      evidence、验证报告和离线 verifier 均属于同一完整集合。再确认架构
      `x86_64`、真实版本、包 SHA-256、RPC/Workbench/桥、Netplan 行为均通过；NFS 证据必须包含
      RFC1918 CIDR、服务器地址、文件系统/共享文件夹/导出 UUID、共享文件夹与 NFS 两个 planId、
      `sharedFolderCreatedByEchoBridge:true`、`sharedFolderPermissionsVerified:true`、
      `/export/echo-ci-nfs`、
      NFSv4 实际写入摘要、只读重挂载拒绝写，以及插件 purge/reinstall 后规则和文件摘要保留。
      家庭成员证据还必须包含固定一次性组/用户、创建与密码重置 planId、SMB share UUID/planId、
      `SMB3`、真实密码认证与上传/下载、旧密码拒绝、新密码认证和账户字段保持，以及 purge/reinstall
      后再次认证和三个完全相同的 SMB 载荷 SHA-256；两个密码都不能出现
      在 argv、环境值、临时文件、日志、原始 evidence 或 verifier 报告中。
      同时保存 `.verification.json`，再用 artifact 内的 `verify-real-omv-x86-evidence.py` 绑定同一 `.deb` 和
      artifact 的 40 位 Git SHA 离线复核。没有这两个文件或验证器不通过时，只能说“x86 CI 门
      已接线”，不能说 x86 验收完成；同容器 NFS 客户端也不能替代物理 NAS 的异机互通验收
- [ ] `.github/workflows/omv-real-x86.yml`、桥、探针、严格 evidence verifier 和对应测试必须进入同一
      个正式提交并在远端默认分支可见；触发器必须覆盖 `os-main`，第三方 Action 必须固定到完整提交，
      正式分支成功运行应为 `.deb`、SBOM 和 evidence 集合生成 OIDC attestation。当前
      `origin/os-main` 快照没有该 workflow。恢复 GitHub 登录后核对远端 workflow 身份与运行 SHA，
      不能用本地未跟踪文件或 404 响应证明远端门已经存在

- [ ] 从正式 CI/发布页取得 `.deb`、`.deb.sha256` 与 SPDX 2.3 SBOM；摘要一致，
      `gh attestation verify <deb> --repo <owner>/<repository>` 验证到预期仓库和 `os-main`/正式发布构建，
      `dpkg-deb --info` 显示包名 `openmediavault-echo-os`、架构 `all`、OMV `>=8.0,<9.0` 和
      `XB-Plugin-Architecture: amd64, arm64`，`dpkg-deb --contents` 只包含清单内文件
- [ ] 在干净 Debian 13 + OMV 8 测试机用 `apt install ./openmediavault-echo-os_..._all.deb`
      首装成功；安装前没有组/unit/桥进程，安装后 OMV“服务 → Echo OS”入口可见，unit 位于
      `/usr/lib/systemd/system/echo-omv-bridge.service`，桥代码位于 `/usr/lib/echo-os/omv-bridge`，
      页面只说明受限桥边界，不出现磁盘格式化、阵列、共享文件夹或账户写入口
- [ ] 安装前设备短主机名不超过 15 字符；运行
      `/usr/bin/python3 /usr/lib/echo-os/omv-bridge/platform_preflight.py` 保存 JSON，确认
      `ready:true`、`smbHostnameCompatible:true`、`netplan.compatible:true`。制造超长主机名或
      `40netplan.sh=dnsservers` + 模型 `dnsnameservers` + 活跃 Netplan `nameservers` 时，包配置必须
      在创建组/启动服务前失败，且不得改写主机名、Netplan 或 OMV 文件
- [ ] 原生包拒绝覆盖受管安装器的 `install-state.json`、`/etc/systemd/system` 手动 unit 或无清单
      桥文件；先按旧安装器精确确认卸载后再装 `.deb`，不能静默接管或同时存在两份 unit
- [ ] `getent group echo-omv` 得到实际动态 GID；compose `PGID` 与它逐字一致，不能假定 1000。
      运行验收输出 `omv.host_install.install_mode:nativePluginPackage`；受管宿主包则输出
      `managedHostBundle`，同机两份 unit 必须失败
- [ ] 安装前 `sudo python3 deploy/omv/echo_omv_host.py plan --gid <PGID>` 输出
      `supported:true`、`distribution:debian`、`distributionVersion:13`、`omvMajor:8`、真实
      `omvVersion`、`supportMatrix:debian-13+omv-8`、当前机器正确的 `amd64`/`arm64` 和
      `platformPreflight.ready:true`、`platformPreflight.smbHostnameCompatible:true`、
      `platformPreflight.netplan.compatible:true`、`action:install`；运行 plan 前后只有固定
      `dpkg-query` 与受信任配置文件读操作，没有新组、新 unit 或服务进程
- [ ] 在隔离测试机分别模拟 Debian 12、Ubuntu、OMV 7、OMV 9 和缺失 `openmediavault` 包，plan
      必须在写宿主文件前拒绝；受管桥安装后再把系统标识改成不支持版本，精确确认卸载仍应成功，
      且 NAS 数据保持不变
- [ ] 从正式 CI/发布页取得宿主包、`.sha256` 与 SPDX 2.3 SBOM；摘要核对成功，且
      `gh attestation verify <包> --repo <owner>/<repository>` 验证到预期仓库和 main 发布构建
- [ ] 安装后 unit 与桥文件为 root 所有，unit/代码为 0644、安装清单为 0600；unit 的工作目录和
      `PYTHONPATH` 都是 `/usr/lib/echo-os/omv-bridge`，不包含 `/opt/echo-os` 或其他用户可写仓库
- [ ] 同一源码重新 `plan` 显示 `action:unchanged`；源码升级后显示 `action:upgrade`，使用旧确认语
      会拒绝，使用新确认语升级失败时旧 unit、桥代码和原服务状态自动恢复
- [ ] `/run/echo-omv/omv.sock` 为 `0660 root:echo-omv`，没有桥 TCP 监听端口
- [ ] `docker-control` 没有 OMV socket mount；只有 `echo-os` 只读挂载 `/run/echo-omv`
- [ ] 未登录访问 `/api/appliance/omv/status`、`filesystems`、`smart` 均返回 401
- [ ] 登录后“系统设置 → 存储健康”显示 OMV 物理盘，以及已挂载卷的名称、文件系统、容量和使用率
- [ ] 物理盘显示型号、健康和温度；按需读取通电时间/启停次数，页面与日志不出现序列号或
      可能含序列号的 `/dev/disk/by-id/...` 路径
- [ ] 伪造未枚举设备、包含分号/空白的设备路径均被拒绝，不能借参数调用任意命令或任意 RPC
- [ ] 停止宿主桥后页面显示不可用，数据 API 返回 503；桌面、Agent、文件管理仍正常
- [ ] 等待至少两个轮询周期，确认 `/api/appliance/omv/health` 的 `monitoring=true`、检查时间推进，
      `/data/omv-health-state.json` 为 0600；停止桥后原告警仍保留并标记过期，恢复后产生恢复事件
- [ ] 用测试阈值或安全测试盘制造高温/容量/阵列告警，确认首次与最近出现时间、连续次数及最近
      告警变化可见；不要在承载唯一数据副本的阵列上人为拔盘
- [ ] RAID/LVM/Btrfs 多设备环境里，物理盘即使尚未映射到具体卷也必须出现在磁盘列表；记录
      物理盘→分区→md RAID→LVM 层级，阵列降级/重建/校验状态与 `/proc/mdstat` 一致
- [ ] “系统设置 → 共享与用户”显示共享文件夹、普通用户/组、SMB/NFS 启用状态和共享权限；
      页面不出现用户 home、SSH 公钥或共享绝对宿主路径
- [ ] 打开“新建用户组”，只允许严格小写的普通账户名和备注；系统/保留名称、已有组、成员列表及
      任意额外字段均在写前拒绝。预览不写入，不输入设备管理员密码不能应用，审批必须绑定
      `omv.group.create:<planId>`；应用后 `getGroup` 回读为空组，重复创建拒绝，审计不含敏感字段
- [ ] 打开“添加家庭成员”，只允许严格小写新用户名、显示名、强密码和页面枚举出的现有普通附加组；
      OMV 自动 home 选项开启时必须拒绝。页面明确显示固定 `/usr/sbin/nologin`、无 email/SSH key、
      禁止用户自改资料；密码与确认不一致、弱密码、系统/已有用户、未知组及额外字段均在写前拒绝
- [ ] 预览家庭成员只返回安全 desired 和 changeFields，响应、浏览器日志、桥日志、进程参数、审计及
      CI evidence 均不得出现密码。审批必须绑定 `omv.user.create:<planId>`；应用后从 OMV、系统账户
      和 Samba 账户三处回读一致，密码输入框立即清空，重复提交/错误计划/重复票据均拒绝
- [ ] 用该成员通过 SMB3 真实登录一个 guest 关闭、`users` 组可读写的专用测试共享，上传后下载固定
      载荷并核对 SHA-256；测试工具必须用一次性匿名 FD/pipe 传密码，不能放入 argv、环境值或文件。
      插件 purge 和 reinstall 后分别重新认证并下载，三次摘要必须完全相同
- [ ] 在成员卡片打开“重置密码”，只有仍保持 nologin、无 email/SSH key、禁止自改资料的普通成员
      能生成预览；计划只回传 `passwordBound:true`，审批绑定
      `omv.user.password.reset:<planId>`。应用后确认 UID/GID、显示名、组和安全属性不变，旧密码 SMB3
      登录失败、新密码可下载原载荷；页面、响应、argv、环境值、文件、日志、审计和 evidence 均无两份密码
- [ ] 在一次可恢复故障注入中确认尚未交付的新用户/空组会回滚；成功交付后的账户在插件 remove、
      purge 与 reinstall 后仍存在。密码写入成功后不可自动恢复旧值，错误结果必须提示凭据状态可能
      不确定并要求重新预览/登录验证；Echo 不提供其他账户/组更新或日常删除入口，这些回到 OMV
- [ ] 在一个不承载唯一数据副本的已挂载可写卷上打开“新建共享文件夹”，只允许选择目标卷、
      输入便携名称和备注；页面明确显示相对目录由名称推导、固定 `2770/users`，没有任意路径、
      ACL、更新或删除字段。`../escape`、`a..b`、`CON`、首尾点和额外 `relativePath` 均在写前拒绝
- [ ] 预览创建只返回 name/comment 差异；不输入设备管理员密码不能写，审批必须绑定
      `omv.shared-folder.create:<planId>`，错误 planId 和重复票据均拒绝。应用后 OMV 配置、目录、
      users 组和 2770 权限都回读一致；重复预览为 no-op，审计有 attempted/succeeded 和 intentId
- [ ] 用测试桩制造创建后回读失败，确认回滚只调用 `ShareMgmt.delete recursive=false`；真 OMV
      故障注入后目录或已写入数据不得被删除。该入口没有修改/删除 API，需复杂操作时回到 OMV
- [ ] 选择一个测试共享文件夹生成 SMB 计划，确认只出现启用/只读/发现/回收站/备注差异；不输入
      设备管理员密码不能写，错误 planId、重复审批票据、OMV 尚有未应用 SMB 变更均在写前拒绝
- [ ] 应用后用 Linux/macOS/Windows 客户端实际连接，确认 guest 始终关闭、读写行为与计划一致；
      重复提交变成 no-op，审计同时有 attempted/succeeded 与 intentId
- [ ] 用测试桩验证 deploy 失败能恢复原规则；再在不承载唯一数据副本的真 OMV 测试共享上制造
      可恢复部署失败，核对 OMV dirty state、最终配置和 Echo 审计，不得只看 HTTP 200
- [ ] 准备一个不承载唯一数据副本的测试文件系统和专用测试用户/组；确认卷已挂载、可写且 OMV
      报告支持 quota。在“共享与用户 → 文件系统硬配额”选择卷和对象，页面必须明确显示“按所有者
      覆盖本机/SMB/NFS、不是共享文件夹独立限额”
- [ ] 只预览 1 GiB 硬限制，确认计划只含 `hardLimitBytes` 差异；不输入设备管理员密码不能写，
      错误 planId、SMB 审批票据、重复配额审批票据及 OMV 未应用 quota 变更均在写前拒绝
- [ ] 应用后分别从本机、SMB 和 NFS 以同一测试所有者写入，累计达到限制后所有入口都被底层
      文件系统拒绝；同一用户在该文件系统其他目录的文件也必须计入，不能把它误记为共享目录配额
- [ ] 恢复该对象原限制（原值可能为 0），确认审计同时存在 `omv.quota.apply`
      attempted/succeeded 和 intentId。用测试桩制造 deploy/回读失败时必须恢复并回读确认原限制；
      真机故障注入不得使用家庭唯一数据卷
- [ ] 先仅传 `--omv-smb-test-folder <UUID>`，确认输出只有 comment 差异且未写；再复制精确
      `confirmationRequired`，加 `--require-omv-smb-write` 执行，最终必须同时报告
      `writeExecuted:true`、`restored:true`、`applyVerified:true`、`auditVerified:true`
- [ ] 为专用测试用户/组传入文件系统 UUID、对象类型/名称和一个更严格的
      `--omv-quota-test-bytes`；第一次输出必须只有 `hardLimitBytes` 差异且未写。复制与 UUID、
      对象、原值和探针值绑定的 `confirmationRequired`，再加 `--require-omv-quota-write` 执行；
      最终必须报告 `writeExecuted:true`、`restored:true`、`applyVerified:true`、
      `auditVerified:true`，并确认对象原限制已恢复
- [ ] 设置 `ECHO_OMV_ADMIN_URL` 后“在 OMV 中管理”只打开该 HTTP(S) origin；Echo 只在受限创建
      或密码重置请求期间瞬时转交成员密码，不持久化、不记录、不回显；没有密码以外的账户/组更新、
      日常删除、共享文件夹修改/删除或 ACL 的代理接口
- [ ] 按安装器给出的 `uninstallConfirmation` 卸载后，只有桥 unit/代码消失；OMV 阵列、卷、共享、
      权限和 Echo/NAS 文件不变，`last-uninstall.json` 记录 `preservedNasData:true`；重新安装成功
- [ ] 对原生 `.deb` 完成同版本重装、跨版本升级和可恢复失败升级；服务/Workbench 最终来自新包，
      失败升级不留下半配置状态。随后依次验证 `apt remove`、重新安装和 `apt purge`：包管理文件与
      socket 消失，`echo-omv` 组有意保留，OMV 阵列、卷、共享、权限和 Echo/NAS 文件哈希/数量不变

## 4d. x86_64 / ARM64 双架构验收

同一不可变 release 摘要至少分别在一台 `amd64` 和一台 `arm64` Linux/NAS 上执行完整验收。
不要用 Apple Silicon Docker Desktop 的模拟结果代替 ARM NAS，也不要只看镜像能启动。
验收脚本必须直接在对应 NAS 宿主上运行，不能从另一种架构的机器通过远程 Docker context 代跑。

```bash
# x86_64 机器
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py --require-clean-bundle --expected-arch amd64

# ARM64 机器
ECHO_ADMIN_PASSWORD="$ECHO_ADMIN_PASSWORD" python \
  deploy/appliance/verify-running-appliance.py --require-clean-bundle --expected-arch arm64
```

| 项目 | amd64 | arm64 |
| ---- | ----- | ----- |
| 主容器与宿主 `uname -m` 一致，脚本输出 architecture | ☐ | ☐ |
| Agent wheel、resources、Linux Codex 同源校验通过 | ☐ | ☐ |
| 登录、桌面、Agent 工作台、Docker 启停审批 | ☐ | ☐ |
| Hub 九应用两轮安装/卸载/保留数据重装、整组停止/启动/重启及公开入口响应、Syncthing 双设备 LAN 发现、Paperless 五格式功能计划/结果及 Home Assistant LAN 发现，全部通过离线复核 | ☐ | ☐ |
| 1 GiB 上传/下载哈希一致，Range 与磁盘保留水位正常 | ☐ | ☐ |
| 状态备份、校验、恢复、升级失败回滚 | ☐ | ☐ |
| OMV 机型：SMART、拓扑、SMB/NFS/权限概览 | ☐ / 不适用 | ☐ / 不适用 |

两列都通过前只能称“本机/单架构候选”，不能称 x86/ARM 正式交付。

上述单次命令只是运行时预检，不能证明 24 小时连续运行或物理断电。正式签名门必须从当前候选
运维包运行 `device_endurance_lab.py`：把 root-owned mode-0400 的完整安装器 transcript 保存在
`physical-evidence/` 之外，第一次冷启动后的六小时内生成计划，依次完成 `baseline`、同一 Boot ID
至少 86400 秒后的 `soak`、`arm-power-cut`，再物理拔电/恢复供电后执行 `recovered`。不得用 reboot、
poweroff、虚拟机 reset 或修改 journal 代替物理断电；恢复阶段必须从上一启动持久 journal 同时证明
断电意图存在且正常 `systemd-shutdown` 痕迹不存在。

- [ ] amd64 的 `device-baseline.log`、`device-soak.log`、`device-power-cut-armed.log`、
      `device-recovered.log` 均为 mode-0444，同一 planId，最终生成候选绑定的
      `device-endurance-lifecycle.json`
- [ ] arm64 独立执行同一四阶段流程；不能复用 amd64 的计划、设备身份、Boot ID、日志或生命周期
- [ ] amd64 与 arm64 各自从干净设备运行 `hub_lifecycle_lab.py plan|run|verify`，在对应 gate 目录保留
      固定名称 `hub-lifecycle-plan.json` 与 `hub-lifecycle-result.json`；每个应用的 stop/start/restart
      operationId、planId、服务顺序和状态证据必须完整，且不能跨架构、跨候选或跨运维包复用
- [ ] 两个 baseline、soak 和 recovered 阶段各自运行完整 1 GiB 上传/下载/Range/取消/回收站恢复，
      并在首块后真实重启主容器、从同一上传会话偏移继续
- [ ] 原始安装器 transcript 没有进入签名证据目录；签名附件只包含它的 SHA-256 和哈希目标身份
- [ ] x86 使用受信 HTTPS origin；浏览器证书信任、Secure cookie、会话吊销、Origin/WebSocket 与
      第三方 CSP 仍有独立真实浏览器证据，不把设备耐久生命周期冒充完整 G4

## 5. 安全复核(docker.sock 只允许进入窄控制 sidecar)

- [ ] 确认 8000 端口**只在内网**可达,未直接暴露公网
- [ ] 无 token 直接 `curl http://<IP>:8000/api/appliance/apps` → 应 401
- [ ] 退出登录/换浏览器 → 桌面要求重新登录
- [ ] 登录后浏览器只靠 host-only HttpOnly cookie；Local Storage 中没有新写入设备 JWT
- [ ] Dock“系统设置”打开 Echo OS 的“账户与安全”，不是跳回 Agent 工作区设置
- [ ] 点“退出所有登录”并完成密码复核后，当前桌面回到锁屏；操作前取得的 Cookie、Bearer
      和 Agent 实时连接都不能继续使用，重新输入原密码仍可登录
- [ ] 更改管理员密码后当前桌面回到锁屏；旧密码登录返回 401，新密码无需重启即可登录；
      `appliance-auth.json` 和审计事件中均没有新旧明文密码
- [ ] `GET /api/appliance/audit/verify`（已登录）返回 `ok:true`；events 中能看到 actor、动作、
      目标和 attempted/succeeded/failed，但看不到密码、会话 JWT 或审批令牌
- [ ] 系统设置“审计与证据”显示健康、记录数和 `sha256:` 设备指纹；下载的锚点可在设备外验证
- [ ] “轮换密钥”未输入管理员密码时失败；审批后产生 `audit.key.rotate` 记录，重启后轮换前后
      的全部事件仍通过验链；篡改或删除密钥环后新写入 fail closed
- [ ] `docker inspect echo-os` 的 Mounts 中**没有** `/var/run/docker.sock`
- [ ] `docker inspect echo-docker-control` 才有 socket mount，且没有宿主 `Ports`
- [ ] `docker compose top docker-control` 显示代理主进程已不是 root
- [ ] `docker compose top echo-os` 显示主进程完成初始化后也已不是 root；`/data`
      中 `.echo-runtime-owner`、认证和运行配置归 `PUID:PGID`，NAS 文件本身未被批量改属主
- [ ] 在主容器内请求 `http://docker-control:2375/images/json` → 404，`containers/create`、
      `/containers/<id>/json` 与 `/containers/<id>/stats` → 404，DELETE → 405；除 list/start/stop 外，
      只允许脱敏 `GET /hub/apps/<id>/runtime` 和 `/hub/apps/<id>/install` 接受
      `planId` 与 `catalogDigest`，附加 `HostConfig`、镜像、卷或端口字段必须返回 400
- [ ] Echo 主容器和 `echo-docker-control` 带 `sh.echo.control-protected=true`，通过
      启动器 API 停止二者均返回 403

## 6. 设备状态备份与恢复演练

- [ ] 按部署 README 停止 `echo-os` 后导出 `.echo-backup`；服务未停止时导出必须因状态锁
      返回失败，不能生成看似成功的热备份
- [ ] 备份文件权限为 `0600`，直接搜索看不到 JWT secret、密码哈希、Agent 记忆正文或 NAS 文件
- [ ] 正确口令执行 `verify` 成功；错误口令和手工修改任一字节都失败
- [ ] 恢复到一个不存在的新目录成功；指定既有目录、路径穿越/外部符号链接归档均被拒绝
- [ ] 恢复目录包含 `appliance-auth.json`、审计链和 Agent 状态，但不包含 `/data/nas` 用户文件
- [ ] 首次运行 `restore-state.sh` 只验证备份并打印 SHA-256/精确确认语，不停止服务、不创建回滚
      目录；错误或过期确认语不能触碰现场状态
- [ ] 使用精确摘要确认后按“停服务 → 暂存恢复 → schema 正向迁移 → 认证/审计/权限预检 → 原子
      晋级 → 健康等待 → 容器内复核”执行；可用备份时密码登录、审计 `ok:true`、Agent 关键状态可读
- [ ] 模拟暂存恢复失败时原目录不移动、原本运行的服务恢复；模拟晋级后健康或容器内复核失败时
      旧目录自动切回并复机，失败新状态保留在 `.data.echo-failed-*`
- [ ] 成功恢复后原状态保留在 `.data.echo-rollback-*`，脚本不自动删除或合并；完成登录、审计、
      Agent 核对和一次新备份之前不人工清理
- [ ] 在暂存恢复期间替换备份文件，晋级前必须因 SHA-256 改变而失败，原目录保持不变
- [ ] 运行 `backup-state.sh` 时，原本运行的服务按“停机 → 导出 → 校验 → 复机 → 轮换”排序；
      模拟导出失败后服务仍被拉回，失败备份不会触发旧备份删除
- [ ] `ECHO_BACKUP_DIR` 与 `ECHO_BACKUP_MOUNTPOINT` 同时显式设置，目标位于精确活动的外置或远端
      挂载；卸载/断开该挂载、改成系统盘同名目录、符号链接、tmpfs/overlay 或与部署、`data/`、
      `NAS_STORAGE` 同文件系统时，任务必须在取得维护锁、查询或停止 Docker 前失败
- [ ] 保留策略只删除命名匹配的旧 Echo 状态包；最新包口令错误或被篡改时，旧包一份不删
- [ ] 安装 systemd timer 后手工启动一次 service 成功；口令来自 `LoadCredentialEncrypted`，
      普通环境文件和日志中没有明文，定时任务错过后会补跑
- [ ] 使用 `operations_systemd.py plan/apply` 在实体 Debian 13/OMV 主机安装五个 unit；upgrade recovery
      service 处于 enabled/inactive，两个 timer 都处于 enabled/active，实际各触发一次且产物通过独立
      校验，不能只提交 `systemd-analyze verify`
- [ ] 在第二个 timer 启用阶段注入失败，安装器恢复安装前五个 unit 内容/权限、recovery service 以及两个 timer 的
      enabled/active 状态；出现“不完整回滚”时本门必须失败
- [ ] 升级镜像/compose 前最后一次任务成功，并已将至少一份已验证备份复制到异机或离线介质
- [ ] `echo-state-schema.json` 为 0600；无标记旧目录首次启动依次执行 v0→v1 标记迁移和
      v1→v2 审计密钥环契约迁移，迁移前后的既有认证、审计和 Agent 状态内容不变
- [ ] 把测试标记改成高于当前程序的版本后，旧镜像明确拒绝启动且标记/数据不变；恢复正确标记后
      才能启动，不能用删除标记的方式绕过降级保护
- [ ] `upgrade-appliance.sh` 拒绝普通 tag/`latest`，只接受 `@sha256:` 摘要；当前容器与
      `echo-release.env` 不一致时在拉取/切换前失败
- [ ] 无 schema 变化的目标镜像按“已验证备份 → pull → 目标预检 → 健康切换 → 两容器摘要核对”
      成功；模拟目标健康失败后旧 release 选择恢复且旧服务重新运行
- [ ] 目标报告 `migrationRequired:true` 时自动升级明确拒绝，不能在没有迁移/回滚手册时写入
      release 文件或启动新版本
- [ ] 使用 `power_state_recovery_lab.py` 在目标 digest 已持久选择、Compose 尚未执行的 arm 边界人工
      断电；恢复供电后 boot ID 必须改变，持久 journal 只出现一次 arm marker 且没有正常关机标记，
      boot recovery service 自动恢复旧 digest、清除事务，并复核两个容器与两个 canary
- [ ] 随后完成候选 digest 正常升级；在降回旧 digest 的 Compose 阶段注入失败后仍恢复并保持候选
      digest；`docker compose down --remove-orphans` 不带 `-v`，重装后 device-state 与 NAS canary 不变
- [ ] 外部加密状态备份先通过不改 live state 的恢复预检；修改专用 state canary 后按摘要确认恢复，
      原 canary、NAS canary、候选容器和 rollback 目录同时验证；生成七份固定只读阶段日志及
      `power-state-lifecycle.json`

## 7. 审计证据外置与保留演练

- [ ] `ECHO_AUDIT_EXPORT_DIR` 与 `ECHO_AUDIT_EXPORT_MOUNTPOINT` 同时指向外置盘、异机文件系统或
      受控远端挂载，不在 `data/`、NAS 用户目录或部署树中；卸载该挂载、替换成同名系统盘目录、
      符号链接、tmpfs/overlay 或同设备文件系统后任务明确失败，不能回落到本机目录
- [ ] `export-audit-evidence.sh` 按“停服务 → 导出 → verify → 复机 → prune”运行；模拟导出失败
      后原本运行的服务仍恢复，且不会执行 prune
- [ ] `.echo-audit` 权限为 `0600`，直接搜索看不到 JWT secret、密码哈希、审计正文或 NAS 文件；
      错误口令和修改任一密文字节都失败
- [ ] 证据包的清单哈希、尾序号/MAC、Ed25519 签名全部通过；使用首次异机保存的
      `--expected-signing-key-id` 可通过，换成其他指纹必须失败
- [ ] 保留策略只删命名匹配、超过天数且不属于最小保留集合的加密证据包；最新包损坏时一份不删，
      实时 `appliance-audit.jsonl` 永远不被该策略处理
- [ ] systemd 口令来自 `LoadCredentialEncrypted`；手工运行一次 service 成功，错过定时后补跑，
      外置挂载不可用时 unit 失败并在日志中可见
- [ ] 分别断开备份挂载和审计挂载；对应 service 都在接触 Docker 或写入系统盘同名目录前失败，
      恢复挂载后才允许下一次 timer 成功
- [ ] 用 `operations_systemd.py remove-plan/remove` 受管移除；五个 unit 均不存在、两个 timer 均未启用
      且不活动，daemon reload 后无孤儿触发；两份加密凭据、设备状态、NAS 数据、状态备份和审计证据
      的摘要与移除前一致
- [ ] 在删除第二个 unit 时注入失败；移除器恢复五个 unit 的内容/权限、recovery service 及两个 timer 原先的
      enabled/active 状态；出现“不完整回滚”时本门必须失败

---

## 反馈给开发(回填后贴回来)

1. **第 1b 节（单前端 Agent 工作台）⭐ 最关键**——确认内建工作台无 iframe、
   无第二 UI 进程，且四个核心 Agent 页面能调用同源 API。
2. **第 1 节** build 成功?失败贴 `docker compose build` 中 bundle verify、wheel install
   或启动时 provenance mismatch 的原始报错。
3. **第 3 节那张表**:哪些第三方应用 iframe 直接显示、哪些空白 + Console 报错文本。
4. 第 2/4/5 节有任何不符预期的,描述现象即可。

---

## 附：当前进度对照（代码、本地证据与远端证据分别标注）

> 下面的 ✅ 只表示对应单元格写明的本地或既有真机证据，不表示当前工作区改动已经推送。
> workflow、首次绿色 artifact、正式签名和物理机项仍以各行的 ⏳ 为准。

| 阶段 | 项                                                       | 状态                                   |
| ---- | -------------------------------------------------------- | -------------------------------------- |
| P0   | agent 插件扩展 API(挂路由/注册技能)                      | ✅ 本机验证                            |
| P1   | 后端去 fork + Agent 三制品同源/哈希/启动校验            | ✅ 本机打包烟测；⏳ 第 1 节 Docker 真机 |
| P2   | OS 单前端内建 Agent 工作台，退役第二 WebUI          | ✅ 本机验证；⏳ **本清单第 1b 节** 真机 |
| P1   | 应用注册器 + Dock 接真实应用                             | ✅ 本机验证                            |
| P1   | 桌面视觉(极光壁纸 + Dock 邻近缩放)                       | ✅ 预览验证                            |
| P1   | 原生默认主页(去寄生叠加)                                 | ✅ 预览验证                            |
| P1   | NAS 部署打包(compose + CasaOS 清单)                      | ⏳ **本清单第 1 节**                   |
| P1   | 单用户认证(首启设密码 + 长会话)                          | ✅ 本机验证                            |
| P1   | 全会话吊销 + 运行时管理员密码轮换                        | ✅ 本机真实 HTTP/浏览器闭环；⏳ 第 5 节 Docker 真机 |
| P1   | 高风险密码复核 + 单次审批令牌 + 防篡改审计              | ✅ 本机真实 HTTP 闭环；⏳ 第 5 节 Docker 真机 |
| P2   | NAS 文件上传/下载/复制 + 回收站硬约束                    | ✅ 本机真实 HTTP 闭环(第 4 节真机复核) |
| P1   | Echo 文件入口共享目录逻辑配额                            | ✅ 本机逻辑闭环；⏳ SMB/NFS 原生硬配额真机对账 |
| P1   | OMV 原生插件包 + 受限宿主桥                            | ✅ ARM64 Debian 13 + 真实 OMV 8.5.6 闭环；✅ x86 验收合同本地回归；⏳ 提交远端 workflow/首次真实 x86 绿色 artifact/物理机/签名 |
| P1   | OMV 物理盘 + 挂载卷 + SMART 只读桥                      | ✅ 真实 OMV RPC + 虚拟盘；⏳ 物理 SMART/md RAID |
| P1   | OMV 文件系统用户/组硬配额                                | ✅ 真实 OMV ext4 + 内核读回；⏳ SMB/NFS 客户端写满 |
| P1   | OMV 家庭成员、用户组创建与成员密码重置                    | ✅ 代码/API/UI/验收合同本地回归；⏳ 提交远端 workflow/首次真实 x86 绿色 artifact/物理机复核 |
| P2   | 窗口管理器(桌面即窗口系统)                               | ✅ 预览验证(第 3 节复核)               |
| P2   | 反向代理剥 X-Frame-Options                               | ⏳ 需第 3 节的应用反馈                 |
| P2   | 应用技能 SKILL.md                                        | ⏳ 需真实应用                          |
| P2   | 照片只读库 + Agent 语义索引复用                          | ✅ API/UI/路径安全闭环；⏳ 真机模型冷启动/性能/大图库 |
| P1   | 桌面存储中心 + 本机容量分类                              | ✅ API/UI/安全上限/OMV 页面复用闭环；⏳ 实体 NAS 大目录/断盘/容量变化 |
| P1   | 设备连接 + Tentacle 安全启停/配对/单设备撤销             | ✅ API/UI/审批/摘要凭据闭环；⏳ Mobile 真机配对/断线重连/撤销 |

后端文件管理器定向测试：51 个（`uv run pytest tests/appliance/test_files.py`）；
2026-08-28 本地 appliance 全套为 **863 通过、1 项环境性跳过**（macOS 测试进程无
`dpkg-deb`；同一包已在 ARM64 Debian 13 VM 用真实 `dpkg-deb`/`dpkg`/systemd 通过，CI 也会
强制执行固定摘要 Debian 13 生命周期门），前端 Vitest 全套为 **465 通过**，另有
**Electron 原生边界自检全套通过**；前端传输/文件管理器定向测试为 **12 通过**。高风险路径
另由真实临时服务验证 Cookie 鉴权、未审批拦截、密码签发、单次消费、防重放、全会话吊销、
运行时密码轮换和审计验链。Agent 内部 arm/skill 已纳入 Echo 单仓测试，
OS 侧只验证正式 wheel 的 distribution/version、`echo-agent` 入口和三类制品来源一致性。
