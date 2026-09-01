# Echo Mobile · 技能体系

> **30+ 移动技能 · SKILL.md 驱动 · 自进化可热加载**

## 1. 设计原则

1. **每个技能 = 一个 SKILL.md**（Markdown 描述，参考 SKILL.md 协议）
2. **每个技能可被独立装载**（无需重新编译 APK）
3. **每个技能有明确 affinity**（让 Cerebrum 知道何时调用）
4. **每个技能可远程下发**（Regeneration 锻造的新技能自动推送到手机）

## 2. 技能分类

### 2.1 基础操作（5 个）⭐ 必装

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.tap` | 点击 | 在坐标 (x, y) 处点击 |
| `android.swipe` | 滑动 | 从 (x1, y1) 滑到 (x2, y2) |
| `android.input_text` | 输入文本 | 在当前聚焦输入框输入文本 |
| `android.long_press` | 长按 | 在坐标处长按 N 毫秒 |
| `android.system_key` | 系统按键 | 模拟 Home / Back / Recents / Power / Volume |

### 2.2 屏幕感知（4 个）⭐ 必装

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.get_screen_info` | 获取屏幕信息 | 无障碍树（节点+文字+坐标+类名）|
| `android.take_screenshot` | 截图 | 当前屏幕截图（base64 PNG）|
| `android.find_node` | 查找节点 | 按 text/desc/class 找节点 |
| `android.find_text` | 查找文本 | 返回包含目标文字的节点列表 |

### 2.3 应用管理（4 个）

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.open_app` | 打开应用 | 按 app_name 或 package_name 启动 App |
| `android.install_app` | 安装应用 | 从 URL 或本地路径安装 APK |
| `android.get_installed_apps` | 已装应用列表 | 返回所有已装 App 列表 |
| `android.wait` | 等待 | 等待 N 毫秒 / 等待节点出现 / 等待节点消失 |

### 2.4 智能复合（4 个）

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.scroll_to_find` | 滚动查找 | 滚动直到找到目标文字 |
| `android.detect_dialog` | 检测弹窗 | 检测系统/应用弹窗并尝试关闭 |
| `android.find_and_tap` | 查找并点击 | 按 text 找节点并点击（最常用）|
| `android.get_current_app` | 当前前台应用 | 返回当前 package_name + activity |

### 2.5 任务控制（2 个）

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.finish` | 完成任务 | 通知 LLM 任务完成（带可选摘要）|
| `android.fail` | 任务失败 | 通知 LLM 任务失败（带错误信息）|

### 2.6 浏览器操作（7 个）🔮 Phase 7

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.browser.navigate` | 导航 | 打开 URL，等待加载 |
| `android.browser.get_dom` | 获取 DOM | 当前页 DOM 树（YAML/JSON）|
| `android.browser.click` | 浏览器点击 | 通过 selector 或 ref 点击 |
| `android.browser.type` | 浏览器输入 | 在聚焦元素中输入 |
| `android.browser.screenshot` | 浏览器截图 | 当前页截图 |
| `android.browser.evaluate` | 执行 JS | 执行任意 JS 并返回结果 |
| `android.browser.install_extension` | 装扩展 | 安装 Chrome 扩展 |

### 2.7 文件与剪贴板（4 个）⭐ 实用

| 技能 ID | 名称 | 描述 |
|---|---|---|
| `android.read_file` | 读文件 | 读 /sdcard/ 或 /data/data/ 中的文件 |
| `android.write_file` | 写文件 | 写文件到 /sdcard/ |
| `android.get_clipboard` | 读剪贴板 | 获取当前剪贴板内容 |
| `android.set_clipboard` | 写剪贴板 | 设置剪贴板内容 |

**总计 30 个技能**（与 Echo Mobile 现有 30 个 BaseTool 一一对应）。

---

## 3. 技能详细说明

### 3.1 `android.tap` · 点击

**SKILL.md**：
```yaml
---
name: android.tap
description: |
  Tap at coordinate (x, y). Use this to click buttons, links, icons.
  Coordinates come from android.get_screen_info's `bounds` field.
  Always get_screen_info first to find the right coordinate.
affinity: [mobile, gui, automation, input]
parameters:
  - name: x
    type: integer
    required: true
    description: X coordinate in pixels
  - name: y
    type: integer
    required: true
    description: Y coordinate in pixels
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after tap
---

# Android Tap

## When to use
- After `get_screen_info` to click a known element
- Single tap on a button or icon

## When NOT to use
- For text input → use `android.input_text`
- For long press → use `android.long_press`
- For swipe → use `android.swipe`

## Best practices
- Always use coordinates from the latest `get_screen_info` result
- The node center is `(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2`
- Add `wait_after` if the tap triggers a network call (default 1000ms)

## Example
```json
{
  "tool": "android.tap",
  "args": {"x": 540, "y": 1200, "wait_after": 1000}
}
```
```

### 3.2 `android.get_screen_info` · 获取屏幕信息

这是**最常用**的技能，**类似 Echo Mobile 的同款**。

**返回结构**：
```json
{
  "current_app": "com.tencent.mm",
  "current_activity": "com.tencent.mm.ui.LauncherUI",
  "screen_size": [1080, 2400],
  "is_keyboard_shown": false,
  "tree": [
    {
      "ref": "e001",
      "class": "android.widget.FrameLayout",
      "text": "",
      "desc": "",
      "bounds": [0, 0, 1080, 2400],
      "clickable": false,
      "enabled": true,
      "children": [
        {
          "ref": "e002",
          "class": "android.widget.LinearLayout",
          "bounds": [0, 100, 1080, 300],
          "clickable": true,
          "children": [
            {
              "ref": "e003",
              "class": "android.widget.TextView",
              "text": "微信",
              "bounds": [50, 130, 150, 200],
              "clickable": true
            }
          ]
        }
      ]
    }
  ]
}
```

**关键优化**（参考 Echo Mobile）：
1. **过滤空节点**（无 text/desc/不可交互）
2. **类名简化**（`android.widget.TextView` → `TextView`）
3. **不可见节点不递归子节点**
4. **稳定 ref ID**（基于父子关系 + 兄弟索引）

### 3.3 `android.open_app` · 打开应用

```yaml
name: android.open_app
parameters:
  - name: app_name
    type: string
    required: false
    description: |
      App name (Chinese or English), e.g. "微信" or "WeChat".
      Case-insensitive fuzzy match.
  - name: package_name
    type: string
    required: false
    description: Exact package name, e.g. "com.tencent.mm"
  - name: wait_after
    type: integer
    default: 2000
```

**实现逻辑**（按优先级）：
1. 如果给了 `package_name`，直接 `am start -n pkg/.MainActivity`
2. 否则扫描已装 App，按 `app_name` 模糊匹配
3. 启动后等待 `wait_after`，返回当前 Activity

**常见 App 别名映射**（built-in）：

| app_name | package_name |
|---|---|
| 微信 / WeChat | com.tencent.mm |
| 淘宝 / Taobao | com.taobao.taobao |
| 京东 / JD | com.jingdong.app.mall |
| 抖音 / TikTok | com.ss.android.ugc.aweme |
| 美团 / Meituan | com.sankuai.meituan |
| 钉钉 / DingTalk | com.alibaba.android.rimet |
| 飞书 / Feishu | com.bytedance.ee.lark |
| 支付宝 / Alipay | com.eg.android.AlipayGphone |
| QQ | com.tencent.mobileqq |
| 网易云音乐 / NetEaseMusic | com.netease.cloudmusic |

### 3.4 `android.scroll_to_find` · 滚动查找（复合技能）

```yaml
name: android.scroll_to_find
description: |
  Scroll the current screen up/down until the target text/element appears.
  Returns when found or max scrolls reached.
parameters:
  - name: target
    type: string
    required: true
    description: Text to find (or desc/class with prefix)
  - name: direction
    type: string
    required: false
    default: down
    enum: [up, down, left, right]
  - name: max_scrolls
    type: integer
    default: 10
  - name: scroll_duration_ms
    type: integer
    default: 300
```

**实现**：
1. 调用 `get_screen_info` 看是否包含 target
2. 如果没有，调用 `swipe` 滚动
3. 重复直到找到或达到 max_scrolls
4. 返回最终位置 + 是否找到

**Anti-pattern 防御**：
- 如果连续 3 次滚动都没找到，注入 LLM 提示"目标可能不在当前屏幕或需要切换 Tab"
- 滚动方向错误时反向再试一次

### 3.5 `android.browser.*` · 浏览器操作（Phase 7）

详见 [browser-integration.md](browser-integration.md)。

---

## 4. 技能装载机制

### 4.1 装载时机

| 时机 | 加载哪些 |
|---|---|
| **APK 安装时** | 所有 30 个内置技能（hardcode 30 个 SKILL.md 在 assets/）|
| **APK 启动时** | 读取 MMKV 中"已远程安装的技能"列表，加载 |
| **运行时** | 通过 `skill/install` 远程推送，立即生效 |
| **自进化** | Regeneration 每天 02:00 跑，锻造后自动 push |

### 4.2 远程推送的技能格式

```yaml
# 一个远程推送的技能
name: android_taobao_add_to_cart_v1
version: 1.0.0
description: "在淘宝商品页一键加购（专用技能）"
affinity: [ecommerce, taobao, automation]
implementation:
  type: deeplink  # 简单实现
  deeplink: "taobao://item.htm?id={product_id}&buy_now=false"
  fallback:  # 失败时回退
    - tool: android_open_app
      args: {app_name: "淘宝"}
    - tool: android_wait
      args: {ms: 2000}
parameters:
  - name: product_url
    type: string
    required: true
  - name: quantity
    type: integer
    default: 1
```

**实现类型**（`implementation.type`）：

| 类型 | 说明 |
|---|---|
| `deeplink` | 调 `am start -a VIEW -d <url>` |
| `script` | 序列调用其他工具 |
| `intent` | 发 Intent |
| `native` | APK 内置实现（不通过远程推送）|

### 4.3 安全性

- 远程推送的技能必须由"主控端"签名（防恶意 skill）
- 默认只接受 `sign_by=self_hosted_master` 的技能
- 关键操作（支付、登录、删除）禁止通过远程技能执行
- 用户首次安装新技能时弹权限说明

---

## 5. 技能命名规范

```
android.<类别>.<动作>           # 内置技能（小类别，不带版本）
android.<app>_<动作>            # 远程推送的专用技能
```

例子：
- 内置：`android.tap`, `android.open_app`, `android.get_screen_info`
- 远程：`android_taobao_add_to_cart_v1`, `android_jd_check_price_v2`

**affinity 必须明确**（让 Cerebrum 知道何时调用）：
- `mobile, gui, automation, input` — 通用 GUI 技能
- `ecommerce, taobao, automation` — 专用领域技能
- `mobile, browser, anti_bot` — 反爬浏览器

---

## 6. 技能 vs Echo Mobile BaseTool 的映射

| SKILL.md 技能 | Echo Mobile BaseTool | 实现 |
|---|---|---|
| `android.tap` | `TapTool.java` | ClawAccessibilityService.dispatchGesture |
| `android.swipe` | `SwipeTool.java` | ClawAccessibilityService.dispatchGesture |
| `android.input_text` | `InputTextTool.java` | `adb shell input text` |
| `android.long_press` | `LongPressTool.java` | ClawAccessibilityService.dispatchGesture |
| `android.system_key` | `SystemKeyTool.java` | `adb shell input keyevent` |
| `android.get_screen_info` | `GetScreenInfoTool.java` | ClawAccessibilityService.getScreenTree |
| `android.take_screenshot` | `TakeScreenshotTool.java` | `MediaProjection` |
| `android.find_node` | `FindNodeTool.java` | 在 getScreenTree 基础上筛选 |
| `android.find_text` | `FindTextTool.java` | 在 getScreenTree 基础上筛选 |
| `android.open_app` | `OpenAppTool.java` | `am start` |
| `android.install_app` | `InstallAppTool.java` | PackageInstaller |
| `android.get_installed_apps` | `GetInstalledAppsTool.java` | PackageManager |
| `android.wait` | `WaitTool.java` | `Thread.sleep` |
| `android.scroll_to_find` | `ScrollToFindTool.java` | 复合 tap + swipe + get_screen_info |
| `android.detect_dialog` | `DetectDialogTool.java` | 弹窗节点检测 + close |
| `android.find_and_tap` | `FindAndTapTool.java` | 复合 find + tap |
| `android.get_current_app` | `GetCurrentAppTool.java` | `dumpsys activity` |
| `android.finish` | `FinishTool.java` | 信号机制 |
| `android.fail` | `FailTool.java` | 信号机制 |
| `android.read_file` | `ReadFileTool.java` | `File.readText` |
| `android.write_file` | `WriteFileTool.java` | `File.writeText` |
| `android.get_clipboard` | `GetClipboardTool.java` | `ClipboardManager` |
| `android.set_clipboard` | `SetClipboardTool.java` | `ClipboardManager` |

**改造原则**：**所有 30 个 BaseTool 的实现代码 100% 复用**，只是把硬编码的"BaseTool registry" 改成"既能本地调用，也能 RPC 调用"。

---

## 7. 技能未来扩展

- **iOS 技能**（需企业证书）—— 暂未规划
- **Android TV 技能**（遥控器按键）—— 可复用，affinity 加 `tv`
- **Android Auto 技能**（车机）—— 远期
- **iPad 技能**（多窗口）—— 复用 Android 平板模式
- **HarmonyOS 技能** —— 鸿蒙原生 API 适配

---

> 🐙 **30 个技能是章鱼的 30 个吸盘 —— 每个都能抓住不同形状的对象。**
