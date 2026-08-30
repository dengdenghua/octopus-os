---
name: android.get_clipboard
description: |
  Get the current content of the Android clipboard.
  Returns the clipboard text content.
  Useful for reading copied text or verifying copy operations.
affinity: [mobile, gui, automation, clipboard]
parameters: []
---

# Android Get Clipboard

## When to use
- Read text that was copied to clipboard
- Verify that a copy operation was successful
- Retrieve text from clipboard for processing
- Access text shared via clipboard between apps

## When NOT to use
- For reading file content → use `android.read_file`
- For reading screen text → use `android.get_screen_info` or `android.find_text`
- For reading web content → use `android.browser.get_dom`

## Best practices
- Use after a copy operation to verify the content
- Clipboard content may change at any time — read it promptly
- Some apps may clear the clipboard after reading

## Example
```json
{
  "tool": "android.get_clipboard",
  "args": {}
}
```
