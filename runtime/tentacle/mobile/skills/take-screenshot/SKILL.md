---
name: android.take_screenshot
description: |
  Take a screenshot of the current screen, returned as base64 PNG.
  Use this when you need visual information that the accessibility tree cannot provide,
  such as images, icons, colors, or layout context.
affinity: [mobile, gui, automation, perception]
parameters:
  - name: quality
    type: integer
    required: false
    default: 80
    description: Image quality (1-100), lower means smaller file size
  - name: scale
    type: float
    required: false
    default: 0.5
    description: Scale factor for the screenshot (0.5 = half resolution, reduces size)
---

# Android Take Screenshot

## When to use
- When you need to see visual content (images, icons, colors)
- When the accessibility tree is insufficient to understand the screen
- For visual verification after an action
- When dealing with image-heavy apps (gallery, camera, maps)

## When NOT to use
- For getting UI element coordinates → use `android.get_screen_info` (faster and more precise)
- For finding text → use `android.find_text` (more reliable)
- For browser screenshots → use `android.browser.screenshot`

## Best practices
- Use `get_screen_info` as the primary perception method; screenshots are supplementary
- Reduce `scale` to 0.5 (default) to minimize data transfer
- Lower `quality` for faster transfer when visual detail is not critical
- Screenshots are expensive — prefer `get_screen_info` when possible

## Example
```json
{
  "tool": "android.take_screenshot",
  "args": {"quality": 80, "scale": 0.5}
}
```
