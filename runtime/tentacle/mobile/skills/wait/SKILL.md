---
name: android.wait
description: |
  Wait for a specified duration, or wait for a specific node to appear/disappear.
  Essential for handling loading states, animations, and async operations.
affinity: [mobile, gui, automation, timing]
parameters:
  - name: ms
    type: integer
    required: false
    default: 1000
    description: Milliseconds to wait (used when waiting by time)
  - name: wait_for_node
    type: string
    required: false
    default: ""
    description: |
      Text to wait for — waits until a node with this text appears.
      If set, `ms` becomes the timeout (default 10000ms).
  - name: wait_for_node_gone
    type: string
    required: false
    default: ""
    description: |
      Text to wait to disappear — waits until no node with this text exists.
      If set, `ms` becomes the timeout (default 10000ms).
  - name: poll_interval
    type: integer
    required: false
    default: 500
    description: Polling interval in ms when waiting for a node
---

# Android Wait

## When to use
- Wait for a page to load after navigation
- Wait for a loading spinner to disappear
- Wait for a specific element to appear on screen
- Add a delay between operations for stability

## When NOT to use
- For scrolling to find text → use `android.scroll_to_find`
- For detecting and handling dialogs → use `android.detect_dialog`
- As a substitute for proper error handling

## Best practices
- Use `wait_for_node` instead of fixed delays when possible (more reliable)
- Use `wait_for_node_gone` to wait for loading indicators to disappear
- Set appropriate `ms` as timeout when waiting for nodes (default 10000ms)
- Use `poll_interval` to control how frequently to check (default 500ms)
- For simple delays, use `ms` parameter alone

## Example
```json
{
  "tool": "android.wait",
  "args": {"wait_for_node": "登录", "ms": 10000, "poll_interval": 500}
}
```
