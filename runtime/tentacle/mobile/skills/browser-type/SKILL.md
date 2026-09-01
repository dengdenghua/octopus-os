---
name: android.browser.type
description: |
  Type text into the currently focused browser element.
  Optionally clear existing text before typing and press Enter after.
affinity: [mobile, browser, automation, input]
parameters:
  - name: text
    type: string
    required: true
    description: Text to type into the focused element
  - name: clear_first
    type: boolean
    required: false
    default: true
    description: Whether to clear existing text before typing
  - name: press_enter
    type: boolean
    required: false
    default: false
    description: Whether to press Enter after typing
---

# Android Browser Type

## When to use
- Type text into a web input field (search box, form field, chat input)
- After clicking on an input element to focus it
- Fill in form fields on web pages

## When NOT to use
- For typing in native app fields → use `android.input_text`
- For clicking a button → use `android.browser.click`
- For navigating to a URL → use `android.browser.navigate`

## Best practices
- Always click on the input element first using `android.browser.click`
- Use `clear_first: true` (default) to avoid appending to existing text
- Use `press_enter: true` to submit forms or searches after typing
- After typing, call `get_dom` to verify the input was accepted

## Example
```json
{
  "tool": "android.browser.type",
  "args": {"text": "iPhone 15 Pro", "clear_first": true, "press_enter": true}
}
```
