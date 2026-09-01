"""Bounded, dependency-light text extraction for uploaded documents.

OOXML formats are ZIP containers, so PPTX, DOCX, and XLSX can be inspected
with the standard library. PDF extraction remains best-effort because PDF is
not a structured XML container and needs an optional parser.
"""

from __future__ import annotations

import csv
import posixpath
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree as ET

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_xml_fromstring

DEFAULT_MAX_EXTRACT_CHARS = 12_000
_MAX_ARCHIVE_ENTRIES = 4_096
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_TRUNCATION_MARKER = "\n\n[…truncated; use read_file with the attachment path for more]"

_PLAIN_TEXT_EXTENSIONS = {
    "bash",
    "bat",
    "c",
    "cfg",
    "cmake",
    "cmd",
    "conf",
    "cpp",
    "cs",
    "css",
    "csv",
    "dockerfile",
    "env",
    "go",
    "gradle",
    "h",
    "hpp",
    "htm",
    "html",
    "ini",
    "java",
    "js",
    "json",
    "jsx",
    "kt",
    "lock",
    "makefile",
    "md",
    "php",
    "properties",
    "ps1",
    "py",
    "rb",
    "rs",
    "rst",
    "sh",
    "sql",
    "svg",
    "swift",
    "toml",
    "ts",
    "tsv",
    "tsx",
    "txt",
    "xml",
    "yaml",
    "yml",
    "zsh",
}


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    format: str
    truncated: bool = False


def extract_document_text(
    data: bytes,
    extension: str | None,
    *,
    max_chars: int = DEFAULT_MAX_EXTRACT_CHARS,
) -> ExtractedDocument | None:
    """Return bounded text for a supported document, or ``None``."""

    ext = (extension or "").lower().lstrip(".")
    if not ext or max_chars <= 0:
        return None
    extractors: dict[str, Callable[[bytes], str | None]] = {
        "pdf": _extract_pdf,
        "pptx": _extract_pptx,
        "docx": _extract_docx,
        "xlsx": _extract_xlsx,
    }
    extractor = extractors.get(ext)
    if extractor is not None:
        text = extractor(data)
    elif ext in _PLAIN_TEXT_EXTENSIONS:
        text = (
            _extract_delimited(data, "\t" if ext == "tsv" else ",")
            if ext in {"csv", "tsv"}
            else _extract_plain_text(data)
        )
    else:
        return None
    if not text or not text.strip():
        return None
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return ExtractedDocument(text=normalized, format=ext)
    return ExtractedDocument(
        text=normalized[:max_chars] + _TRUNCATION_MARKER,
        format=ext,
        truncated=True,
    )


def extract_document_path(
    path: Path,
    *,
    max_chars: int = DEFAULT_MAX_EXTRACT_CHARS,
) -> ExtractedDocument | None:
    return extract_document_text(
        path.read_bytes(),
        path.suffix,
        max_chars=max_chars,
    )


def extract_text_from_upload(data: bytes, extension: str | None) -> str | None:
    """Compatibility wrapper used by the upload response preview."""

    result = extract_document_text(data, extension)
    return result.text if result is not None else None


def _safe_ooxml(data: bytes) -> zipfile.ZipFile | None:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile):
        return None
    if len(entries) > _MAX_ARCHIVE_ENTRIES:
        archive.close()
        return None
    total_size = 0
    for entry in entries:
        if entry.flag_bits & 0x1:
            archive.close()
            return None
        total_size += entry.file_size
        if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            archive.close()
            return None
    return archive


def _xml_root(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return safe_xml_fromstring(archive.read(name))
    except (
        KeyError,
        RuntimeError,
        ET.ParseError,
        DefusedXmlException,
        zipfile.BadZipFile,
    ):
        return None


def _natural_xml_key(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.xml$)", name)
    return (int(match.group(1)) if match else 0, name)


def _extract_pptx(data: bytes) -> str | None:
    archive = _safe_ooxml(data)
    if archive is None:
        return None
    try:
        slide_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_natural_xml_key,
        )
        slides: list[str] = []
        for index, name in enumerate(slide_names, start=1):
            root = _xml_root(archive, name)
            if root is None:
                continue
            texts = [
                node.text.strip()
                for node in root.iter()
                if node.tag.endswith("}t") and node.text and node.text.strip()
            ]
            if texts:
                slides.append(f"--- slide {index} ---\n" + "\n".join(texts))
        return "\n\n".join(slides) or None
    finally:
        archive.close()


def _extract_docx(data: bytes) -> str | None:
    archive = _safe_ooxml(data)
    if archive is None:
        return None
    try:
        names = ["word/document.xml"] + sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            ),
            key=_natural_xml_key,
        )
        sections: list[str] = []
        for name in names:
            root = _xml_root(archive, name)
            if root is None:
                continue
            paragraphs: list[str] = []
            for paragraph in (node for node in root.iter() if node.tag.endswith("}p")):
                text = "".join(
                    node.text or "" for node in paragraph.iter() if node.tag.endswith("}t")
                ).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                sections.append("\n".join(paragraphs))
        return "\n\n".join(sections) or None
    finally:
        archive.close()


def _extract_xlsx(data: bytes) -> str | None:
    archive = _safe_ooxml(data)
    if archive is None:
        return None
    try:
        shared: list[str] = []
        shared_root = _xml_root(archive, "xl/sharedStrings.xml")
        if shared_root is not None:
            for item in (node for node in shared_root.iter() if node.tag.endswith("}si")):
                shared.append(
                    "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
                )
        sheet_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ),
            key=_natural_xml_key,
        )
        sheet_titles = _xlsx_sheet_titles(archive)
        sheets: list[str] = []
        for index, name in enumerate(sheet_names, start=1):
            root = _xml_root(archive, name)
            if root is None:
                continue
            rows: list[str] = []
            for fallback_row, row in enumerate(
                (node for node in root.iter() if node.tag.endswith("}row")),
                start=1,
            ):
                cells: list[str] = []
                for cell in (node for node in row if node.tag.endswith("}c")):
                    reference = cell.attrib.get("r", "")
                    column_index = _xlsx_column_index(reference)
                    if column_index is not None:
                        while len(cells) < column_index:
                            cells.append("")
                    cell_type = cell.attrib.get("t")
                    value_node = next((node for node in cell if node.tag.endswith("}v")), None)
                    if cell_type == "inlineStr":
                        value = "".join(
                            node.text or "" for node in cell.iter() if node.tag.endswith("}t")
                        )
                    else:
                        value = (
                            value_node.text if value_node is not None and value_node.text else ""
                        )
                        if cell_type == "s" and value.isdigit():
                            shared_index = int(value)
                            value = shared[shared_index] if shared_index < len(shared) else value
                    cells.append(value)
                if cells:
                    row_number = _xlsx_row_number(row, fallback_row)
                    rows.append(f"Row {row_number}\t" + "\t".join(cells))
            if rows:
                title = sheet_titles.get(name, f"Sheet {index}")
                sheets.append(f"--- sheet {index}: {title} ---\n" + "\n".join(rows))
        return "\n\n".join(sheets) or None
    finally:
        archive.close()


def _xlsx_sheet_titles(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map worksheet archive paths to their user-visible workbook names."""

    workbook = _xml_root(archive, "xl/workbook.xml")
    relationships = _xml_root(archive, "xl/_rels/workbook.xml.rels")
    if workbook is None or relationships is None:
        return {}
    targets: dict[str, str] = {}
    for relationship in relationships:
        relationship_id = relationship.attrib.get("Id", "")
        target = relationship.attrib.get("Target", "")
        if not relationship_id or not target:
            continue
        normalized = posixpath.normpath(
            target.lstrip("/") if target.startswith("/xl/") else f"xl/{target}"
        )
        targets[relationship_id] = normalized
    titles: dict[str, str] = {}
    for sheet in (node for node in workbook.iter() if node.tag.endswith("}sheet")):
        relationship_id = next(
            (value for key, value in sheet.attrib.items() if key.endswith("}id")),
            "",
        )
        target = targets.get(relationship_id)
        title = sheet.attrib.get("name", "").strip()
        if target and title:
            titles[target] = title
    return titles


def _xlsx_column_index(reference: str) -> int | None:
    match = re.match(r"([A-Za-z]+)", reference)
    if match is None:
        return None
    value = 0
    for character in match.group(1).upper():
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value - 1


def _xlsx_row_number(row: ET.Element, fallback: int) -> str:
    explicit = row.attrib.get("r", "").strip()
    if explicit.isdigit():
        return explicit
    for cell in (node for node in row if node.tag.endswith("}c")):
        match = re.search(r"(\d+)$", cell.attrib.get("r", ""))
        if match:
            return match.group(1)
    return str(fallback)


def _extract_pdf(data: bytes) -> str | None:
    try:
        import pdfplumber  # type: ignore[import-not-found]

        with pdfplumber.open(BytesIO(data)) as pdf:
            pages = [
                f"--- page {index} ---\n{text}"
                for index, page in enumerate(pdf.pages, start=1)
                if (text := (page.extract_text() or "").strip())
            ]
            return "\n\n".join(pages) or None
    except Exception:  # noqa: BLE001 — malformed/unsupported PDFs fall through to pypdf
        pass
    try:
        import pypdf  # type: ignore[import-not-found]

        reader = pypdf.PdfReader(BytesIO(data))
        pages = [
            f"--- page {index} ---\n{text}"
            for index, page in enumerate(reader.pages, start=1)
            if (text := (page.extract_text() or "").strip())
        ]
        return "\n\n".join(pages) or None
    except Exception:
        return None


def _extract_delimited(data: bytes, delimiter: str) -> str | None:
    text = data.decode("utf-8", errors="replace")
    rows = [
        "\t".join(cell.strip() for cell in row)
        for row in csv.reader(StringIO(text), delimiter=delimiter)
    ]
    return "\n".join(rows) or None


def _extract_plain_text(data: bytes) -> str | None:
    return data.decode("utf-8", errors="replace").strip() or None
