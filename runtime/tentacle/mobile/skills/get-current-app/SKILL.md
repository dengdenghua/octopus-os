---
name: android.get_current_app
description: |
  Get the current foreground application's package name and activity.
  Uses dumpsys activity internally.
  Useful for verifying which app is currently active.
affinity: [mobile, gui, automation, perception]
parameters: []
---

# Android Get Current App

## When to use
- Verify which app is currently in the foreground
- Check if an app switch was successful
- Get the current activity name for debugging
- Determine the current context before performing actions

## When NOT to use
- For getting the full screen state → use `android.get_screen_info` (also returns current_app)
- For listing all installed apps → use `android.get_installed_apps`
- For opening an app → use `android.open_app`

## Best practices
- Use this for quick checks when you only need the app identity
- For more comprehensive screen state, use `get_screen_info` which also includes `current_app`
- Compare the returned `package_name` with expected values to verify app state

## Example
```json
{
  "tool": "android.get_current_app",
  "args": {}
}
```

## Return structure
```json
{
  "package_name": "com.tencent.mm",
  "activity": "com.tencent.mm.ui.LauncherUI"
}
```
