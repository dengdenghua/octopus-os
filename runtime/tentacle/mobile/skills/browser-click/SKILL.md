---
name: android.browser.click
description: |
  Click an element in the browser by CSS selector or ref from get_dom.
  Supports both selector-based and ref-based clicking for flexibility.
affinity: [mobile, browser, automation, input]
parameters:
  - name: ref
    type: string
    required: false
    description: Element ref from get_dom result (e.g., "b1")
  - name: selector
    type: string
    required: false
    description: CSS selector (e.g., ".buy-btn" or "#submit")
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after click
---

# Android Browser Click

## When to use
- Click a button, link, or other element on a web page
- After `android.browser.get_dom` to identify the target element
- Interact with web page controls

## When NOT to use
- For clicking native app elements → use `android.tap` or `android.find_and_tap`
- For typing text → use `android.browser.type`
- For navigating to a URL → use `android.browser.navigate`

## Best practices
- Prefer using `ref` from `get_dom` results for reliable targeting
- Use `selector` when you know the exact CSS selector
- Always call `get_dom` first to find the right element
- Add `wait_after` if the click triggers navigation or async loading

## Example
```json
{
  "tool": "android.browser.click",
  "args": {"ref": "b1", "wait_after": 1000}
}
```
