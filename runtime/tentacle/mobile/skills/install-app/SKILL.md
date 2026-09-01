---
name: android.install_app
description: |
  Install an APK from a URL or local file path.
  Uses Android PackageInstaller internally.
  The APK will be installed and ready to launch.
affinity: [mobile, gui, automation, app_management]
parameters:
  - name: source
    type: string
    required: true
    description: |
      APK source — URL or local file path.
      URL: "https://example.com/app.apk"
      Local: "/sdcard/Download/app.apk"
  - name: wait_after
    type: integer
    required: false
    default: 5000
    description: Milliseconds to wait after installation completes
---

# Android Install App

## When to use
- Install a new application from a download URL
- Install an APK file already on the device
- Set up an app as part of a task prerequisite

## When NOT to use
- For opening an already installed app → use `android.open_app`
- For checking if an app is installed → use `android.get_installed_apps`
- For uninstalling an app → use `android.system_key` to navigate to Settings

## Best practices
- Prefer URL sources for automatic download and install
- Increase `wait_after` for large APK files
- After installation, use `android.open_app` to launch the newly installed app
- Ensure the APK source is trusted — security risk from unverified sources

## Example
```json
{
  "tool": "android.install_app",
  "args": {"source": "https://example.com/app.apk", "wait_after": 5000}
}
```
