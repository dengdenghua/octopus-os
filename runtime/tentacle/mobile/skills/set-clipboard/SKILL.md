---
name: android.set_clipboard
description: |
  Set the Android clipboard content to the specified text.
  Uses ClipboardManager internally.
  Useful for preparing text to paste into input fields.
affinity: [mobile, gui, automation, clipboard]
parameters:
  - name: text
    type: string
    required: true
    description: Text to set as clipboard content
---

# Android Set Clipboard

## When to use
- Prepare text for pasting into input fields
- Copy text that will be used in another app
- Set up clipboard content before a paste operation
- Useful for long text that is difficult to type with `input_text`

## When NOT to use
- For typing text directly into a field → use `android.input_text`
- For writing to a file → use `android.write_file`
- For browser input → use `android.browser.type`

## Best practices
- After setting clipboard, use `android.long_press` on an input field and select "Paste"
- Useful for entering long passwords or URLs that are hard to type
- Combine with `android.input_text` for complex input scenarios
- Clipboard content persists until overwritten

## Example
```json
{
  "tool": "android.set_clipboard",
  "args": {"text": "https://www.example.com/very/long/url"}
}
```
