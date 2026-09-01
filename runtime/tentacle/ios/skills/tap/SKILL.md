---
name: ios.tap
description: |
  Tap at coordinate (x, y) on the iOS screen. Coordinates are in logical
  points (not pixels). Use ios.get_screen_info first to find the right
  element bounds, then tap the node center.
affinity: [mobile, ios, gui, automation, input]
parameters:
  - name: x
    type: integer
    required: true
    description: X coordinate in logical points
  - name: y
    type: integer
    required: true
    description: Y coordinate in logical points
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after tap (advisory)
---

# iOS Tap

## When to use
- After `ios.get_screen_info` to click a known element
- Single tap on a button, link, or icon

## When NOT to use
- Double tap → use `ios.double_tap`
- Long press → use `ios.long_press`
- Swipe gesture → use `ios.swipe`
- Typing text → use `ios.input_text`

## Best practices
- Coordinates come from `ios.get_screen_info` (logical points, not pixels)
- WDA reports element frames in points; the node center is
  `(frame.x + frame.width/2, frame.y + frame.height/2)`
- For network-triggering taps, prefer a follow-up `ios.wait` of 1000ms

## Example
```json
{
  "tool": "ios.tap",
  "args": {"x": 195, "y": 422}
}
```
