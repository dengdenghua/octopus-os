---
name: ios.take_screenshot
description: |
  Capture the current screen and return it as a base64-encoded PNG.
  Use when visual information (images, icons, colors) is needed that the
  accessibility tree cannot provide.
affinity: [mobile, ios, gui, automation, perception]
parameters:
  - name: scale
    type: number
    required: false
    default: 1.0
    description: Scale factor (1.0 = full resolution). WDA returns full-res; scale is advisory.
---

# iOS Take Screenshot

## When to use
- Visual verification after an action
- Image-heavy apps (Photos, Maps, Camera)
- When the accessibility tree is insufficient

## When NOT to use
- Getting UI element coordinates → `ios.get_screen_info` (faster, precise)
- Finding text → `ios.find_element` with accessibility_id

## Best practices
- Prefer `ios.get_screen_info` as the primary perception method
- Screenshots are large; only capture when necessary
- Use the returned base64 directly for VLM analysis

## Example
```json
{
  "tool": "ios.take_screenshot",
  "args": {"scale": 1.0}
}
```
