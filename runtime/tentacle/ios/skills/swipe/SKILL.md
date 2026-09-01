---
name: ios.swipe
description: |
  Swipe from (x1, y1) to (x2, y2) over a given duration. Used for scrolling,
  paging, pulling down the notification shade, and dismissing cards.
  Coordinates are in logical points.
affinity: [mobile, ios, gui, automation, input]
parameters:
  - name: x1
    type: integer
    required: true
    description: Start X coordinate in logical points
  - name: y1
    type: integer
    required: true
    description: Start Y coordinate in logical points
  - name: x2
    type: integer
    required: true
    description: End X coordinate in logical points
  - name: y2
    type: integer
    required: true
    description: End Y coordinate in logical points
  - name: duration_s
    type: number
    required: false
    default: 0.5
    description: Swipe duration in seconds (slower = more natural)
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after swipe (advisory)
---

# iOS Swipe

## When to use
- Scroll a list or page vertically/horizontally
- Swipe between home screen pages
- Pull down Control Center / Notification Center
- Dismiss a card or sheet

## When NOT to use
- Tap → `ios.tap`
- Long press → `ios.long_press`

## Best practices
- For vertical scroll: swipe from middle-bottom to middle-top
  (e.g. `y1=600, y2=200` with `x1==x2`)
- For horizontal paging: keep y constant, vary x
- Increase `duration_s` (e.g. 0.8) for slower, more natural swipes
- Use `ios.get_screen_info` first to locate the scrollable region

## Example
```json
{
  "tool": "ios.swipe",
  "args": {"x1": 195, "y1": 600, "x2": 195, "y2": 200, "duration_s": 0.4}
}
```
