---
name: ios.get_active_app
description: |
  Return information about the currently active app, including its bundle id
  and process id. Use to verify which app is in the foreground.
affinity: [mobile, ios, gui, automation, perception]
parameters: []
---

# iOS Get Active App

## When to use
- Verify an app launch succeeded
- Detect which app is in the foreground
- Disambiguate context before an action

## Example
```json
{
  "tool": "ios.get_active_app",
  "args": {}
}
```
