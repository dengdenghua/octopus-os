---
name: ios.get_screen_info
description: |
  Return the current accessibility tree (source) and the screen window size
  in logical points. Use this as the primary perception primitive: it is
  faster and more precise than a screenshot, and exposes element bounds
  for coordinate-based tools.
affinity: [mobile, ios, gui, automation, perception]
parameters: []
---

# iOS Get Screen Info

## When to use
- Before any coordinate-based action (`ios.tap`, `ios.swipe`, ...)
- To locate an element by its label/value
- To verify screen state changed after an action

## When NOT to use
- Visual-only context (colors, images) → `ios.take_screenshot`
- Looking for a single named element → `ios.find_element`

## Best practices
- This is the iOS equivalent of Android's `get_screen_info`
- The returned `source` is the WDA accessibility tree (XML or JSON)
- `window_size` gives the logical point dimensions; element frames are in
  the same coordinate space — compute centers with
  `(x + width/2, y + height/2)`

## Example
```json
{
  "tool": "ios.get_screen_info",
  "args": {}
}
```
