---
name: android.finish
description: |
  Signal that the current task has been completed successfully.
  Call this when the task goal has been achieved.
  Optionally include a summary of what was accomplished.
affinity: [mobile, gui, automation, task_control]
parameters:
  - name: summary
    type: string
    required: false
    default: ""
    description: Optional summary of what was accomplished
---

# Android Finish

## When to use
- The task goal has been fully achieved
- All required steps have been completed successfully
- You need to signal task completion to the orchestration layer

## When NOT to use
- When the task has encountered an error → use `android.fail`
- When the task is still in progress → continue with other skills
- When you are unsure if the task is complete → verify with `get_screen_info` first

## Best practices
- Always provide a `summary` describing what was accomplished
- Verify the final state with `get_screen_info` before calling finish
- Do not call finish if there are remaining steps in the task

## Example
```json
{
  "tool": "android.finish",
  "args": {"summary": "Successfully sent message 'Hello' to contact 张三 in WeChat"}
}
```
