---
name: android.browser.install_extension
description: |
  Install a browser extension package into the built-in mobile browser.
  Use this for anti-bot workflows, scraper helpers, custom AI bridge extensions,
  or site-specific automation extensions that must run inside the Android browser.
affinity: [mobile, browser, automation, extension, anti_bot]
parameters:
  - name: crx_url
    type: string
    required: false
    description: URL of the .crx extension package to download and install
  - name: xpi_url
    type: string
    required: false
    description: URL of the .xpi extension package to download and install
  - name: local_path
    type: string
    required: false
    description: Local extension package path on the Android device
  - name: extension_id
    type: string
    required: false
    description: Optional expected extension id for verification after install
  - name: enabled
    type: boolean
    required: false
    default: true
    description: Whether to enable the extension immediately after installation
---

# Android Browser Install Extension

## When to use
- Install a helper extension before a browser automation task
- Add anti-bot, proxy, scraper, or custom bridge capabilities to the mobile browser
- Prepare a repeatable browser profile for a long-running task

## When NOT to use
- For ordinary page interaction -> use `android.browser.click`, `android.browser.type`, or `android.browser.evaluate`
- For native APK installation -> use `android.install_app`
- When the extension source is untrusted or has not been approved

## Best practices
- Prefer a signed, pinned extension package from a trusted source
- Provide exactly one of `crx_url`, `xpi_url`, or `local_path`
- Use `extension_id` when you need to verify the installed extension
- Treat extension installation as a privileged action and log the source URL/path

## Example
```json
{
  "tool": "android.browser.install_extension",
  "args": {
    "xpi_url": "https://example.com/echo-ai-bridge.xpi",
    "extension_id": "echo-ai-bridge",
    "enabled": true
  }
}
```
