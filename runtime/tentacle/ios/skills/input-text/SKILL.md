---
name: ios.input_text
description: |
  Type text into the currently focused input field. Focus a text field with
  `ios.tap` first, then call this to enter text. Supports Unicode (emoji,
  CJK).
affinity: [mobile, ios, gui, automation, input]
parameters:
  - name: text
    type: string
    required: true
    description: Text to type into the focused field
---

# iOS Input Text

## When to use
- After tapping a text field to focus it
- Fill in forms, search boxes, chat inputs

## When NOT to use
- Non-text key events (volume, etc.) — not supported via WDA keys
- Pasting clipboard contents — use the app's own paste affordance

## Best practices
- Always `ios.tap` the field first to ensure focus
- For long text, WDA may take noticeable time; raise the tool timeout
- Special keys (Return, Backspace) can be sent as their Unicode codepoints
  (e.g. `\n` for Return, `\u0008` for Backspace)

## Example
```json
{
  "tool": "ios.input_text",
  "args": {"text": "hello world"}
}
```
