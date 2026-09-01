---
name: android.find_node
description: |
  Find accessibility nodes by text, content description, or class name.
  Searches the current screen's accessibility tree and returns matching nodes
  with their ref, bounds, text, and other properties.
affinity: [mobile, gui, automation, perception]
parameters:
  - name: text
    type: string
    required: false
    description: Match node text (case-insensitive, partial match)
  - name: desc
    type: string
    required: false
    description: Match node content description (case-insensitive, partial match)
  - name: class_name
    type: string
    required: false
    description: Match node class name (e.g., "Button", "TextView")
  - name: clickable_only
    type: boolean
    required: false
    default: false
    description: Only return clickable nodes
  - name: exact_match
    type: boolean
    required: false
    default: false
    description: Require exact text match instead of partial
---

# Android Find Node

## When to use
- Find a specific UI element by its text, description, or class
- Locate a button or link before tapping it
- Check if a specific element exists on screen

## When NOT to use
- For getting the full screen state → use `android.get_screen_info`
- For finding all text occurrences → use `android.find_text`
- For finding and immediately tapping → use `android.find_and_tap` (combined operation)

## Best practices
- Use `clickable_only: true` when looking for interactive elements
- Use `exact_match: false` (default) for flexible matching
- Combine `text` and `class_name` for more precise results
- If no results, try `get_screen_info` to see the full tree

## Example
```json
{
  "tool": "android.find_node",
  "args": {"text": "登录", "clickable_only": true}
}
```
