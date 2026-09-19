# RK3576 (tvi3512r) ARM64 系统固件装配线

Status: **scaffold（骨架已落地，未经硬件验证）**。与 x86 ISO 链路
（`deploy/provision/`）完全平行，**不修改其中任何文件**；与现有
`deploy/rk3576/`（厂商 Debian 容器 overlay，路线 B）也互不影响。

## 目标形态

板子刷入的固件**就是 Echo OS**：上电 → U-Boot → BSP 内核 6.1 →
systemd → `echo-appliance.service`（NAS 控制面 + Agent runtime）。
无 Docker 层、无厂商 Hub 依赖、无首启联网下载。

## 两条路线的取舍（决策记录）

| | 路线 A：本目录（rootfs 固件） | 路线 B：`../`（容器 overlay） |
|---|---|---|
| 形态 | Echo 直接住在 rootfs，systemd 管理 | 厂商系统作宿主，Echo 住容器 |
| 存储栈 | ZFS/udisks2/smartctl/Samba 直宿主内核 | 受限（no /dev、无 ZFS） |
| 符合"板就用我们系统" | ✅ | ❌（厂商系统是宿主） |
| 风险 | BSP 内核 ABI、打包链待验证 | 已部分落地但能力残缺 |

结论：走 A 做固件；B 保留作厂商共存期的过渡验证手段。

## 与 x86 链路的复用关系（只读复用，零修改）

| 复用物 | 来源 | 用法 |
|---|---|---|
| 包清单基线 | `deploy/provision/system-packages-nas.txt` | 派生为 `packages-arm64.txt`（去掉 amd64/intel 固件与 microcode，去掉首启 docker-ce——固件烘焙期决定，加 udisks2 等） |
| 服务单元范式 | `deploy/provision/base/echo-appliance.service` | `overlay/echo-appliance-firmware.service` 几乎逐行同构 |
| firstboot 幂等范式 | `deploy/provision/base/setup-base.sh` | `overlay/setup-firmware.sh` 同样哨兵+阶段，但阶段集缩减为 identity/storage/services |
| 更新通道 | `deploy/update/`（sysupdate.d/trust） | P2 接入，固件期先手动刷写 |
| Agent 载荷管线 | `deploy/appliance/prepare-agent-bundle.sh`（`ECHO_LINUX_ARCH=arm64`） | 构建主机产出 `agent-codex` aarch64-musl 切片 |

关键差异：x86 ISO 路线是**首启在线/离线包装配**（setup-base 十阶段）；
固件路线是**构建期全部烘焙**，首启只做设备相关三步，严格离线。

## 构建流程（Linux x86_64 构建主机）

```sh
# 0. 前置:解压 SDK(cat tgzaa tgzab > tgz && tar xzf)到 Linux 主机
#    需要: losetup/mount(root)、qemu-user-static、binfmt 支持

# 1. 出厂商基线(拿到能启动的板子 + parameter.txt/打包链)
cd rk3576_linux-6.1 && source envsetup.sh && lunch   # 选 tvi3512r debian 目标
./build.sh && ./mkfirmware.sh                         # 先刷原厂固件验证硬件

# 2. 产出 Echo 载荷(仓库根目录,同 Docker 构建前奏)
pnpm --dir frontend install --frozen-lockfile
ECHO_LINUX_ARCH=arm64 ./deploy/appliance/prepare-agent-bundle.sh
./deploy/provision/build-python-wheelhouse.sh         # 确认 arm64 闭包
# 载荷目录布局(供 stage_payload 消费):
#   echo-payload/
#     wheelhouse/        # --only-binary 闭包(arm64)
#     webui-dist/        # pnpm build 产物
#     agent-codex/       # aarch64-unknown-linux-musl 切片
#     tree/              # 本仓库源码树(含 deploy/)

# 3. 装配 rootfs 并打包
RK_SDK_DIR=~/rk3576_linux-6.1 \
RK_ROOTFS=out/rootfs.img \
ECHO_PAYLOAD=./echo-payload \
BSP_HEADERS_DEB=out/kernel-headers.deb \
  ./deploy/rk3576/firmware/build-rootfs.sh
```

## 阶段清单（`build-rootfs.sh`，每阶段带哨兵可重入）

| 阶段 | 动作 | 风险点 |
|---|---|---|
| preflight | 校验 SDK/rootfs/qemu/载荷齐备 | `tvi3512r` parameter.txt 定位 |
| mount | loop 挂载 rootfs + chroot 布置 | rootfs 分区容量需扩（见开放问题） |
| apt | chroot 安装 `packages-arm64.txt` | 首版允许在线；量产改离线 deb 仓 |
| sdk-userspace | 烘焙 RKNN/RKLLM/MPP/RGA/libmali 用户态 deb | deb 产物与许可以 SDK `debian/` 输出为准 |
| zfs | 装 BSP headers → `dkms` 构建 OpenZFS kmod | **P0**：BSP 6.1 树必须能出 headers |
| payload | wheel→`/opt/echo-os/.venv`、webui、codex、deploy 树烘焙 | manifest 校验复用 `agent_bundle.py verify` |
| overlay | 拷入 systemd 单元 + firstboot 脚本并 enable | — |
| resource-tune | 按内存档位写死 ARC/zram/内存上限/journald 封顶（见下节） | 4G+桌面组合大声警告 |
| pack | 卸载 + e2fsck，交回 SDK `mkfirmware.sh` → `update.img` | rootfs 分区大小须同步调大 |

## 资源档位（`ECHO_FW_RAM_GB`，resource-tune 阶段落盘）

原则（对照 fnOS 实测 2.05GB 全功能足迹得出的三条）：**ARC 是最大的可压项**（ZFS 默认吃一半 RAM）；
**zram 是低内存 ARM 标配**（LPDDR 带宽 14.9GB/s，A72 花 CPU 换内存划算）；
**模型权重压不动**（NPU 共享主存，只能按档位选模型）。

| 档位 | ARC cap | zram | 后端 MemoryHigh | 适用形态 |
|---|---|---|---|---|
| 4G | 512M | 3G | 1.5G | headless（`ECHO_FW_DESKTOP=0`）+ RKLLM 1.5B |
| 8G（默认） | 1G | 2G | 3G | 全形态 + RKLLM 3B |
| 16G | 2G | 4G | 6G | 全形态 + RKLLM 7–8B |

落盘五件：`/etc/modprobe.d/echo-zfs.conf`（ARC）、`/etc/systemd/zram-generator.conf`（zstd），
`/etc/sysctl.d/99-echo-tune.conf`（swappiness=100 让 zram 承接匿名页）、
`echo-appliance-firmware.service.d/memory.conf`（MemoryHigh/MemoryMax）、
`journald.conf.d/echo-cap.conf`（64M/7d，eMMC 寿命）+ `fstrim.timer`。
全部构建期定死，首启零动作、零联网。

**agent 常驻进程三件套**（写在 `echo-appliance-firmware.service` 的 Environment）：
`LD_PRELOAD=libjemalloc2` + `MALLOC_ARENA_MAX=2`（glibc arena 碎片化，长驻 Python
实测多占 30–50% RSS）、`OMP/OPENBLAS/NUMEXPR_NUM_THREADS=2`（big.LITTLE 上压
BLAS 线程爆炸）。⬜ P2 待做：agent 栈懒加载审计（sensing/hub 不用不 import）；
RKLLM 空闲自动卸载（lease + idle timeout）。

## 首启三步（`overlay/setup-firmware.sh`，严格离线）

1. **identity** — machine-id / 设备身份落盘
2. **storage** — 加载 zfs kmod、探测并 import 既有 pool；**不自动建池**
   （建池走 Web 桌面的 native-storage broker 引导，与 x86 行为一致）
3. **services** — 拉起 `echo-appliance-firmware.service`；桌面栈经
   `hdmi-hotplug.sh` 初始判定（无显示器不启动）

## HDMI 热插拔（实时升降，2026-09-13 拍板）

桌面栈是"进程"不是"显示器"——不拔就关，1.5–2.5G 内存照付。热插拔通路：

```
HDMI 插拔 → 内核 DRM change 事件 → udev(99-echo-hdmi.rules)
  → systemd 按需拉起 echo-desktop-hotplug.service(oneshot,不阻塞 udev)
    → hdmi-hotplug.sh: 插入→enable --now sddm / 拔出→disable --now sddm
```

- 状态唯一事实源：`/var/lib/echo-os/desktop-mode.txt`（mode + event 来源）
- `ECHO_FW_FORCE_DESKTOP=1` 强制开（无视显示器状态）
- `ECHO_FW_HOTPLUG_KEEP=1` 拔线**不**关桌面（保留会话，放弃内存回收）
- 探测用 `grep '^connected'` 锚定行首——"disconnected" 含 "connected" 子串，
  裸 grep 必误判（固件 bug 预防记录在案）

## SDK 组件对账（哪些进固件）

### 已集成（固件链直接消费）

| SDK 组件 | 用法 |
|---|---|
| `kernel-6.1/`（含 rknpu/VPU/RGA 内核驱动） | BSP 内核 + DTB 原样保留，厂商所有权不动 |
| `u-boot/` + `rkbin/` | 启动链 idbloader/TPL/SPL/TOS blob 原样进固件 |
| `device/rockchip/` | 板级配置 + parameter.txt 分区表 + build.sh/mkfirmware.sh 打包链 |
| `debian/` | rootfs 底座来源（路线 A 核心） |
| `tools/` | 烧写（upgrade_tool/RKDevTool） |
| `prebuilts/` | aarch64 工具链（编内核模块时备用） |

### 已纳入集成（2026-09-12 决策：RKNN + 音视频硬加速进固件）

| SDK 组件 | 集成方式 | 消费方 |
|---|---|---|
| `external/rknpu2`（Runtime） | **用户态 deb 烘焙**：`librknnrt.so` 进 rootfs；rknpu 内核驱动随 BSP 内核 | Echo 本地 AI 栈新增 **NPU provider**（librknnrt C API / rknn-lite2 绑定） |
| `external/rknn-toolkit2` | **不进固件**——x86_64 模型转换工具，跑在**构建主机**；`.rknn` 预转换后作载荷 | 模型仓库烘焙至 `/opt/echo-os/models/` |
| `external/mpp` | 用户态 deb（librockchip-mpp + 工具）烘焙；VPU 驱动随内核 | 媒体服务硬件转解码（ffmpeg/gst 走 mpp） |
| `external/linux-rga` | 用户态 deb（librga）烘焙；RGA 驱动随内核 | 转码色彩空间转换/缩放卸载 |
| `external/libmali` | **已纳入**（2026-09-12 拍板 HDMI 本地桌面）：须选 **Wayland+GLES** 变体 blob；消费方是 SDDM+KWin(Wayland) 桌面栈（`packages-arm64.txt` 桌面层） | 板载 HDMI 桌面 |
| `external/rockit`、`iva/avs` | **暂缓**——绑定 rockit 框架的 IPC 链路，确认 Echo 媒体面需要后再接 | — |

**Echo 侧进展**（2026-09-12 落地）：
1. ✅ `runtime/sensing/model_router/managed_rknn.py` — NPU 探针/模型目录/
   租约/开关，与 managed_ollama 同构。关键区分：**LLM 走 RKLLM
   （librkllmrt/.rkllm），视觉/经典走 RKNN（librknnrt/.rknn）**，转换均在
   x86 主机做。`start()` 未接线前**响亮失败**，绝不静默回落 CPU
2. ✅ `hwfit.detect_hardware` 新增 `rknn-npu` 档位（设备+runtime 齐备时，
   LPDDR 带宽 14.9 GB/s 做 decode 屋顶线，不再误判为"CPU 慢"）
3. ✅ `local_ai_deployment.status()` 三个生命周期分支均暴露 `npu` 探针
4. ⬜ ctypes 桥接 librkllmrt（等固件 bring-up + SDK runtime deb 实物）
5. ⬜ 媒体服务 mpp 硬件转码配置（探测 `/dev/mpp_service`、`/dev/rga`）
6. ⬜ 首启 storage/services 阶段补设备节点健康检查（`/dev/rknpu*` 存在性）

测试：`tests/test_managed_rknn.py`（13 用例，硬件边缘全部可 monkeypatch）。

## 桌面形态澄清（2026-09-12 修正）

Echo 桌面本体是 **Electron 应用**（`frontend/electron/main.cjs` →
`echo-os-desktop`；x86 会话脚本 `echo-desktop-session.sh` 启动的就是
`$APP_DIR/release` 里的它）。nginx + 浏览器只是 headless/服务器通道。
因此固件 HDMI 桌面应烘焙 **Electron arm64 包**，而非只放 webui-dist：

- electron-builder 当前只出 `--x64`（`packaging/image/build-image.sh:38`），
  需加 **`--linux dir --arm64` 变体** → 载荷 `desktop/` →
  `/opt/echo-os/frontend/release/`
- Electron 自带 Chromium，**不需要** SDK `external/chromium`（维持排除；
  此前"桌面走浏览器"的说法仅对 headless 通道成立，已纠正）
- KWin 会话脚本按 `$APP_DIR/release` 找应用，固件同路径即可复用；
  x86 硬编码路径移植照旧记在开放问题

用户态 deb 用 `RK_USERSPACE_DEB_DIR` 喂给 `build-rootfs.sh` 的
`sdk-userspace` 阶段；确切包名以 SDK 实际产物为准（`sdk-userspace.txt`）。

### 明确不集成

| SDK 组件 | 理由 |
|---|---|
| `buildroot/` + `yocto/` | 三套构建系统只用 Debian 一路 |
| `external/camera_engine_rkaiq`、`app/rkipc`、`rkadk` | 摄像头/IPC 厂商业务面 |
| `external/chromium`、`xserver` | Electron 自带 Chromium、桌面走 KWin/Wayland，不需要 SDK 的浏览器/X |
| `external/dpdk`、`rk_ethercat` | 高性能网络/工控，无需求 |
| `external/security`（rkcrypto/TEE）、`recovery` | P2 量产化再评估 |

## 开放问题（刷机前必须逐一关闭）

- [ ] 板卡 RAM 配置（≥8GB 才达 NAS 验收线；4GB 需压 ARC）
- [ ] `tvi3512r` 分区表（parameter.txt）与 rootfs 分区可扩容上限
- [ ] BSP 6.1 内核 headers 产出方式（SDK `debian/` 是否自带，否则自打包）
- [ ] OpenZFS 版本选型（2.2.x 对 6.1 的兼容验证）
- [ ] 存储介质路径：eMMC 直用 or SATA/combo-PHY 出盘位（看板卡引出）
- [ ] GPU/NPU 用户态库 ABI 与授权：**libmali/librknnrt/mpp/rga 均已纳入**，
      deb 产物与许可条款需从 SDK `debian/` 输出确认
- [ ] **libmali 变体选择**（新 P0）：Wayland+GLES 变体（板载桌面走
      SDDM+KWin/Wayland）；变体选错整条显示链起不来
- [ ] **KWin/Mali 适配验证**（新）：KWin 在 Mali blob 上的 GLES 渲染、
      窗口桥/液态玻璃效果是否可用
- [ ] **desktop-session x86 硬编码路径移植**（新）：现有会话脚本硬编码
      `/usr/lib/x86_64-linux-gnu/...`（polkit-kde-agent、powerdevil 等），
      arm64 下需加架构守护；本目录零改动 x86 链路，移植版放固件 overlay
- [ ] **Electron arm64 桌面包**（新 P0）：electron-builder 加
      `--linux dir --arm64` 变体；Electron/Mali 的 GLES(ozone-wayland)
      硬件加速待板端验证，回落软渲染则 HDMI 桌面不可用
- [ ] rknn backend 设计：provider 接口、`.rknn` 模型清单、CPU 回落路径
- [ ] 媒体面硬加速验证：板端 `gst-inspect`/`mpi_dec_test` 确认 mpp 通路
- [ ] 量产 OTA：`deploy/update` sysupdate 通道适配 A/B 分区
