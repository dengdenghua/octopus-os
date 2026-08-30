---
name: android.read_file
description: |
  Read a file from the device's storage.
  Supports reading from /sdcard/ (public storage) and /data/data/ (app-private, requires root).
  Returns the file content as a string.
affinity: [mobile, gui, automation, file]
parameters:
  - name: path
    type: string
    required: true
    description: Absolute file path to read (e.g., "/sdcard/Download/report.txt")
  - name: encoding
    type: string
    required: false
    default: "utf-8"
    description: File encoding (e.g., "utf-8", "gbk", "ascii")
  - name: max_size
    type: integer
    required: false
    default: 1048576
    description: Maximum file size to read in bytes (default 1MB)
---

# Android Read File

## When to use
- Read configuration or data files from device storage
- Access downloaded files for processing
- Read log files for debugging

## When NOT to use
- For reading clipboard content → use `android.get_clipboard`
- For reading web page content → use `android.browser.get_dom`
- For reading screen text → use `android.get_screen_info`

## Best practices
- Use absolute paths starting with `/sdcard/` for public storage
- Set appropriate `encoding` for files with non-UTF-8 content
- Use `max_size` to avoid reading very large files
- Reading from `/data/data/` requires root access

## Example
```json
{
  "tool": "android.read_file",
  "args": {"path": "/sdcard/Download/report.txt", "encoding": "utf-8"}
}
```
