---
name: pdf
description: 本地创建、提取、合并和按页拆分 PDF。
---

# PDF

- `pdf.create`: 从标题、段落、列表、表格和分页块创建 PDF，支持中文。
- `pdf.extract_text`: 按 `1-3,5` 形式有界提取文本。
- `pdf.merge`: 按输入顺序合并多个 PDF。
- `pdf.split`: 将选定页拆成单页 PDF。
- `pdf.info`: 检查页数、元数据、加密状态和表单字段。

写入已存在的目标文件一律需要显式 `overwrite=true`。
