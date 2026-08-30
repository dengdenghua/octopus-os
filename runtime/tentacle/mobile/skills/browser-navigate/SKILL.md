---
name: android.browser.navigate
description: |
  Navigate to a URL in the built-in Chromium browser and wait for the page to load.
  Returns the current URL, page title, and a screenshot reference.
  The integrated Chromium provides real browser fingerprints for anti-bot evasion.
affinity: [mobile, browser, automation, anti_bot]
parameters:
  - name: url
    type: string
    required: true
    description: URL to navigate to
  - name: wait_until
    type: string
    required: false
    default: networkidle
    description: When to consider navigation complete
    enum: [load, domcontentloaded, networkidle]
  - name: timeout_ms
    type: integer
    required: false
    default: 30000
    description: Navigation timeout in milliseconds
---

# Android Browser Navigate

## When to use
- Open a URL in the integrated Chromium browser
- Navigate to a web page for scraping or interaction
- Start a web-based task flow

## When NOT to use
- For opening a native app → use `android.open_app`
- For getting the current page DOM → use `android.browser.get_dom`
- For clicking elements on the page → use `android.browser.click`

## Best practices
- Use `wait_until: networkidle` (default) for most pages to ensure full loading
- Use `wait_until: domcontentloaded` for faster response when you don't need all resources
- Set appropriate `timeout_ms` for slow-loading pages
- After navigation, use `android.browser.get_dom` to understand the page content

## Example
```json
{
  "tool": "android.browser.navigate",
  "args": {"url": "https://www.taobao.com", "wait_until": "networkidle", "timeout_ms": 30000}
}
```
