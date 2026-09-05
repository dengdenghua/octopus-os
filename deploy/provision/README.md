# deploy/provision — Echo OS 装机链路

把 Debian 13 (trixie) 官方 netinst 改造成 Echo OS 装机镜像。

设计依据见 [`docs/P3_provision_BASE_PLAN.md`](../../docs/P3_provision_BASE_PLAN.md)。
一句话:**寄生式改造 d-i,不 fork 安装器一行代码。**

## 目录

```
deploy/provision/
├── build-iso.sh                     # 组装 ISO(解包 → 塞载荷 → 改引导 → 重打包)
├── echo-firstboot.service        # 首次开机跑 setup-base.sh
├── 99-echo-os                       # sudoers 命令白名单
├── installer/
│   ├── echo-install              # whiptail 装机 TUI(跑在 d-i 之前)
│   └── preseed.cfg                  # d-i 静态预置
└── base/
    ├── setup-base.sh                # Debian → Echo OS(7 步,幂等)
    ├── echo-appliance.service    # 后端单元
    └── echo-nginx.conf           # 对外唯一入口(80/443)
```

## 装机流程

```
U 盘启动
  │
  ├─ preseed/early_command → /cdrom/echo-os/echo-install
  │     选系统盘 / 主机名 / 管理员密码 → debconf-set-selections
  │
  ├─ d-i 无人值守跑完:分区 → 装 Debian → 装 grub
  │     (静态策略来自 preseed.cfg)
  │
  ├─ late_command → 投放 setup-base.sh + 启用 echo-firstboot.service
  │
  └─ reboot
        └─ 首次开机:setup-base.sh 七步
             1. 软件源      2. 存储栈(ZFS/Samba/NFS/SMART)
             3. Docker      4. Node
             5. echo-os(源码 + Python 依赖 + 前端构建)
             6. 原生 shell(检测到 GPU 才装)
             7. 服务(appliance + nginx + 每设备自签证书)
```

## 用法

```bash
# 自动下载最新 Debian netinst 并组装
./build-iso.sh --mirror https://mirrors.ustc.edu.cn/debian

# 或指定本地 ISO
./build-iso.sh --iso ~/debian-13.1.0-amd64-netinst.iso --out ~/echo-os.iso

# 写入 U 盘
sudo dd if=dist/echo-os.iso of=/dev/sdX bs=4M status=progress && sync
```

需要 `xorriso` 和 `isolinux`(提供 `isohdpfx.bin`),仅支持 Linux。

## 已安装设备升级

裸机主机升级与 `deploy/appliance/upgrade-appliance.sh` 的容器镜像事务是两条
独立边界。更新 `/opt/echo-os` 源码后，先生成主机迁移计划，再用同一计划 ID
应用；计划只允许增量安装 NUT/SMART 包、更新固定 systemd unit，并启用 UPS
守卫与 SMART 定时器，不修改数据盘：

```bash
sudo /opt/echo-os/deploy/provision/upgrade-host.sh --plan
sudo /opt/echo-os/deploy/provision/upgrade-host.sh --apply <planId>
```

unit 或 systemd 操作失败时会恢复原 unit；已成功安装的 Debian 包不会自动卸载，
这一不可逆边界会明确写入计划和迁移凭据。

## 为什么重活不放装机阶段

ZFS 编译、Docker 安装、前端构建都要联网且耗时。放进 d-i,失败会让整台机器
装不起来;放到首次开机,可重试、可查日志(`journalctl -u echo-firstboot`),
装机本身则又快又稳。

每步有哨兵文件(`/var/lib/echo-os/firstboot/<step>`),重跑自动跳过已完成
的部分。

## 关键取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 装机 TUI 注入方式 | `preseed/early_command` | 官方钩子跨版本稳定;参考 NAS的 initrd 注入依赖 d-i 内部结构,上游一改就碎 |
| 系统与数据 | 分离(系统盘 ext4 / 数据盘 ZFS 后组) | 与参考 NAS同思路,重刷系统不动数据 |
| 软硬件 | 无 GPU 自动跳过原生 shell | 纯无头 NAS 不该背几百 MB 图形栈 |
| TLS 证书 | 每设备首启生成 | 参考 NAS随包带固定私钥是隐患 |
| 提权 | 命令白名单,不给 `NOPASSWD:ALL` | 最小权限 |
