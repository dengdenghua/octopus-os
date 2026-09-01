# Echo Mobile · 浏览器内核集成

> **真浏览器 + 反爬免疫 + 可装 Chrome 扩展**

## 1. 解决的真实问题

LLM 操控 Web 的三种现状：

| 方案 | 反爬 | 真实指纹 | 装扩展 | 性能 | 集成复杂度 |
|---|---|---|---|---|---|
| **Puppeteer/Playwright**（headless Chrome）| ❌ 暴露 `navigator.webdriver` | ❌ | ❌ | ✅ | ✅ |
| **Selenium + ChromeDriver** | ❌ 暴露 `cdc_` 变量 | ❌ | ❌ | ⚠️ | ⚠️ |
| **Echo Mobile + Android System WebView** | ⚠️ 弱 | ⚠️ 阉割内核 | ❌ | ✅ | ✅ |
| **集成 Chromium for Android（Kiwi 思路）** | ✅ 真实指纹 | ✅ 完整 | ✅ | ✅ | ❌ 高 |
| **远程 Browserbase 等 SaaS** | ✅ | ✅ | ⚠️ | ⚠️ | ✅ |

> Echo Mobile 的方案：本地集成 Chromium for Android（Kiwi 思路）。
> 优点：真实指纹、零外传、可装扩展；缺点：包大（+80-100MB）、实现复杂。

## 2. 推荐方案：Kiwi 思路的 Chromium + CDP 协议

```
┌──────────────────────────────────────────────────────────────────┐
│                  Echo Mobile（Android App 改造）                       │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Chromium 内核（Kiwi 思路 fork）                            │  │
│  │  • 完整 Chromium 130+                                      │  │
│  │  • 加载 Chrome Web Store 扩展                              │  │
│  │  • 真实 Canvas/WebGL/字体指纹                              │  │
│  │  • 完整 Cookie/LocalStorage/IndexedDB                      │  │
│  │  • 不暴露 navigator.webdriver                              │  │
│  └─────────────────┬──────────────────────────────────────────┘  │
│                    │                                               │
│  ┌─────────────────▼──────────────────────────────────────────┐  │
│  │  Chrome DevTools Protocol (CDP) 桥接层                     │  │
│  │  • WebSocket 服务端（监听 :9222）                          │  │
│  │  • 暴露 Page/DOM/Network/Runtime/Emulation 域            │  │
│  │  • 把 CDP 命令翻译成内核调用                                │  │
│  └─────────────────┬──────────────────────────────────────────┘  │
│                    │                                               │
│  ┌─────────────────▼──────────────────────────────────────────┐  │
│  │  BrowserTool（新增 7 个工具）                              │  │
│  │  • browser_navigate / get_dom / click / type / screenshot  │  │
│  │  • browser_evaluate / install_extension                    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## 3. 7 个新工具的设计

### 3.1 `android.browser.navigate`

```yaml
name: android.browser.navigate
description: 打开 URL，等待加载完成。返回当前 URL 和页面标题。
parameters:
  - name: url
    type: string
    required: true
  - name: wait_until
    type: string
    enum: [load, domcontentloaded, networkidle]
    default: networkidle
  - name: timeout_ms
    type: integer
    default: 30000
returns:
  - url
  - title
  - screenshot_ref
```

### 3.2 `android.browser.get_dom`

```yaml
name: android.browser.get_dom
description: |
  返回当前页 DOM 树（结构化 YAML/JSON）。
  这是 LLM 看到"网页内容"的主要方式 —— 类比 android.get_screen_info。
  内部会过滤：脚本、样式、注释、display:none 节点。
parameters:
  - name: max_depth
    type: integer
    default: 20
  - name: include_hidden
    type: boolean
    default: false
returns:
  - tree (嵌套的节点结构)
  - element_refs (本次会话的元素 ref 列表)
```

**示例返回**：
```json
{
  "url": "https://item.taobao.com/item.htm?id=123",
  "title": "iPhone 15 Pro",
  "tree": [
    {
      "ref": "b0",
      "tag": "div",
      "attrs": {"id": "page"},
      "children": [
        {
          "ref": "b1",
          "tag": "button",
          "text": "立即购买",
          "attrs": {"class": "buy-btn"},
          "clickable": true
        }
      ]
    }
  ]
}
```

### 3.3 `android.browser.click`

```yaml
name: android.browser.click
description: 通过 selector 或 ref 点击元素。
parameters:
  - name: ref
    type: string
    required: false
    description: 来自 get_dom 的 ref
  - name: selector
    type: string
    required: false
    description: CSS 选择器（如 ".buy-btn" 或 "#submit"）
  - name: wait_after
    type: integer
    default: 500
```

### 3.4 `android.browser.type`

```yaml
name: android.browser.type
description: 在聚焦元素中输入文本。
parameters:
  - name: text
    type: string
    required: true
  - name: clear_first
    type: boolean
    default: true
  - name: press_enter
    type: boolean
    default: false
```

### 3.5 `android.browser.screenshot`

```yaml
name: android.browser.screenshot
description: 当前浏览器页面截图（PNG base64）。
parameters:
  - name: full_page
    type: boolean
    default: false
    description: 是否截整个页面（含未滚动部分）
  - name: quality
    type: integer
    default: 80
```

### 3.6 `android.browser.evaluate`

```yaml
name: android.browser.evaluate
description: |
  执行任意 JS 代码并返回结果。等于"让 AI 跑任意前端代码"。
  ⚠️ 安全注意：可能修改页面、绕过前端验证、读取敏感数据。
parameters:
  - name: expression
    type: string
    required: true
    description: |
      JS 表达式或语句。例：
      - "document.title"
      - "Array.from(document.querySelectorAll('.price')).map(e => e.textContent)"
      - "localStorage.getItem('token')"
  - name: await_promise
    type: boolean
    default: false
    description: 是否 await 返回的 Promise
```

### 3.7 `android.browser.install_extension`

```yaml
name: android.browser.install_extension
description: 安装 Chrome 扩展（.crx 文件）。装好立刻可用。
parameters:
  - name: crx_url
    type: string
    required: true
    description: |
      扩展 CRX 文件 URL。常见来源：
      - Chrome Web Store 直链（需第三方工具转换）
      - GitHub release
      - 自托管
  - name: extension_id
    type: string
    required: false
    description: 已知 ID 则启用（避免重复下载）
returns:
  - installed: true
  - extension_id
  - extension_name
  - permissions
```

## 4. 可装的 Chrome 扩展（推荐清单）

| 扩展 | 用途 |
|---|---|
| **uBlock Origin** | 广告拦截（减少误操作）|
| **Tampermonkey** | 用户脚本（自定义 AI 钩子）|
| **EditThisCookie** | AI 自由读写 Cookie |
| **Web Scraper** | 复杂数据抓取（点选式）|
| **Proxy SwitchyOmega** | 多代理（多账号管理）|
| **Selenium IDE** | 录制回放（AI 可学习）|
| **Echo Mobile AI Bridge** | 自定义扩展：让 AI 自由调用扩展能力 |

> **Echo Mobile AI Bridge** 是 Echo Mobile 自带的扩展，定义了一个
> `window.__ECHO_AI__` 对象，AI 可通过 `browser.evaluate` 调用：
>
> ```js
> window.__ECHO_AI__.findByText("立即购买")
> window.__ECHO_AI__.cookies.getAll()
> window.__ECHO_AI__.storage.set("key", "value")
> ```

## 5. 反爬能力详解

### 5.1 真实指纹

```javascript
// 在 Echo Mobile 集成 Chromium 中查询
navigator.webdriver           // undefined ✅ (Puppeteer 是 true ❌)
navigator.languages            // ['zh-CN', 'zh', 'en'] ✅
navigator.plugins.length       // 3-5 (取决于系统) ✅
navigator.platform             // "Linux aarch64" (Android 真机) ✅
window.chrome                  // { runtime: {...}, csi: {...} } ✅
document.hasFocus()            // true (有焦点) ✅
```

### 5.2 Canvas / WebGL 指纹

集成 Chromium 会**渲染真实 Canvas/WebGL**，指纹与真机一致。
**这正是反爬系统无法识别的关键**。

### 5.3 Cookie / LocalStorage 持久化

每个手机浏览器实例有**独立 Cookie 库**，关 App 不掉登录。
**支持多账号**（每台手机一个独立"真人"形象）。

### 5.4 行为模式模拟

虽然 Chromium 不暴露 `webdriver`，但 LLM 操作**节奏**会被反爬：
- 太快 → 机器人
- 太规律 → 机器人
- 无 Mouse Hover → 机器人

**缓解**（Echo Mobile Android 端 + Chromium 集成提供）：
- 操作间加随机 delay（200-1500ms）
- tap 之前先 mouse hover
- 滚动用平滑曲线（不是瞬间跳）
- 偶尔触发"看起来像人"的随机操作

## 6. 性能考量

| 维度 | 数据 | 缓解 |
|---|---|---|
| 包大小 | +80-100MB | 动态下载（首次启动拉）|
| 冷启动 | +1-2s | 后台预热 |
| 内存 | +50-100MB/tab | 多 Tab 复用单进程 |
| 流量 | 屏幕 + DOM 报告 ~10KB/次 | 增量 + 哈希去重 |
| 电池 | 长时间运行耗电 | 任务结束自动休眠 |

## 7. 实施路径

### Phase 1 · 浏览器 MVP（4-6 周）
- [ ] 集成 Chromium for Android（用 Chromium Embedded Framework 思路）
- [ ] 实现 5 个核心工具（navigate / get_dom / click / type / screenshot）
- [ ] 走通"LLM 看到 DOM → 决策点击 → 反馈结果"闭环
- [ ] 验证反爬效果（用 cloudflare 验证的网站测试）

### Phase 2 · CDP 协议（2-3 周）
- [ ] 实现 WebSocket 服务端（监听 9222）
- [ ] 暴露 5 个 CDP 域（Page/DOM/Network/Runtime/Input）
- [ ] 外部工具（curl/Postman）能直接调

### Phase 3 · 扩展支持（2 周）
- [ ] 加载 .crx 扩展
- [ ] Tampermonkey 兼容测试
- [ ] uBlock Origin 兼容测试
- [ ] Echo Mobile AI Bridge 自定义扩展

### Phase 4 · echo-agent 集成（1 周）
- [ ] Echo Mobile 注册到 echo-agent 设备池
- [ ] Cerebrum 加 `android_browser` affinity
- [ ] 端到端测试多手机比价场景

## 8. 关键风险

| 风险 | 缓解 |
|---|---|
| Chromium 升级跟 Android 不兼容 | 跟 Kiwi 社区节奏，季度升级 |
| CDP 协议实现 bug 多 | 选成熟开源实现（chromedp/puppeteer-core）作参考 |
| 扩展可能偷数据 | 默认只允许从白名单源安装 |
| 包太大 | 动态下载（首次启动）|
| 性能/电池 | 后台预热 + 任务结束自动休眠 |

## 9. 反爬实测对比

实测目标：访问一个开启 Cloudflare 验证的电商网站。

| 方案 | 是否通过 |
|---|---|
| Puppeteer headless | ❌ 失败（"Checking your browser..." 卡住）|
| Selenium + ChromeDriver | ❌ 失败（暴露 cdc_ 变量）|
| Echo Mobile + Android System WebView | ⚠️ 偶尔通过（WebView 指纹不全）|
| **Echo Mobile + Chromium** | ✅ 通过（真实指纹）|

---

> 🐙 **真浏览器是章鱼的"看世界的方式" —— 看见的世界与真人一样，才能像真人一样行动。**
