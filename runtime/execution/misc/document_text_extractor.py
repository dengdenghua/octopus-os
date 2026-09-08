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
from collections.abc import Iterator
from dataclasses import dataclass
from io import BufferedReader, BytesIO, RawIOBase, TextIOWrapper
from pathlib import Path
from xml.etree import ElementTree as ET

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import iterparse as safe_xml_iterparse

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
    page_count: int | None = None
    pages_extracted: tuple[int, ...] = ()


class _LimitReached(Exception):
    """Internal control flow; never downgrade a reached budget to no text."""


class PageSelectionError(ValueError):
    """The requested PDF pages are not valid for the source document."""

    def __init__(self, message: str, *, page_count: int | None = None) -> None:
        super().__init__(message)
        self.page_count = page_count


class _TextBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: list[str] = []
        self.length = 0
        self.overflow_whitespace = False

    @property
    def remaining(self) -> int:
        return self.limit - self.length

    def add(self, text: str) -> None:
        if not self.length:
            text = text.lstrip()
        if not text:
            return
        if self.overflow_whitespace:
            if text.strip():
                raise _LimitReached
            return
        taken = text[: self.remaining]
        self.parts.append(taken)
        self.length += len(taken)
        extra = text[len(taken) :]
        if extra:
            if extra.strip():
                raise _LimitReached
            # Trailing whitespace is removed by the historical public ABI.
            # Remember it without allocating an arbitrarily large blank gap.
            self.overflow_whitespace = True

    def value(self) -> str:
        return "".join(self.parts).strip()

    def prefix(self) -> str:
        return "".join(self.parts)


class _ExpansionBudget:
    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)

    def charge(self, count: int) -> None:
        self.used += count
        if self.limit is not None and self.used > self.limit:
            raise _LimitReached


class _BudgetReader(RawIOBase):
    """Limit actual expanded bytes delivered from ZIP/plain streams to parsers."""

    def __init__(self, stream: object, size: int, budget: _ExpansionBudget) -> None:
        super().__init__()
        self.stream = stream
        self.size = size
        self.consumed = 0
        self.budget = budget

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size == 0 or self.consumed >= self.size:
            return b""
        count = min(64 * 1024, self.size - self.consumed)
        if size >= 0:
            count = min(count, size)
        if self.budget.remaining is not None:
            count = min(count, self.budget.remaining)
        if count <= 0:
            raise _LimitReached
        data = self.stream.read(count)
        self.consumed += len(data)
        self.budget.charge(len(data))
        return data

    def readinto(self, buffer: bytearray) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _positive_limit(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError(f"{name} must be a positive integer or None")


def extract_document_text(
    data: bytes,
    extension: str | None,
    *,
    max_chars: int = DEFAULT_MAX_EXTRACT_CHARS,
    max_pages: int | None = None,
    max_expanded_bytes: int | None = None,
    pages: list[int] | tuple[int, ...] | None = None,
    include_page_markers: bool = False,
) -> ExtractedDocument | None:
    """Extract incrementally; a reached budget always yields ``truncated=True``.

    Expansion counts bytes actually read from OOXML/plain streams and decoded
    PDF page content streams. PDF font/CMap/xref work and allocation inside a
    single decoder are not a process-memory cap: the external worker must apply
    hard OS limits. MemoryError propagates to that worker, without retrying a
    second PDF parser. ``max_pages`` covers PDF pages and PPTX slides.
    """
    _positive_limit(max_pages, "max_pages")
    _positive_limit(max_expanded_bytes, "max_expanded_bytes")
    ext = (extension or "").lower().lstrip(".")
    if not ext or max_chars <= 0:
        return None
    if type(max_chars) is not int:
        raise ValueError("max_chars must be an integer")
    if ext not in {"pdf", "pptx", "docx", "xlsx"} | _PLAIN_TEXT_EXTENSIONS:
        return None
    selected_pages: list[int] | None = None
    if pages is not None:
        if ext != "pdf":
            raise ValueError("pages is only supported for PDF documents")
        if not isinstance(pages, (list, tuple)) or len(pages) > 200:
            raise ValueError("pages must contain at most 200 page numbers")
        selected_pages = []
        for page in pages:
            if type(page) is not int or page <= 0:
                raise ValueError("pages must contain positive page numbers")
            if page not in selected_pages:
                selected_pages.append(page)
        selected_pages.sort()
        if not selected_pages:
            raise ValueError("pages must contain at least one page number")
    output = _TextBudget(max_chars)
    expansion = _ExpansionBudget(max_expanded_bytes)
    truncated = False
    page_info: dict[str, object] = {}
    try:
        if ext == "pdf":
            _extract_pdf(
                data,
                output,
                expansion,
                max_pages,
                pages=selected_pages,
                page_info=page_info,
                include_page_markers=include_page_markers,
            )
        elif ext in {"pptx", "docx", "xlsx"}:
            # Preserve the existing independent archive ceiling even when the
            # caller does not request a smaller cumulative expanded-byte budget.
            expansion.limit = min(
                max_expanded_bytes or _MAX_ARCHIVE_UNCOMPRESSED_BYTES,
                _MAX_ARCHIVE_UNCOMPRESSED_BYTES,
            )
            _extract_ooxml(data, ext, output, expansion, max_pages)
        else:
            with TextIOWrapper(
                BufferedReader(_BudgetReader(BytesIO(data), len(data), expansion)),
                encoding="utf-8",
                errors="replace",
                newline="",
            ) as stream:
                if ext in {"csv", "tsv"}:
                    _extract_delimited(stream, "\t" if ext == "tsv" else ",", output)
                else:
                    while chunk := stream.read(4096):
                        output.add(chunk)
    except _LimitReached:
        truncated = True
    except (OSError, zipfile.BadZipFile, ET.ParseError, DefusedXmlException, csv.Error):
        # A failed tail must never turn a valid-looking prefix into a complete
        # document. A wholly unreadable input retains the existing None result.
        truncated = bool(output.length)
    text = output.prefix() if truncated else output.value()
    raw_pages = page_info.get("pages_extracted")
    pages_extracted = tuple(int(page) for page in raw_pages) if isinstance(raw_pages, list) else ()
    raw_page_count = page_info.get("page_count")
    page_count = int(raw_page_count) if type(raw_page_count) is int else None
    if truncated:
        return ExtractedDocument(
            text + _TRUNCATION_MARKER,
            ext,
            truncated=True,
            page_count=page_count,
            pages_extracted=pages_extracted,
        )
    if not text:
        return None
    return ExtractedDocument(
        text,
        ext,
        truncated,
        page_count=page_count,
        pages_extracted=pages_extracted,
    )


def extract_document_path(
    path: Path,
    *,
    max_chars: int = DEFAULT_MAX_EXTRACT_CHARS,
    max_pages: int | None = None,
    max_expanded_bytes: int | None = None,
    pages: list[int] | tuple[int, ...] | None = None,
) -> ExtractedDocument | None:
    return extract_document_text(
        path.read_bytes(),
        path.suffix,
        max_chars=max_chars,
        max_pages=max_pages,
        max_expanded_bytes=max_expanded_bytes,
        pages=pages,
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
    if (
        len(entries) > _MAX_ARCHIVE_ENTRIES
        or sum(e.file_size for e in entries) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES
    ):
        archive.close()
        raise _LimitReached
    if any(entry.flag_bits & 0x1 for entry in entries):
        archive.close()
        return None
    return archive


def _xml_elements(
    archive: zipfile.ZipFile,
    name: str,
    tag: str,
    budget: _ExpansionBudget,
) -> Iterator[ET.Element]:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return
    with archive.open(info) as stream:
        reader = _BudgetReader(stream, info.file_size, budget)
        stack: list[ET.Element] = []
        captured_depth: int | None = None
        for event, element in safe_xml_iterparse(reader, events=("start", "end")):
            if event == "start":
                stack.append(element)
                if captured_depth is None and element.tag.rsplit("}", 1)[-1] == tag:
                    captured_depth = len(stack)
                continue
            if captured_depth == len(stack):
                yield element
                captured_depth = None
            if captured_depth is None:
                element.clear()
                if len(stack) > 1:
                    stack[-2].remove(element)
            stack.pop()


def _natural_xml_key(name: str) -> tuple[int, str, str]:
    match = re.search(r"(\d+)(?=\.xml$)", name)
    # Archive entry names are untrusted; avoid converting an unbounded integer.
    digits = (match.group(1).lstrip("0") if match else "") or "0"
    return (len(digits), digits, name)


def _extract_ooxml(
    data: bytes,
    ext: str,
    output: _TextBudget,
    budget: _ExpansionBudget,
    max_pages: int | None,
) -> None:
    archive = _safe_ooxml(data)
    if archive is None:
        return
    with archive:
        if ext == "docx":
            names = ["word/document.xml"] + sorted(
                (
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
                ),
                key=_natural_xml_key,
            )
            for name in names:
                first = True
                for paragraph in _xml_elements(archive, name, "p", budget):
                    text = "".join(
                        node.text or "" for node in paragraph.iter() if node.tag.endswith("}t")
                    ).strip()
                    if text:
                        output.add(("\n\n" if first else "\n") if output.length else "")
                        output.add(text)
                        first = False
        elif ext == "pptx":
            names = sorted(
                (
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ),
                key=_natural_xml_key,
            )
            for index, name in enumerate(names, start=1):
                if max_pages is not None and index > max_pages:
                    raise _LimitReached
                first = True
                for node in _xml_elements(archive, name, "t", budget):
                    text = (node.text or "").strip()
                    if text:
                        output.add(
                            ("\n\n" if output.length else "") + f"--- slide {index} ---\n"
                            if first
                            else "\n"
                        )
                        output.add(text)
                        first = False
        else:
            _extract_xlsx(archive, output, budget)


def _extract_xlsx(archive: zipfile.ZipFile, output: _TextBudget, budget: _ExpansionBudget) -> None:
    shared = [
        "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
        for item in _xml_elements(archive, "xl/sharedStrings.xml", "si", budget)
    ]
    titles = _xlsx_sheet_titles(archive, budget)
    names = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=_natural_xml_key,
    )
    for index, name in enumerate(names, start=1):
        first = True
        for fallback_row, row in enumerate(_xml_elements(archive, name, "row", budget), start=1):
            cells = (node for node in row if node.tag.endswith("}c"))
            column = 0
            row_started = False
            for cell in cells:
                if not row_started:
                    if first:
                        output.add(
                            ("\n\n" if output.length else "")
                            + f"--- sheet {index}: {titles.get(name, f'Sheet {index}')} ---\n"
                        )
                        first = False
                    else:
                        output.add("\n")
                    output.add(f"Row {_xlsx_row_number(row, fallback_row)}\t")
                    row_started = True
                column_index = _xlsx_column_index(
                    cell.attrib.get("r", ""), ceiling=column + output.remaining + 1
                )
                gap = max(0, column_index - column) if column_index is not None else 0
                output.add("\t" * min(gap + (1 if column else 0), output.remaining + 1))
                column += gap
                cell_type = cell.attrib.get("t")
                value_node = next((node for node in cell if node.tag.endswith("}v")), None)
                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter() if node.tag.endswith("}t")
                    )
                else:
                    value = value_node.text if value_node is not None and value_node.text else ""
                    if cell_type == "s" and value.isdigit() and len(value) <= 12:
                        shared_index = int(value)
                        value = shared[shared_index] if shared_index < len(shared) else value
                output.add(value)
                column += 1


def _xlsx_sheet_titles(archive: zipfile.ZipFile, budget: _ExpansionBudget) -> dict[str, str]:
    targets: dict[str, str] = {}
    for relationship in _xml_elements(
        archive, "xl/_rels/workbook.xml.rels", "Relationship", budget
    ):
        identity, target = relationship.attrib.get("Id", ""), relationship.attrib.get("Target", "")
        if identity and target:
            targets[identity] = posixpath.normpath(
                target.lstrip("/") if target.startswith("/xl/") else f"xl/{target}"
            )
    titles: dict[str, str] = {}
    for sheet in _xml_elements(archive, "xl/workbook.xml", "sheet", budget):
        identity = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), "")
        title = sheet.attrib.get("name", "").strip()
        if targets.get(identity) and title:
            titles[targets[identity]] = title
    return titles


def _xlsx_column_index(reference: str, *, ceiling: int) -> int | None:
    value = 0
    for character in reference:
        if not character.isascii() or not character.isalpha():
            break
        value = value * 26 + ord(character.upper()) - ord("A") + 1
        if value > ceiling:
            return ceiling
    return value - 1 if value else None


def _xlsx_row_number(row: ET.Element, fallback: int) -> str:
    explicit = row.attrib.get("r", "").strip()
    if explicit.isdigit():
        return explicit
    for cell in (node for node in row if node.tag.endswith("}c")):
        match = re.search(r"(\d+)$", cell.attrib.get("r", ""))
        if match:
            return match.group(1)
    return str(fallback)


def _extract_pdf(
    data: bytes,
    output: _TextBudget,
    budget: _ExpansionBudget,
    max_pages: int | None,
    *,
    pages: list[int] | None = None,
    page_info: dict[str, object] | None = None,
    include_page_markers: bool = False,
) -> None:
    page_info = page_info if page_info is not None else {}

    def selected_indexes(total: int) -> list[int]:
        page_info["page_count"] = total
        if pages is not None:
            invalid = [page for page in pages if page > total]
            if invalid:
                raise PageSelectionError(
                    f"pages 超出 1-{total} 范围",
                    page_count=total,
                )
            return [page - 1 for page in pages]
        return list(range(total))

    def mark_page(index: int) -> None:
        extracted = page_info.setdefault("pages_extracted", [])
        if isinstance(extracted, list):
            extracted.append(index + 1)

    try:
        import pypdf
        from pypdf.errors import LimitReachedError
    except ImportError:
        pypdf = None
    if pypdf is not None:
        candidate = _TextBudget(output.limit)
        try:
            reader = pypdf.PdfReader(BytesIO(data))
            indexes = selected_indexes(len(reader.pages))
            for position, index in enumerate(indexes):
                if pages is None and max_pages is not None and position >= max_pages:
                    raise _LimitReached
                if position and (candidate.remaining == 0 or budget.remaining == 0):
                    raise _LimitReached
                page = reader.pages[index]
                content = page.get_contents()
                if content is not None:
                    budget.charge(len(content.get_data()))
                text = (page.extract_text() or "").strip()
                mark_page(index)
                if text or include_page_markers:
                    candidate.add(
                        ("\n\n" if candidate.length else "") + f"--- page {index + 1} ---\n"
                    )
                if text:
                    candidate.add(text)
            output.add(candidate.value())
            return
        except MemoryError:
            raise
        except _LimitReached:
            output.add(candidate.prefix())
            raise
        except LimitReachedError as exc:
            output.add(candidate.prefix())
            raise _LimitReached from exc
        except PageSelectionError:
            raise
        except Exception:  # noqa: BLE001 — malformed/unsupported PDF; never catch resource failures above
            pass
    try:
        import pdfplumber
        from pdfminer.pdftypes import resolve1

        with pdfplumber.open(BytesIO(data)) as pdf:
            indexes = selected_indexes(len(pdf.pages))
            for position, index in enumerate(indexes):
                if pages is None and max_pages is not None and position >= max_pages:
                    raise _LimitReached
                if position and (output.remaining == 0 or budget.remaining == 0):
                    raise _LimitReached
                page = pdf.pages[index]
                for stream in page.page_obj.contents:
                    budget.charge(len(resolve1(stream).get_data()))
                text = (page.extract_text() or "").strip()
                mark_page(index)
                if text or include_page_markers:
                    output.add(("\n\n" if output.length else "") + f"--- page {index + 1} ---\n")
                if text:
                    output.add(text)
    except (MemoryError, _LimitReached, PageSelectionError):
        raise
    except Exception:  # noqa: BLE001 — optional parser or malformed PDF
        if output.length:
            raise _LimitReached from None


def _extract_delimited(stream: TextIOWrapper, delimiter: str, output: _TextBudget) -> None:
    first = True
    for row in csv.reader(stream, delimiter=delimiter):
        output.add("" if first else "\n")
        first = False
        for index, cell in enumerate(row):
            if index:
                output.add("\t")
            output.add(cell.strip())
