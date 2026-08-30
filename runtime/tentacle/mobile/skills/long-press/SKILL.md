---
name: android.long_press
description: |
  Long press at coordinate (x, y) for specified duration.
  Used for context menus, selecting items, dragging, etc.
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
  - name: duration_ms
    type: integer
    required: false
    default: 1000
    description: Duration of long press in milliseconds
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after long press
---

# Android Long Press

## When to use
- Open context menu on an item (e.g., long press a message for options)
- Select text or items for multi-selection
- Trigger drag-and-drop mode
- Access widget options on home screen

## When NOT to use
- For simple tap → use `android.tap`
- For swipe gesture → use `android.swipe`

## Best practices
- Use coordinates from the latest `get_screen_info` result
- Default duration is 1000ms; some apps require longer (1500-2000ms)
- After long press, call `get_screen_info` to see the context menu that appeared
- Some apps show different menus for different long press durations

## Example
```json
{
  "tool": "android.long_press",
  "args": {"x": 540, "y": 800, "duration_ms": 1000, "wait_after": 500}
}
```
