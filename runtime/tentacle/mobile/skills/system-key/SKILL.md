---
name: android.system_key
description: |
  Simulate system key presses: Home, Back, Recents, Power, Volume Up/Down.
  Uses adb shell input keyevent internally.
affinity: [mobile, gui, automation, input]
parameters:
  - name: key
    type: string
    required: true
    description: |
      Key to press. Supported values:
      - home: Go to home screen
      - back: Go back
      - recents: Open recent apps
      - power: Power button
      - volume_up: Volume up
      - volume_down: Volume down
      - enter: Enter key
      - tab: Tab key
      - delete: Delete/Backspace key
    enum: [home, back, recents, power, volume_up, volume_down, enter, tab, delete]
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after key press
---

# Android System Key

## When to use
- Press Back to navigate back in an app
- Press Home to return to home screen
- Open recent apps switcher
- Adjust volume
- Press Enter to submit a form

## When NOT to use
- For tapping a specific UI element → use `android.tap`
- For typing text → use `android.input_text`
- For dismissing a dialog → try `android.detect_dialog` first

## Best practices
- Use `back` key as the primary navigation method when you need to go back
- Use `home` key to reset to a known state before opening a new app
- After pressing a system key, call `get_screen_info` to verify the result
- Use `recents` to switch between apps

## Example
```json
{
  "tool": "android.system_key",
  "args": {"key": "back", "wait_after": 500}
}
```
