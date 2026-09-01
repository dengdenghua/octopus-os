# 在 NAS 上部署 Echo OS 桌面

原生路线:桌面即系统主页 + Docker 应用启动器。装在任意带 Docker 的 NAS /
主机上,浏览器打开即原生桌面,自动发现并点亮已装的 Docker 应用。

## 正式发行安装

正式设备不需要克隆源码或在 NAS 上安装 Node/uv。下载版本流水线生成的发布证据，先验证总校验和
和 GitHub OIDC 来源，再用同一发布物里的运维包完成安装：

```bash
sha256sum -c echo-appliance-release.json.sha256
python3 operations_bundle.py verify echo-appliance-operations.tar.gz
sudo python3 operations_bundle.py extract echo-appliance-operations.tar.gz \
  --destination /opt/echo-os --require-root-owner
cd /opt/echo-os/echo-appliance-operations-*

sudo install -m 0600 appliance.env.example appliance.env
# 编辑 appliance.env：至少确认 NAS_STORAGE、PUID/PGID 和监听方式
sudoedit appliance.env
sudo docker login ghcr.io
sudo ./install-appliance.sh
```

运维包严格绑定一个 `registry@sha256:...` 镜像，包含 Compose/TLS 配置、首次安装、升级、备份、
审计导出、恢复脚本和事务式 systemd 安装器。验证器不直接调用 tar 解包：它先拒绝链接、设备、绝对/穿越
路径、额外文件、错误权限、摘要、SBOM 或镜像身份，再只写固定白名单。`appliance.env` 保存本机
端口与 NAS 挂载配置；只读发布选择单独保存在 0600 的 `echo-release.env`，升级不会覆盖本机配置。
正式目录必须由 root 提取并持有；这是 systemd 安装器的显式信任边界，不得在 `/opt` 下改成普通
用户可写目录。验证归档本身不需要 root，只有写入正式安装目录和运行宿主安装步骤使用 `sudo`。

应用镜像升级不再只依赖 shell 退出 trap。`upgrade-appliance.sh` 在切换不可变镜像前落盘并 fsync
mode-`0600` 的 `.echo-upgrade-transaction.json`，目标选择、双容器健康确认和事务提交分别持久化。
普通失败会先恢复旧镜像并验证两个容器，再删除事务；若宿主在任意未提交阶段掉电，重启后运行
`sudo ./recover-appliance-upgrade.sh`，它会从严格校验的事务中恢复旧 digest、等待 Compose 健康并
复核两个容器，成功后才清除事务。事务存在时禁止开始下一次升级，也禁止手工删除或改写事务文件。

## 源码开发快速开始

在创建发布标签、提交重型 workflow 或请求正式 runner 前，先从 OS 仓库根目录执行只读发布源预检：

```bash
python3 deploy/appliance/delivery_source_preflight.py \
  > echo-delivery-source-preflight.json
```

只有 JSON 返回 `ready:true` 且进程退出 0 才能继续。它要求当前分支是 `os-main`、工作树完全干净、
七个交付/发布 workflow 都是 Git 跟踪的普通文件、OS `HEAD` 同时等于缓存和 GitHub 上的
`origin/os-main`、`origin` 不含凭据、内建 Agent 的 `runtime/__init__.py` 存在，并且
`gh auth status` 有效。报告只给变化数量，不输出未跟踪路径，避免把本地敏感文件名
带进 CI 记录。`--offline` 只用于查看本地检查，会明确加入 `online_verification_required` 并保持
`ready:false`/退出 1；不能用离线结果批准发布。

当 raw 镜像、A/B、真实 OMV x86 和 appliance 标签发布四条远端门都完成后，从 GitHub 四个
run 页面取得数字 ID，在 `os-main` 上启动 `Echo delivery release candidate`：

```bash
gh workflow run delivery-release-candidate.yml --ref os-main \
  -f source_revision=<40位OS提交> \
  -f release_tag=echo-appliance-v<semver> \
  -f os_image_run_id=<数字ID> \
  -f ab_update_run_id=<数字ID> \
  -f real_omv_x86_run_id=<数字ID> \
  -f appliance_run_id=<数字ID>
```

该 workflow 只允许自己运行在输入 SHA 对应的最新 `os-main`，并重新执行在线干净源码门。它逐个
拒绝错误 workflow、非成功结论、其他 SHA、`main`/PR、错误标签或重复 run ID；随后用固定
signer workflow、source SHA 和 source ref 在线复验十份 GitHub OIDC provenance。raw 与 A/B
明确接受仓库固定的专用 self-hosted Linux x86 runner，并由签名证据内的 runner preflight 继续
绑定其宿主合同；OMV 与 appliance 则必须通过 `--deny-self-hosted-runners` 证明来自 GitHub-hosted
runner。raw/A-B/OMV 的 PR 只跑无写权限 source contract；真实特权 job 只接受 `os-main/main` 的
非 PR ref，并独占 OIDC/attestation 写权限，任意分支的手工 dispatch 不会运行特权负载。两类 runner
policy 都写进候选报告并由统一索引精确复验。OS 与内建 Agent 共用当前 job token 和同一个
source revision，不再读取额外私有仓库凭据。raw 与 A/B 的 manifest、
signature 和各自 public keyring 还会再次执行 GPG
验证；二者各自使用只含 manifest、detached signature 和 public keyring 的专用 30 天 artifact，
避免混合工作区/runner 临时目录改变下载根路径。OMV 报告必须匹配原证据和 `.deb` 字节，
appliance 必须匹配不可变 OCI 摘要。只有全部一致
才上传保留 90 天且再次带 OIDC 证明的
`echo-delivery-release-candidate-<标签>`，其中包含来源报告、run/attestation 报告、统一索引、
离线验证器和总 SHA-256。

下载候选 artifact 后，联网时先验证总校验清单自身的 GitHub OIDC 来源；之后整份目录可以带到
隔离网络，由随包脚本重新执行 raw/A-B GPG、OMV 包字节、appliance 摘要和统一索引回放：

```bash
gh attestation verify echo-delivery-release-candidate.sha256 -R <owner>/<repository>
./verify-release-candidate-bundle.sh
```

workflow 在 attestation 与上传前也会执行同一条离线回放。验证器要求固定文件清单完整且没有额外
路径，总校验清单必须逐项且只出现一次；打包的 GPG 验证器优先使用同目录 public-keyring 审计器，
不依赖原仓库相对路径。OIDC 验证证明候选包来自受信 workflow，离线回放证明包内字节没有串版；
两者不能互相替代。候选包同时固定携带 `hub_lifecycle_lab.py`、
`paperless_functional_lab.py`、`lan_discovery_functional_lab.py`、`physical_acceptance.py`、
`physical_acceptance_capture.py` 和 `product_delivery_bundle.py`，真机实验室不需要另行克隆仓库或
下载可能漂移的脚本。

候选离线回放成功后，先在 `physical-evidence/` 之外生成一份与候选 `indexId` 绑定的只读六门计划：

```bash
python3 physical_acceptance_capture.py plan \
  --candidate-index echo-delivery-release-evidence-index.json \
  --output ../echo-physical-acceptance-lab-plan.json

python3 physical_acceptance_capture.py verify-plan \
  --candidate-index echo-delivery-release-evidence-index.json \
  --plan ../echo-physical-acceptance-lab-plan.json
```

同一候选会生成逐字节相同的计划和 `planId`。计划列出六门固定顺序、门对应的 `profileClass`、
架构约束、最低设备数、默认目录、精确成功 marker、固定 `gate-result.json` 名称与逐项结果检查，
并明确保持
`physicalAcceptanceComplete:false` / `nasProductDeliveryReady:false`。它是现场执行清单，不是签名
证据；开始和交接实验前都必须用 `verify-plan` 复核，它会按候选重建完整计划，拒绝重新计算
`planId` 后的内容篡改和其他候选的计划。不得把计划放进只允许六个 gate 文件夹的
`physical-evidence/` 根目录，也不能用它代替任何日志、附件、负责人签名或最终验收报告。

### Echo Hub 九应用真 Docker 生命周期

在一台全新的验收机上，确认 8096、4533、8081、2283、3005、3006、3007、3008、8123、6881/TCP+UDP 和
22000/TCP+UDP 均未占用，且 Jellyfin、Navidrome、Syncthing、Nextcloud、Immich、Open WebUI、
qBittorrent、Paperless-ngx、Home Assistant 均未安装。管理员密码必须由秘密管理器注入环境，
不要写进命令行或计划文件：

```bash
export ECHO_ADMIN_PASSWORD='<由秘密管理器注入>'
GATE_DIR="$PWD/physical-evidence/physical_x86_64_install_and_cold_boot"
sudo --preserve-env=ECHO_ADMIN_PASSWORD ./hub_lifecycle_lab.py plan \
  --base-url http://127.0.0.1:8000 \
  --candidate-index /root/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --output "$GATE_DIR/hub-lifecycle-plan.json"
```

审核计划中的目录摘要、架构、九份不可变应用合同、最小权限发现代理、Home Assistant host-LAN
安全边界和打印出的完整确认串后，再执行：

```bash
sudo install -d -o root -g root -m 0700 /root/echo-paperless-private
sudo --preserve-env=ECHO_ADMIN_PASSWORD ./hub_lifecycle_lab.py run \
  --plan "$GATE_DIR/hub-lifecycle-plan.json" \
  --confirmation 'RUN ECHO HUB LIFECYCLE <完整 planId>' \
  --private-paperless-secret-output \
    /root/echo-paperless-private/paperless-functional-private-secret.json \
  --output "$GATE_DIR/hub-lifecycle-result.json"

./hub_lifecycle_lab.py verify \
  --plan "$GATE_DIR/hub-lifecycle-plan.json" \
  --result "$GATE_DIR/hub-lifecycle-result.json"
```

实验会实际拉取并两次安装九个应用，核对镜像、端口、健康、私有/host-LAN 网络、NAS 来源、最小能力、资源
上限、数据卷与密钥卷身份；两轮之间执行保留数据卸载，重装不得再次回显 Nextcloud、qBittorrent、Syncthing 或 Paperless-ngx
初始密码。每轮还会从设备 loopback 访问目录绑定的九个公开 HTTP 入口，要求全部返回非 5xx 响应；
证据只保存状态码、媒体类型、尝试次数与最多 64 KiB 响应样本摘要，不保存登录页或正文。最终再次
卸载容器，但按产品合同保留应用卷、密钥卷、Immich `photos/immich` 与
qBittorrent `downloads/qbittorrent`、Syncthing `sync/syncthing` 和 Paperless-ngx
`documents/paperless/{media,consume,export}` 目录，因此只允许在专用
验收设备上运行。结果文件只记录密钥名称和宿主路径摘要，不记录管理员密码、生成密码或明文宿主路径。
这两个固定名称文件必须在 x86 与 ARM 各自的设备门独立执行，不能跨架构复用；采集器和最终验收会
离线复核九应用合同、两轮运行证据、卷保留身份、候选索引、运维包和不可变运行镜像，缺一份、改写
内容后重算摘要或串用另一候选都会失败。

`--private-paperless-secret-output` 只在 Paperless 首装时接收一次性管理员密码，写入候选与 Hub planId
绑定的 root-owned mode-0400 JSON。其父目录必须是 root-owned mode-0700，并且不能位于计划、结果或
公开证据目录之下；密码不会进入 Hub 结果或标准输出。Hub 生命周期最后卸载 Paperless 但保留其密钥
卷。完成 Hub 复核后，从商城重新安装 Paperless；它会沿用同一密码且不会再次回显，随后把这个私有
文件直接交给下面的功能实验。私有文件不得加入 gate manifest 或签名附件。

### Paperless 中文/英文 OCR 与 Office 真功能实验

五个 Paperless 容器健康和 3008 能打开只证明服务启动，不证明文档功能可交付。每个 x86/ARM 设备门
都必须在当前候选的 Paperless 已安装且五服务健康时，独立运行候选包内的
`paperless_functional_lab.py`。私有夹具目录必须由 root 所有、mode-0700，且只包含 mode-0400 的
`paperless-fixtures.json` 和五份真实夹具：中文扫描 PDF、英文扫描 PDF、DOCX、XLSX、PPTX。清单中的
固定 ID 为 `ocr-zh`、`ocr-en`、`office-docx`、`office-xlsx`、`office-pptx`，每项指定文件名和只在
私有目录使用的预置检索词；不要把夹具、检索词明文或密码复制到公开证据目录。

```bash
export ECHO_ADMIN_PASSWORD='<由秘密管理器注入的 Echo 管理员密码>'
GATE_DIR="$PWD/physical-evidence/physical_x86_64_install_and_cold_boot"
FIXTURE_DIR=/root/echo-paperless-private-fixtures
PASSWORD_FILE=/root/echo-paperless-private/paperless-functional-private-secret.json

sudo --preserve-env=ECHO_ADMIN_PASSWORD ./paperless_functional_lab.py plan \
  --echo-base-url http://127.0.0.1:8000 \
  --paperless-base-url http://127.0.0.1:3008 \
  --candidate-index /root/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --fixture-directory "$FIXTURE_DIR" \
  --output "$GATE_DIR/paperless-functional-plan.json"

sudo ./paperless_functional_lab.py run \
  --plan "$GATE_DIR/paperless-functional-plan.json" \
  --fixture-directory "$FIXTURE_DIR" \
  --confirmation 'RUN ECHO PAPERLESS FUNCTIONAL LAB <完整 planId>' \
  --password-file "$PASSWORD_FILE" \
  --output "$GATE_DIR/paperless-functional-result.json"

sudo ./paperless_functional_lab.py verify \
  --plan "$GATE_DIR/paperless-functional-plan.json" \
  --result "$GATE_DIR/paperless-functional-result.json"
```

实验会用 Paperless 官方 API 逐份上传、等待消费任务成功、用私有检索词命中同一文档、下载原文件并
核对输入 SHA-256，最后删除本次合成测试文档且要求 204。公开计划只保存文件名、覆盖项、MIME、大小、
文件摘要与检索词摘要；结果只保存任务/文档身份摘要、搜索命中、原文件摘要和清理状态，不保存 token、
密码、正文、检索词明文或私有绝对路径。`verify` 要在 Paperless 仍安装时完成；之后可按 Hub 保留数据
合同卸载。采集器和最终验收器会再次离线复核这对文件，任一格式缺失、伪造检查后重算摘要、跨候选或
跨架构复用都会失败。

### Syncthing / Home Assistant 真局域网发现实验

应用能打开和基线日志写着“发现成功”都不能证明没有手工填写 IP。每个 x86/ARM 设备门必须在
Syncthing 与 Home Assistant 已安装时运行候选包内的 `lan_discovery_functional_lab.py`。实验只调用
Syncthing REST 与 Home Assistant WebSocket/REST 公开接口，不读取两者私有数据库或配置存储。
实验开始前先确认 NAS 与伴机均已启用 NTP。局域网实验契约现为 schema 2：每次
`plan`、`credentials`、`syncthing`、`home-assistant` 与 `verify` 都会把当前正在执行的工具重新计算 SHA-256
和大小，并要求它与计划绑定的候选工具完全一致且权限为 mode-0755；手工改过、传输损坏或仅复制了
计划而未复制同一候选工具都会立即失败。

先准备同一物理 LAN 上的第二台 Syncthing 设备。双方只把对方设备 ID 加入配置，地址必须保持默认
`dynamic`，不得填写 IP；等待双方直连并产生少量同步流量。Home Assistant 中必须已有一项来源为
`zeroconf` 的 loaded 配置项和一项来源为 `ssdp` 的 loaded 配置项，并选一个属于其中任一配置项的
真实 `switch.*` 或 `light.*` 实体做可逆控制。NAS 与第二台设备各自建立 owner-only mode-0700 私有
目录和 mode-0400 凭据文件，固定文件名分别为 `lan-discovery-nas-credentials.json` 与
`lan-discovery-companion-credentials.json`。两份文件都包含 schema、kind、当前 `planId`、role 和
Syncthing 用户名/密码；NAS 文件额外包含 Home Assistant 长期访问令牌与 `controlEntityId`。凭据文件
不得进入 gate 目录、manifest、签名附件或命令行。

```bash
export ECHO_ADMIN_PASSWORD='<由秘密管理器注入的 Echo 管理员密码>'
GATE_DIR="$PWD/physical-evidence/physical_x86_64_install_and_cold_boot"

sudo --preserve-env=ECHO_ADMIN_PASSWORD ./lan_discovery_functional_lab.py plan \
  --echo-base-url http://127.0.0.1:8000 \
  --syncthing-base-url http://127.0.0.1:3007 \
  --home-assistant-base-url http://127.0.0.1:8123 \
  --candidate-index /root/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --output "$GATE_DIR/lan-discovery-functional-plan.json"

sudo install -d -o root -g root -m 0700 /root/echo-lan-private
export SYNCTHING_ADMIN_PASSWORD='<由秘密管理器注入的 Syncthing 密码>'
export HOME_ASSISTANT_TOKEN='<由秘密管理器注入的长期访问令牌>'
export HOME_ASSISTANT_CONTROL_ENTITY='<真实 switch.* 或 light.* 实体 ID>'
sudo --preserve-env=SYNCTHING_ADMIN_PASSWORD,HOME_ASSISTANT_TOKEN,HOME_ASSISTANT_CONTROL_ENTITY \
  ./lan_discovery_functional_lab.py credentials \
  --plan "$GATE_DIR/lan-discovery-functional-plan.json" \
  --role nas \
  --output /root/echo-lan-private/lan-discovery-nas-credentials.json
unset SYNCTHING_ADMIN_PASSWORD HOME_ASSISTANT_TOKEN HOME_ASSISTANT_CONTROL_ENTITY

sudo ./lan_discovery_functional_lab.py syncthing \
  --plan "$GATE_DIR/lan-discovery-functional-plan.json" \
  --role nas \
  --credentials /root/echo-lan-private/lan-discovery-nas-credentials.json \
  --confirmation 'RUN ECHO LAN DISCOVERY FUNCTIONAL LAB <完整 planId>' \
  --output "$GATE_DIR/lan-syncthing-nas.json"

sudo ./lan_discovery_functional_lab.py home-assistant \
  --plan "$GATE_DIR/lan-discovery-functional-plan.json" \
  --credentials /root/echo-lan-private/lan-discovery-nas-credentials.json \
  --confirmation 'RUN ECHO LAN DISCOVERY FUNCTIONAL LAB <完整 planId>' \
  --output "$GATE_DIR/lan-home-assistant.json"
```

`credentials` 只从指定环境变量读入秘密，原子创建固定名称 mode-0400 文件；目标已存在、父目录不是
当前用户所有的 mode-0700 目录、与计划同目录、角色混用或字段不完整都会失败，标准输出只打印 kind
与 planId。把同一份 mode-0400 计划和候选包内同一版本工具安全复制到第二台设备，在该设备用同一
`credentials --role companion` 流程生成伴机凭据，再运行 `syncthing --role companion`，输出固定的
`lan-syncthing-companion.json`，最后以 mode-0444 安全复制回同一 gate 目录。复制后的工具必须保持
逐字节一致并显式设为 mode-0755，不能用本地工作区里的另一个版本替换。在 NAS 合并并离线复核：

```bash
sudo ./lan_discovery_functional_lab.py verify \
  --plan "$GATE_DIR/lan-discovery-functional-plan.json" \
  --syncthing-nas "$GATE_DIR/lan-syncthing-nas.json" \
  --syncthing-companion "$GATE_DIR/lan-syncthing-companion.json" \
  --home-assistant "$GATE_DIR/lan-home-assistant.json" \
  --output "$GATE_DIR/lan-discovery-functional-result.json"
```

两端探针都要求 Syncthing 配置地址严格为 `dynamic`、本地发现缓存命中、连接为非 relay 的
TCP/QUIC 直连、`isLocal:true`、对端地址属于私网且已有实际流量；两份证据必须来自不同机器并互相
交叉匹配设备身份摘要。Home Assistant 探针通过公开配置项 API 证明 `zeroconf` 与 `ssdp` 来源，
读取开关初态、切换一次并恢复初态。公开文件只保存加盐身份摘要、连接类型、字节数与状态，不保存
设备 ID、IP、实体 ID、密码或 token。最终签名附件必须同时包含 mode-0400 计划、mode-0444 汇总结果
和三份 mode-0444 原始探针；采集器会逐字节核对汇总结果绑定的探针摘要。三份探针必须在最终
`verify` 前一小时内生成，最早与最晚探针相差不得超过十分钟；最多容忍五分钟的未来时钟偏差。
过期证据、未来时间戳或把不同轮次的探针拼在一起都会失败。

当前计划为 schema 17；x86 与 ARM 两门会额外写出固定的 `deviceEnduranceLifecycle`、
`hubLifecyclePlan`、`hubLifecycleResult`、`paperlessFunctionalPlan` 与
`paperlessFunctionalResult`、`lanDiscoveryFunctionalPlan`、`lanDiscoveryFunctionalResult` 及三份
局域网原始探针，G2 会写出
`storageRecoveryLifecycle`，G3 会写出 `protocolInteroperabilityLifecycle`，G5 会写出
`operationsSystemdLifecycle` 与 `powerStateLifecycle`，G6 会写出
`bareMetalRecoveryLifecycle`，每门同时写出各自与候选绑定的实验入口。旧计划缺少机器绑定的设备
耐久、存储、客户端协议、systemd 或裸机恢复生命周期证据要求，必须从当前候选重新生成，不能继续
签名使用。

只有这条汇总 workflow 才能产出 `ciReleaseCandidateReady:true`。这仍不等于整机交付：索引固定保持
`nasProductDeliveryReady:false`，并列出 x86/ARM 实机冷启动、真实硬盘 SMART/阵列降级恢复、
外部 SMB/NFS 客户端、断电更新/恢复和裸机 Recovery 六项物理验收门；当前工具没有跳过或手工
改绿这些门的参数。

## 真机证据与最终产品交付

候选汇总包仍固定写入 `nasProductDeliveryReady:false`。只有六项真机实验都完成、由验收负责人使用
同一专用 OpenPGP acceptance key 签名后，才能生成最终产品交付 manifest。私钥不得进入仓库、
GitHub secret、候选 artifact 或 NAS；发布包只携带经过 packet 审计的 public keyring。

证据根目录必须只包含以下六个固定文件夹：

```text
physical-evidence/
├── physical_x86_64_install_and_cold_boot/
├── supported_arm64_hardware_install_and_cold_boot/
├── real_disk_smart_and_raid_degradation_recovery/
├── external_smb_and_nfs_client_interoperability/
├── power_loss_during_update_and_state_restore/
└── recovery_media_bare_metal_restore/
```

先核对计划中的候选 OS/Agent SHA、标签和当前实验门，再在对应文件夹放真实实验产生的
`acceptance.log` 和经过脱敏的附件。取得绑定候选 SHA 的精确成功
marker；测试程序只能在该项全部成功后把这行写入日志：

```bash
python3 deploy/appliance/physical_acceptance_capture.py marker \
  --candidate-index echo-delivery-release-evidence-index.json \
  --gate physical_x86_64_install_and_cold_boot
```

先用候选包内的采集工具生成固定名称、只读的 `hardware-profile.json`。下面只是 x86 示例；ARM
必须在真实 ARM 宿主执行，SMART/RAID 门的 `--device-count` 至少为 2：

```bash
python3 deploy/appliance/physical_acceptance_capture.py profile \
  --gate physical_x86_64_install_and_cold_boot \
  --architecture x86_64 \
  --device-count 1 \
  --output physical-evidence/physical_x86_64_install_and_cold_boot/hardware-profile.json
```

生成器只写入 `schemaVersion`、固定 `kind`、当前 `gate`、门对应的非唯一 `profileClass`、
`architecture`、`deviceCount` 和 `serialsRedacted:true`。它不记录厂商、型号、序列号或 WWN，
也不证明实验已经执行或成功；真实硬件身份和实验结果仍由现场原始附件、日志、负责人签名及人工
验收记录共同承担。禁止手写扩展字段或把另一门的画像复制过来。

该门所有现场检查真实完成后，再逐项显式生成只读 `gate-result.json`。每门允许的检查名固定在候选
绑定计划中；缺一项、重复一项或混入其他门的检查都会失败。x86 冷启动门示例：

```bash
python3 deploy/appliance/physical_acceptance_capture.py result \
  --gate physical_x86_64_install_and_cold_boot \
  --pass-check installerCompleted \
  --pass-check firstColdBootHealthy \
  --pass-check administratorLoginReady \
  --pass-check fileUploadDownloadCopyTrashVerified \
  --pass-check agentWorkbenchVerified \
  --pass-check oneGiBTransferVerified \
  --pass-check dockerControlApprovalVerified \
  --pass-check hubNineAppLifecycleVerified \
  --pass-check hubNineAppPublicEndpointsVerified \
  --pass-check paperlessOfficeOcrVerified \
  --pass-check homeAssistantLanDiscoveryVerified \
  --pass-check syncthingLanDiscoveryVerified \
  --pass-check tlsBrowserTrustVerified \
  --pass-check secureCookieVerified \
  --pass-check originAndWebSocketPolicyVerified \
  --pass-check sessionRevocationVerified \
  --pass-check approvalReplayRejected \
  --pass-check auditChainVerified \
  --pass-check dockerSocketIsolationVerified \
  --pass-check thirdPartyCspAllowlistVerified \
  --pass-check continuousRunStable \
  --pass-check hardPowerCycleRecovered \
  --output physical-evidence/physical_x86_64_install_and_cold_boot/gate-result.json
```

`result` 命令只是把负责人显式提交的逐项结论写成固定 schema；它不运行测试，也不能替代原始日志、
附件或签名。六门的 `deliveryRequirements` 并集必须精确覆盖 G1–G6，最终报告会固定输出完整
`deliveryRequirementsVerified`；漏掉任何一类时不能生成产品 ready。具体补强包括：

- x86/ARM 门都要求完整文件流、Agent 工作台、1 GiB 传输、连续运行和普通断电恢复；
- x86 门另外要求真实 TLS 浏览器信任、Secure cookie、Origin/WebSocket 策略、会话吊销、审批防重放、
  审计验链、docker.sock 隔离和第三方应用 CSP 白名单；
- 存储门要求真实拔盘/降级之外，还要验证只读卷、空间写满、重启恢复和回收站恢复；
- 协议门要求 Windows/macOS/Linux 的 SMB，以及 macOS/Linux 的 NFS，并验证用户/ACL、跨协议配额与
  大文件；
- 生命周期门要求不可变摘要升级、断电回滚、失败升级回滚、受管卸载保数据、状态恢复和审计证据导出；
  还必须在实体 Debian 13/OMV 主机上事务安装五个运维 unit，启用掉电升级恢复服务，真实触发备份与审计两个 timer，注入
  安装/启用失败并证明原 unit 与 timer 状态完整回滚，分别断开备份和审计挂载证明 fail closed，
  最后注入移除失败并证明受管 unit/timer 被完整恢复，再成功受管移除并证明没有残留 unit/timer，
  同时加密凭据、设备状态、NAS 数据、备份和审计证据均保留。

x86 与 ARM 两个设备门还必须生成固定名称、只读的
`device-endurance-lifecycle.json`。正式运维包中的 `device_endurance_lab.py` 与独立的
`hub_lifecycle_lab.py` 把两门共同的十五项
G1/G6 检查绑定到真实安装、首次冷启动、24 小时同一次启动、物理断电和恢复后的 Echo API 结果。
安装器原始终端日志可能含磁盘型号或序列信息，必须保存在证据目录之外、root 所有且 mode-0400；
实验器只把它的 SHA-256、镜像版本、安装 manifest/source 摘要和哈希后的目标身份写进计划和脱敏日志，
原始安装日志不能进入最终签名附件。

在 Recovery 环境把安装器的完整输出直接保存到私有介质，完成第一次冷启动并启动当前候选的
appliance 后，在前六小时内生成计划。x86 门属于 G4 安全验收的一部分，必须使用已经通过证书预检的
HTTPS loopback origin；ARM 门也推荐使用 HTTPS：

私有 fixture 必须恰有两个专用成员；`visibleRoots`/`hiddenRoots` 是根目录名，四个探针路径必须已存在，
其中照片路径需为可解码图片。两位成员的可见/隐藏目录应互为交叉边界：

```json
{
  "schemaVersion": 1,
  "kind": "echo.family-isolation-acceptance.v1",
  "members": [
    {
      "username": "echoaccepta",
      "password": "<独立 Echo 强密码>",
      "visibleRoots": ["FamilyShared", "AlicePrivate"],
      "hiddenRoots": ["BobPrivate"],
      "readableFile": "AlicePrivate/acceptance.txt",
      "deniedFile": "BobPrivate/acceptance.txt",
      "readablePhoto": "AlicePrivate/acceptance.jpg",
      "deniedPhoto": "BobPrivate/acceptance.jpg"
    },
    {
      "username": "echoacceptb",
      "password": "<另一独立 Echo 强密码>",
      "visibleRoots": ["FamilyShared", "BobPrivate"],
      "hiddenRoots": ["AlicePrivate"],
      "readableFile": "BobPrivate/acceptance.txt",
      "deniedFile": "AlicePrivate/acceptance.txt",
      "readablePhoto": "BobPrivate/acceptance.jpg",
      "deniedPhoto": "AlicePrivate/acceptance.jpg"
    }
  ]
}
```

```bash
sudo install -o root -g root -m 0400 /mnt/private/echo-installer-console.log \
  /root/echo-installer-console.log

# 在专用验收成员及 OMV 共享中预置这些文件；密码必须是对应成员的独立 Echo 密码。
# JSON 必须严格符合 echo.family-isolation-acceptance.v1，并为 root:root mode-0400。
sudo install -o root -g root -m 0400 /mnt/private/echo-family-isolation.json \
  /root/echo-family-isolation.json

sudo ./device_endurance_lab.py plan \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --installer-log /root/echo-installer-console.log \
  --evidence-directory "$PWD/physical-evidence/physical_x86_64_install_and_cold_boot" \
  --nas-transfer-path lab/device-endurance \
  --family-isolation-fixture /root/echo-family-isolation.json \
  --base-url https://echo.home.example \
  --main-container echo-os \
  --proxy-container echo-docker-control \
  --output /root/echo-device-endurance-lab-plan.json

sudo ./device_endurance_lab.py run \
  --plan /root/echo-device-endurance-lab-plan.json \
  --phase baseline \
  --confirm 'RUN ECHO DEVICE ENDURANCE LAB baseline <64位planId>'
```

`baseline` 会重新验证候选索引、运维包与两个执行器字节、Debian 13/OMV 8、当前容器的不可变镜像，
再实际运行管理员登录、桌面/Agent 工作台、Docker 最小权限与审批防重放，以及带主容器重启恢复的
1 GiB 上传、完整/Range 下载、取消和回收站恢复。它还会用两个专用家庭成员实际登录，逐一核对自身
账户目录、OMV 投影后的可见/隐藏根、文件下载、照片原图和成员管理拒绝。凭据文件只读取 mode-0400
原件，计划仅绑定路径、大小和 SHA-256，最终 JSON 只保留身份集/策略集摘要，不返回用户名、路径或密码。
它不会只读取一份人工结果表。

保持机器不重启运行至少 86400 秒，再以计划打印的精确确认执行 `soak`。该阶段要求 Boot ID 与
baseline 完全相同并再次执行完整 appliance 探针。随后执行 `arm-power-cut`；它只会把候选和 Boot ID
绑定的意图写入持久 journal、同步 journal 和文件系统，并生成
`device-power-cut-armed.log`，**不会替现场人员关机**。日志成功后应直接物理移除电源，再恢复供电，
不能执行 reboot、poweroff 或 Web 关机。启动后执行 `recovered`：它要求 Boot ID 改变、上一启动的
持久 journal 中恰好存在断电意图，并拒绝任何 `systemd-shutdown`、正常关机或正常重启痕迹；随后
第三次执行完整 appliance 探针。

四份固定日志收齐后生成生命周期：

```bash
python3 physical_acceptance_capture.py device-result \
  --gate physical_x86_64_install_and_cold_boot \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --lab-plan /root/echo-device-endurance-lab-plan.json \
  --lab-directory "$PWD/physical-evidence/physical_x86_64_install_and_cold_boot" \
  --output "$PWD/physical-evidence/physical_x86_64_install_and_cold_boot/device-endurance-lifecycle.json"
```

ARM 设备使用同一流程和 `supported_arm64_hardware_install_and_cold_boot` 目录。采集器会逐字段复验四份
mode-0444 日志，并把 `installerCompleted`、首次冷启动、登录、文件生命周期、Agent 工作台、1 GiB、
Docker 审批、Hub 九应用安装/卸载/保留数据重装、九个公开 HTTP 入口响应、Paperless 中文/英文 OCR
与 Office 文档解析、Home Assistant 局域网发现、Syncthing 双设备局域网自动发现、24 小时连续运行
、双成员家庭数据隔离和物理断电恢复十五项检查绑定到
固定文件的真实大小和 SHA-256。Hub 检查必须绑定同门的 `hub-lifecycle-result.json`，同时携带并复核
同门的 `hub-lifecycle-plan.json`；Paperless 检查必须绑定同门的
`paperless-functional-plan.json` 与 `paperless-functional-result.json`；Home Assistant 与 Syncthing
检查必须绑定同门的 `lan-discovery-functional-plan.json`、汇总结果和三份原始探针，不能再由普通
设备日志中的手写布尔值满足；x86 门
额外的浏览器证书信任、会话吊销、Origin/WebSocket 和第三方 CSP 等 G4 检查仍必须提供独立真实浏览器
附件，不能由这个共同生命周期替代。

`paperlessOfficeOcrVerified` 不能只看五个容器健康：必须用 3008 页面的一次性管理员密码登录，分别
导入包含可检索中文与英文的扫描 PDF，以及 DOCX、XLSX、PPTX；等待任务完成后按预置文字检索到对应
文档，再下载原文件并核对摘要。必须使用上面的候选绑定功能实验室生成机器证据；截图只能辅助复核，
不能替代固定计划/结果。证据不得记录密码、token、检索词明文、文档正文或私有宿主绝对路径。

`homeAssistantLanDiscoveryVerified` 必须在 8123 完成首次配置，并从真实物理 LAN 自动发现至少一个
mDNS 设备和一个 SSDP/UPnP 设备，再完成一次可逆状态读取或控制。容器必须回读为 host 网络但
`Privileged:false`、`CapDrop:[ALL]`、禁止提权、无 Docker socket、无 `/dev` 和 `/run/dbus` 挂载；
USB、Bluetooth、Zigbee 直通不属于当前 Hub 安全模式，不能用手工 IP 配置或虚构设备替代自动发现证据；
必须由上面的候选绑定局域网实验生成并保留三份原始探针。

G3 还必须生成固定名称、只读的 `protocol-interoperability-lifecycle.json`。正式运维包中的
`protocol_interoperability_lab.py` 不是结果表生成器：它在真实客户端上检查原生挂载身份并执行
实际文件 I/O。先为同一候选准备专用实验共享、专用允许身份、专用拒绝身份和带较小硬配额的专用
配额身份；每个通过 SMB/NFS 挂载看到的共享根都必须包含同一份
`.echo-protocol-interoperability-lab.json`。该标记固定包含当前候选 `candidateIndexId`、一个 UUIDv4
`labShareId`、`dedicatedLabShare:true` 和固定 `markerName`，不能包含口令或登录凭据。每次探针开始前，
挂载根目录必须只含这一个标记；发现任何已有文件都会拒绝运行，避免把生产共享误当实验共享。

在 NAS/实验协调机上，从候选运维包生成只读计划；`physical-evidence/...` 对应目录在生成计划时必须
为空：

```bash
./protocol_interoperability_lab.py plan \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --server echo-nas.lan \
  --lab-share-id 11111111-2222-4333-8444-555555555555 \
  --evidence-directory "$PWD/physical-evidence/external_smb_and_nfs_client_interoperability" \
  --output /root/protocol-interoperability-lab-plan.json
```

把候选包内同一字节的执行器和计划带到客户端，分别执行 `windows-smb`、`macos-smb`、`linux-smb`、
`macos-nfs`、`linux-nfs` 五个角色。每个角色都要求对应的原生 SMB2/SMB3、CIFS、smbfs、NFS 或
NFSv4 挂载来自计划中的服务器，然后真实写入并 `fsync` 8 MiB、读回摘要、重命名、再次读回和删除。
例如 Linux SMB 客户端：

```bash
./protocol_interoperability_lab.py probe \
  --plan /root/protocol-interoperability-lab-plan.json \
  --role linux-smb \
  --mount /mnt/echo-smb-rw \
  --confirm 'RUN ECHO PROTOCOL LAB linux-smb <64位planId>' \
  --output /root/protocol-linux-smb.log
```

Windows 角色读取 `Get-SmbConnection` 的真实 SMB2/SMB3 连接；macOS 读取系统挂载表；Linux 读取
`findmnt`。输出只保留原生证据摘要、哈希后的客户端身份和 I/O 结果，不保留主机名、共享口令或用户
凭据。把五份固定名称日志原样收回计划中的证据目录。

随后在 Linux 客户端执行三个政策阶段：`permissions` 必须在 SMB/NFS 的允许身份上成功写入，同时在
两种协议的拒绝身份上得到 `EACCES`、`EPERM` 或只读拒绝；`quota` 会在专用配额身份上经 SMB 连续写入
真实数据，最多 1 GiB，必须得到 `EDQUOT`/`ENOSPC`，随后同一账户经 NFS 也必须立即被拒绝；
`large-file` 会经 SMB 完整写入 1 GiB 非稀疏文件、经 NFS 流式读回同一 SHA-256，再经 NFS 删除并由
SMB 观察删除。三个阶段都有独立确认口令，所有探针都在 `finally` 中清理自己的固定随机路径，但只能
在专用实验共享上运行，不能使用家庭生产共享或真实用户配额。

八份日志收齐后，由执行器本身生成生命周期绑定：

```bash
./protocol_interoperability_lab.py verify \
  --plan /root/protocol-interoperability-lab-plan.json \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --evidence-directory "$PWD/physical-evidence/external_smb_and_nfs_client_interoperability" \
  --output "$PWD/physical-evidence/external_smb_and_nfs_client_interoperability/protocol-interoperability-lifecycle.json"
```

`verify` 会重新读取 root 所有 mode-0444 候选索引，并复核当前解包运维包的 artifact、镜像、清单和
两个实验执行器字节；旧候选、自改计划或被替换的工具不能生成生命周期。最终验收器要求八个固定
检查分别绑定八个固定日志的真实名称、大小和 SHA-256，并要求生命周期中的候选 OS、Agent、运维包
和 `planId` 与当前候选一致。缺任一操作系统/协议、把某个检查绑定到另一份
日志、篡改字节或只手写 `gate-result.json`，都不能通过 G3。

G2 还必须生成固定名称、只读的 `storage-recovery-lifecycle.json`。正式运维包中的
`storage_recovery_lab.py` 只允许 Linux root 在 Debian 13 + OMV 8 上运行，并固定要求：一个
4–64 GiB、当前健康读写、除授权标记外为空的专用 RAID1 测试卷；两块不同物理盘；一个明确的牺牲
成员盘；root 所有且 mode-0444 的 `.echo-storage-recovery-lab.json`。标记必须写明
`schemaVersion:1`、`kind:"echo.storage-recovery-lab-authorization"`、`disposable:true`、当前候选
`candidateIndexId`、精确阵列设备、精确挂载点和 UUIDv4 `labVolumeId`。不要让工具替你创建授权标记：
现场负责人应先核对设备拓扑和数据副本，再把审查过的标记安装进空测试卷。

```bash
sudo install -o root -g root -m 0444 reviewed-storage-lab-marker.json \
  /mnt/echo-storage-lab/.echo-storage-recovery-lab.json

sudo ./storage_recovery_lab.py plan \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --array /dev/md7 \
  --member /dev/sdb1 \
  --member /dev/sdc1 \
  --sacrificial-member /dev/sdc1 \
  --mountpoint /mnt/echo-storage-lab \
  --evidence-directory "$PWD/physical-evidence/real_disk_smart_and_raid_degradation_recovery" \
  --nas-transfer-path lab/storage-recycle-probe.bin \
  --base-url http://127.0.0.1:8000 \
  --output /root/echo-storage-recovery-lab-plan.json

# 先执行 baseline；按现场步骤物理拔下 plan 指定的牺牲盘，再执行 degraded/readonly/volume-full；
# 接回同一块盘后执行 reconnect，等待实际重建完成再执行 rebuild；真实重启后执行 reboot；
# 最后由 Echo 文件 API 执行 1 GiB 上传、下载、回收站删除、恢复、重新下载校验和再次进入回收站：
sudo ./storage_recovery_lab.py run \
  --plan /root/echo-storage-recovery-lab-plan.json \
  --phase baseline \
  --confirm 'RUN ECHO STORAGE RECOVERY LAB baseline <64位planId>'

python3 physical_acceptance_capture.py storage-result \
  --gate real_disk_smart_and_raid_degradation_recovery \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --lab-plan /root/echo-storage-recovery-lab-plan.json \
  --lab-directory "$PWD/physical-evidence/real_disk_smart_and_raid_degradation_recovery" \
  --output "$PWD/physical-evidence/real_disk_smart_and_raid_degradation_recovery/storage-recovery-lifecycle.json"
```

八阶段固定生成 `storage-baseline.log`、`storage-degraded.log`、`storage-readonly.log`、
`storage-volume-full.log`、`storage-reconnect.log`、`storage-rebuild.log`、`storage-reboot.log` 和
`storage-recycle-restore.log`。每次 `run` 都重新校验候选索引、运维包清单/执行器字节、平台、授权
标记和适用阶段的完整设备身份；计划和阶段日志分别固定为 mode-0400/0444。写满测试从 256 MiB
逐级缩小到 1 MiB，只接受明确 ENOSPC/配额耗尽，并用 1 MiB 实际写入再次确认拒绝。G2 manifest
必须把八份日志、生命周期 JSON、画像、gate result、主日志和其他声明附件全部传给
`build --artifact`；缺阶段、替换字节、跨计划或仅手写九项 `true` 都会失败。该过程会真实填充卷、
重挂载文件系统并操作阵列，只能在可丢弃专用实验机运行，不能在家庭生产 NAS 上尝试。

x86 与 ARM 冷启动门的签名实验时间都不得少于 24 小时；计划会写出
`minimumDurationSeconds:86400`，采集器和最终验收器分别复算。其他门仍按真实实验持续时间记录，
但全部限制在 1 秒至 7 天内。缩短时间戳不能把启动烟测伪装成连续运行验收。

特别地，断电更新/状态恢复门固定要求 `externalBackupVerified`、
`missingBackupMountFailedClosed` 与 `missingAuditMountFailedClosed`，裸机恢复门固定要求
`offDeviceBackupRestored`。因此同盘目录备份、任一运维挂载掉线后回落系统盘，或从未在另一台设备
恢复，都不能满足最终产品门。`operationsSystemdInstalled`、`backupTimerTriggered`、
`auditTimerTriggered` 等检查必须来自同一候选的物理门日志与附件；Debian 容器中的
`systemd-analyze verify` 报告不能替代它们。

G5 还必须生成固定名称、只读的 `operations-systemd-lifecycle.json`。每个生命周期检查只能提交一次，
并必须绑定同一证据目录里已经存在的脱敏日志或附件；采集器会安全读取文件并写入实际名称、大小与
SHA-256，不能手填摘要、引用目录外文件、链接、保留清单文件或其他 gate 的检查名。下面的文件名是
推荐拆分，实际可以让多个检查引用同一份包含完整阶段记录的日志，但引用的字节必须真实存在并在
随后 `build --artifact` 中声明：

正式运维包现已携带 `operations_systemd_lab.py`，用于在专用 Debian 13 + OMV 8 验收机上产生这些
日志。它拒绝非 root、其他发行版、OMV 非 8.x、已有受管 unit/timer、共用或嵌套的备份/审计挂载、
不安全宿主工具和缺失的四类保留样本。`plan` 输出 mode-`0400` 的摘要绑定计划和八个不同确认语；
同时复核发布候选索引、索引中声明的运维包制品 ID/归档摘要/不可变镜像，以及当前解包目录的
`bundle-manifest.json` 和执行器字节；因此旧候选、同镜像但不同运维包或被替换的实验脚本都不能
继续生成可采纳日志。
每个 `run --phase` 只能按顺序执行，既有阶段日志必须是同一个 planId 的 mode-`0444` 严格 JSON。
安装/卸载故障通过包装真实 `/usr/bin/systemctl` 的单次预定失败注入，其他调用仍进入真实 systemd；
挂载掉线阶段会停止对应 timer、卸载指定测试挂载、要求 service 非零失败且系统盘无回落文件，再
重新挂载、复核原存储身份并恢复 timer。这个工具具有真实卸载/挂载破坏性，只能使用专用实验设备和
具备其他副本的测试介质，不能在家庭生产 NAS 上试跑。

计划示例（四个 `--preserve` 必须分别指向预先存在、可公开摘要且整个实验中不应变化的测试文件）：

先把候选索引安装成 root 所有的只读文件（不要直接使用普通用户可改写的下载副本）：

```bash
sudo install -o root -g root -m 0444 \
  /path/to/downloaded/echo-delivery-release-evidence-index.json \
  /root/echo-candidate/echo-delivery-release-evidence-index.json
```

```bash
sudo ./operations_systemd_lab.py plan \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --backup-directory /mnt/echo-backup/echo-os \
  --backup-mountpoint /mnt/echo-backup \
  --audit-directory /mnt/echo-audit/evidence \
  --audit-mountpoint /mnt/echo-audit \
  --backup-credential /etc/credstore.encrypted/echo-backup-passphrase \
  --audit-credential /etc/credstore.encrypted/echo-audit-export-passphrase \
  --evidence-directory "$PWD/physical-evidence/power_loss_during_update_and_state_restore" \
  --preserve deviceState=/root/echo-lab-preserve/device-state.sample \
  --preserve NASData=/root/echo-lab-preserve/nas-data.sample \
  --preserve stateBackups=/root/echo-lab-preserve/state-backup.sample \
  --preserve auditEvidence=/root/echo-lab-preserve/audit-evidence.sample \
  --output /root/echo-operations-systemd-lab-plan.json

# 按 plan 打印顺序逐阶段执行，并逐字复制该阶段确认语；两个 observe 阶段必须等待生产 timer
# 在真实日历时间触发，不能用手工 start service 代替：
sudo ./operations_systemd_lab.py run \
  --plan /root/echo-operations-systemd-lab-plan.json \
  --phase install-rollback \
  --confirm 'RUN ECHO OPERATIONS LAB install-rollback <64位planId>'
```

八阶段全部完成后，可以直接使用固定输出映射，不再手写九个 `--evidence`：

```bash
python3 physical_acceptance_capture.py operations-result \
  --gate power_loss_during_update_and_state_restore \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --lab-plan /root/echo-operations-systemd-lab-plan.json \
  --lab-directory "$PWD/physical-evidence/power_loss_during_update_and_state_restore" \
  --output "$PWD/physical-evidence/power_loss_during_update_and_state_restore/operations-systemd-lifecycle.json"
```

`--lab-directory` 会先把私有 lab plan 重新绑定到同一候选索引和 G5 证据目录，再严格校验八份日志
的 schema、mode、候选绑定的同一 planId 和逐阶段结果字段，最后把其中九项检查映射到生命周期文件；
生命周期文件自身也携带候选、运维制品和 lab plan 身份。任一日志来自旧候选/其他计划、为手写占位、
`false`、错摘要或不完整细节都会失败。
也可使用下面的逐项形式审查映射：

```bash
python3 physical_acceptance_capture.py operations-result \
  --gate power_loss_during_update_and_state_restore \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --lab-plan /root/echo-operations-systemd-lab-plan.json \
  --evidence operationsSystemdInstalled=physical-evidence/power_loss_during_update_and_state_restore/operations-install.log \
  --evidence operationsSystemdInstallRollbackVerified=physical-evidence/power_loss_during_update_and_state_restore/operations-install-rollback.log \
  --evidence backupTimerTriggered=physical-evidence/power_loss_during_update_and_state_restore/backup-timer.log \
  --evidence auditTimerTriggered=physical-evidence/power_loss_during_update_and_state_restore/audit-timer.log \
  --evidence missingBackupMountFailedClosed=physical-evidence/power_loss_during_update_and_state_restore/backup-mount-loss.log \
  --evidence missingAuditMountFailedClosed=physical-evidence/power_loss_during_update_and_state_restore/audit-mount-loss.log \
  --evidence operationsSystemdRemovalLeftNoUnitsOrTimers=physical-evidence/power_loss_during_update_and_state_restore/operations-remove.log \
  --evidence operationsSystemdRemovalPreservedCredentialsAndData=physical-evidence/power_loss_during_update_and_state_restore/operations-remove.log \
  --evidence operationsSystemdRemovalRollbackVerified=physical-evidence/power_loss_during_update_and_state_restore/operations-remove-rollback.log \
  --output physical-evidence/power_loss_during_update_and_state_restore/operations-systemd-lifecycle.json
```

systemd 生命周期完成并保持 recovery service 已启用、未运行后，再使用同一候选运维包执行真正的
断电升级和状态恢复实验。计划要求当前两个容器仍运行在旧的不可变 digest；1 MiB device-state canary
必须直接位于 `$PWD/data/`，1 GiB NAS canary 必须位于专用测试 NAS 数据集，二者都必须是 root 所有、
mode-`0600` 的普通文件。备份路径和加密凭据沿用上面的 systemd lab plan。该实验会停止在升级已持久
选择候选镜像、尚未执行 Compose 的边界，只有看到 `arm-power-cut` 成功后才能人工断开并恢复整机电源：

```bash
# 新候选包自带候选 digest 作为首次安装种子。断电升级实验必须先把这个可变选择对齐到当前两个
# 运行容器的旧 digest。第一次不带 --confirm 只打印候选、旧/新 digest 和精确确认语，不写文件：
sudo ./power_state_recovery_lab.py seed \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD"

sudo ./power_state_recovery_lab.py seed \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --confirm 'SEED ECHO POWER STATE <candidate-indexId> FROM <旧的registry@sha256:digest>'

sudo ./power_state_recovery_lab.py plan \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --bundle-root "$PWD" \
  --operations-lab-plan /root/echo-operations-systemd-lab-plan.json \
  --evidence-directory "$PWD/physical-evidence/power_loss_during_update_and_state_restore" \
  --state-canary "$PWD/data/power-state-canary.bin" \
  --nas-canary /srv/echo-nas-lab/power-state-canary.bin \
  --output /root/echo-power-state-lab-plan.json

# 严格按 plan 中七个阶段及确认语执行。arm-power-cut 返回后立即人工断电；恢复供电并启动后，
# 才执行 recover-power-cut。其余阶段继续验证正常升级、失败回滚、受管卸载/重装和加密状态恢复。
sudo ./power_state_recovery_lab.py run \
  --plan /root/echo-power-state-lab-plan.json \
  --phase baseline \
  --confirm 'RUN ECHO POWER STATE LAB baseline <64位planId>'

sudo ./power_state_recovery_lab.py verify \
  --plan /root/echo-power-state-lab-plan.json \
  --evidence-directory "$PWD/physical-evidence/power_loss_during_update_and_state_restore"

python3 physical_acceptance_capture.py power-result \
  --gate power_loss_during_update_and_state_restore \
  --candidate-index /root/echo-candidate/echo-delivery-release-evidence-index.json \
  --lab-plan /root/echo-power-state-lab-plan.json \
  --lab-directory "$PWD/physical-evidence/power_loss_during_update_and_state_restore" \
  --output "$PWD/physical-evidence/power_loss_during_update_and_state_restore/power-state-lifecycle.json"
```

`power-result` 和最终验收器都会重新解析七份固定 mode-`0444` 日志：旧/新镜像摘要、断电前后不同
Boot ID、持久 journal 中唯一的断电意图、没有正常关机标记、启动恢复 service 成功、升级事务消失、
两个容器回到旧 digest、正常升级提交、注入失败后仍保持候选 digest、Compose 卸载未删除 volume、
只读恢复预检、外部加密备份及两个 canary 未变都必须同时成立。普通 reboot、手写 `true`、缩短阶段、
遗漏 baseline/arm 日志或只提交最终恢复日志都不能生成生命周期文件。

G5 的 manifest 必须把 `operations-systemd-lifecycle.json`、`power-state-lifecycle.json` 及二者引用的
每一份日志都作为 `--artifact` 提交。采集器和最终验收器
会再次按 manifest 的实际附件记录复核所有引用；删除日志、替换日志后只重算 JSON、改成另一个附件
摘要、把 `passed` 改成整数 `1`，或仅提供 `gate-result.json` 都不能晋级。最终产品交付报告因此升级为
schema 2；旧 schema 1 报告不能冒充包含机器绑定 G5 证据的新交付结果。

取得画像 SHA-256 后再创建只读 manifest：

```bash
python3 deploy/appliance/physical_acceptance_capture.py build \
  --candidate-index echo-delivery-release-evidence-index.json \
  --gate physical_x86_64_install_and_cold_boot \
  --architecture x86_64 \
  --hardware-profile-sha256 <脱敏硬件画像SHA-256> \
  --device-count 1 \
  --lab-run-id <UUIDv4> \
  --started-at 2026-08-26T01:00:00Z \
  --finished-at 2026-08-27T02:00:00Z \
  --primary-log physical-evidence/physical_x86_64_install_and_cold_boot/acceptance.log \
  --artifact physical-evidence/physical_x86_64_install_and_cold_boot/acceptance.log \
  --artifact physical-evidence/physical_x86_64_install_and_cold_boot/gate-result.json \
  --artifact physical-evidence/physical_x86_64_install_and_cold_boot/hardware-profile.json \
  --output physical-evidence/physical_x86_64_install_and_cold_boot/evidence.json

gpg --batch --local-user <完整验收密钥指纹> --detach-sign \
  --output physical-evidence/physical_x86_64_install_and_cold_boot/evidence.json.gpg \
  physical-evidence/physical_x86_64_install_and_cold_boot/evidence.json
```

capture 工具只做确定性绑定，不接触私钥，也不会代替测试程序产生成功 marker。它强制逐门携带
内容完全匹配固定检查集合且全部为 `true` 的 `gate-result.json`，并拒绝目录外附件、符号链接、
未声明文件、错架构、少设备、画像字段漂移、非 UUIDv4、非规范 UTC 时间、少于 1 秒或
超过 7 天的实验、重复 marker，以及文本中的序列号、WWN、`/dev/disk/by-id`、密码、token、secret
或 Authorization。`startedAt` / `finishedAt` 必须包含秒，使用 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`。
每个文件夹最终只能包含
`evidence.json`、`evidence.json.gpg` 和 manifest 列出的附件。自动敏感信息扫描只覆盖 UTF-8 文本
附件；声明的画像 SHA 必须逐字节匹配 `hardware-profile.json`，画像内容必须再次通过同一严格
schema 与 gate/架构/设备数绑定校验，逐项结果也会在采集和最终验收两个阶段分别重验。G5 还会
强制复核 `operations-systemd-lifecycle.json` 中九个生命周期检查与实际附件名称、大小、摘要的逐项
绑定。图片、视频、
压缩包等二进制附件必须在进入证据目录前人工
脱敏并复核。

六项签名完成后执行最终晋级：

```bash
python3 deploy/appliance/physical_acceptance.py \
  --candidate-index echo-delivery-release-evidence-index.json \
  --evidence-root "$PWD/physical-evidence" \
  --acceptance-keyring echo-physical-acceptance-keyring.gpg \
  --output echo-nas-product-delivery-release.json
```

验证器对六份 manifest 实际执行 public-only `gpgv`，从每次 `VALIDSIG` 状态中提取完整签名指纹，
要求同一个 acceptance keyring、同一个 signer fingerprint、同一个候选 `indexId`、OS/Agent SHA
和标签，且六门各用一个不同的门级 `labRunId`，并逐字节复算所有日志与附件。只有全部通过且六门
映射精确覆盖 G1–G6，才输出 `ECHO_NAS_PRODUCT_DELIVERY_READY`、完整
`deliveryRequirementsVerified` 和 `nasProductDeliveryReady:true`；缺任一门或任一 G 时不会生成部分通过
报告。交付时必须同时归档候选索引、六个完整证据目录、public keyring、两个 verifier 和最终报告，
使接收方可以离线重跑，而不是只相信最终布尔值。

不要再手工复制这些文件拼“最终包”。保留下载并通过 OIDC/离线回放的完整候选 artifact 目录，
然后由产品交付打包器复制全部字节并在复制后再次回放候选与六门验收：

```bash
mkdir -p "$PWD/dist"
candidate_bundle="$PWD/echo-delivery-release-candidate-<标签>"
python3 "$candidate_bundle/product_delivery_bundle.py" build \
  --candidate-bundle "$candidate_bundle" \
  --evidence-root "$PWD/physical-evidence" \
  --acceptance-keyring "$PWD/echo-physical-acceptance-keyring.gpg" \
  --output-directory "$PWD/dist"

cd "$PWD/dist/echo-nas-product-delivery-<报告ID前16位>"
python3 tools/product_delivery_bundle.py verify "$PWD"
```

最终目录固定包含完整候选审计包、六门证据、public-only acceptance keyring、最终产品报告以及离线
工具。所有载荷进入自校验 manifest，目录名绑定产品 `reportId`；文件只读、工具只执行，额外路径、
链接、设备、错权限、篡改、回放期间变化或“重新计算清单但产品报告不等于六门结果”都会失败。
打包器有 512 文件、单文件 2 GiB、总计 8 GiB 的显式上限，只复制普通非链接文件，不读取或携带
验收私钥。接收方的成功结果必须同时给出 `bundleId`、`productReportId`、候选 `indexId` 和完整
signer fingerprint，不能只看目录名称。

```bash
git clone https://github.com/dengdenghua/echo-os.git
cd echo-os

# 从当前同一源码快照生成统一 wheel、运行资源、Linux Codex 与完整性清单
make agent-bundle

cd deploy/appliance
cp appliance.env.example appliance.env
# 编辑 appliance.env，并设置一次性生成后长期保存的 ECHO_DOCKER_PROXY_TOKEN：
# python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
docker compose --env-file appliance.env up -d --build
```

准备阶段需要 Python 3.11+、Node 20、pnpm，以及仓库固定的 `uv 0.11.25`；脚本会优先使用
OS 仓库的 `.venv`。
正式包要求当前 Echo OS 是干净工作树。开发中确需验证未提交内容，可运行
`make agent-bundle-local`：脚本会先在仓库外冻结 QA 快照，清单
明确写入 `dirty: true`，不会把它误当发布包。

Agent 运行时已经内建；发布只读取当前 Echo checkout，不再准备第二份 Agent 源码。
交付预检只接受当前 OS 的干净完整 commit，并在线证明该 commit 精确位于受审交付分支。

生成物都在 `deploy/appliance/`（不入 Git）：

- `agent-dist/`：实际 Python distribution 的 wheel、直接安装清单，以及构建/运行依赖锁；
- `agent-resources/`：与该 wheel 同源的 agents/skills/prompts/protocols/teams；
- `agent-codex/`：由 Agent 自身锁定并校验的 Linux x86-64 Codex 可执行包；
- `agent-bundle.json`：Git 来源、版本、文件数和 SHA-256 总清单。

仓库内的 `build-requirements.lock`、`runtime-requirements.lock` 与
`python-dependency-lock.json` 是正式发行输入：它们绑定统一 `pyproject.toml`、Agent
extras、Python 3.12、uv 版本以及 `linux/amd64`、`linux/arm64`。两种架构必须解析出字节完全
相同的锁，且每个包必须精确固定版本、只允许二进制分发并携带 SHA-256。正式 clean source 会
约束重放这些锁；dirty QA 快照只在临时 staging 中生成独立锁，不会覆盖正式锁。

Docker 构建会先重算三类 Agent 制品哈希和 Python 锁哈希，按 `--require-hashes` 安装完整依赖
闭包，再以 `--no-deps` 安装已验证的统一 wheel；构建后核对 Python 版本和
`echo-agent` 入口点。构建后端只存在于中间层，不进入运行镜像。容器
启动时还会核对 runtime、resources 与 Codex 身份。任一处混版都会失败，不会静默回退。
容器入口会先生成/读取同一份设备管理员凭据，并注入 Agent 的运行配置；因此 Agent 的
公网监听安全门和 Echo OS 登录共用一个密码/JWT，不会出现两套登录。生成的 YAML 只保存
一次性环境引用，不直接复制 bcrypt/JWT 原值；这样也避免 Agent 的通用环境插值把 bcrypt
盐中偶然出现的 `$ABC` 片段误替换，造成“文件里密码正确、启动后永远登录失败”。
GPU 与视频加速依赖仍是独立可选项，不塞进通用 NAS 镜像。

浏览器打开 `http://<NAS_IP>:8000/#/desktop` —— 极光壁纸的原生桌面,
Dock 里"本地应用"段会列出宿主上已装的 Docker 应用(运行中带绿点,
已停止的点击即启动),点击运行中的应用在新标签打开它的 Web UI。

## 生产 HTTPS 入口

正式设备不要把裸 HTTP 8000 发布到局域网以外。仓库提供独立 TLS overlay：它使用固定 OCI
index 摘要的官方 Nginx `1.28.0-alpine-slim`（同时锁定 linux/amd64 与 linux/arm64 子清单），
只发布 80/443；Echo 后端的兼容端口会在 Compose 解析前强制绑定到宿主 `127.0.0.1`。TLS
容器只连接默认应用网络，不进入持有 Docker 控制代理的 internal network；它是只读文件系统、
删除全部 Linux capabilities、启用 `no-new-privileges`，访问日志不记录 query string。

先为设备 DNS 名签发证书。`echo.crt` 必须是包含完整链的 PEM，`echo.key` 必须是无需交互解密的
匹配私钥：

```bash
cd deploy/appliance
install -m 0644 /secure/export/echo-fullchain.pem tls/echo.crt
install -m 0600 /secure/export/echo-private-key.pem tls/echo.key

ECHO_TLS_HOST=echo.home.example ./start-tls.sh --build
```

启动脚本会在任何 Compose 写操作前验证：两个输入均为普通文件且不是符号链接、私钥权限恰为
0400/0600、证书和密钥能够解析且互相匹配、证书至少还有七天有效期，以及 SAN 精确覆盖
`ECHO_TLS_HOST`。主机名、Origin、可信代理 IP 和后端回环监听由脚本显式注入；通配 host/origin
会被拒绝。HTTP 端口只做 308 跳转，HTTPS 网关保留上传、下载、SSE 与 WebSocket 流，不在边缘
缓冲多 GB 请求或响应。

若使用企业内网 CA，先让浏览器和验收主机信任该 CA。启动后用正式运行验收器验证 TLS 下确实
签发 Secure + HttpOnly + SameSite=Lax Cookie，并复跑登录、Origin、WebSocket、审批和审计链：

```bash
cd ../..
SSL_CERT_FILE=/secure/export/echo-ca.pem \
ECHO_ADMIN_PASSWORD='从安全输入注入，不写入命令历史' \
uv run python deploy/appliance/verify-running-appliance.py \
  --base-url https://echo.home.example
```

不要把示例密码原样用于 shell；正式验收应由秘密管理器向环境注入。私钥和证书路径已在
`deploy/appliance/tls/.gitignore` 中排除。默认不强开 HSTS，因为家庭私有 CA 尚未被所有设备
信任时，HSTS 会把恢复入口一起锁死；只有证书信任和应急域名均完成后才应在网关中启用。

## Tailscale 私网远程访问（可选）

需要跨网络访问 Echo 时，可使用官方 Tailscale 侧车。它以 userspace 模式运行，不需要 TUN、
NET_ADMIN、host network 或宿主端口；Tailscale Serve 只把 Tailnet 内的 HTTPS 443 反代到
`echo-os:8000`。官方镜像同时固定 tag、OCI index 以及 linux/amd64、linux/arm64 子摘要。

先在 Tailnet 管理页开启 HTTPS，创建一次性、预授权、非可复用的设备 auth key，并确认设备完整
MagicDNS 名，例如 `echo-os.example.ts.net`。key 文件不得带换行：

```bash
cd deploy/appliance
install -d -m 0700 remote-access/private
printf %s '从安全输入取得的 tskey-auth-…' > remote-access/private/tailscale-auth.key
chmod 0600 remote-access/private/tailscale-auth.key

ECHO_TAILSCALE_DNS_NAME=echo-os.example.ts.net \
  ./start-remote-access.sh remote-access/private/tailscale-auth.key --build
```

脚本在任何 Compose 写操作前拒绝非 `*.ts.net` 精确主机名、符号链接、错误权限、过长、带换行或
不是 `tskey-auth-` 形状的 key。key 只以 `file:/run/secrets/tailscale-auth-key` 交给侧车，不会进入
Echo 环境、API、审计或输出。首次授权后 Tailscale 状态持久化在独立卷中，`TS_AUTH_ONCE=true`
避免每次重启重复登录；一次性 key 用毕即会在控制面失效，仍应保留受限文件直至完成恢复演练。

“设备连接 → 远程访问”会显示私网健康与精确 HTTPS 地址。overlay 只开放 Echo 的认证 HTTPS
表面，因此远程网页与已挂载的设备文件/照片备份 API 可用；Tentacle 8765 仍限局域网。设备必须先
使用一机一凭据配对，再由管理员按设备、按照片/文件分别授权。网页、设备控制和两类备份继续作为
独立 feature 展示，不能用私网健康冒充未挂载的能力。

创建配对邀请时，Echo 会把可用的自动备份基址写入深链的 `sync` 参数：已连通的 Tailscale HTTPS
优先，否则仅在已配置 `ECHO_DEVICE_SYNC_PORT` 时使用 `http://<NAS_IP>:<port>`。移动端不需要
从 Tentacle 8765 猜测 Web 端口；若宿主映射了非默认端口，应同步修改该变量。

基础 Compose 还会把 `ECHO_DEVICE_LINK_PORT` 同号发布到宿主。上游 Agent 的共享 Tentacle 在
appliance 运行配置中被强制关闭，实际监听器只有管理员在桌面批准“开启连接”后才启动。二维码优先
采用当前桌面请求中的 RFC1918 IP/局域网主机名；若浏览器通过公网域名、localhost 或多层反代访问，
应在 `appliance.env` 将 `ECHO_DEVICE_LINK_HOST` 设置为手机能访问的 NAS 局域网地址。

## 配置项(环境变量,均可选)

| 变量                                                                             | 默认                             | 说明                                                                               |
| -------------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------- |
| `PORT`                                                                           | `8000`                           | 对外端口                                                                           |
| `ECHO_DEVICE_LINK_PORT`                                                       | `8765`                           | 手机 Tentacle 端口；容器内外同号，只有管理员批准 Device Link 后才监听              |
| `ECHO_DEVICE_LINK_BIND_ADDRESS`                                               | `0.0.0.0`                        | Tentacle 宿主绑定地址；只应绑定可信局域网接口                                       |
| `ECHO_DEVICE_LINK_HOST`                                                       | 空                               | 可选的手机可达 RFC1918 IP、单标签 LAN 名或 `*.local`；复杂反代/远程打开桌面时设置   |
| `ECHO_DEVICE_SYNC_PORT`                                                       | `8000`                           | 写入手机配对深链的局域网备份端口；非默认宿主映射时应与手机实际访问端口一致         |
| `ECHO_BIND_ADDRESS`                                                           | `0.0.0.0`                        | 后端宿主绑定地址；`start-tls.sh` 固定为 `127.0.0.1`，防止绕过 TLS 网关             |
| `PUID` / `PGID`                                                                  | `1000`                           | 拥有宿主 data 与 NAS 共享目录的用户/组数字 ID；主进程启动后降为此身份              |
| `ECHO_ADMIN_PASSWORD`                                                         | 空                               | 管理员登录密码(用户名固定 `admin`);**不设则首启随机生成并打印到容器日志**          |
| `ECHO_APPLIANCE_TRUSTED_HOSTS`                                                | 空                               | 反代/FQDN 主机名白名单，逗号分隔；私网 IP、localhost、`*.local`、单标签 LAN 名免配 |
| `ECHO_APPLIANCE_TRUSTED_ORIGINS`                                              | 空                               | 反代后的公开 HTTP(S) origin 白名单，逗号分隔且必须精确到 scheme/port               |
| `ECHO_APPLIANCE_FRAME_ORIGINS`                                                | 空                               | 允许嵌入 Echo OS 窗口的额外 HTTP(S) origin；默认只允许同源，不接受路径或 `*`       |
| `ECHO_APPLIANCE_CONNECT_ORIGINS`                                              | 空                               | 浏览器可直连的额外 HTTP(S)/WS(S) origin；默认只允许同源与本机同源 WebSocket        |
| `ECHO_TRUSTED_PROXY_IPS`                                                      | `127.0.0.1`                      | 可发送可信 `X-Forwarded-*` 的直连代理 IP/CIDR；不要设为 `*`                        |
| `ECHO_TLS_HOST`                                                                  | 无                               | TLS 证书 SAN 覆盖的精确 DNS 名或 IPv4；使用 `start-tls.sh` 时必填                  |
| `ECHO_TLS_HTTP_PORT` / `ECHO_TLS_HTTPS_PORT`                                     | `80` / `443`                     | TLS overlay 发布的跳转端口和 HTTPS 端口                                            |
| `ECHO_TLS_PROXY_IP` / `ECHO_TLS_SUBNET`                                          | `172.30.90.2` / `172.30.90.0/24` | 固定可信代理 IP 与专用应用网络；冲突时成对调整                                     |
| `ECHO_TAILSCALE_DNS_NAME`                                                        | 无                               | Tailscale Serve 的精确 `*.ts.net` MagicDNS 名；远程 overlay 必填                   |
| `ECHO_TAILSCALE_AUTHKEY_FILE`                                                    | 无                               | 0400/0600 一次性 auth-key 文件绝对路径；启动脚本由首个参数设置                     |
| `ECHO_TAILSCALE_HOSTNAME`                                                        | `echo-os`                        | Tailnet 内申请的设备短名                                                           |
| `ECHO_TAILSCALE_PROXY_IP` / `ECHO_TAILSCALE_SUBNET`                              | `172.30.91.2` / `172.30.91.0/24` | 远程侧车的固定可信代理 IP 与专用网络；冲突时成对调整                               |
| `NAS_STORAGE`                                                                    | `./storage`                      | 挂进桌面文件区的宿主共享目录(如 `/DATA` / `/volume1`)                              |
| `ECHO_NAS_OMV_SHARED_FOLDER_REF`                                              | 空                               | `NAS_STORAGE` 直接绑定单个 OMV 共享目录时填写其 UUID；绑定文件系统根时留空          |
| `ECHO_UPLOAD_RESERVE_BYTES`                                                   | `536870912`                      | 上传不得侵占的磁盘保留空间（默认 512 MiB）                                         |
| `ECHO_UPLOAD_MAX_BYTES`                                                       | `53687091200`                    | 单文件上传硬上限（默认 50 GiB；multipart 终态仍受下述边界限制）                    |
| `ECHO_UPLOAD_STALE_SECONDS`                                                   | `86400`                          | 崩溃遗留上传临时文件的最短清理年龄（默认 24 小时）                                 |
| `ECHO_UPLOAD_MAX_SESSIONS`                                                    | `64`                             | 同时保留的可恢复上传会话上限；前端多文件队列默认逐个传输                           |
| `ECHO_SHARE_QUOTAS_JSON`                                                      | 空                               | Echo 文件入口的共享目录逻辑字节配额，例如 `{"family":536870912000}`；空值表示关闭  |
| `ECHO_OMV_SOCKET`                                                             | 空                               | 可选 OMV 宿主受限桥 Unix socket；OMV 部署请使用 `deploy/omv` override              |
| `ECHO_OMV_ADMIN_URL`                                                          | 空                               | OMV 官方管理页 HTTP(S) origin；只用于“在 OMV 中管理”链接，不接收其密码             |
| `ECHO_OMV_HEALTH_INTERVAL_SECONDS`                                            | `300`                            | OMV 持续健康轮询周期；仅 OMV override 使用，允许 60–86400 秒                       |
| `ECHO_OMV_TEMP_WARNING_C` / `ECHO_OMV_TEMP_CRITICAL_C`                     | `50` / `60`                      | 磁盘温度提醒/严重阈值                                                              |
| `ECHO_OMV_CAPACITY_WARNING_PERCENT` / `ECHO_OMV_CAPACITY_CRITICAL_PERCENT` | `90` / `95`                      | 卷容量提醒/严重阈值                                                                |
| `ANTHROPIC_API_KEY`                                                              | 空                               | 配上才有对话 Agent;桌面/启动器/文件不需要                                          |
| `ECHO_PM_URL`                                                                 | 空                               | 企业版地址。配上后 Agent 获得 PM 工具(列项目/建任务),能在企业版里操作项目管理      |
| `ECHO_PM_TOKEN`                                                               | 空                               | 企业版登录 JWT(`Authorization: Bearer`)                                            |
| `ECHO_PM_TENANT`                                                              | 空                               | 企业版租户 ID(`X-Tenant-ID`;单租户可留空)                                          |
| `ECHO_LOG_LEVEL`                                                              | `INFO`                           | 日志级别                                                                           |

### 共享目录配额（可选）

家庭成员的数据权限只来自 OMV 的公开共享目录与用户/组权限投影。若 `NAS_STORAGE` 指向
OMV 文件系统根，Echo 会按各共享的 `relativePath` 授权；若它直接指向一个共享目录，必须设置
`ECHO_NAS_OMV_SHARED_FOLDER_REF`，Echo 才会把该共享映射为文件区虚拟根，并继续应用其下
嵌套共享的显式拒绝。共享 UUID 缺失、重复、离线、路径未挂载、符号链接逃逸或 OMV 权限查询失败
都会失败关闭。默认每个请求重新读取权限投影，因此 OMV 撤权会在下一次文件或相册请求立即生效；
同一请求内部仍只解析一次权限，避免列表和单文件处理出现不一致。

可以在部署目录的 `.env` 中按 `NAS_STORAGE` 相对路径设置逻辑字节上限：

```dotenv
ECHO_SHARE_QUOTAS_JSON='{"family":536870912000,"family/photos":322122547200,"backups":1099511627776}'
```

每条匹配的父级和子级规则都会同时生效；用 `"."` 可约束整个活动文件区。计量包含共享目录中
已有的普通文件，并把未完成 multipart 和可恢复分块会话的**最终声明大小**提前预留。普通上传
在启用配额后必须声明大小。预检成功响应会返回每条匹配规则的上限、已用、已预留、可用和预计
用量；越界返回 507，且不创建目标文件。上传提交、复制、跨共享移动和回收站恢复都经过同一硬
门，同一共享内重命名不重复计费。内部上传临时文件、会话元数据和回收站不计入活动共享逻辑
用量；它们仍受整盘 `ECHO_UPLOAD_RESERVE_BYTES` 保护。

这是 **Echo 文件 API 的应用层配额**，不会拦截管理员从宿主、SMB、NFS 或其他容器直接写入
同一目录。OMV 接入已提供文件系统级的已有用户/组硬配额控制，可在“系统设置 → 共享与用户”
预览后经管理员密码审批应用，并覆盖同一所有者经本机、SMB、NFS 的写入。但它按整个文件系统
上的文件所有者计量，**不能代替某个共享目录的路径级配额**。需要共享目录彼此独立的跨入口硬
限制时，仍应使用 XFS project quota、ZFS dataset quota 或经过产品支持矩阵验证的等价能力，
并在真机验收时与 Echo 报告对账。
修改配额配置后需要重启 Echo OS 服务；如果新上限低于已有未完成会话的预计用量，该会话会标记
为 `quotaBlocked` 并在写入下一块数据前返回 507，取消其他会话或调高上限后可继续。

### Agent 调企业版 PM(可选)

把企业版部署在同一机器(它会自动出现在启动器,见上文),再给本服务设
`ECHO_PM_URL` 指向企业版,Agent 即获得三个工具:`pm_list_projects` /
`pm_list_tasks` / `pm_create_task`。这样用户可以直接对 Agent 说「把这次调研拆成
任务记到项目里」,Agent 调企业版的 PM API 完成——UI 在窗口里看,操作可对话驱动。

### OpenMediaVault 存储健康（可选）

OMV 机器请按 [`deploy/omv/README.md`](../omv/README.md) 安装宿主受限桥，再叠加
`docker-compose.omv.yml`。登录后在 **系统设置 → 存储健康** 查看物理盘健康、挂载卷容量和
物理盘 → 软件 RAID → LVM 拓扑，并按需读取通电时间/启停次数；**共享与用户**页展示
SMB/NFS、普通用户/组和共享权限，并可在预览、密码审批、审计和回读/回滚保护下创建空的普通
用户组、创建无 Shell/无 SSH 的家庭成员，以及修改一个已有用户或组的共享服务权限。新成员密码
只在内存中转交 OMV，不进入命令行、响应或审计；账户/组更新与日常删除、密码重置、文件 ACL
和复杂规则仍跳转 OMV 官方管理页。这个接入没有新 TCP 端口，不把 OMV 管理密码、原始 RPC socket、
宿主 `/dev` 或 root shell 交给 Echo；未安装时只显示“尚未接入”，其余功能不受影响。
Echo 默认每 5 分钟持续检测 SMART、温度、容量和阵列状态，并在设备状态目录中以 0600 文件保留
活跃告警及最近变化；邮件或推送接收人仍由 OMV 官方通知设置管理。

### 首次登录与家庭账号

桌面打开即要求登录。设备首次只有管理员账号：

- **设了 `ECHO_ADMIN_PASSWORD`** → 用它登录;
- **没设** → 首启随机生成,查容器日志拿初始密码:
  ```bash
  docker compose logs | grep "appliance admin password"
  ```

密码哈希与会话密钥持久化在 data 卷(`appliance-auth.json`,0600),
会话最长 30 天。改 `ECHO_ADMIN_PASSWORD` 不会覆盖已初始化设备；登录桌面后打开
**系统设置 → 账户与安全**即可更改管理员密码或退出全部登录。密码更新会让旧密码、全部
旧 JWT 和尚未使用的高风险授权立即失效，不需要重启容器。不要通过删除
`appliance-auth.json` 重置生产设备，否则会同时更换会话密钥并失去既有认证状态。

管理员可在 **存储中心 → 共享与用户** 选择一个 OMV 已有普通用户，为其设置独立的 Echo
显示名和登录密码。Echo 只保存自身 bcrypt 哈希与 `OMV 用户名 ↔ Echo principal` 映射，不读取
OMV/Agent 私有数据库，也不复用或修改 OMV 密码。管理员可随后停用、重新启用、单独重置成员
Echo 密码，或在停用后移除 Echo 映射；停用、改密和移除会立即吊销该成员旧 Cookie、Bearer 与
WebSocket，但不会影响其他成员。移除映射不会删除 OMV 用户、SMB 密码、共享权限或 NAS 数据。
成员只看到 OMV 授予自己的共享文件和照片，以及自己的账号/Agent 连接；OMV 整机状态、设备连接、
应用安装卸载、容器启停和管理员安全设置固定只对设备管理员开放。

### 加密备份与恢复（离线）

设备状态与 NAS 用户文件是两个备份域：`data/` 保存设备认证、审计、Agent 记忆和运行状态；
`NAS_STORAGE` 保存家庭文件。下面的状态备份会明确排除 `/data/nas`，不能代替 NAS 文件备份。

状态导出必须在 Echo 主服务停止时执行。运行中的服务持有独占锁，工具会拒绝抓取可能不一致的
半份状态。口令不放进命令行；丢失口令后加密备份无法恢复。生产环境优先使用编排脚本：它会
保留服务原始运行状态，按“停服务 → 导出 → 解密校验 → 恢复服务 → 校验后轮换”的顺序执行；
中途失败时也会尝试拉回原本正在运行的服务。

```bash
cd deploy/appliance
read -s ECHO_BACKUP_PASSPHRASE && export ECHO_BACKUP_PASSPHRASE
ECHO_BACKUP_MOUNTPOINT=/mnt/echo-state-backups \
  ECHO_BACKUP_DIR=/mnt/echo-state-backups/echo-os \
  ECHO_BACKUP_KEEP=7 ./backup-state.sh
unset ECHO_BACKUP_PASSPHRASE
```

两个路径都没有本机目录默认值，而且必须在运行前已经存在。脚本会从内核挂载表确认
`ECHO_BACKUP_MOUNTPOINT` 是精确的活动挂载点，拒绝根文件系统、临时/系统伪文件系统、任意路径层级
中的符号链接，以及与部署目录、`data/` 或实际 `NAS_STORAGE` 共用设备号的目标。如果 USB/NFS/CIFS
挂载掉线，只剩系统盘上的同名目录，任务会在取得维护锁和接触 Docker 之前失败，不会自动重建目录。
这能证明目标是另一个活动文件系统，但不能证明它物理上位于另一台设备；整机灾难恢复仍须使用
外置介质或远端文件系统。

同一时间只能运行一个备份任务；文件名使用 UTC 时间。轮换只匹配
`echo-state-YYYYMMDDTHHMMSSZ.echo-backup`，至少保留 2 份，其他文件不删除。最新一份无法完成
认证解密和归档检查时，任何旧备份都不会被删除。升级镜像或 compose 前先成功运行一次该脚本。
备份、审计证据导出、状态恢复和镜像升级还会共同持有
`/run/lock/echo-os-appliance-maintenance.lock`，所以不会同时停服务、切换状态或升级镜像；升级脚本
调用备份时继承同一个锁句柄，不在两步之间释放。非 root 测试环境可用
`ECHO_MAINTENANCE_LOCK=/安全且已存在的目录/echo-maintenance.lock` 显式改址，生产 OMV 保持
`/run/lock` 默认值。

Debian/OMV 可用运维包中的事务式 systemd 安装器每天自动运行。先通过 OMV/fstab 把备份和审计
目标挂成活动的独立文件系统，再创建其下的输出目录。生成的 `RequiresMountsFor` 负责等待挂载，
脚本仍会在每次任务开始时独立验证文件系统身份。先用 `systemd-creds` 创建绑定本机/TPM 的两份
加密凭据；不要把无人值守口令写进普通 `.env`：

```bash
findmnt --mountpoint /var/backups
findmnt --mountpoint /mnt/echo-audit-evidence
sudo install -d -m 0700 /var/backups/echo-os /mnt/echo-audit-evidence \
  /etc/credstore.encrypted
read -sr ECHO_BACKUP_PASSPHRASE
printf %s "$ECHO_BACKUP_PASSPHRASE" | sudo systemd-creds encrypt \
  --name=echo-backup-passphrase - \
  /etc/credstore.encrypted/echo-backup-passphrase
unset ECHO_BACKUP_PASSPHRASE

read -sr ECHO_AUDIT_EXPORT_PASSPHRASE
printf %s "$ECHO_AUDIT_EXPORT_PASSPHRASE" | sudo systemd-creds encrypt \
  --name=echo-audit-export-passphrase - \
  /etc/credstore.encrypted/echo-audit-export-passphrase
unset ECHO_AUDIT_EXPORT_PASSPHRASE

cd /opt/echo-os/deploy/appliance
sudo ./operations_systemd.py plan \
  --bundle-root "$PWD" \
  --backup-directory /var/backups/echo-os \
  --backup-mountpoint /var/backups \
  --audit-directory /mnt/echo-audit-evidence \
  --audit-mountpoint /mnt/echo-audit-evidence \
  --backup-credential /etc/credstore.encrypted/echo-backup-passphrase \
  --audit-credential /etc/credstore.encrypted/echo-audit-export-passphrase \
  --output /root/echo-operations-systemd-plan.json

# 核对 planId、两处现场挂载、凭据摘要和五个 unit 摘要后，逐字复制打印的确认语：
sudo ./operations_systemd.py apply \
  --plan /root/echo-operations-systemd-plan.json \
  --confirm 'INSTALL ECHO OPERATIONS <上一步打印的64位planId>'

sudo systemctl start echo-state-backup.service
sudo systemctl start echo-audit-evidence.service
```

`plan` 和 `apply` 都会重新验证运维包归属/权限、两处活动外置文件系统和加密凭据；计划固定为
`0400`，并绑定凭据、挂载检查结果、现场路径和 unit 内容摘要。五个 unit 通过
`systemd-analyze verify` 后才会逐个原子替换到固定的 `/etc/systemd/system`；任一 timer 启用失败会恢复
安装前的 unit 内容、权限和 timer 启用/运行状态。若回滚本身失败，工具会明确报告“不完整回滚”，
不能当作安装成功。`systemd/*.example` 只用于审查默认渲染，生产环境不再手改模板。

A/B 发布源码门还会在 `debian:trixie-slim` 中安装 Debian 13 的 systemd，并运行
`verify_operations_systemd_units.py --require-os-id debian --require-version-id 13
--source-revision <40位OS提交>`。验证器生成与生产
安装器同源的五个 unit，交给该发行版真实的 `systemd-analyze verify`，报告同时记录 OS 身份、
systemd 版本、OS source commit 和 unit 摘要；版本或任一 unit 解析失败都会让发布门失败。正式
A/B job 会再次运行同一门，`verify-ab-update-evidence.py` 严格复核报告 schema、Debian 13 身份、
五个 unit、`verified:true` 和 source commit，然后把完整字段及原报告 SHA-256 嵌入 schema 3 的
A/B manifest。该 manifest 再由专用更新密钥 GPG 签名，候选汇总和离线回放会拒绝缺失、伪造、
其他 OS/source 或未验证的 systemd 字段。因此原生解析结果不再只是临时 CI 日志。这个门证明
Debian 13 解析兼容性，但仍不能代替实体 OMV 主机上的安装/启用、两个 timer 真实触发、安装失败
回滚、备份与审计挂载分别掉线、移除失败回滚、受管移除及断电测试；这些结果必须逐项进入 G5 的签名
`gate-result.json`。

备份定时器默认每日 03:30、审计定时器默认每日 04:15，均随机延迟 0–30 分钟并补跑错过的任务。
用 `systemctl status` 和对应 `journalctl -u` 确认两次首次任务成功；任务会短暂停止 Echo 主服务，
因此应安排在无人使用时段。备份仍需通过 OMV 任务复制到另一台设备或离线介质，本机轮换不等于
灾难恢复。

移除 Echo 或运维包前，必须先受管移除定时任务，避免 timer 继续调用已不存在的路径。卸载计划
绑定五个现场 unit 的内容/权限、升级恢复服务状态、两项 timer 状态和宿主 systemd 工具；执行时再次比对并要求精确
确认。它只禁用恢复服务与 timer、删除这五个 unit 并 reload systemd，明确保留加密凭据、设备状态、NAS
数据、状态备份和审计证据。任一步失败会恢复 unit 与原 timer 状态：

```bash
cd /opt/echo-os/deploy/appliance
sudo ./operations_systemd.py remove-plan \
  --output /root/echo-operations-systemd-remove-plan.json

# 核对 unit 摘要、timer 状态和 preserved 清单后，逐字复制打印的确认语：
sudo ./operations_systemd.py remove \
  --plan /root/echo-operations-systemd-remove-plan.json \
  --confirm 'REMOVE ECHO OPERATIONS <上一步打印的64位planId>'
```

卸载器不会删除 `/etc/credstore.encrypted` 中的凭据或任何备份/证据。管理员完成保留期、异机副本
和审计要求复核后，如确需销毁这些资产，必须另走明确的数据销毁流程，不能由应用卸载隐式代办。

正式恢复使用 `restore-state.sh`，不再手工交换宿主 `data/`。第一次运行只完成备份认证解密和
结构验证，打印备份 SHA-256、精确目标路径和所需确认语，不停止服务、不修改现场状态。设置精确
确认语后再次运行，脚本会执行：停止原服务 → 恢复到同文件系统暂存目录 → 只在暂存副本上执行
受支持的 schema 正向迁移 → 验证管理员凭据结构、审计链、密钥环、私有文件权限、无 NAS 数据与
无运行锁 → 原子晋级目录 → 启动健康检查 → 容器内再次只读验收。备份在暂存期间被替换也会因
SHA-256 变化在晋级前失败。

```bash
cd deploy/appliance
read -s ECHO_BACKUP_PASSPHRASE && export ECHO_BACKUP_PASSPHRASE
./restore-state.sh /mnt/off-device/echo-state-20260827T010000Z.echo-backup

# 核对上一步打印的摘要与目标后，逐字复制确认语：
export ECHO_RESTORE_CONFIRM='RESTORE sha256:<64位摘要> TO /opt/echo-os/deploy/appliance/data'
./restore-state.sh /mnt/off-device/echo-state-20260827T010000Z.echo-backup
unset ECHO_RESTORE_CONFIRM ECHO_BACKUP_PASSPHRASE
```

如果新状态不能启动、健康等待失败或容器内复核失败，脚本会先停止失败实例，再把目录切回旧状态并
恢复原服务运行状态。失败的新状态保存在 `.data.echo-failed-*`；成功时旧状态保存在
`.data.echo-rollback-*`。脚本从不自动删除这两类目录，也不合并新旧内容。至少在管理员使用备份时
密码登录、检查审计 `ok:true`、核对 Agent 关键状态且另做一份新备份前，不要人工清理旧目录。
原服务执行前就是停止状态时，恢复验收会短暂启动新实例，验证后再恢复为停止状态。

NAS 用户文件不在上述设备状态包里，使用独立的 `nas_data_backup.py`。备份源必须是 OMV/文件系统
已经冻结出的只读快照，脚本拒绝直接读取仍可写的在线共享；仓库必须位于经
`external_storage.py` 验证的异机或可移除挂载上。口令优先从 systemd credential
`echo-nas-backup-password` 读取，传给 restic 时只存在于匿名内存文件中：

```bash
cd deploy/appliance
sudo ./nas_data_backup.py init \
  --repository /mnt/off-device/echo-nas-data \
  --repository-mount /mnt/off-device \
  --deployment-root "$PWD" --appliance-env appliance.env
sudo ./nas_data_backup.py backup \
  --repository /mnt/off-device/echo-nas-data \
  --repository-mount /mnt/off-device \
  --deployment-root "$PWD" --appliance-env appliance.env \
  --source-snapshot /srv/echo-nas-snapshots/2026-08-27
sudo ./nas_data_backup.py check \
  --repository /mnt/off-device/echo-nas-data \
  --repository-mount /mnt/off-device \
  --deployment-root "$PWD" --appliance-env appliance.env
```

裸机恢复时先创建与 `NAS_STORAGE` 完全一致的空目录。`restore` 会全仓库读校验并解析认证快照索引；
只有逐字输入 `RESTORE ECHO NAS <完整64位snapshot-id> TO <NAS_STORAGE绝对路径>` 才会写暂存区。
暂存树会拒绝越界符号链接、特殊文件及异常层级，随后通过 Linux 原子目录交换一次性晋级；它不会把
备份合并或覆盖到已有 NAS 文件中。

G6 裸机恢复不能再用一条手工日志声明完成。仍在原候选系统运行时，先完成设备状态、原生 Agent/用户
和 NAS 三路异机备份，并为设备状态与 Agent 各放入 1 MiB 恢复金丝雀、为 NAS 放入 1 GiB 金丝雀；
随后用运维包中的 `bare_metal_recovery_lab.py plan` 生成候选、目标盘、备份收据和金丝雀绑定的
mode-0400 私有计划。候选索引、解包运维包、安装包、恢复密钥、备份/收据、私有计划目录和独立的
公开证据目录都必须位于同一已验证异机/可移除仓库挂载的子目录内，绝不能放在即将被清空的目标盘
或部署目录里。重启到同候选 Recovery 后，依次执行
`recovery-install → cold-boot → restore → recovery-promote → trial-verify → recovery-commit → final-verify`，
每阶段都要复制计划输出的完整确认语；安装阶段还必须单独提供安装器刚刚打印的目标盘确认语。
这八份 mode-0444 日志（含 plan 自动生成的 `source-backup`）全部通过后，再生成正式生命周期：

```bash
sudo ./bare_metal_recovery_lab.py verify \
  --plan /mnt/off-device/echo-g6-private/echo-bare-metal-plan.json
./physical_acceptance_capture.py bare-metal-result \
  --gate recovery_media_bare_metal_restore \
  --candidate-index ./echo-delivery-release-evidence-index.json \
  --lab-plan /mnt/off-device/echo-g6-private/echo-bare-metal-plan.json \
  --lab-directory /mnt/off-device/echo-g6-evidence \
  --output /mnt/off-device/echo-g6-evidence/bare-metal-recovery-lifecycle.json
```

最终校验会逐字节绑定三类金丝雀、原设备与替换系统的 machine/boot 身份变化、完整 NAS 树统计、
管理员认证、审计签名链、Agent 事务提升/提交以及最终冷启动。私有计划不得放进公开证据目录，恢复
密钥、密码和磁盘序列号也不得进入日志。这里恢复并验证的是 appliance 内的管理员认证状态、会话
撤销边界与审计签名身份；替换系统的本地管理员由安装流程重新创建，`/etc/shadow`、machine-id 和
磁盘身份不得从原机克隆。该流程会清空目标盘，只能在牺牲设备上运行。

底层 `python -m appliance.state_backup restore` 仍可用于取证式恢复到任意不存在的新目录，但它
只证明归档安全解密，不负责宿主目录晋级、服务健康或失败回滚；生产切换必须使用上述编排脚本。

备份使用 scrypt 派生密钥与 AES-256-GCM 认证加密；错误口令、任一字节篡改、路径穿越、外部
符号链接、特殊设备文件、重复路径和超限归档都会 fail closed。备份产物权限固定为 `0600`。

### 外置审计证据与密钥轮换

实时审计链保存在设备状态中，用 HMAC 链和签名尾检查点发现修改、插入或截尾。系统设置的
**审计与证据**页会再次验链，显示设备 Ed25519 公钥指纹，可下载不含审计正文的签名锚点；
“轮换密钥”必须再次输入设备管理员密码，旧记录继续用历史密钥验证。建议首次交付就把公钥
指纹或下载的锚点保存到另一台受控设备，之后校验时固定该 `signingKeyId`，不要只相信证据包
自带的公钥。

完整证据包必须离线导出到 `data/`、NAS 用户目录和部署目录之外的外置盘或远端挂载。脚本按
“停服务 → 验链并导出 → 解密/签名复核 → 恢复服务 → 校验后保留”的顺序运行。包中只有审计
JSONL、签名检查点、无秘密的密钥 ID 列表、逐文件清单和签名锚点；不含 `appliance-auth.json`、
JWT 密钥、密码哈希、Agent 记忆或 NAS 用户文件。外层使用 scrypt + AES-256-GCM，权限为 `0600`。

```bash
sudo install -d -m 0700 /mnt/echo-audit-evidence
cd deploy/appliance
read -s ECHO_AUDIT_EXPORT_PASSPHRASE && export ECHO_AUDIT_EXPORT_PASSPHRASE
ECHO_AUDIT_EXPORT_DIR=/mnt/echo-audit-evidence \
  ECHO_AUDIT_EXPORT_MOUNTPOINT=/mnt/echo-audit-evidence \
  ECHO_AUDIT_KEEP_DAYS=365 ECHO_AUDIT_KEEP_MINIMUM=12 \
  ./export-audit-evidence.sh
unset ECHO_AUDIT_EXPORT_PASSPHRASE
```

保留策略只匹配 `echo-audit-YYYYMMDDTHHMMSSZ.echo-audit`：至少保留指定的最小份数，同时保留
指定天数内的全部包。它会先完整验证最新一份，若口令错误、文件被替换或签名不通过，一份旧包
也不删除。该策略**永远不删实时审计日志**。`ECHO_AUDIT_EXPORT_DIR` 和
`ECHO_AUDIT_EXPORT_MOUNTPOINT` 都没有危险默认值，必须显式指向已挂载的外部位置；脚本应用与状态
备份相同的内核挂载表、设备号、伪文件系统和符号链接检查。若该挂载只是设备内的另一个分区，
仍不能抵御整机或整盘丢失。

Debian/OMV 的无人值守审计任务使用另一份加密凭据，默认每天 04:15 后随机延迟 0–30 分钟。
凭据和定时器已经在上一节通过同一个四-unit 事务计划安装；不要再次手工复制模板。需要单独复核
凭据创建命令时可使用：

```bash
read -sr ECHO_AUDIT_EXPORT_PASSPHRASE
printf %s "$ECHO_AUDIT_EXPORT_PASSPHRASE" | sudo systemd-creds encrypt \
  --name=echo-audit-export-passphrase - \
  /etc/credstore.encrypted/echo-audit-export-passphrase
unset ECHO_AUDIT_EXPORT_PASSPHRASE

sudo systemctl start echo-audit-evidence.service
```

现场路径只能通过 `operations_systemd.py plan` 的显式参数进入 unit；安装器不会猜测目录或接受
手工替换后的模板。
远端挂载不可用时任务必须失败，不能静默回落到系统盘。可用下面的离线命令复核证据包；跨设备
或长期归档时应追加首次保存的 `--expected-signing-key-id sha256:...`：

```bash
docker compose run --rm --no-deps --entrypoint python \
  -e ECHO_AUDIT_EXPORT_PASSPHRASE \
  -v /mnt/echo-audit-evidence:/evidence:ro \
  echo-os -m appliance.audit_evidence verify \
  /evidence/echo-audit-YYYYMMDDTHHMMSSZ.echo-audit \
  --expected-signing-key-id 'sha256:保存过的设备公钥指纹'
```

`data/echo-state-schema.json` 是设备状态的版本闸门。旧式无标记目录视为 v0，首次由运行时在
独占锁内执行明确的 `0 → 1 → 2` 元数据迁移；v2 增加审计密钥环与外部锚点契约，每次迁移
必须只前进一个版本。旧镜像遇到更高版本
会拒绝启动，不会猜测兼容或自动降级。备份的 `verify` 报告会返回 `stateSchemaVersion` 和
`stateCompatible`。可在升级前查看当前目标镜像的读取结论（不修改状态）：

```bash
docker compose run --rm --no-deps --entrypoint python echo-os \
  -m appliance.state_schema --state-dir /data
```

真正迁移由 Echo 正常启动在状态锁内执行；不要对生产目录手工运行 `--prepare`。当前迁移链为
v0 → v1 → v2；未来任何涉及数据结构的版本都必须先增加正向迁移、备份恢复测试和回滚说明，
才能提高 schema 号。

### 正式 amd64 / arm64 镜像发布

正式容器发布由 `.github/workflows/appliance-release.yml` 负责，并且只响应
`echo-appliance-v<semver>` 标签。它不会发布 `latest`：同一次构建会把固定摘要的基础镜像、
当前 Echo OS 提交（同时包含内建 Agent）、状态 schema，以及 `linux/amd64`、`linux/arm64`
两个平台绑定到一个 GHCR OCI 索引。发布前必须审查并推送当前统一工作树；dirty QA bundle
和本地未提交 UI 均不能进入发布镜像。

版本流水线会生成 `ghcr.io/<owner>/echo-os` 镜像及以下证据，不生成可漂移的升级入口：

- `echo-appliance-release.json`：不可变索引摘要、两个平台各自摘要、OS/Agent 提交、Python
  依赖锁身份和状态 schema；
- `echo-release.env`：升级器可直接读取的 `ECHO_OS_IMAGE=...@sha256:...`；
- `echo-appliance-operations.tar.gz`：绑定同一镜像摘要的确定性运维包，含固定文件清单、权限、
  内层 SHA-256、独立外层校验和与 SPDX 2.3；旁附同源 `operations_bundle.py` 用于安全验证/解包；
- `echo-appliance-index.json`：registry 返回的 OCI 索引原文，发布脚本会复算其 SHA-256；
- 两份平台级 SPDX SBOM、构建/运行依赖锁及其元数据，以及覆盖全部这些证据的校验和文件；
- registry 内的 BuildKit `mode=max` provenance/SBOM 与 GitHub OIDC provenance attestation。

从对应 Actions run 下载证据后，在 Linux 管理机上验证并安全解包：

```bash
cd echo-appliance-<版本标签>
sha256sum -c echo-appliance-release.json.sha256
source echo-release.env

docker login ghcr.io
docker buildx imagetools inspect "$ECHO_OS_IMAGE" --raw > /dev/null
gh attestation verify "oci://$ECHO_OS_IMAGE" -R <owner>/<repository>

python3 operations_bundle.py verify echo-appliance-operations.tar.gz
sudo python3 operations_bundle.py extract echo-appliance-operations.tar.gz \
  --destination /opt/echo-os --require-root-owner
```

总校验和同时绑定运维包、运维包自身校验文件、SPDX 和验证器；内层清单再逐文件绑定内容、大小与
执行权限。校验和只能证明下载文件没有分叉；`gh attestation verify` 才核对发布来源。完整容器
SBOM 由 BuildKit 附着在每个平台镜像上，工作流再从同一不可变引用提取、校验并随发布证据归档。

### Docker appliance 安全升级

发布镜像后，生产升级只接受带仓库摘要的不可变引用，不接受 `latest` 或普通 tag：

```bash
cd /opt/echo-os/echo-appliance-operations-<制品ID>
read -s ECHO_BACKUP_PASSPHRASE && export ECHO_BACKUP_PASSPHRASE
export ECHO_BACKUP_MOUNTPOINT=/mnt/echo-state-backups
export ECHO_BACKUP_DIR=/mnt/echo-state-backups/echo-os
./upgrade-appliance.sh \
  registry.example/echo-os@sha256:<64位镜像摘要>
unset ECHO_BACKUP_PASSPHRASE ECHO_BACKUP_MOUNTPOINT ECHO_BACKUP_DIR
```

升级器会先核对当前容器与 `echo-release.env` 没有漂移，然后调用前述完整备份编排；因此升级同样
要求已挂载且通过独立文件系统检查的备份目标。备份验证
成功后才拉取目标镜像。目标镜像以只读方式检查现有 schema：如果需要数据迁移，自动升级会
拒绝，必须走单独审查的迁移/回滚手册。只有无 schema 变化的版本才会写入 0600 的
`echo-release.env`，以 `--no-build --wait` 切换，并复核主容器和 docker-control 都运行同一个
摘要。健康检查失败时恢复旧镜像选择并重新拉起旧版本。后续定时备份也自动读取该 release
文件，避免用错工具镜像。

这解决的是**无数据迁移版本**的安全切换。需要提高 schema 号的发布不能假装可以原地回滚：
应先保留已验证异机备份，提供幂等正向迁移及“恢复旧状态目录 + 旧镜像”的回滚流程，再单独
放行。正式多架构发布链已落地，但当前还没有标签流水线成功产出的 registry 摘要；因此升级器
仍只有本机协议测试，必须补一次正式标签发布以及真实 OMV/Docker 演练。

例:把群晖 `/volume1/share` 挂进来、换 9000 端口:

```bash
PORT=9000 NAS_STORAGE=/volume1/share docker compose up -d --build
```

## 工作原理

- `ECHO_APPLIANCE=1` 启用启动器应用注册器,挂载 `/api/appliance/*`；Agent runtime
  由独立 Echo Agent wheel 提供，OS 仓库不再复制其内部实现。
- 独立 `docker-control` sidecar 挂载宿主 `/var/run/docker.sock`;Echo 主容器只通过
  内部隔离网络调用列举/启停端点，主进程内不存在原始 socket。
  应用元数据(名称/图标/Web 端口)从容器 label 读取,**兼容 CasaOS /
  homepage / Unraid 的 label 约定**——已用这些面板装的应用,图标直接复用。

## ⚠ 安全须知

宿主 `docker.sock` 仍是 root 等价能力，因此只交给不发布宿主端口的窄 sidecar：它以
root 读取 socket 的数字组后立即降为 `echo` 用户，只在 internal network 提供
`ping/list/start/stop`，以及仅接受应用 ID、计划 ID、目录摘要的 Hub 安装入口。后者会在
代理内重新加载受信目录并重算固定镜像、端口和卷；其他 Docker API 一律 404/405。bind mount
的 `:ro` 不是安全边界，
真正边界是独立进程、降权、内部网络和显式路由白名单。真机烟测还会读取两个容器
PID 1 的 `/proc/1/status`，要求有效能力为零并启用 `NoNewPrivs`，避免“表面非 root、实际仍
能提权”。

- 普通 HTTP 模式**仅限可信内网**，不要把 8000 端口直接暴露到公网；正式 TLS 模式必须经
  `start-tls.sh` 启动，使 8000 只绑定宿主回环且网络入口仅为零能力 TLS 网关;
- 所有 HTTP/WebSocket 先校验 Host；所有带 Origin 的请求必须同源或进入显式白名单，
  跨站 API 修改、跨站 WebSocket 和 DNS rebinding 在认证逻辑前即被拒绝;
- 所有 HTTP 响应附带 CSP、`SAMEORIGIN`、`nosniff` 与 `no-referrer`；脚本只允许同源，
  iframe 默认只允许同源。第三方 NAS 应用
  必须逐项加入 `ECHO_APPLIANCE_FRAME_ORIGINS`，否则使用“新标签打开”兜底;
- appliance 扩展只接管 `/`、`/index.html` 与 OS 构建产物内的真实公共文件，使
  `/#/desktop` 稳定进入 Echo OS 锁屏；Agent API 与旧 dashboard 路由不被混写;
- 反向代理/FQDN 部署必须同时设置可信 host/origin 和代理 IP；只有被信任的代理才能通过
  `X-Forwarded-Proto` 让应用识别 HTTPS。登录失败固定按账号/IP 双层限速，浏览器会话
  cookie 为 host-only、HttpOnly、SameSite=Lax，HTTPS 下自动加 Secure；设备登录后不会
  再把 JWT 复制进浏览器 localStorage;
- Echo 主服务未配置 `ECHO_DOCKER_HOST` 时不会在 appliance 模式静默回退直连 socket;
- Echo 主容器只在入口阶段以受限能力修复 `/data` 状态目录属主，随后按 `PUID/PGID`
  永久降权；NAS 数据挂载不会被递归 chown，必须由宿主 ACL 允许该身份读写;
- 启停应用与物理清空回收站必须重新输入设备管理员密码；签发的审批令牌绑定当前
  actor/action/target，90 秒内单次有效且服务重启即失效，错误密码独立限速;
- 管理员密码轮换与“退出所有登录”走同一复核/审计门；持久会话使用签发时间下限全局吊销，
  同时覆盖 Echo API、Agent API、Cookie、Bearer、WebSocket 和查询令牌;
- 家庭成员停用或改密使用独立的每账号签发时间下限，只吊销目标成员；成员无法读取 OMV 控制面、
  Device Link、Hub 后台任务和一次性应用凭据，也不能仅凭管理员复核密码提升为设备管理员;
- 设备状态备份必须离线并加密；运行时独占锁阻止热拷贝，归档明确排除 NAS 用户数据，恢复只
  创建新目录而不覆盖现场。NAS 文件仍需由 OMV 快照/备份任务单独保护;
- 所有文件修改、应用控制和审批结果写入 Agent 官方 HMAC 链，并以签名尾检查点检测末尾
  删除；链异常时新的受控修改 fail closed。管理员可查
  `/api/appliance/audit/verify` 与 `/api/appliance/audit/events`;
- Echo Hub 只开放受信目录中的固定包安装，并复用同一 planId、管理员密码单次审批和审计门；
  任意 Compose、任意镜像及卸载/升级仍未开放，不能另做确认框旁路。

## 在 CasaOS / ZimaOS 上一键安装

`docker-compose.yml` 内置了 `x-casaos` 应用商店元数据。在 CasaOS:

1. 应用商店 → 右上「自定义安装」;
2. 粘贴本 compose 内容(或导入文件);
3. 按需改端口/存储卷 → 安装。

CasaOS 会用 `x-casaos` 里的标题/图标/描述生成应用卡片,装完点开即桌面。

## 已知边界

- **未发布预构建镜像**:当前 `build: context` 从源码本地构建。发布到
  registry 后,CasaOS 商店可改为拉取镜像、免本地构建。
- 文件上传、下载、复制与回收站闭环已经实现；16 MiB 以上上传自动使用 8 MiB 分块，每块
  SHA-256 校验、严格偏移、失败查询服务端进度后重试，并可暂停、继续或取消。会话和数据
  fsync 后持久化，服务重启后能按真实落盘长度恢复，完成时再次计算整文件 SHA-256 并同目录
  原子提交；旧 multipart 上传保持兼容。Chromium/Electron 下载优先用文件句柄边读边写，网络
  中断后以 Range 从已写偏移继续并可取消。正式 appliance 的 HttpOnly Cookie 会话在不支持
  File System Access API 的浏览器中交给同源原生下载管理器落盘，不创建页面 Blob 或带凭据的
  URL；进度和取消由浏览器界面负责。仅非正式设备的 Bearer-only 旧开发客户端保留 XHR Blob
  兼容。Echo 文件入口还支持路径级逻辑配额和并发/重启会话预留；SMB/NFS 等旁路可用新增的
  OMV 用户/组文件系统硬配额控制，但路径独立硬限制仍需 project/dataset 等底层能力。包含活动
  上传的目录不能被整体移动或移入回收站；复制目录会排除内部上传
  临时文件。真实 1 GiB/多架构/断电恢复压力证据尚未完成。
- `verify-running-appliance.py --nas-transfer-test-bytes 1073741824` 可先零写预览，再用与字节数、
  NAS 相对目录和设备 origin 绑定的 `confirmationRequired` 加 `--require-nas-transfer` 执行真机
  分块/恢复/摘要/完整下载/Range/取消门；加 `--nas-transfer-restart-main` 后确认语还绑定主容器
  名，并在首块后短暂重启服务、等待健康门再续传。测试产物只移入回收站且不会调用全量清空；执行后仍
  占用约 1 GiB，需管理员核对后显式清理。
- 加密设备状态导出、验证、暂存恢复、schema/认证/审计预检、宿主目录原子晋级、失败回滚、
  停机编排、校验后轮换，以及绑定现场挂载/凭据/unit 摘要、精确确认、systemd 验证和失败回滚的
  定时任务事务安装器，以及只移除受管 unit/timer、保留凭据与数据并可失败回滚的卸载器已落地；
  异机副本和一次真实 Docker 灾难恢复演练
  仍待完成。
- 不可变摘要升级、升级前备份、目标 schema 预检、健康等待、双容器摘要核对和无迁移版本的
  失败回滚已落地；正式镜像发布和真实 Engine 演练仍待完成。
- 本机无 Docker 环境,compose/Dockerfile 的镜像构建未在本地验证;
  Agent 三制品真实构建、哈希校验、安装后入口点校验、OS wheel 隔离和最终 bundle 的
  根路径 Echo OS 锁屏、公共资源、安全头、登录/HttpOnly cookie、Host/Origin/限速和工作台
  HTTP 烟测已在本地验证；高风险
  路径也已通过真实临时服务验证 Cookie 鉴权、未审批拦截、密码签发、单次消费、防重放、
  全会话吊销、运行时密码轮换、审计验链及凭据不落审计。
  docker-control 的 HTTP 白名单、受保护
  容器、降权计划及“真实 Unix socket → 窄代理 → EchoClient”链已用本地假 daemon 验证，
  宿主 Docker Engine 仍需真机确认。
- 正式发布前必须把统一仓库的已审查改动提交并推送到交付分支；在线来源预检会拒绝
  dirty/unpushed source，`dirty` QA 包不可发布。八个 Agent API 领域和实际方法面仍在
  bundle、镜像与启动门中验证；合同不兼容会在生成签名或 raw 之前明确失败。
