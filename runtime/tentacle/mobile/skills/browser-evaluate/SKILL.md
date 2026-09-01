---
name: android.browser.evaluate
description: |
  Execute arbitrary JavaScript code in the browser and return the result.
  This gives the AI full power to run any frontend code.
  WARNING: May modify page state, bypass frontend validation, or read sensitive data.
affinity: [mobile, browser, automation, advanced]
parameters:
  - name: expression
    type: string
    required: true
    description: |
      JavaScript expression or statement to execute. Examples:
      - "document.title"
      - "Array.from(document.querySelectorAll('.price')).map(e => e.textContent)"
      - "localStorage.getItem('token')"
  - name: await_promise
    type: boolean
    required: false
    default: false
    description: Whether to await the returned Promise before returning result
---

# Android Browser Evaluate

## When to use
- Extract data that is not available in the DOM tree (computed values, localStorage, etc.)
- Execute custom JavaScript for complex interactions
- Access browser APIs (localStorage, sessionStorage, cookies)
- Perform calculations or transformations on page data

## When NOT to use
- For simple element clicking → use `android.browser.click`
- For typing text → use `android.browser.type`
- For getting the DOM structure → use `android.browser.get_dom`
- For navigation → use `android.browser.navigate`

## Best practices
- Keep expressions simple and focused
- Use `await_promise: true` for async operations (fetch, setTimeout, etc.)
- Be cautious with expressions that modify page state
- Use for data extraction when `get_dom` doesn't provide enough detail
- Can access `window.__ECHO_AI__` bridge for enhanced capabilities

## Example
```json
{
  "tool": "android.browser.evaluate",
  "args": {"expression": "Array.from(document.querySelectorAll('.price')).map(e => e.textContent)", "await_promise": false}
}
```
