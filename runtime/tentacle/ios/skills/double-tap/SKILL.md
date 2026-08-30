---
name: ios.double_tap
description: |
  Double-tap at coordinate (x, y). Used for zoom toggles in maps/photos,
  word selection in text, and other double-tap affordances.
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
---

# iOS Double Tap

## When to use
- Zoom in/out toggle in Maps, Photos, Safari
- Select a word in editable text
- App-specific double-tap gestures

## When NOT to use
- Single tap → `ios.tap`
- Long press for context menu → `ios.long_press`

## Example
```json
{
  "tool": "ios.double_tap",
  "args": {"x": 195, "y": 422}
}
```
