---
name: android.browser.screenshot
description: |
  Take a screenshot of the current browser page as PNG base64.
  Supports full-page screenshots that capture content below the fold.
affinity: [mobile, browser, automation, perception]
parameters:
  - name: full_page
    type: boolean
    required: false
    default: false
    description: Whether to capture the entire page including scrolled content
  - name: quality
    type: integer
    required: false
    default: 80
    description: Image quality (1-100)
---

# Android Browser Screenshot

## When to use
- Capture visual content of a web page
- Verify the current state of a web page
- When DOM tree is insufficient to understand the visual layout
- Capture full-page content for analysis

## When NOT to use
- For native app screenshots → use `android.take_screenshot`
- For getting page structure → use `android.browser.get_dom`
- For extracting text → use `android.browser.get_dom` or `android.browser.evaluate`

## Best practices
- Use `full_page: false` (default) for viewport-only screenshots
- Use `full_page: true` when you need to see content below the fold
- Prefer `get_dom` for programmatic interaction; screenshots for visual verification
- Lower `quality` for faster transfer when detail is not critical

## Example
```json
{
  "tool": "android.browser.screenshot",
  "args": {"full_page": false, "quality": 80}
}
```
