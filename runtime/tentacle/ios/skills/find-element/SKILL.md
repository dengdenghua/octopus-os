---
name: ios.find_element
description: |
  Find a UI element by accessibility id, class name, or XPath. Returns the
  WDA element descriptor (including its ELEMENT id) which can be used for
  downstream interactions.
affinity: [mobile, ios, gui, automation, perception]
parameters:
  - name: accessibility_id
    type: string
    required: false
    description: The element's accessibility identifier (preferred locator)
  - name: class_name
    type: string
    required: false
    description: XCUIElementType class name (e.g. XCUIElementTypeButton)
  - name: xpath
    type: string
    required: false
    description: XPath expression against the WDA accessibility tree
  - name: partial
    type: boolean
    required: false
    default: false
    description: Reserved for partial name matching (not all WDA builds honor it)
---

# iOS Find Element

## When to use
- Locate a specific element by its accessibility id (most reliable)
- Verify an element is present before tapping
- Get an element handle for chained operations

## When NOT to use
- Pure coordinate tap → use `ios.tap` directly with known coords
- Reading the full screen tree → use `ios.get_screen_info`

## Best practices
- Prefer `accessibility_id` — it is stable across localizations
- `class_name` is useful for "tap the first button" flows
- XPath is powerful but slow; avoid for hot paths

## Example
```json
{
  "tool": "ios.find_element",
  "args": {"accessibility_id": "login_button"}
}
```
