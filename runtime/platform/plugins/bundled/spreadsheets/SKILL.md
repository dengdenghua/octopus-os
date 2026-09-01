---
name: spreadsheets
description: 本地创建、读取、分析和修改 xlsx/xlsm，保留公式及未修改样式。
---

# Spreadsheets

- `spreadsheets.create_xlsx`: 从多个 sheet 的二维数组创建工作簿，支持冻结窗格、列宽、表头和自动筛选。
- `spreadsheets.read_sheet`: 有界读取指定工作表或 A1 区域。
- `spreadsheets.update_cells`: 原位修改单元格值、公式和数字格式，默认备份。
- `spreadsheets.workbook_info`: 检查工作表、使用区域、合并单元格和公式数量。

对现有工作簿优先使用 `update_cells`，不要重建整个文件，以免破坏公式、样式和其他工作表。
