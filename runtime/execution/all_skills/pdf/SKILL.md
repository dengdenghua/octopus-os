---
name: pdf
description: "Read, inspect, create, render, and verify PDF files when the user asks to work with PDFs, extract PDF content, generate a PDF deliverable, or visually validate PDF layout."
license: MIT
---

# PDF Workflows

Use this skill for PDF-heavy tasks: extracting text or tables, inspecting metadata,
creating polished PDF deliverables, rendering pages for visual QA, and verifying
layout before handoff.

## Workflow

1. Identify whether the task is read-only, conversion, generation, or visual QA.
2. Prefer structured PDF libraries for extraction and generation instead of ad
   hoc text parsing.
3. When layout matters, render pages to images and inspect the output before
   calling the result complete.
4. Keep generated files in the workspace and report the exact output path.

## Notes

- For large PDFs, ask for page ranges or process a bounded sample first.
- For generated PDFs, verify pagination, clipping, fonts, and table overflow.
- For scanned PDFs, use OCR only when text extraction fails or the document is
  image-only.
