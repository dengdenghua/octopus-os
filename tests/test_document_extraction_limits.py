"""Logical extraction budgets using real PDF/ZIP/CSV bytes, not model doubles.

These prove parser control flow and bounded reads; OS memory/CPU enforcement is
the external worker's responsibility. Small injected limits are test conditions,
not proposed production resource limits.
"""

from __future__ import annotations

import sys
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pypdf
import pytest
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from runtime.execution.misc import document_text_extractor as extraction


def _pdf(*pages: str, compressed: bool = False) -> tuple[bytes, list[int]]:
    writer = pypdf.PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    lengths = []
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        content = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode("ascii")
        stream.set_data(content)
        lengths.append(len(content))
        page[NameObject("/Contents")] = writer._add_object(
            stream.flate_encode() if compressed else stream
        )
    data = BytesIO()
    writer.write(data)
    writer.close()
    return data.getvalue(), lengths


def _zip(parts: dict[str, str]) -> bytes:
    data = BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text.encode("utf-8"))
    return data.getvalue()


def _docx(text: str) -> str:
    return f'<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'


@pytest.mark.parametrize("name", ["max_pages", "max_expanded_bytes"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, float("inf"), "10"])
def test_new_limits_reject_non_positive_or_non_integer_values(name, value):
    with pytest.raises(ValueError, match=name):
        extraction.extract_document_text(b"text", "txt", **{name: value})


def test_pdf_page_limit_stops_real_extraction_and_preserves_prefix(monkeypatch):
    data, _ = _pdf("First invoice", "Later invoice", "Last invoice")
    extract = pypdf.PageObject.extract_text
    visited = []

    def observed(page, *args, **kwargs):
        visited.append(page)
        return extract(page, *args, **kwargs)

    monkeypatch.setattr(pypdf.PageObject, "extract_text", observed)
    result = extraction.extract_document_text(data, "pdf", max_pages=1)
    assert result is not None and result.truncated
    assert "First invoice" in result.text and "Later invoice" not in result.text
    assert len(visited) == 1
    complete = extraction.extract_document_text(data, "pdf", max_pages=3)
    assert complete is not None and not complete.truncated
    assert "Last invoice" in complete.text


def test_pdf_character_limit_never_parses_the_next_page(monkeypatch):
    data, _ = _pdf("x" * 200, "must not parse")
    extract = pypdf.PageObject.extract_text
    calls = []

    def observed(page, *args, **kwargs):
        calls.append(page)
        assert len(calls) == 1
        return extract(page, *args, **kwargs)

    monkeypatch.setattr(pypdf.PageObject, "extract_text", observed)
    result = extraction.extract_document_text(data, "pdf", max_chars=30)
    assert result is not None and result.truncated
    assert result.text.startswith("--- page 1 ---\n" + "x" * 15)
    assert len(calls) == 1


def test_pdf_expansion_is_cumulative_and_exact_boundary_is_complete():
    data, lengths = _pdf("First", "Second", compressed=True)
    result = extraction.extract_document_text(data, "pdf", max_expanded_bytes=lengths[0])
    assert result is not None and result.truncated
    assert "First" in result.text and "Second" not in result.text
    complete = extraction.extract_document_text(data, "pdf", max_expanded_bytes=sum(lengths))
    assert complete is not None and not complete.truncated and "Second" in complete.text
    too_small = extraction.extract_document_text(data, "pdf", max_expanded_bytes=1)
    assert too_small is not None and too_small.truncated
    assert "First" not in too_small.text


def test_blank_pdf_with_unread_pages_is_truncated_not_no_text():
    data, _ = _pdf("", "unread")
    result = extraction.extract_document_text(data, "pdf", max_pages=1)
    assert result is not None and result.truncated
    assert result.text == extraction._TRUNCATION_MARKER


@pytest.mark.parametrize("failure", [MemoryError, pypdf.errors.LimitReachedError])
def test_pypdf_resource_failure_never_falls_back(monkeypatch, failure):
    def broken(*args, **kwargs):
        raise failure("synthetic parser resource stop")

    def forbidden(*args, **kwargs):
        raise AssertionError("budget/resource failure must not retry another parser")

    monkeypatch.setattr(pypdf, "PdfReader", broken)
    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=forbidden))
    if failure is MemoryError:
        with pytest.raises(MemoryError):
            extraction.extract_document_text(b"synthetic", "pdf")
    else:
        result = extraction.extract_document_text(b"synthetic", "pdf")
        assert result is not None and result.truncated


def test_optional_pdf_fallback_memory_error_also_propagates(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("synthetic unsupported PDF")

    def exhausted(*args, **kwargs):
        raise MemoryError("synthetic fallback exhausted")

    monkeypatch.setattr(pypdf, "PdfReader", broken)
    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=exhausted))
    monkeypatch.setitem(
        sys.modules, "pdfminer.pdftypes", SimpleNamespace(resolve1=lambda value: value)
    )
    with pytest.raises(MemoryError):
        extraction.extract_document_text(b"synthetic", "pdf")


def test_docx_character_budget_stops_before_reading_another_xml_part(monkeypatch):
    data = _zip({"word/document.xml": _docx("x" * 100), "word/header1.xml": _docx("do not read")})
    original = zipfile.ZipFile.open
    opened = []

    def observed(archive, name, *args, **kwargs):
        opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", observed)
    result = extraction.extract_document_text(data, "docx", max_chars=20)
    assert result is not None and result.truncated
    assert result.text.startswith("x" * 20)
    assert opened == ["word/document.xml"]


def test_ooxml_limits_actual_reads_and_accumulates_across_parts(monkeypatch):
    document, header = _docx("Invoice Date: 2026-09-05"), _docx("second part")
    data = _zip({"word/document.xml": document, "word/header1.xml": header})
    sizes = [len(value.encode()) for value in (document, header)]
    original = zipfile.ZipExtFile.read
    observed = []

    def read(stream, count=-1):
        assert 0 < count <= 64 * 1024
        result = original(stream, count)
        observed.append(len(result))
        return result

    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    result = extraction.extract_document_text(data, "docx", max_expanded_bytes=sizes[0] + 3)
    assert result is not None and result.truncated
    assert "Invoice Date" in result.text and "second part" not in result.text
    assert sum(observed) == sizes[0] + 3
    complete = extraction.extract_document_text(data, "docx", max_expanded_bytes=sum(sizes))
    assert complete is not None and not complete.truncated
    assert complete.text.endswith("second part")


def test_ooxml_limit_before_first_paragraph_remains_explicitly_truncated():
    result = extraction.extract_document_text(
        _zip({"word/document.xml": _docx("invoice")}),
        "docx",
        max_expanded_bytes=10,
    )
    assert result is not None and result.truncated
    assert result.text == extraction._TRUNCATION_MARKER


def test_docx_memory_error_is_not_changed_to_unreadable(monkeypatch):
    def exhausted(*args, **kwargs):
        raise MemoryError("synthetic XML allocation failure")

    monkeypatch.setattr(extraction, "safe_xml_iterparse", exhausted)
    with pytest.raises(MemoryError):
        extraction.extract_document_text(_zip({"word/document.xml": _docx("invoice")}), "docx")


def test_csv_streams_rows_and_preserves_multiline_and_sparse_cells(monkeypatch):
    data = b'Name,Value,Note\r\nAda,,"first\r\nsecond"\r\n'
    normal = extraction.extract_document_text(data, "csv")
    assert normal is not None and normal.text == "Name\tValue\tNote\nAda\t\tfirst\r\nsecond"
    original = extraction._BudgetReader.read
    seen = []

    def observed(reader, count=-1):
        value = original(reader, count)
        seen.append(len(value))
        return value

    monkeypatch.setattr(extraction._BudgetReader, "read", observed)
    large = b"Invoice Date,Total\n2026-09-05,1234.50\n" + b"later,row\n" * 100_000
    limited = extraction.extract_document_text(large, "csv", max_chars=24)
    assert limited is not None and limited.truncated
    assert sum(seen) < len(large)


def test_plain_text_exact_byte_and_character_boundaries_and_utf8():
    text = "开票日期：2026-09-05"
    data = text.encode()
    complete = extraction.extract_document_text(
        data, "txt", max_chars=len(text), max_expanded_bytes=len(data)
    )
    assert complete is not None and complete.text == text and not complete.truncated
    cut = extraction.extract_document_text(data, "txt", max_expanded_bytes=len(data) - 1)
    assert cut is not None and cut.truncated
    assert "2026-09-05" not in cut.text
    trailing = extraction.extract_document_text(b"hello   ", "txt", max_chars=5)
    assert trailing is not None and not trailing.truncated and trailing.text == "hello"


def test_xlsx_arbitrary_column_reference_cannot_allocate_an_arbitrary_blank_list():
    reference = "Z" * 100_000 + "1"
    data = _zip(
        {
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="x"><sheetData><row r="1">'
            f'<c r="{reference}" t="inlineStr"><is><t>far cell</t></is></c>'
            "</row></sheetData></worksheet>"
        }
    )
    result = extraction.extract_document_text(data, "xlsx", max_chars=64)
    assert result is not None and result.truncated
    assert len(result.text) <= 64 + len(extraction._TRUNCATION_MARKER)


def test_pptx_slide_count_limit_preserves_numeric_order():
    data = _zip(
        {
            "ppt/slides/slide10.xml": '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Ten</a:t></p:sld>',
            "ppt/slides/slide2.xml": '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Two</a:t></p:sld>',
        }
    )
    result = extraction.extract_document_text(data, "pptx", max_pages=1)
    assert result is not None and result.truncated
    assert "Two" in result.text and "Ten" not in result.text
