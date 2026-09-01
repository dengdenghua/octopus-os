---
name: android.browser.get_dom
description: |
  Get the current page's DOM tree in structured YAML/JSON format.
  This is the primary way for LLM to "see" web page content — analogous to android.get_screen_info.
  Internally filters: scripts, styles, comments, and display:none nodes.
affinity: [mobile, browser, automation, perception]
parameters:
  - name: max_depth
    type: integer
    required: false
    default: 20
    description: Maximum DOM tree depth to traverse
  - name: include_hidden
    type: boolean
    required: false
    default: false
    description: Whether to include hidden (display:none) elements
---

# Android Browser Get DOM

## When to use
- Understand the structure and content of the current web page
- Find elements to interact with (buttons, links, inputs)
- Extract text content from the page
- This is the browser equivalent of `android.get_screen_info`

## When NOT to use
- For native app screens → use `android.get_screen_info`
- For taking a visual screenshot → use `android.browser.screenshot`
- For executing JavaScript → use `android.browser.evaluate`

## Best practices
- Use this before any browser interaction to find element refs and selectors
- The returned `ref` values can be used with `android.browser.click`
- Set `include_hidden: false` (default) to reduce noise
- Adjust `max_depth` if the DOM is too large or too shallow

## Example
```json
{
  "tool": "android.browser.get_dom",
  "args": {"max_depth": 20, "include_hidden": false}
}
```

## Return structure
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
