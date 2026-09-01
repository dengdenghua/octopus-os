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
- Add `wait_after` if the tap triggers a network call (default 500ms, recommend 1000ms for network)

## Example
```json
{
  "tool": "android.tap",
  "args": {"x": 540, "y": 1200, "wait_after": 1000}
}
```
