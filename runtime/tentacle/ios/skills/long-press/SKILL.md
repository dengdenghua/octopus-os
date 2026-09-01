---
name: ios.long_press
description: |
  Touch and hold at (x, y) for the given duration. Triggers context menus,
  drag handles, and reposition affordances in iOS apps.
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
  - name: duration_s
    type: number
    required: false
    default: 2.0
    description: Hold duration in seconds
---

# iOS Long Press

## When to use
- Open a context menu (app icon on home screen, list item)
- Start drag-and-drop on a reorderable element
- Trigger 3D Touch / Haptic Touch affordances

## When NOT to use
- Simple tap → `ios.tap`
- Swipe → `ios.swipe`

## Best practices
- iOS context menus typically need ≥1.5s hold; default 2.0s is safe
- For drag-and-drop, follow with `ios.swipe` to move the element

## Example
```json
{
  "tool": "ios.long_press",
  "args": {"x": 195, "y": 422, "duration_s": 2.0}
}
```
