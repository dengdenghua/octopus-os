---
name: ios.open_app
description: |
  Launch an app by its bundle identifier (e.g. com.apple.mobilesafari).
  The app is brought to the foreground. Use `ios.get_active_app` to verify.
affinity: [mobile, ios, gui, automation, system]
parameters:
  - name: bundle_id
    type: string
    required: true
    description: iOS app bundle identifier
---

# iOS Open App

## When to use
- Switch the foreground app by bundle id
- Start an app from the springboard without navigating icons

## When NOT to use
- Tapping an icon on the home screen → use `ios.tap` with coords
- Closing an app → use `ios.close_app`

## Best practices
- Common bundle ids:
  - `com.apple.mobilesafari` — Safari
  - `com.apple.mobilephone` — Phone
  - `com.apple.MobileSMS` — Messages
  - `com.apple.springboard` — Home screen
- After launch, call `ios.get_screen_info` to confirm the app loaded

## Example
```json
{
  "tool": "ios.open_app",
  "args": {"bundle_id": "com.apple.mobilesafari"}
}
```
