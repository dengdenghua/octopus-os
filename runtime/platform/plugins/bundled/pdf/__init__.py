"""Local PDF creation, extraction and page operations for Echo."""

from __future__ import annotations

import contextlib
from html import escape
from pathlib import Path
from typing import Any

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.bundled._office_io import atomic_package_save, scoped_path_denial
from runtime.platform.plugins.plugin_base import ModulePlugin

PLUGIN_NAME = "pdf"
_TRUSTED_SOURCE = "plugin://pdf"
_MAX_EXTRACT_CHARS = 200_000

try:  # pragma: no cover - dependency probe
    from pypdf import PdfReader, PdfWriter

    _PYPDF_OK = True
except Exception:  # pragma: no cover
    _PYPDF_OK = False

try:  # pragma: no cover - dependency probe
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    _REPORTLAB_OK = True
except Exception:  # pragma: no cover
    _REPORTLAB_OK = False


def _resolve_pdf(path: Any, *, write: bool = False) -> tuple[Path | None, dict[str, Any] | None]:
    if not isinstance(path, (str, Path)) or not str(path).strip():
        return None, {"ok": False, "error": "path 不能为空"}
    try:
        resolved = Path(str(path)).expanduser().resolve()
    except Exception as exc:
        return None, {"ok": False, "error": f"无效路径: {exc}"}
    if resolved.suffix.lower() != ".pdf":
        return None, {"ok": False, "error": "仅支持 .pdf 文件"}
    denial = scoped_path_denial(resolved, write=write)
    if denial:
        return None, {"ok": False, "error": denial}
    return resolved, None


def _require_pypdf() -> dict[str, Any] | None:
    if _PYPDF_OK:
        return None
    return {"ok": False, "error": "pypdf 未安装，无法处理 PDF"}


def _open_reader(path_value: Any, *, allow_encrypted: bool = False):
    path, err = _resolve_pdf(path_value)
    if err:
        return None, None, err
    assert path is not None
    if not path.is_file():
        return None, None, {"ok": False, "error": f"文件不存在: {path}"}
    dependency_error = _require_pypdf()
    if dependency_error:
        return None, None, dependency_error
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not allow_encrypted:
            return (
                None,
                None,
                {
                    "ok": False,
                    "error": "PDF 已加密，当前本地插件不能在未提供安全解密流程时读取或改写",
                },
            )
        return path, reader, None
    except Exception as exc:  # noqa: BLE001
        return None, None, {"ok": False, "error": f"无法打开 PDF: {exc}"}


def _parse_pages(value: Any, total: int) -> tuple[list[int] | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return list(range(total)), None
    selected: set[int] = set()
    try:
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_raw, end_raw = part.split("-", 1)
                start, end = int(start_raw), int(end_raw)
                if start > end:
                    start, end = end, start
                selected.update(range(start - 1, end))
            else:
                selected.add(int(part) - 1)
    except ValueError:
        return None, "pages 格式应为 1-3,5"
    if not selected or min(selected) < 0 or max(selected) >= total:
        return None, f"pages 超出 1-{total} 范围"
    return sorted(selected), None


class PdfPlugin(ModulePlugin):
    name = PLUGIN_NAME
    display_name = "PDF"
    version = "0.1.0"
    description = "本地创建、提取、合并和拆分 PDF。"
    author = "Echo"

    def register_skills(self) -> None:
        if self.ctx is None:
            return
        skills = [
            Skill(
                name="pdf.create",
                description=(
                    "从结构化 blocks 创建 PDF。参数:path，title 可选，blocks 支持"
                    "heading/paragraph/list/table/page_break，overwrite 默认 false。"
                ),
                summary="创建 PDF(path+blocks)",
                affinity=["pdf", "document", "file", "write", "create", "report"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._create,
            ),
            Skill(
                name="pdf.extract_text",
                description="按页提取 PDF 文本。参数:path，pages 可选(如 1-3,5)。",
                summary="提取 PDF 文本",
                affinity=["pdf", "document", "file", "extract", "read"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._extract_text,
            ),
            Skill(
                name="pdf.merge",
                description="按顺序合并多个 PDF。参数:paths，output_path，overwrite 默认 false。",
                summary="合并 PDF",
                affinity=["pdf", "document", "file", "write", "merge"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._merge,
            ),
            Skill(
                name="pdf.split",
                description=(
                    "将 PDF 的选定页拆成独立文件。参数:path，output_dir，pages 可选，"
                    "overwrite 默认 false。"
                ),
                summary="按页拆分 PDF",
                affinity=["pdf", "document", "file", "write", "split"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._split,
            ),
            Skill(
                name="pdf.info",
                description="返回 PDF 页数、元数据、加密和表单字段信息。参数:path。",
                summary="PDF 结构信息",
                affinity=["pdf", "document", "file", "read", "info"],
                cost_profile="low",
                trusted_source=_TRUSTED_SOURCE,
                handler=self._info,
            ),
        ]
        for skill in skills:
            with contextlib.suppress(Exception):
                self.ctx.register_skill(skill)

    def _create(self, **kwargs: Any) -> dict[str, Any]:
        path, err = _resolve_pdf(kwargs.get("path"), write=True)
        if err:
            return err
        assert path is not None
        if path.exists() and not kwargs.get("overwrite"):
            return {"ok": False, "error": f"目标已存在: {path},需 overwrite=true"}
        if not _REPORTLAB_OK:
            return {"ok": False, "error": "reportlab 未安装，无法创建 PDF"}
        blocks = kwargs.get("blocks") or []
        if not isinstance(blocks, list):
            return {"ok": False, "error": "blocks 必须是数组"}
        try:
            font_name = "Helvetica"
            try:
                pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
                font_name = "STSong-Light"
            except Exception:  # pragma: no cover - host font fallback
                pass
            styles = getSampleStyleSheet()
            body_style = ParagraphStyle(
                "EchoBody",
                parent=styles["BodyText"],
                fontName=font_name,
                fontSize=10.5,
                leading=17,
                spaceAfter=7,
            )
            heading_style = ParagraphStyle(
                "EchoHeading",
                parent=body_style,
                fontSize=18,
                leading=24,
                spaceBefore=10,
                spaceAfter=8,
            )
            title_style = ParagraphStyle(
                "EchoTitle",
                parent=heading_style,
                fontSize=24,
                leading=30,
                alignment=TA_CENTER,
                spaceAfter=18,
            )
            story: list[Any] = []
            if kwargs.get("title"):
                story.append(Paragraph(escape(str(kwargs["title"])), title_style))
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                kind = str(block.get("type") or "paragraph")
                if kind == "page_break":
                    story.append(PageBreak())
                elif kind == "heading":
                    story.append(Paragraph(escape(str(block.get("text") or "")), heading_style))
                elif kind == "list":
                    for item in block.get("items") or []:
                        story.append(Paragraph(escape(f"• {item}"), body_style))
                elif kind == "table":
                    rows = []
                    headers = block.get("headers") or []
                    if headers:
                        rows.append([str(value) for value in headers])
                    rows.extend(
                        [str(value) for value in row]
                        for row in (block.get("rows") or [])
                        if isinstance(row, (list, tuple))
                    )
                    if rows:
                        table = Table(rows, repeatRows=1 if headers else 0)
                        table.setStyle(
                            TableStyle(
                                [
                                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF8")),
                                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                ]
                            )
                        )
                        story.extend([table, Spacer(1, 4 * mm)])
                else:
                    story.append(Paragraph(escape(str(block.get("text") or "")), body_style))

            def save_pdf(temporary: Path) -> None:
                document = SimpleDocTemplate(
                    str(temporary),
                    pagesize=A4,
                    rightMargin=18 * mm,
                    leftMargin=18 * mm,
                    topMargin=18 * mm,
                    bottomMargin=18 * mm,
                    title=str(kwargs.get("title") or ""),
                )
                document.build(story)

            atomic_package_save(path, save_pdf)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"创建 PDF 失败: {exc}"}
        return {"ok": True, "path": str(path), "size_bytes": path.stat().st_size}

    def _extract_text(self, **kwargs: Any) -> dict[str, Any]:
        path, reader, err = _open_reader(kwargs.get("path"))
        if err:
            return err
        selected, pages_error = _parse_pages(kwargs.get("pages"), len(reader.pages))
        if pages_error:
            return {"ok": False, "error": pages_error}
        extracted = []
        total_chars = 0
        truncated = False
        for index in selected or []:
            text = str(reader.pages[index].extract_text() or "")
            remaining = _MAX_EXTRACT_CHARS - total_chars
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            total_chars += len(text)
            extracted.append({"page": index + 1, "text": text})
        return {
            "ok": True,
            "path": str(path),
            "pages": extracted,
            "total_chars": total_chars,
            "truncated": truncated,
        }

    def _merge(self, **kwargs: Any) -> dict[str, Any]:
        raw_paths = kwargs.get("paths")
        if not isinstance(raw_paths, list) or len(raw_paths) < 2:
            return {"ok": False, "error": "paths 至少需要 2 个 PDF"}
        output, output_err = _resolve_pdf(kwargs.get("output_path"), write=True)
        if output_err:
            return output_err
        assert output is not None
        if output.exists() and not kwargs.get("overwrite"):
            return {"ok": False, "error": f"目标已存在: {output},需 overwrite=true"}
        dependency_error = _require_pypdf()
        if dependency_error:
            return dependency_error
        writer = PdfWriter()
        inputs: list[str] = []
        try:
            for value in raw_paths:
                path, reader, err = _open_reader(value)
                if err:
                    return err
                for page in reader.pages:
                    writer.add_page(page)
                inputs.append(str(path))

            def save_merged(temporary: Path) -> None:
                with temporary.open("wb") as stream:
                    writer.write(stream)

            atomic_package_save(output, save_merged)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"合并 PDF 失败: {exc}"}
        return {"ok": True, "path": str(output), "inputs": inputs, "pages": len(writer.pages)}

    def _split(self, **kwargs: Any) -> dict[str, Any]:
        path, reader, err = _open_reader(kwargs.get("path"))
        if err:
            return err
        selected, pages_error = _parse_pages(kwargs.get("pages"), len(reader.pages))
        if pages_error:
            return {"ok": False, "error": pages_error}
        output_raw = kwargs.get("output_dir")
        if not isinstance(output_raw, (str, Path)) or not str(output_raw).strip():
            return {"ok": False, "error": "output_dir 不能为空"}
        try:
            output_dir = Path(str(output_raw)).expanduser().resolve()
        except Exception as exc:
            return {"ok": False, "error": f"无效 output_dir: {exc}"}
        denial = scoped_path_denial(output_dir, write=True)
        if denial:
            return {"ok": False, "error": denial}
        planned = [output_dir / f"{path.stem}-page-{index + 1}.pdf" for index in (selected or [])]
        existing = [str(item) for item in planned if item.exists()]
        if existing and not kwargs.get("overwrite"):
            return {
                "ok": False,
                "error": "目标页文件已存在，需 overwrite=true",
                "existing": existing,
            }
        try:
            for index, target in zip(selected or [], planned, strict=True):
                writer = PdfWriter()
                writer.add_page(reader.pages[index])

                def save_page(temporary: Path, page_writer: Any = writer) -> None:
                    with temporary.open("wb") as stream:
                        page_writer.write(stream)

                atomic_package_save(target, save_page)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"拆分 PDF 失败: {exc}"}
        return {"ok": True, "source": str(path), "files": [str(item) for item in planned]}

    def _info(self, **kwargs: Any) -> dict[str, Any]:
        path, reader, err = _open_reader(kwargs.get("path"), allow_encrypted=True)
        if err:
            return err
        metadata: dict[str, str] = {}
        fields: dict[str, Any] = {}
        if not reader.is_encrypted:
            metadata = {
                str(key).lstrip("/"): str(value) for key, value in (reader.metadata or {}).items()
            }
            fields = reader.get_fields() or {}
        return {
            "ok": True,
            "path": str(path),
            "pages": None if reader.is_encrypted else len(reader.pages),
            "encrypted": bool(reader.is_encrypted),
            "metadata": metadata,
            "form_fields": sorted(str(name) for name in fields),
            "size_bytes": path.stat().st_size,
        }


__all__ = ["PdfPlugin"]
