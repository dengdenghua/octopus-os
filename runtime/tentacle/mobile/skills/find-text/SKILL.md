---
name: android.find_text
description: |
  Find all nodes containing the target text on the current screen.
  Returns a list of matching nodes with their ref, bounds, and full text.
  Useful for locating specific content or verifying text presence.
affinity: [mobile, gui, automation, perception]
parameters:
  - name: target
    type: string
    required: true
    description: Text to search for (case-insensitive, partial match)
  - name: include_desc
    type: boolean
    required: false
    default: true
    description: Also search in content description field
---

# Android Find Text

## When to use
- Find all occurrences of specific text on screen
- Verify that certain text is displayed
- Locate text content before interacting with it

## When NOT to use
- For getting the full screen tree → use `android.get_screen_info`
- For finding nodes by class or description → use `android.find_node`
- For scrolling to find text → use `android.scroll_to_find`

## Best practices
- Use partial text matching for flexibility
- Set `include_desc: true` to also search content descriptions
- If no results, the text may not be visible — try scrolling with `android.scroll_to_find`
- Use the returned `bounds` to calculate tap coordinates

## Example
```json
{
  "tool": "android.find_text",
  "args": {"target": "确认", "include_desc": true}
}
```
