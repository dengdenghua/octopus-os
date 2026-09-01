---
name: android.fail
description: |
  Signal that the current task has failed.
  Call this when the task cannot be completed due to an error or unexpected state.
  Include error information to help with debugging and retry decisions.
affinity: [mobile, gui, automation, task_control]
parameters:
  - name: error
    type: string
    required: true
    description: Error message describing why the task failed
  - name: recoverable
    type: boolean
    required: false
    default: true
    description: Whether the task might succeed if retried
---

# Android Fail

## When to use
- The task cannot be completed due to an error
- An unexpected state prevents further progress
- A required element is not found after reasonable attempts
- The app crashes or behaves unexpectedly

## When NOT to use
- When the task is completed successfully → use `android.finish`
- When a minor issue can be resolved by trying an alternative approach
- When waiting might resolve the issue → use `android.wait`

## Best practices
- Always provide a descriptive `error` message
- Set `recoverable: true` (default) if retrying might help
- Set `recoverable: false` for fundamental issues (e.g., app not installed, permission denied)
- Before calling fail, try reasonable recovery attempts (scroll, wait, go back)

## Example
```json
{
  "tool": "android.fail",
  "args": {"error": "Could not find '登录' button after 3 attempts", "recoverable": true}
}
```
