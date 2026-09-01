---
name: android.open_app
description: |
  Open an application by app name or package name.
  Supports Chinese/English app names with fuzzy matching.
  Has built-in alias mapping for common Chinese apps.
affinity: [mobile, gui, automation, app_management]
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
    required: false
    default: 2000
    description: Milliseconds to wait after launching the app
---

# Android Open App

## When to use
- Launch an app at the start of a task
- Switch to a different app during a task
- Open a specific app by name or package

## When NOT to use
- For going back to a previously used app → use `android.system_key` with `recents`
- For going to home screen → use `android.system_key` with `home`
- For installing a new app → use `android.install_app`

## Best practices
- Use `package_name` when you know the exact package for reliability
- Use `app_name` for user-friendly references (supports Chinese names)
- Default `wait_after` is 2000ms; increase for heavy apps (3000-5000ms)
- After opening, call `get_screen_info` to verify the app launched correctly
- Built-in alias mapping for common apps: 微信→com.tencent.mm, 淘宝→com.taobao.taobao, etc.

## Common app aliases
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

## Example
```json
{
  "tool": "android.open_app",
  "args": {"app_name": "微信", "wait_after": 3000}
}
```
