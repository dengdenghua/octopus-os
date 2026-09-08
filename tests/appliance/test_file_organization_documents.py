"""Real document bytes through extraction, persisted plans, moves and undo.

PDF is a core dependency: missing pypdf must fail this suite, not silently skip
the principal invoice format. All fixtures are generated here without optional
office libraries, OCR, network access or files outside pytest's temporary root.
These call the provider directly; HTTP approval is covered by the router suite.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from appliance.agent_api.documents import extract_invoice_document
from appliance.files.manager import FileManager
from appliance.files.organization import FileOrganizationService, OrganizationError
from runtime.execution.misc.document_text_extractor import extract_document_text
from runtime.platform.process.task_supervisor import TaskSupervisor

ACTOR = "local:document-test"
DATE = "2026-09-05"
PDF_INVOICE = ("INVOICE", f"Invoice Date: {DATE}", "Total: USD 1234.50")
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _pdf(
    pages: tuple[tuple[str, ...], ...] = (PDF_INVOICE,),
    *,
    image_only: bool = False,
    password: str | None = None,
) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        escaped = [
            line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines
        ]
        commands = " 0 -16 Td ".join(f"({line}) Tj" for line in escaped)
        stream.set_data(f"BT /F1 12 Tf 40 740 Td {commands} ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if image_only:
        # A real raster XObject painted onto a PDF page, with no text operators.
        # Its invoice-like filename/metadata cannot stand in for readable text.
        page = writer.pages[0]
        raster = DecodedStreamObject()
        raster.set_data(
            bytes(0 if (x // 4 + y // 3) % 2 else 255 for y in range(24) for x in range(64))
        )
        raster.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(64),
                NameObject("/Height"): NumberObject(24),
                NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/XObject"): DictionaryObject(
                    {NameObject("/Scan"): writer._add_object(raster)}
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(b"q 512 0 0 192 40 500 cm /Scan Do Q")
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_metadata({"/Title": "Invoice Date: 2026-09-05"})
    if password is not None:
        # The pure-Python algorithm needs no optional crypto package.
        writer.encrypt(password, algorithm="RC4-128")
    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def _office_archive(
    main: str,
    content_type: str,
    parts: dict[str, str],
    *,
    extra_types: dict[str, str] | None = None,
) -> bytes:
    output = BytesIO()
    types = {main: content_type, **(extra_types or {})}
    overrides = "".join(
        f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in types.items()
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{overrides}</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" '
            f'Type="{_OFFICE_REL}/officeDocument" Target="{main}"/></Relationships>',
        )
        for name, text in parts.items():
            archive.writestr(name, text.encode("utf-8"))
    return output.getvalue()


def _paragraph(*runs: str) -> str:
    return "<w:p>" + "".join(f"<w:r><w:t>{escape(run)}</w:t></w:r>" for run in runs) + "</w:p>"


def _docx() -> bytes:
    rows = "".join(
        f"<w:tr><w:tc>{label}</w:tc><w:tc>{_paragraph(value)}</w:tc></w:tr>"
        for label, value in (
            (_paragraph("开票", "日期"), "2026年09月05日"),
            (_paragraph("价税合计（小写）"), "CNY 1,234.50"),
        )
    )
    return _office_archive(
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        {
            "word/document.xml": f'<w:document xmlns:w="{_WORD}"><w:body>'
            f"{_paragraph('电子发票（普通发票）')}<w:tbl>{rows}</w:tbl>"
            f"{_paragraph('付款日期：2026年10月05日')}</w:body></w:document>"
        },
    )


def _xlsx() -> bytes:
    def cell(reference: str, text: str) -> str:
        return f'<c r="{reference}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'

    return _office_archive(
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        {
            "xl/workbook.xml": f'<workbook xmlns="{_SHEET}" xmlns:r="{_OFFICE_REL}">'
            '<sheets><sheet name="Invoice" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{_REL}">'
            f'<Relationship Id="rId1" Type="{_OFFICE_REL}/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{_SHEET}"><sheetData>'
            f'<row r="1">{cell("A1", "Invoice Date")}{cell("B1", "Total")}</row>'
            '<row r="2"><c r="A2"><v>46270</v></c>'
            '<c r="B2"><f>SUM(1000,234.50)</f><v>42</v></c></row>'
            "</sheetData></worksheet>",
        },
        extra_types={
            "xl/worksheets/sheet1.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
        },
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): _sha(path.read_bytes())
        for path in folder.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "runtime-data"))
    root = tmp_path / "nas"
    folder = root / "Invoices"
    folder.mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    supervisor = TaskSupervisor.from_path(state / "tasks.json", holder_id="document-fixtures")
    return FileOrganizationService(FileManager(root), state, supervisor=supervisor), folder, state


@pytest.mark.parametrize("format", ["pdf", "docx"])
def test_real_invoice_document_extract_plan_move_reopen_undo_preserves_bytes(library, format):
    service, folder, state = library
    data = _pdf() if format == "pdf" else _docx()
    source = folder / f"invoice-1999-01-02.{format}"
    source.write_bytes(data)
    before = _files(folder), source.stat().st_mtime_ns
    extracted = extract_document_text(data, format)
    assert extracted is not None and not extracted.truncated
    if format == "docx":
        assert "开票日期\n2026年09月05日" in extracted.text
    else:
        assert f"Invoice Date: {DATE}" in extracted.text
    isolated = extract_invoice_document(data, format)
    assert {key: isolated[key] for key in ("text", "truncated", "available")} == {
        "text": extracted.text,
        "truncated": False,
        "available": True,
    }
    assert isolated["outcome"] == "ok"
    assert isolated["limits"]["ready"] is True
    assert isolated["limits"]["worker_pid"] != os.getpid()
    plan = service.create_plan(ACTOR, "Invoices")
    assert (_files(folder), source.stat().st_mtime_ns) == before
    assert plan["ready"] is True and plan["scanComplete"] is True
    (item,) = plan["entries"]
    assert item["date"] == DATE and item["amount"] == "1234.50"
    assert item["currency"] == ("USD" if format == "pdf" else "CNY")
    assert item["evidence"]["filenameDateHints"] == ["1999-01-02"]
    target = folder / "2026/09" / source.name
    assert item["target"] == f"Invoices/2026/09/{source.name}"
    snapshot = service.store.load(plan["planId"])["snapshots"][item["entryId"]]
    assert snapshot["sha256"] == _sha(data)
    result = service.apply(ACTOR, plan["planId"])
    assert result["state"] == "completed" and result["counts"]["moved"] == 1
    assert result["results"][0]["sha256"] == _sha(data)
    assert target.read_bytes() == data and not source.exists()
    reopened = FileOrganizationService(service.manager, state, supervisor=service.supervisor)
    assert reopened.get_result(ACTOR, plan["planId"])["executionComplete"] is True
    undo = reopened.create_undo_plan(ACTOR, plan["planId"])
    assert undo["ready"] is True and undo["direction"] == "undo"
    restored = reopened.apply(ACTOR, undo["planId"])
    assert restored["state"] == "completed" and restored["counts"]["moved"] == 1
    assert source.read_bytes() == data and not target.exists()
    assert _files(folder) == before[0]


def _uncertain_pdf(kind: str) -> bytes:
    if kind == "scan":
        return _pdf(image_only=True)
    if kind == "empty":
        return _pdf(((),))
    if kind == "malformed":
        return b"%PDF-1.7\nsynthetic malformed body\n%%EOF"
    if kind == "encrypted":
        return _pdf(password="synthetic-password-no-automatic-decryption")
    if kind == "conflicting":
        return _pdf((PDF_INVOICE, ("INVOICE", "Invoice Date: 2026-08-31")))
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["scan", "empty", "malformed", "encrypted", "conflicting"])
def test_uncertain_real_pdfs_have_no_executable_target_and_originals_stay(library, kind):
    service, folder, _ = library
    data = _uncertain_pdf(kind)
    source = folder / f"invoice-{DATE}-{kind}.pdf"
    source.write_bytes(data)
    before = _files(folder), source.stat().st_mtime_ns
    if kind == "scan":
        reader = PdfReader(BytesIO(data))
        assert reader.pages[0]["/Resources"]["/XObject"]["/Scan"]["/Subtype"] == "/Image"
        assert reader.pages[0].extract_text() == ""
    if kind == "encrypted":
        assert PdfReader(BytesIO(data)).is_encrypted is True
    extracted = extract_invoice_document(data, "pdf")
    assert extracted["available"] is True and extracted["truncated"] is False
    if kind != "conflicting":
        assert extracted["text"] is None
    plan = service.create_plan(ACTOR, "Invoices")
    assert plan["scanComplete"] is True and plan["ready"] is False
    (item,) = plan["entries"]
    assert item["status"] == "needs_review"
    assert item["reason"] == ("conflicting_invoice_dates" if kind == "conflicting" else "no_text")
    assert item["target"] is None and item["date"] is None
    with pytest.raises(OrganizationError) as rejected:
        service.apply(ACTOR, plan["planId"])
    assert rejected.value.error == "plan_not_ready"
    assert (_files(folder), source.stat().st_mtime_ns) == before
    assert not (folder / "2026").exists()


def test_real_xlsx_can_extract_raw_cells_but_is_not_automatically_organized(library):
    service, folder, _ = library
    data = _xlsx()
    source = folder / f"invoice-{DATE}.xlsx"
    source.write_bytes(data)
    extracted = extract_document_text(data, "xlsx")
    assert extracted is not None
    assert "Invoice Date\tTotal" in extracted.text and "46270\t42" in extracted.text
    # Raw date serials and cached formula values are not interpreted as invoices.
    assert "1234.50" not in extracted.text
    plan = service.create_plan(ACTOR, "Invoices")
    (item,) = plan["entries"]
    assert item["status"] == "unsupported" and item["reason"] == "unsupported_format"
    assert item["target"] is None and item["date"] is None
    assert plan["ready"] is False
    with pytest.raises(OrganizationError) as rejected:
        service.apply(ACTOR, plan["planId"])
    assert rejected.value.error == "plan_not_ready"
    assert _files(folder) == {source.name: _sha(data)}


def test_pdf_text_truncation_never_allows_a_partial_date_decision(library):
    service, folder, _ = library
    data = _pdf((PDF_INVOICE, tuple("x" * 800 for _ in range(82)), ("Invoice Date: 2026-08-31",)))
    source = folder / "long.pdf"
    source.write_bytes(data)
    extracted = extract_invoice_document(data, "pdf")
    assert extracted["available"] is True and extracted["truncated"] is True
    assert "2026-08-31" not in extracted["text"]
    plan = service.create_plan(ACTOR, "Invoices")
    assert plan["ready"] is False
    assert plan["entries"][0]["reason"] == "text_truncated"
    with pytest.raises(OrganizationError):
        service.apply(ACTOR, plan["planId"])
    assert _files(folder) == {"long.pdf": _sha(data)}


def test_mixed_document_batch_moves_only_confirmed_files_and_undo_restores_original_hashes(library):
    service, folder, _ = library
    data = {
        "invoice.pdf": _pdf(),
        "invoice.docx": _docx(),
        "scanned.pdf": _uncertain_pdf("scan"),
        "empty.pdf": _uncertain_pdf("empty"),
        "broken.pdf": _uncertain_pdf("malformed"),
        "encrypted.pdf": _uncertain_pdf("encrypted"),
        "conflicting.pdf": _uncertain_pdf("conflicting"),
        "invoice.xlsx": _xlsx(),
    }
    for name, content in data.items():
        (folder / name).write_bytes(content)
    before = _files(folder)
    plan = service.create_plan(ACTOR, "Invoices")
    assert plan["ready"] is True and plan["scanComplete"] is True
    assert sum(row["status"] == "ready" for row in plan["entries"]) == 2
    assert _files(folder) == before
    result = service.apply(ACTOR, plan["planId"])
    assert result["counts"]["moved"] == 2
    assert result["counts"]["skipped"] == 6
    for name, content in data.items():
        expected = folder / ("2026/09" if name in {"invoice.pdf", "invoice.docx"} else "") / name
        assert expected.read_bytes() == content
    undo = service.create_undo_plan(ACTOR, plan["planId"])
    assert sum(row["status"] == "ready" for row in undo["entries"]) == 2
    restored = service.apply(ACTOR, undo["planId"])
    assert restored["state"] == "completed" and restored["counts"]["moved"] == 2
    assert _files(folder) == before
