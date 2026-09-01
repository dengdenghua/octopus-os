---
name: android.scroll_to_find
description: |
  Scroll the current screen up/down until the target text/element appears.
  Combines get_screen_info + swipe in a loop. Returns when found or max scrolls reached.
  Anti-pattern: if 3 consecutive scrolls find nothing, suggests the target may not be on this screen.
affinity: [mobile, gui, automation, composite]
parameters:
  - name: target
    type: string
    required: true
    description: Text to find (or desc/class with prefix)
  - name: direction
    type: string
    required: false
    default: down
    description: Scroll direction
    enum: [up, down, left, right]
  - name: max_scrolls
    type: integer
    required: false
    default: 10
    description: Maximum number of scroll attempts
  - name: scroll_duration_ms
    type: integer
    required: false
    default: 300
    description: Duration of each scroll swipe in milliseconds
---

# Android Scroll To Find

## When to use
- Find an item in a long scrollable list
- Locate a setting buried in a settings page
- Search for text that may not be currently visible

## When NOT to use
- When the element is already visible → use `android.find_text` + `android.tap`
- For simple scrolling without a target → use `android.swipe`
- For finding and tapping in one step → use `android.find_and_tap` (if visible)

## Best practices
- Start with `direction: down` (default) for most list views
- If not found after scrolling down, try `direction: up` to check above
- Set reasonable `max_scrolls` (default 10) to avoid infinite scrolling
- After finding the target, use the returned bounds to tap the element
- If the target is not found, it may be on a different tab or page — consider switching tabs

## Example
```json
{
  "tool": "android.scroll_to_find",
  "args": {"target": "关于手机", "direction": "down", "max_scrolls": 10}
}
```
