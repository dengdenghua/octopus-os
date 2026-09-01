---
name: android.swipe
description: |
  Swipe from (x1, y1) to (x2, y2) with specified duration.
  Used for scrolling, swiping between pages, pulling down notifications, etc.
affinity: [mobile, gui, automation, input]
parameters:
  - name: x1
    type: integer
    required: true
    description: Start X coordinate in pixels
  - name: y1
    type: integer
    required: true
    description: Start Y coordinate in pixels
  - name: x2
    type: integer
    required: true
    description: End X coordinate in pixels
  - name: y2
    type: integer
    required: true
    description: End Y coordinate in pixels
  - name: duration_ms
    type: integer
    required: false
    default: 300
    description: Duration of swipe in milliseconds
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after swipe
---

# Android Swipe

## When to use
- Scroll a list or page up/down
- Swipe between screens (e.g., home screen pages)
- Pull down notification shade
- Dismiss cards or items with a swipe gesture

## When NOT to use
- For tapping → use `android.tap`
- For scrolling to find specific text → use `android.scroll_to_find`
- For long press → use `android.long_press`

## Best practices
- Use screen coordinates from `get_screen_info` to determine swipe area
- For vertical scroll: swipe from middle-bottom to middle-top (e.g., y1=1800, y2=600)
- For horizontal swipe: use consistent y values, vary x (e.g., x1=900, x2=200)
- Increase `duration_ms` for slower, more natural-looking swipes
- Add `wait_after` to allow content to load after scrolling

## Example
```json
{
  "tool": "android.swipe",
  "args": {"x1": 540, "y1": 1800, "x2": 540, "y2": 600, "duration_ms": 300, "wait_after": 500}
}
```
