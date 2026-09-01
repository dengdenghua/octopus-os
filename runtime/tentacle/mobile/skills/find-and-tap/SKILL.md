---
name: android.find_and_tap
description: |
  Find a node by text and tap it in one operation.
  This is the most commonly used composite skill — combines find_text + tap.
  Searches the accessibility tree for matching text and taps the center of the found node.
affinity: [mobile, gui, automation, composite]
parameters:
  - name: text
    type: string
    required: true
    description: Text to search for (case-insensitive, partial match)
  - name: exact_match
    type: boolean
    required: false
    default: false
    description: Whether to require exact text match
  - name: index
    type: integer
    required: false
    default: 0
    description: Which match to tap if multiple found (0 = first)
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after tap
---

# Android Find And Tap

## When to use
- Tap a button or link by its text label
- The most common interaction pattern — find text, then tap it
- Quick one-step operation instead of find_text + tap separately

## When NOT to use
- When you already know the exact coordinates → use `android.tap` directly
- When the element is not visible on screen → use `android.scroll_to_find` first
- For text input → use `android.input_text`
- For browser elements → use `android.browser.click`

## Best practices
- This is the preferred way to interact with labeled buttons and links
- Use `exact_match: false` (default) for flexible matching
- If multiple matches exist, use `index` to select the right one
- If the text is not found, try `get_screen_info` to see what's available
- Add `wait_after` if the tap triggers navigation or network requests

## Example
```json
{
  "tool": "android.find_and_tap",
  "args": {"text": "登录", "wait_after": 1000}
}
```
