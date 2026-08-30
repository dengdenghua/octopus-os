---
name: presentations
description: 本地创建、读取和修改 pptx，支持按幻灯片精确替换。
---

# Presentations

- `presentations.create_pptx`: 从标题、副标题和要点创建 16:9 演示文稿。
- `presentations.extract_text`: 按页提取形状、表格文本和备注。
- `presentations.replace_text`: 全局或按页码精确替换文本，默认备份原文件。
- `presentations.presentation_info`: 检查页数、尺寸、形状、表格与图片。

修改现有 PPT 时优先用 `replace_text`，不要为了改一页而重建整份演示文稿。
