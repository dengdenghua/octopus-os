from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from runtime.execution.misc.office_preview import render_office_preview


def _archive(entries: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_docx_preview_escapes_document_content(tmp_path: Path) -> None:
    path = tmp_path / "report.docx"
    path.write_bytes(
        _archive(
            {
                "word/document.xml": (
                    '<w:document xmlns:w="urn:w"><w:body><w:p><w:r>'
                    "<w:t>&lt;script&gt;alert(1)&lt;/script&gt;</w:t>"
                    "</w:r></w:p></w:body></w:document>"
                )
            }
        )
    )

    preview = render_office_preview(path, script_nonce="preview-nonce")

    assert preview is not None
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in preview
    assert "<script>alert(1)</script>" not in preview
    assert 'class="document-page"' in preview
    assert 'data-office-node="paragraph:1"' in preview
    assert '<script nonce="preview-nonce">' in preview
    assert "script-src &#x27;nonce-preview-nonce&#x27;" in preview
    assert "echo:office:select" in preview
    assert ".join(' · ')" in preview
    assert "querySelectorAll('h1,h2,h3,p,li')" in preview


def test_xlsx_preview_renders_cells_as_a_grid(tmp_path: Path) -> None:
    path = tmp_path / "model.xlsx"
    path.write_bytes(
        _archive(
            {
                "xl/workbook.xml": (
                    '<workbook xmlns="urn:x" xmlns:r="urn:r"><sheets>'
                    '<sheet name="Revenue 2026" sheetId="1" r:id="rId1"/>'
                    '<sheet name="Costs" sheetId="2" r:id="rId2"/>'
                    "</sheets></workbook>"
                ),
                "xl/_rels/workbook.xml.rels": (
                    '<Relationships xmlns="urn:rels"><Relationship Id="rId1" '
                    'Target="worksheets/sheet1.xml"/><Relationship Id="rId2" '
                    'Target="worksheets/sheet2.xml"/></Relationships>'
                ),
                "xl/sharedStrings.xml": ('<sst xmlns="urn:x"><si><t>Revenue</t></si></sst>'),
                "xl/worksheets/sheet1.xml": (
                    '<worksheet xmlns="urn:x"><sheetData><row>'
                    '<c r="A1" t="s"><v>0</v></c><c r="C1"><v>42</v></c>'
                    "</row></sheetData></worksheet>"
                ),
                "xl/worksheets/sheet2.xml": (
                    '<worksheet xmlns="urn:x"><sheetData><row>'
                    '<c r="A10"><v>7</v></c>'
                    "</row></sheetData></worksheet>"
                ),
            }
        )
    )

    preview = render_office_preview(path)

    assert preview is not None
    assert "<table>" in preview
    assert "Revenue" in preview
    assert ">42</td>" in preview
    assert "Revenue 2026" in preview
    assert 'data-office-node="sheet:1:cell:B1"' in preview
    assert 'data-office-node="sheet:1:cell:C1"' in preview
    assert 'data-office-label="Revenue 2026 · C1" tabindex="0">42</td>' in preview
    assert 'data-office-node="sheet:2:cell:A10"' in preview
    assert 'data-office-label="Costs · A10" tabindex="0">7</td>' in preview


def test_pptx_preview_renders_separate_slide_cards(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    path.write_bytes(
        _archive(
            {
                "ppt/slides/slide1.xml": (
                    '<p:sld xmlns:p="urn:p" xmlns:a="urn:a">'
                    "<a:t>Opening</a:t><a:t>Evidence</a:t></p:sld>"
                ),
                "ppt/slides/slide2.xml": (
                    '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Conclusion</a:t></p:sld>'
                ),
            }
        )
    )

    preview = render_office_preview(path)

    assert preview is not None
    assert preview.count('class="slide"') == 2
    assert "Opening" in preview
    assert "Conclusion" in preview
    assert 'data-office-node="slide:2"' in preview


def test_csv_preview_is_a_selectable_spreadsheet(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    path.write_text('name,value\n"Revenue, net",42\n', encoding="utf-8")

    preview = render_office_preview(path, script_nonce="preview-nonce")

    assert preview is not None
    assert '<section class="sheet">' in preview
    assert "Revenue, net" in preview
    assert 'data-office-node="sheet:1:cell:B2"' in preview
    assert 'data-office-label="metrics.csv · B2" tabindex="0">42</td>' in preview
    assert "echo:office:select" in preview

