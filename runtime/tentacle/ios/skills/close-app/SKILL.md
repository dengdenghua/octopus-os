---
name: ios.close_app
description: |
  Terminate an app by its bundle identifier. The app is moved to the
  background and its process is killed.
affinity: [mobile, ios, gui, automation, system]
parameters:
  - name: bundle_id
    type: string
    required: true
    description: iOS app bundle identifier
---

# iOS Close App

## When to use
- Force-quit a misbehaving app
- Reset to a clean app state before a test
- Free device resources before launching another app

## When NOT to use
- Going to the home screen without terminating → `ios.home`

## Example
```json
{
  "tool": "ios.close_app",
  "args": {"bundle_id": "com.apple.mobilesafari"}
}
```
