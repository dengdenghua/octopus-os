---
name: android.input_text
description: |
  Input text into the currently focused input field.
  Make sure to tap on an input field first to focus it before calling this.
  Uses adb shell input text internally.
affinity: [mobile, gui, automation, input]
parameters:
  - name: text
    type: string
    required: true
    description: Text to input
  - name: clear_first
    type: boolean
    required: false
    default: true
    description: Whether to clear existing text before input
  - name: wait_after
    type: integer
    required: false
    default: 500
    description: Milliseconds to wait after input
---

# Android Input Text

## When to use
- Type text into a focused input field (search box, chat input, form field)
- After tapping on an input field to focus it

## When NOT to use
- For tapping a button → use `android.tap`
- For pasting from clipboard → use `android.get_clipboard` + `android.input_text`
- For browser input → use `android.browser.type`

## Best practices
- Always tap the input field first to ensure it is focused
- Use `clear_first: true` (default) to avoid appending to existing text
- For Chinese text, the input method handles the conversion
- Add `wait_after` if the input triggers a search or network request

## Example
```json
{
  "tool": "android.input_text",
  "args": {"text": "Hello World", "clear_first": true, "wait_after": 500}
}
```
