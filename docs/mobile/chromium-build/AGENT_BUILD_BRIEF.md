# AGENT BUILD BRIEF · Chromium WebView AAR 编译任务

> ⚠️ **状态：后备方案** — 当前主力引擎已切换为 GeckoView（Maven 一行依赖，自带 WebExtension 扩展支持）。
> 本文档仅在 GeckoView 无法满足需求时（如必须用 Chromium 指纹、必须装 CRX 原生格式）才启动。
> 优先级：GeckoView（主力）→ SystemWebView（兜底）→ Chromium AAR（本方案，后备）

> **目标读者**：另一个 AI agent（接手人）
> **任务**：从零开始，编译一份裁剪过的 Chromium Android WebView，打成 AAR，交付给本仓库
> **产物路径**：`../echo-mobile/app/libs/echo-webview-arm64-v8a-1.0.0.aar`
> **时间预算**：1-3 周（含首次编译 4-8 小时 + 排错）

---

## 0. 你的角色与边界

你是**执行 agent**，不是设计师。任务边界严格按本文档执行：

| 你需要做的 | 你不需要做的 |
|---|---|
| 拉源码、装依赖、配 GN args、跑编译 | 重新设计架构 |
| 解决编译错误（看 stack trace + Google）| 决定要不要换内核（已定 Chromium 124）|
| 报告进度与卡点 | 改 AAR 集成代码（Kotlin 那边另一个 agent 做）|
| 裁剪到目标大小（15-20 MB）| 写新功能 |

**遇到本文档未覆盖的决策时**：停下来回报主 agent，不要自作主张。

---

## 1. 最终交付物清单

完成后必须产出：

```
1. AAR 文件
   ../echo-mobile/app/libs/echo-webview-arm64-v8a-1.0.0.aar
   · 大小目标：15-20 MB（裁剪后）
   · 包含 jni/arm64-v8a/*.so + AndroidManifest.xml + proguard.txt

2. 编译报告
   docs/mobile/chromium-build/BUILD_REPORT.md
   · 最终 .so 列表 + 大小
   · 编译总耗时
   · 用过的关键 GN args
   · 已知遗留问题

3. SHA256 校验
   · AAR 文件的 sha256 写入 BUILD_REPORT.md
   · 校验方法：sha256sum <AAR 文件>
```

---

## 2. 前置环境检查

开始前**必须**确认每项：

```bash
# ── 硬件 ──
[ ] CPU ≥ 8 核 x86_64
[ ] RAM ≥ 16 GB（推荐 32 GB）
[ ] 磁盘可用 ≥ 200 GB SSD（推荐 NVMe）
[ ] 网络：能访问 chromium.googlesource.com 与 storage.googleapis.com

# ── 操作系统 ──
[ ] Linux: Ubuntu 22.04 / Debian 12（推荐）
[ ] macOS: 14+（次选）
[ ] Windows: 必须用 WSL2（Ubuntu 22.04）

# ── 软件依赖 ──
git --version          # ≥ 2.30
python3 --version      # ≥ 3.8（推荐 3.11）
java -version          # OpenJDK 17
gcc --version          # ≥ 11
make --version         # ≥ 4.3
```

如果任何一项不满足，**先解决再继续**。

---

## 3. 阶段划分（每个阶段都有"成功标志"）

### Phase 0 · 安装 depot_tools 和 NDK

**步骤**：

```bash
# 1. depot_tools
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git $HOME/depot_tools
echo 'export PATH="$HOME/depot_tools:$PATH"' >> ~/.bashrc
source ~/.bashrc
which fetch   # 应输出 /home/<user>/depot_tools/fetch

# 2. NDK r26b
wget -q https://dl.google.com/android/repository/android-ndk-r26b-linux.zip -O /tmp/ndk.zip
unzip -q /tmp/ndk.zip -d $HOME/
export ANDROID_NDK_ROOT=$HOME/android-ndk-r26b
echo 'export ANDROID_NDK_ROOT=$HOME/android-ndk-r26b' >> ~/.bashrc

# 3. Java 17（Ubuntu）
sudo apt install -y openjdk-17-jdk
sudo update-alternatives --config java   # 选 17

# 4. 系统库（Ubuntu）
sudo apt install -y \
  build-essential ninja-build pkg-config \
  libpulse-dev libnss3-dev libxss-dev libasound2-dev \
  libgconf-2-4 libdrm-dev libxcomposite-dev libxrandr-dev \
  libgtk-3-dev libgbm-dev curl unzip
```

**成功标志**：
- `which fetch` 有输出
- `$ANDROID_NDK_ROOT/source.properties` 存在
- `java -version` 显示 17.x
- `ninja --version` ≥ 1.10

---

### Phase 1 · 拉 Chromium 源码

**步骤**（直接跑现成脚本）：

```bash
bash docs/mobile/chromium-build/build_scripts/fetch_chromium.sh $HOME/chromium
```

**耗时**：2-4 小时（30 GB 拉取）。如果中断，重跑脚本会续传。

**成功标志**：
- `$HOME/chromium/src/` 存在
- `cd $HOME/chromium/src && git log -1` 输出 `124.0.6367.78` 或附近 commit
- `du -sh $HOME/chromium` ≥ 30 GB

**失败处理**：
- 网络中断 → 重跑脚本（自动续传）
- `gclient runhooks` 失败 → 看 `~/.gclient_logs/`，重跑
- 磁盘满 → 清理 `out/` 或换磁盘

---

### Phase 2 · 写裁剪版 GN args

**关键决策**：用激进裁剪（目标 15-20 MB），保留 WebRTC + Bluetooth + USB（自动化场景需要）。

在 `$HOME/chromium/src/out/EchoWebView_arm64/args.gn` 写入：

```gn
# Echo Mobile WebView · Aggressive Trim Config
# 目标：从 80 MB 砍到 15-20 MB

target_os = "android"
target_cpu = "arm64"
is_debug = false
is_official_build = true
is_chrome_branded = false
is_component_build = false

# ── 性能 / 体积优化 ──
symbol_level = 0
strip_debug_info = true
use_jumbo_build = true
use_thin_lto = true
use_lld = true

# ── 关掉大体积子系统（按从大到小排序）──

# 大头（40 MB 节省）
enable_pdf = false
enable_print_preview = false
enable_printing = false

# 关闭后台同步与上报
enable_background_mode = false
enable_service_discovery = false
enable_reporting = false
enable_mdns = false

# 关闭 metrics / crashpad（生产里可后端收集）
use_crashpad = false
enable_metrics = false

# 中头（10 MB 节省）
enable_webgl = false
enable_webgpu = false
enable_payments = false
enable_webxr = false
enable_pip = false

# 小头（5 MB 节省）
enable_nfc = false
enable_midi = false
enable_gamepad = false
enable_hid = false
enable_push = false
enable_media_router = false

# 硬件传感器（按需开，自动化可不要）
# enable_bluetooth = false   # ⚠️ 保留！工业自动化需要
# enable_usb = false          # ⚠️ 保留！IoT 自动化需要
# enable_serial = false       # ⚠️ 保留！串口调试
# enable_nfc = false

# 关闭 location 以外的传感器
# Geolocation 保留（地图需要）

# ── 必装（不能砍）──
enable_webrtc = true        # 反爬指纹检测要用
enable_websockets = true
enable_extensions = true    # CRX 支持
enable_offline_pages = true
enable_clipboard = true
enable_credential_management = true
enable_geolocation = true
enable_notifications = true
enable_dom_distiller = true

# ── Android 必需 ──
import("//build/config/android/rules.gni")
android_webview_use_incremental_install = true
```

**成功标志**：
- `gn gen out/EchoWebView_arm64 --check` 无错
- 生成的 ninja 文件大小 > 100 MB

---

### Phase 3 · 首次编译

**步骤**：

```bash
cd $HOME/chromium/src

# 1. 跑现成脚本（已用上面的 args.gn 配置）
bash docs/mobile/chromium-build/build_scripts/build_webview.sh $HOME/chromium/src arm64

# 2. 后台跑（如果想断网也行）
cd $HOME/chromium/src
nohup ninja -C out/EchoWebView_arm64 -j8 webview > out/build.log 2>&1 &
echo $! > out/build.pid
```

**耗时**：首次 4-8 小时（视机器）。增量 5-15 分钟。

**监控命令**：

```bash
# 进度
tail -f $HOME/chromium/src/out/EchoWebView_arm64/build.log

# 是否还在跑
ps -p $(cat $HOME/chromium/src/out/EchoWebView_arm64/build.pid)

# 剩余时间估算
ninja -C $HOME/chromium/src/out/EchoWebView_arm64 -j8 webview  # 再跑一次会显示
```

**成功标志**：
- `out/EchoWebView_arm64/lib.unstripped/libwebviewchromium.so` 存在
- `out/EchoWebView_arm64/lib.unstripped/libwebviewchromium_plat_support.so` 存在
- `libwebviewchromium.so` 大小 ≤ 20 MB（裁剪后），如果 > 30 MB 说明裁剪没生效

**失败处理（编译错误的 5 个常见模式）**：

| 错误 | 原因 | 处理 |
|---|---|---|
| `undefined reference to ...` | LTO 漏符号 | 加 `-Wl,--no-as-needed` 到 ldflags |
| `ninja: error: ... missing dep` | gclient 没装全 | `cd src && gclient sync` |
| `ERROR: Can't find android_ndk_root` | NDK 路径错 | 检查 `$ANDROID_NDK_ROOT` |
| `Java version too old` | JDK 不是 17 | `update-alternatives --config java` |
| `out of memory` | jobs 太多 | 把 `-j8` 降到 `-j4` |

**遇到未列出的错误**：把完整 stack trace 贴回报，记录到 `docs/mobile/chromium-build/BUILD_ISSUES.md`。

---

### Phase 4 · 打 AAR

**步骤**：

```bash
bash docs/mobile/chromium-build/build_scripts/build_aar.sh $HOME/chromium/src/out/EchoWebView_arm64
```

**产物**：

```
$HOME/chromium/dist/echo-webview-arm64-v8a-1.0.0.aar
```

**验证 AAR 内容**：

```bash
unzip -l $HOME/chromium/dist/echo-webview-arm64-v8a-1.0.0.aar
# 应看到：
#   AndroidManifest.xml
#   proguard.txt
#   jni/arm64-v8a/libwebviewchromium.so
#   jni/arm64-v8a/libwebviewchromium_plat_support.so
#   jni/arm64-v8a/libchromium_android_linker.so
#   res/values/*.xml
```

**成功标志**：
- AAR 文件存在
- AAR 大小：15-25 MB（裁剪后）
- 关键 .so 都存在

---

### Phase 5 · 复制到本仓库

```bash
# 1. 复制 AAR
mkdir -p ../echo-mobile/app/libs
cp $HOME/chromium/dist/echo-webview-arm64-v8a-1.0.0.aar ../echo-mobile/app/libs/

# 2. 算 SHA256
sha256sum ../echo-mobile/app/libs/echo-webview-arm64-v8a-1.0.0.aar
# 记录到 BUILD_REPORT.md
```

---

### Phase 6 · 写编译报告

新建 `docs/mobile/chromium-build/BUILD_REPORT.md`：

```markdown
# Chromium WebView AAR · 编译报告

## 编译时间
- 开始：YYYY-MM-DD HH:MM
- 结束：YYYY-MM-DD HH:MM
- 总耗时：X 小时 Y 分钟

## 机器配置
- CPU：
- RAM：
- 磁盘：
- OS：

## 关键版本
- Chromium：124.0.6367.78
- depot_tools commit：xxx
- NDK：r26b

## 关键 GN args
（粘贴用过的 args.gn 全文）

## 最终 .so 列表
| .so 文件 | 大小 |
|---|---|
| libwebviewchromium.so | XX MB |
| ... | ... |
| **合计** | XX MB |

## AAR 信息
- 文件：echo-webview-arm64-v8a-1.0.0.aar
- 大小：XX MB
- SHA256：xxx

## 已知问题
（如果编译里有 warning 或遗留问题）

## 集成提示
（给 Kotlin 集成 agent 的注意事项）
```

---

## 4. 失败 / 卡点时的回报协议

**回报时机**（任意一条满足就回报主 agent，不要硬撑）：

1. 同一错误连续 3 次重试都失败
2. 编译首次跑超过 12 小时还没结束
3. AAR 砍不到 30 MB 以下（说明裁剪配置没生效）
4. 任何文档未覆盖的架构决策
5. 硬件 / 资源不够

**回报格式**（写到 `docs/mobile/chromium-build/BUILD_ISSUES.md`）：

```markdown
## Issue #X · 标题
- 阶段：Phase X
- 时间：YYYY-MM-DD HH:MM
- 现象：（具体错误）
- 已尝试：（3 次重试都失败）
- 影响：编译卡住 X 小时
- 建议方案：（如果能想到）
```

主 agent 收到后会告诉你：继续 / 跳过 / 改方案。

---

## 5. 中断点（每个 Phase 完成后都可暂停）

- **Phase 0-1 后**：源码就绪，编译可后台跑
- **Phase 3 后**：.so 文件可用，但 AAR 没打
- **Phase 4 后**：AAR 可用，集成开始
- **Phase 5 后**：已交付本仓库

每个中断点都安全，重启可续。

---

## 6. 不要做的事（红线）

- ❌ 改 Chromium 源码（哪怕只是注释）
- ❌ 用 `git clone` 代替 `fetch`（会拉错分支）
- ❌ 编译完不验证 .so 大小（可能有模块没关掉）
- ❌ AAR 不 strip 调试符号（+30 MB 浪费）
- ❌ 跳过 `gclient runhooks`（Android 编译会缺组件）
- ❌ 切到 Chromium main 分支（API 会变，必须锁 124）

---

## 7. 关键技术参考

遇到具体问题查这些：

| 问题类型 | 查哪里 |
|---|---|
| GN args 字段含义 | https://gn.googlesource.com/gn/+/main/docs/reference.md |
| Chromium 编译 FAQ | https://chromium.googlesource.com/chromium/src/+/main/docs/android_build_instructions.md |
| WebView 编译 target | `src/chrome/android_webview/BUILD.gn` |
| 各模块大小 | https://chromium.googlesource.com/chromium/src/+/main/docs/size.md |
| 编译错误搜索 | https://groups.google.com/a/chromium.org/g/chromium-dev |

---

## 8. 联系主 agent 的方式

回报写到 `docs/mobile/chromium-build/BUILD_ISSUES.md`，主 agent 会轮询。

如果紧急，写到 `docs/mobile/chromium-build/BUILD_BLOCKED.md` 并在文件首行加 `STATUS: BLOCKED`。

---

**开始吧**。先把 `docs/mobile/chromium-build/BUILD_ISSUES.md` 初始化成空文件，从 Phase 0 启动。
