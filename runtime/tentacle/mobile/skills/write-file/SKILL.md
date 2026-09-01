---
name: android.write_file
description: |
  Write content to a file on the device's /sdcard/ storage.
  Creates the file if it doesn't exist, overwrites if it does.
  Can optionally append instead of overwrite.
affinity: [mobile, gui, automation, file]
parameters:
  - name: path
    type: string
    required: true
    description: Absolute file path to write (e.g., "/sdcard/Download/output.txt")
  - name: content
    type: string
    required: true
    description: Content to write to the file
  - name: append
    type: boolean
    required: false
    default: false
    description: Whether to append to the file instead of overwriting
  - name: encoding
    type: string
    required: false
    default: "utf-8"
    description: File encoding (e.g., "utf-8", "gbk", "ascii")
---

# Android Write File

## When to use
- Save task results or extracted data to a file
- Create configuration files
- Write logs or reports to device storage
- Store intermediate data for later processing

## When NOT to use
- For setting clipboard content → use `android.set_clipboard`
- For writing to app-private directories → not supported without root
- For very large files → consider splitting into chunks

## Best practices
- Always use `/sdcard/` paths for public storage access
- Use `append: true` to add content to existing files
- Use `append: false` (default) to create or overwrite files
- Verify write success by reading the file back with `android.read_file`

## Example
```json
{
  "tool": "android.write_file",
  "args": {"path": "/sdcard/Download/result.txt", "content": "Task completed successfully", "append": false}
}
```
