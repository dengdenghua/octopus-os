# deploy/provision — Echo OS 装机链路

把 Debian 13 (trixie) 官方 netinst 改造成 Echo OS 装机镜像。

> 这是保留的 d-i 兼容/诊断装机路径。正式生产发布以
> `packaging/image/build-image.sh` 生成的 dm-verity raw image 和
> `deploy/installer/create-install-bundle.sh` 生成的 GPG 签名整盘安装包为准；
> 本目录的严格离线 ISO 即使通过完整性检查，也不具备 Secure Boot、A/B root、
> TPM/LUKS2 生命周期和发布签名，不能替代生产候选。

设计依据见 [`docs/P3_provision_BASE_PLAN.md`](../../docs/P3_provision_BASE_PLAN.md)。
一句话:**寄生式改造 d-i,不 fork 安装器一行代码。**

## 目录

```
deploy/provision/
├── build-iso.sh                     # 组装 ISO(解包 → 塞载荷 → 改引导 → 重打包)
├── build-release-iso.sh             # 严格发布：先构建全部离线载荷，再封装 ISO
├── verify-release-iso.sh            # 重开成品，复核引导入口、身份与全部载荷
├── release-manifest.py              # 生成/验证确定性发布身份清单
├── build-system-deb-repo.sh         # 解析首启系统包及依赖闭包
├── render-release-preseed.sh        # 生成不访问镜像源的严格 d-i 策略
├── echo-firstboot.service        # 首次开机跑 setup-base.sh
├── 99-echo-os                       # sudoers 命令白名单
├── installer/
│   ├── echo-install              # whiptail 装机 TUI(跑在 d-i 之前)
│   └── preseed.cfg                  # d-i 静态预置
└── base/
    ├── setup-base.sh                # Debian → Echo OS(7 步,幂等)
    ├── echo-appliance.service    # 后端单元
    ├── echo-os-time-machine.conf    # Time Machine over SMB 受管空白配置
    └── echo-nginx.conf           # 对外唯一入口(80/443)
```

## 装机流程

系统盘容量至少 **32 GiB**（约 34.4 GB）；标称 32 GB 的设备不满足当前分区布局。
容量未知或不足的磁盘不会进入清空确认；提交分区预置前还会重新检查所选磁盘容量。
安装会使用所选整盘，数据盘应与系统盘分离。

```
U 盘启动
  │
  ├─ preseed/early_command → /cdrom/echo-os/echo-install
  │     选系统盘 / 主机名 / 管理员密码 → debconf-set-selections
  │
  ├─ d-i 无人值守跑完:分区 → 装 Debian → 装 grub
  │     (静态策略来自 preseed.cfg)
  │
  ├─ late_command → 投放 setup-base.sh + 精确源码 bundle + 启用 firstboot
  │
  └─ reboot
        └─ 首次开机:setup-base.sh 七步
             1. 软件源      2. 存储栈(ZFS/Samba/NFS/Time Machine/SMART)
             3. Docker      4. Node
             5. echo-os(源码 + Python 依赖 + 前端构建)
             6. 原生 shell(nas 配置默认跳过；desktop 配置才安装)
             7. 服务(appliance + nginx + 每设备自签证书)
```

## 用法

```bash
# 开发/诊断镜像：允许依赖网络，不能当正式制品
./build-iso.sh --profile nas --mirror https://mirrors.ustc.edu.cn/debian

# 需要本机 HDMI 界面时显式生成 desktop 配置（默认 cage）
./build-iso.sh --profile desktop --iso ~/debian-13.1.0-amd64-netinst.iso

# 或指定本地 ISO
./build-iso.sh --iso ~/debian-13.1.0-amd64-netinst.iso --out ~/echo-os.iso

# 写入 U 盘
sudo dd if=dist/echo-os.iso of=/dev/sdX bs=4M status=progress && sync
```

正式 NAS 制品必须走严格发布入口，并使用 Debian 官方发布清单中的 SHA-256：

```bash
bash deploy/provision/build-release-iso.sh \
  --iso ~/debian-13.6.0-amd64-netinst.iso \
  --iso-sha256 <官方发布的64位SHA-256>

(cd dist && sha256sum -c echo-os-release.iso.sha256)
```

该入口先从同一干净 Git tree 构建 Web、Python wheelhouse、原生 Codex 与完整
system-deb 依赖仓，再调用 `build-iso.sh --release`。严格模式只支持无头 NAS，
必须在 Debian 13 (trixie) x86_64 + CPython 3.13 构建机执行，并拒绝缺少
任一载荷、未绑定基础 ISO、传入安装镜像源或脏工作树；d-i 只安装
netinst 介质内的 Debian 基座，SSH、sudo、Python、nginx、存储栈、固件和 Docker
统一在首次开机从已校验的本地仓安装。构建载荷时可以使用网络镜像，目标设备的
安装与首次开机不依赖公网。最终 ISO 同时生成同名 `.sha256` 清单。发布 wrapper
会重新打开成品 ISO，确认 BIOS/UEFI El Torito 入口，提取整张介质后复核
`md5sum.txt`、`release-manifest.json`、Git bundle 及五份载荷摘要；
manifest 会绑定基础 ISO、
源码 commit/tree、运行时版本和载荷摘要，并随安装保留到
`/etc/echo-os/release-manifest.json`。清单用于完整性和版本追溯，不是发布签名。

需要 `xorriso` 和 `isolinux`(提供 `isohdpfx.bin`),仅支持 Linux。

ISO 临时文件默认放在 `/var/tmp`，构建前检查该文件系统至少有 6 GiB 可用空间。
这个值只是准入下限；较大载荷和最终 ISO 还需要额外空间。Debian 的 `/tmp` 可能是
2 GiB 的 tmpfs，根分区空间充足也无法避免它写满。可用
`TMPDIR=/mnt/build ./build-iso.sh ...` 指定已有的构建目录；VM 构建还应检查
宿主机存放虚拟磁盘的分区，客体内的 `df` 无法反映宿主机剩余空间。

介质 `md5sum.txt` 不包含打包器会改写的 `isolinux/isolinux.bin` 和
`isolinux/boot.cat`；它用于检测载荷损坏，不用于认证发布者。引导能力还需分别
验证 BIOS/UEFI。严格入口会自动执行这些静态复核并记录整个 ISO 的 SHA-256；
实际启动、断网安装和发布者真实性仍须由 Linux/VM 验收及外部签名链证明。

## 已安装设备升级

裸机主机升级与 `deploy/appliance/upgrade-appliance.sh` 的容器镜像事务是两条
独立边界。更新 `/opt/echo-os` 源码后，先生成主机迁移计划，再用同一计划 ID
应用；当前 `nas-maintenance-v3` 计划只允许增量安装 NUT/SMART/
`samba-vfs-modules` 包、更新固定 systemd unit、补齐 Echo Time Machine 受管配置与
Samba `[global]` include，并启用 UPS 守卫与 SMART 定时器，不修改数据盘。Samba
配置会绑定计划、经 `testparm` 验证并在失败时原样回滚：

```bash
sudo /opt/echo-os/deploy/provision/upgrade-host.sh --plan
sudo /opt/echo-os/deploy/provision/upgrade-host.sh --apply <planId>
```

unit 或 systemd 操作失败时会恢复原 unit；已成功安装的 Debian 包不会自动卸载，
这一不可逆边界会明确写入计划和迁移凭据。

## 为什么重活不放装机阶段

ZFS DKMS、Docker 与完整 Python 环境耗时较长。放进 d-i，失败会让整台机器
装不起来；放到首次开机，可重试、可查日志(`journalctl -u echo-firstboot`)。
严格 release 已把这些步骤所需字节放进 ISO，本段说的是执行阶段，不代表联网。

源码本身不依赖首启网络：构建器把当前 HEAD 的完整 Git tree 做成单提交
`echo-source.bundle` 放入 ISO。首次开机从本地 bundle 恢复带 `.git` 的精确
工作树；严格 release 同时从 ISO 安装系统包、Python、Web 与 Codex，不会把移动
中的远端 `os-main`、PyPI/npm 或系统镜像源混进成品。普通开发镜像仍保留显式
联网回退，不能作为发布证据。

每步有哨兵文件(`/var/lib/echo-os/firstboot/<step>`),重跑自动跳过已完成
的部分。

## 关键取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 装机 TUI 注入方式 | `preseed/early_command` | 官方钩子跨版本稳定;参考 NAS的 initrd 注入依赖 d-i 内部结构,上游一改就碎 |
| 系统与数据 | 分离(系统盘 ext4 / 数据盘 ZFS 后组) | 与参考 NAS同思路,重刷系统不动数据 |
| 软硬件 | `nas` 默认无头，`desktop` 显式启用 HDMI shell | 虚拟 GPU/核显不等于用户需要桌面，纯 NAS 不应背图形栈 |
| TLS 证书 | 每设备首启生成 | 参考 NAS随包带固定私钥是隐患 |
| 提权 | 命令白名单,不给 `NOPASSWD:ALL` | 最小权限 |
