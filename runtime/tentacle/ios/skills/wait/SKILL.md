---
name: ios.wait
description: |
  Pause execution for the given duration. Useful after network-triggering
  actions (tap a "Submit" button, open an app) to let the UI settle before
  the next perception call.
affinity: [mobile, ios, gui, automation, control]
parameters:
  - name: duration_ms
    type: integer
    required: false
    default: 1000
    description: Milliseconds to wait
---

# iOS Wait

## When to use
- After an action that triggers a network call or animation
- Before re-reading the screen to let content settle
- As a conservative pacing tool in a multi-step flow

## When NOT to use
- Waiting for a specific element to appear — poll with `ios.find_element`
  instead of a fixed sleep

## Example
```json
{
  "tool": "ios.wait",
  "args": {"duration_ms": 1500}
}
```
