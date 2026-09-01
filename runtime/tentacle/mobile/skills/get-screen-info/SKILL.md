---
name: android.get_screen_info
description: |
  Get the current screen's accessibility tree including nodes, text, bounds, and class names.
  This is the MOST IMPORTANT skill — always call this first to understand the screen state.
  Returns current_app, current_activity, screen_size, keyboard state, and the node tree.
affinity: [mobile, gui, automation, perception]
parameters:
  - name: filter_empty
    type: boolean
    required: false
    default: true
    description: Filter out nodes with no text/desc and not interactive
  - name: simplify_class
    type: boolean
    required: false
    default: true
    description: Simplify class names (e.g., android.widget.TextView → TextView)
  - name: max_depth
    type: integer
    required: false
    default: 30
    description: Maximum depth of the accessibility tree to traverse
---

# Android Get Screen Info

## When to use
- Before any tap/swipe/action to find the right coordinates
- To understand what is currently displayed on screen
- To find specific text or UI elements
- After any action to verify the result
- This should be the first call in almost every task step

## When NOT to use
- For taking a visual screenshot → use `android.take_screenshot`
- For finding a specific node by criteria → use `android.find_node`
- For finding text across the screen → use `android.find_text`

## Best practices
- Call this BEFORE any interaction to get current coordinates
- The node center is `(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2`
- Use `ref` field to reference specific nodes in subsequent operations
- `filter_empty: true` reduces noise by hiding non-interactive empty nodes
- Check `is_keyboard_shown` to know if the keyboard is blocking part of the screen
- Check `current_app` to verify you are in the right application

## Example
```json
{
  "tool": "android.get_screen_info",
  "args": {"filter_empty": true, "simplify_class": true}
}
```

## Return structure
```json
{
  "current_app": "com.tencent.mm",
  "current_activity": "com.tencent.mm.ui.LauncherUI",
  "screen_size": [1080, 2400],
  "is_keyboard_shown": false,
  "tree": [
    {
      "ref": "e001",
      "class": "FrameLayout",
      "text": "",
      "desc": "",
      "bounds": [0, 0, 1080, 2400],
      "clickable": false,
      "enabled": true,
      "children": [...]
    }
  ]
}
```
