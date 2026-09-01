---
name: android.detect_dialog
description: |
  Detect system or app dialogs/popups on the current screen.
  Attempts to identify and close common dialogs (permissions, updates, ads, etc.).
  Returns the detected dialog type and whether it was successfully closed.
affinity: [mobile, gui, automation, composite]
parameters:
  - name: auto_close
    type: boolean
    required: false
    default: true
    description: Whether to automatically attempt to close detected dialogs
  - name: close_strategy
    type: string
    required: false
    default: "dismiss"
    description: |
      Strategy for closing dialogs:
      - dismiss: Try pressing back or clicking outside
      - negative: Click the negative button (e.g., "取消", "以后", "拒绝")
      - positive: Click the positive button (use with caution)
    enum: [dismiss, negative, positive]
---

# Android Detect Dialog

## When to use
- When an unexpected dialog blocks your workflow
- After opening an app that may show update/permission dialogs
- When `get_screen_info` shows a dialog overlay
- Periodically during long automation tasks to handle interruptions

## When NOT to use
- For expected dialogs that require user input → handle manually with `android.tap`
- For toast messages (they disappear automatically)
- For notifications → use `android.system_key` to dismiss notification shade

## Best practices
- Use `auto_close: true` (default) for non-critical dialogs
- Use `close_strategy: negative` to decline permissions or updates
- Use `close_strategy: dismiss` as the safest default
- After closing a dialog, call `get_screen_info` to verify the state
- Call this periodically during long tasks to handle unexpected popups

## Example
```json
{
  "tool": "android.detect_dialog",
  "args": {"auto_close": true, "close_strategy": "negative"}
}
```
