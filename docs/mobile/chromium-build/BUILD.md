# Echo Mobile · Chromium 真集成工程

> **路线 A · 真集成 Chromium for Android**
> 包 +80-120MB / 反爬免疫 / 可装 Chrome Web Store 扩展 / 完整 CDP

## 1. 现状（2026-06）

| 选项 | 状态 | 评分 |
|---|---|---|
| **Kiwi Browser** | 2024 停更，GitHub 仓 archive | ❌ |
| **Lemur Browser（狐猴）** | 闭源产品（lemurbrowser.com） | ❌ |
| **Brave for Android** | 活跃开源，但**不提供 WebView AAR** | ⚠️ |
| **Cromite** | Vanadium fork，主要给 GrapheneOS | ⚠️ 需编译 |
| **Iceraven** | 停止 | ❌ |
| **Vanadium** | 闭源（GrapheneOS 内部） | ❌ |

**结论**：没有现成的 "drop-in WebView AAR"，**必须自己编译**。本计划针对这条路。

## 2. 工程阶段

### Phase 0 · 环境准备（1-2 天）

**硬件要求**：

| 资源 | 最低 | 推荐 |
|---|---|---|
| CPU | 8 核 x86_64 | 16 核 / Apple Silicon |
| RAM | 16 GB | 32 GB（编译 webview 子集需 ~16 GB）|
| 磁盘 | 150 GB SSD | 250 GB NVMe |
| 网络 | 100 Mbps | 1 Gbps（首次拉取 30 GB）|

**OS**：Linux（推荐 Ubuntu 22.04 / Debian 12）/ macOS 14+。Windows 需要 WSL2。

**软件**：

```bash
# 1. depot_tools（Chromium 构建工具）
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$PWD/depot_tools:$PATH"

# 2. NDK r26b（Chromium 124+ 要求）
# 下载：https://developer.android.com/ndk/downloads
# 配环境变量
export ANDROID_NDK_ROOT=$HOME/android-ndk-r26b

# 3. Java 17（Android Gradle Plugin 9.x 要求）
sudo apt install openjdk-17-jdk

# 4. Python 3.11+（build 脚本）
sudo apt install python3.11

# 5. 必备系统库（Linux）
sudo apt install -y \
  build-essential ninja-build \
  libpulse-dev libnss3-dev libxss-dev libasound2-dev \
  libgconf-2-4 libdrm-dev libxcomposite-dev libxrandr-dev \
  libgtk-3-dev libgbm-dev
```

### Phase 1 · 拉 Chromium 源码（2-4 小时）

```bash
# 1. 创建工作目录
mkdir -p ~/chromium && cd ~/chromium

# 2. 拉取（注意：必须用 fetch 不用 git clone）
fetch --nohooks android

# 3. 进入 src 目录
cd src

# 4. 装额外 hooks（Android 必需）
./build/install-build-deps-android.sh
gclient runhooks

# 5. 验证
git log -1
# 应看到 chromium 124+ 的最新 commit
```

**警告**：网络要稳定。30 GB 拉取中断会损坏 git 状态。

### Phase 2 · 编译 webview 子集（4-8 小时首次）

```bash
cd ~/chromium/src

# 配 GN args：只为 Android 编译 webview 相关 target
cat > out/Default/args.gn <<EOF
target_os = "android"
target_cpu = "arm64"  # 或 "x86" / "arm"
is_debug = false
is_official_build = true
is_chrome_branded = false
# 关键：只编 webview + content
import("//chromium/config/android/rules.gni")
EOF

# 生成 ninja 文件
gn gen out/Default

# 编译 webview（核心 ~30 个 .so）
ninja -C out/Default webviewchromium_plat_support
ninja -C out/Default webview
ninja -C out/Default chromium_webview

# 编译产物在：
ls -la out/Default/lib.unstripped/
#   libwebviewchromium.so  (~60 MB)
#   libwebviewchromium_plat_support.so  (~30 MB)
```

**首次编译 4-8 小时**（视机器）。增量编译 5-15 分钟。

### Phase 3 · 打 AAR（半天）

```bash
# 写打包脚本
./tools/mb/mb.py gen out/Default \
  --custom-args='target_os="android" is_official_build=true'

# 关键：Chromium 没有现成 AAR，需要自己写 build_aar.sh
# 见 build_scripts/build_aar.sh
./build_scripts/build_aar.sh out/Default

# 产物
ls -la dist/
#   echo-webview-arm64-v8a-1.0.0.aar  (~85 MB)
```

### Phase 4 · 集成到 Echo Mobile（1 周）

```kotlin
// 1. 在 ../echo-mobile/app/build.gradle.kts 加
dependencies {
    implementation(files("libs/echo-webview-arm64-v8a-1.0.0.aar"))
}

// 2. 反射 / JNI 调用 WebView
class KiwiWebViewEngine : BrowserEngine {
    override fun createWebView(context: Context): WebView {
        val wv = WebView(context)
        // 用反射 / WebView.setWebContentsDebuggingEnabled 接管
        WebView.setWebContentsDebuggingEnabled(true)
        return wv
    }
}

// 3. 在 ClawApplication 启动时选择 engine
val engine: BrowserEngine = if (KiwiAarLoader.isAvailable()) {
    KiwiWebViewEngine()
} else {
    SystemWebViewEngine()
}
```

### Phase 5 · 扩展机制（1 周）

```kotlin
// 1. 解析 .crx3 文件
class Crx3Parser {
    fun parse(crxBytes: ByteArray): CrxPackage {
        // CRX3 格式：magic(4) + version(4) + header_len(4) + header + zip
    }
}

// 2. 加载 content_scripts 到 WebView
class ContentScriptInjector(private val webView: WebView) {
    fun inject(scripts: List<ContentScript>) {
        for (script in scripts) {
            webView.evaluateJavascript(script.code, null)
        }
    }
}
```

### Phase 6 · CDP 桥接（3-5 天）

```kotlin
// 1. 起本地 WebSocket 服务（9222 端口）
class CdpWebSocketServer {
    fun start() {
        // 用 OkHttp WebSocket
    }
}

// 2. 暴露 Page / DOM / Network 域
class CdpDomain {
    suspend fun navigate(url: String) { ... }
    suspend fun getDom(): DomSnapshot { ... }
}
```

## 3. 时间表（参考）

| Phase | 内容 | 估时 | 累计 |
|---|---|---|---|
| 0 | 环境准备 | 1-2 天 | 1-2 天 |
| 1 | 拉源码 | 2-4 小时 | 2-3 天 |
| 2 | 首次编译 | 4-8 小时 | 3-5 天 |
| 3 | 打 AAR | 半天 | 4-5 天 |
| 4 | Echo Mobile 集成 | 1 周 | 2-3 周 |
| 5 | 扩展机制 | 1 周 | 3-4 周 |
| 6 | CDP 桥接 | 3-5 天 | 4-5 周 |
| 7 | 7 个 BrowserTool | 3-5 天 | 5-6 周 |
| 8 | 调优 + 联调 | 2-3 周 | **2-3 月** |

## 4. 风险与回退

| 风险 | 影响 | 回退方案 |
|---|---|---|
| Chromium 编译失败 | 工程卡死 | 锁版本到 Chromium 124（最后稳定）|
| WebView API 变更 | 集成不兼容 | 锁 commit hash |
| 包大（+120MB）| 用户流失 | AAB + split 安装（按需下载）|
| 扩展 API 限制 | 部分扩展不能用 | 优先用 Brave 同源扩展 |
| 反爬仍被检测 | 业务受阻 | 改用真实 Chrome 进程的 Custom Tabs |

## 5. 当前本仓库已就绪的部分

| 组件 | 状态 | 位置 |
|---|---|---|
| BrowserEngine 抽象 | 🔄 进行中 | `../echo-mobile/.../echo_mobile/browser/BrowserEngine.kt` |
| SystemWebView 兜底 | 🔄 进行中 | `.../browser/SystemWebViewEngine.kt` |
| 7 个 BrowserTool | 📋 待写 | `.../tool/impl/browser/` |
| 编译脚本脚手架 | 📋 待写 | `docs/mobile/chromium-build/build_scripts/` |
| 7 个 browser.* SKILL.md | ✅ 已写 | `runtime/tentacle/mobile/skills/` |
| 反爬 + 装扩展设计 | ✅ 已写 | `docs/mobile/browser-integration.md` |
| 远程 CDP 兜底（路径 2）| 📋 待写 | `.../browser/CdpRemoteEngine.kt` |

## 6. 同步推进的"软"路径

**重要**：AAR 编译可能需要 1-2 月。但用户**今天**就能用上 7 个 browser.* 工具：
- 短期：SystemWebViewEngine（Android System WebView，0 包大，立即可用）
- 中期：ChromeCustomTabsEngine（用 Chrome 进程，反爬免疫完整）
- 长期：KiwiWebViewEngine（AAR 编译出来后切换）

策略：写抽象 + 兜底实现 + 7 个工具，**今天就能跑**。AAR 编译是后台长线任务。

## 7. 中断点

每个 Phase 完成后都是可中断点：
- Phase 0-1 完成后：源码已就绪，编译可后台跑
- Phase 2 完成后：AAR 可用，集成开始
- Phase 4 完成后：用户能装上 APK
- Phase 6 完成后：开发者能 CDP 调试
- Phase 7 完成后：功能完整

任何 Phase 卡住都可暂停，下个 sprint 继续。
