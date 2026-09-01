# Stage A · Windows + QEMU 装机验证(无需 Linux)

目标:在一台只有 Windows 的机器上,完整验证 `deploy/fnos/installer/preseed.cfg`
的静态策略与 late_command 投放逻辑 —— 装出的 Debian 13 真实可引导。

## 原理

不走 ISO 重打包(那需要 xorriso,Linux only),而是三件套:

1. 从 netinst ISO 抽出 `install.amd/vmlinuz` + `initrd.gz`(PowerShell 挂载复制)
2. 用 `tools/make_initrd_segment.py` 往 initrd.gz **末尾追加一段 gzip cpio**,
   内含 `preseed.cfg` 与 `echo-vmtest/` 载荷 —— Linux 内核 initramfs
   支持多段拼接(飞牛镜像里就是这种结构),无需重打包任何东西
3. QEMU `-kernel/-initrd` 直启,`-append` 指向 initrd 内的 preseed

## 前置

- QEMU for Windows:`winget install SoftwareFreedomConservancy.QEMU`
- netinst ISO(脚本按 `debian-13.6.0-amd64-netinst.iso` 写的,改版本同步改路径)
- 运行目录 `C:\vmtest`(ASCII 路径 —— 中文路径会让部分原生工具写盘失败)

## 步骤

```powershell
# 1. 抽内核与 initrd(挂载 -> 复制 -> 卸载,结果写 extract_status.txt)
powershell -File vmtest\tools\extract-iso.ps1 -IsoPath C:\vmtest\debian-13.6.0-amd64-netinst.iso -OutDir C:\vmtest

# 2. 追加 preseed 段(python,任意平台)
python vmtest\tools\make_initrd_segment.py C:\vmtest\initrd.gz C:\vmtest\initrd-vmtest.gz C:\vmtest\cpio-root

# 3. 建虚拟盘
qemu-img create -f qcow2 C:\vmtest\disk.qcow2 20G

# 4. 启动(推荐放在常驻的后台 shell 里前台跑,见下"已知坑"第 4 条)
powershell -File vmtest\tools\launch-vm.ps1 -WorkDir C:\vmtest -Mode install
```

装完信号:QEMU 进程退出(`-no-reboot`,d-i 重启即退出)。然后验证引导:

```powershell
powershell -File vmtest\tools\launch-vm.ps1 -WorkDir C:\vmtest -Mode boot
# 串口 serial-boot.log 里出现 "echo-vm login:" 即为通过
```

## cpio-root 内容

| 路径(虚拟机内) | 来源 | 说明 |
| --- | --- | --- |
| `/preseed.cfg` | `preseed-vmtest.cfg` | 正式 preseed 的 VM 变体,5 处差异见文件头注释 |
| `/echo-vmtest/setup-base.sh` | `../base/setup-base.sh` | 与正式版同文件 |
| `/echo-vmtest/echo-firstboot.service` | `../echo-firstboot.service` | 同上 |
| `/echo-vmtest/99-echo-os` | `../99-echo-os` | 同上 |

late_command 与正式版逻辑一致,仅源路径 `/cdrom/echo-os` → `/echo-vmtest`,
末尾追加 `echo stageA-ok > /target/var/log/echo-stageA.txt` 作验证标记。

## 已知坑(每条都真实踩过)

1. **PowerShell 5.1 把无 BOM 的 UTF-8 脚本当 ANSI/GBK 读**:中文注释的多字节
   序列会吞引号、截断参数(症状:`-append` 的值凭空消失)。`.ps1` 一律纯 ASCII。
2. **Git Bash 启动原生 Windows exe 会随机触发 cygwin `add_item` fatal error**:
   winget、qemu 都中过招。原生 exe 一律走 PowerShell 工具。
3. **本机环境变量同时存在 `http_proxy` 与 `HTTP_PROXY`**:Start-Process 构建
   环境字典时直接抛异常("已添加项")。启动前 Remove 小写副本。
4. **Start-Process 派生的 QEMU 会在数分钟内被静默杀掉**(无 stderr、无事件日志):
   疑似安全软件/沙箱清理游离进程。可靠做法:在**常驻 shell**(如 CI job、
   持久终端、agent 的后台任务)里前台运行 QEMU,输出重定向到文件。
5. **Start-Process -ArgumentList 数组形态不保留引号**:含空格的 `-append`
   会被拆散。用单个字符串传参,引号自带。
6. **curl 在中文路径下写盘报错 23**。下载产物放 ASCII 路径。

## 与正式链路(build-iso.sh)的关系

本目录验证的是 preseed 内容与 late_command 逻辑;ISO 组装、 isolinux/EFI
双引导、`/cdrom/echo-os` 载荷路径,仍由 `build-iso.sh` 在 Linux 上产出并
验证(Stage B)。两段通过后,M2(装机链路验证)闭环。
