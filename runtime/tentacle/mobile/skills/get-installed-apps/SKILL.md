---
name: android.get_installed_apps
description: |
  Get a list of all installed applications on the device.
  Returns app names, package names, and version info.
  Useful for checking if an app is installed or finding the right package name.
affinity: [mobile, gui, automation, app_management]
parameters:
  - name: filter
    type: string
    required: false
    default: ""
    description: Optional filter string to search app names or package names
  - name: include_system
    type: boolean
    required: false
    default: false
    description: Whether to include system apps in the results
---

# Android Get Installed Apps

## When to use
- Check if a specific app is installed
- Find the package name of an app
- List available apps before choosing which to open
- Verify app installation after `android.install_app`

## When NOT to use
- For opening an app → use `android.open_app`
- For getting the current foreground app → use `android.get_current_app`
- For getting screen content → use `android.get_screen_info`

## Best practices
- Use `filter` to narrow down results instead of listing all apps
- Set `include_system: false` (default) to focus on user-installed apps
- Use the returned `package_name` for precise `android.open_app` calls

## Example
```json
{
  "tool": "android.get_installed_apps",
  "args": {"filter": "微信", "include_system": false}
}
```
