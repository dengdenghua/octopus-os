---
name: ios.home
description: |
  Press the Home button / swipe up to go to the springboard (home screen).
  On notch/Dynamic Island devices this performs the home gesture.
affinity: [mobile, ios, gui, automation, system]
parameters: []
---

# iOS Home

## When to use
- Return to the home screen from any app
- Exit a full-screen modal or lock screen overlay
- Start a fresh navigation from the springboard

## When NOT to use
- Switching between recent apps — use the app switcher gesture instead
- Locking the device — not supported by this skill

## Example
```json
{
  "tool": "ios.home",
  "args": {}
}
```
