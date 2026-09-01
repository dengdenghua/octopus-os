"""Real-file round trips for the bundled Office capability plugins."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from runtime.execution.suckers.registry import SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.plugins.bundled import pdf as pdf_module
from runtime.platform.plugins.bundled import presentations as presentations_module
from runtime.platform.plugins.bundled import spreadsheets as spreadsheets_module
from runtime.platform.plugins.bundled._office_io import atomic_package_save, scoped_path_denial
from runtime.platform.plugins.bundled.pdf import PdfPlugin
from runtime.platform.plugins.bundled.presentations import PresentationsPlugin
from runtime.platform.plugins.bundled.spreadsheets import SpreadsheetsPlugin
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine


def test_atomic_office_save_never_corrupts_live_artifact(tmp_path: Path) -> None:
    target = tmp_path / "artifact.xlsx"
    target.write_bytes(b"original")

    def interrupted_save(temporary: Path) -> None:
        temporary.write_bytes(b"partial")
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        atomic_package_save(target, interrupted_save)

    assert target.read_bytes() == b"original"
    assert list(tmp_path.glob(".artifact-*.xlsx")) == []


def test_office_plugins_reject_missing_paths_cleanly() -> None:
    operations = (
        SpreadsheetsPlugin()._workbook_info,
        PresentationsPlugin()._presentation_info,
        PdfPlugin()._info,
    )
    for operation in operations:
        result = operation(path=None)
        assert result["ok"] is False
        assert "path" in result["error"]


@pytest.mark.skipif(not pdf_module._REPORTLAB_OK, reason="reportlab unavailable")
def test_pdf_table_uses_the_registered_font_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pdf_module,
        "UnicodeCIDFont",
        MagicMock(side_effect=RuntimeError("CID fonts unavailable")),
    )
    result = PdfPlugin()._create(
        path=str(tmp_path / "fallback.pdf"),
        title="Fallback",
        blocks=[
            {
                "type": "table",
                "headers": ["Metric", "Value"],
                "rows": [["Revenue", "100"]],
            }
        ],
    )

    assert result["ok"] is True, result
    assert (tmp_path / "fallback.pdf").stat().st_size > 0


def test_scope_resolution_failure_denies_instead_of_failing_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.platform.process import scope as scope_module

    session = Session(metadata={"mode": "code", "workspace_path": str(tmp_path)})
    monkeypatch.setattr(
        scope_module,
        "resolve_execution_scope",
        lambda _session: (_ for _ in ()).throw(RuntimeError("broken scope")),
    )

    with session_scope(session):
        denial = scoped_path_denial(tmp_path / "artifact.xlsx", write=True)

    assert denial is not None
    assert "cannot verify" in denial


@pytest.mark.skipif(not spreadsheets_module._OPENPYXL_OK, reason="openpyxl unavailable")
def test_office_writes_cannot_escape_the_active_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.xlsx"
    session = Session(
        thread_id="office-scope",
        metadata={"mode": "code", "workspace_path": str(workspace)},
    )

    with session_scope(session):
        denied = SpreadsheetsPlugin()._create_xlsx(
            path=str(outside),
            sheets=[{"name": "Sheet1", "rows": [["x"]]}],
        )
        allowed = SpreadsheetsPlugin()._create_xlsx(
            path=str(workspace / "inside.xlsx"),
            sheets=[{"name": "Sheet1", "rows": [["x"]]}],
        )

    assert denied["ok"] is False
    assert "escapes" in denied["error"]
    assert not outside.exists()
    assert allowed["ok"] is True


@pytest.mark.parametrize("plugin_id", ["spreadsheets", "presentations", "pdf"])
def test_office_plugins_are_bundled_and_loadable(plugin_id: str) -> None:
    hub = PluginHub()
    matches = [item for item in hub.discover() if item["id"] == plugin_id]

    assert len(matches) == 1
    assert matches[0]["bundled"] is True
    assert hub.load(plugin_id) is not None


def test_office_plugin_tools_reach_the_runtime_skill_registry() -> None:
    registry = SkillRegistry()
    hub = PluginHub(skill_registry=registry)

    for plugin_id in ("documents", "spreadsheets", "presentations", "pdf"):
        assert hub.load(plugin_id) is not None

    names = set(registry.all_names())
    assert {
        "documents.replace_text",
        "spreadsheets.update_cells",
        "presentations.replace_text",
        "pdf.create",
    } <= names


@pytest.mark.skipif(not spreadsheets_module._OPENPYXL_OK, reason="openpyxl unavailable")
def test_spreadsheet_executes_through_the_real_tool_engine(tmp_path: Path) -> None:
    registry = SkillRegistry()
    hub = PluginHub(skill_registry=registry)
    assert hub.load("spreadsheets") is not None
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["plugin://*"]),
        journal=InMemoryJournal(),
    )
    task_id = TaskId(uuid4())
    budget = Budget(task_id=task_id, limits=BudgetLimits(tokens=20_000, usd=1.0))
    path = tmp_path / "engine.xlsx"
    common = {
        "caller": "arms/code_arm",
        "task_id": task_id,
        "arm_id": ArmId("code_arm"),
        "budget": budget,
    }
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": [path.name],
        }
    )

    with session_scope(session):
        created = executor.execute_step(
            step_id=0,
            node_id="create",
            sucker_id=SkillId("spreadsheets.create_xlsx"),
            args={
                "path": str(path),
                "sheets": [{"name": "Data", "rows": [["Value"], [100]]}],
            },
            **common,
        )
        read = executor.execute_step(
            step_id=1,
            node_id="read",
            sucker_id=SkillId("spreadsheets.read_sheet"),
            args={"path": str(path), "sheet": "Data", "range": "A1:A2"},
            **common,
        )
        updated = executor.execute_step(
            step_id=2,
            node_id="update",
            sucker_id=SkillId("spreadsheets.update_cells"),
            args={
                "path": str(path),
                "sheet": "Data",
                "updates": [{"cell": "A2", "value": 120}],
            },
            **common,
        )

    assert created.success, created.result
    assert read.success, read.result
    assert updated.success, updated.result
    assert SpreadsheetsPlugin()._read_sheet(
        path=str(path), sheet="Data", range="A1:A2"
    )["rows"] == [["Value"], [120]]


@pytest.mark.parametrize(
    ("plugin", "expected"),
    [
        (
            SpreadsheetsPlugin(),
            {
                "spreadsheets.create_xlsx",
                "spreadsheets.read_sheet",
                "spreadsheets.update_cells",
                "spreadsheets.workbook_info",
            },
        ),
        (
            PresentationsPlugin(),
            {
                "presentations.create_pptx",
                "presentations.extract_text",
                "presentations.replace_text",
                "presentations.presentation_info",
            },
        ),
        (
            PdfPlugin(),
            {"pdf.create", "pdf.extract_text", "pdf.merge", "pdf.split", "pdf.info"},
        ),
    ],
)
def test_office_plugins_register_expected_skills(plugin, expected: set[str]) -> None:
    registered: list[str] = []
    plugin.ctx = ModuleContext(
        plugin_name=plugin.name,
        plugin_dir="",
        manifest=None,
        skill_registry=MagicMock(
            register=lambda skill, verify_tests=False: registered.append(skill.name)
        ),
    )

    plugin.register_skills()

    assert set(registered) == expected


@pytest.mark.skipif(not spreadsheets_module._OPENPYXL_OK, reason="openpyxl unavailable")
def test_spreadsheet_create_read_update_roundtrip(tmp_path: Path) -> None:
    plugin = SpreadsheetsPlugin()
    path = tmp_path / "model.xlsx"

    created = plugin._create_xlsx(
        path=str(path),
        sheets=[
            {
                "name": "Model",
                "rows": [["Item", "Amount"], ["Revenue", 100], ["Profit", "=B2*0.2"]],
                "freeze_panes": "A2",
                "auto_filter": True,
                "column_widths": {"A": 20, "B": 14},
            }
        ],
    )
    assert created["ok"], created

    before = plugin._read_sheet(path=str(path), sheet="Model", range="A1:B3")
    assert before["rows"][1] == ["Revenue", 100]
    assert before["rows"][2] == ["Profit", "=B2*0.2"]

    updated = plugin._update_cells(
        path=str(path),
        sheet="Model",
        updates=[
            {"cell": "B2", "value": 120},
            {"cell": "B3", "formula": "B2*0.25", "number_format": "0.00"},
        ],
    )
    assert updated["ok"], updated
    assert Path(updated["backup_path"]).is_file()
    after = plugin._read_sheet(path=str(path), sheet="Model", range="A1:B3")
    assert after["rows"][1][1] == 120
    assert after["rows"][2][1] == "=B2*0.25"
    info = plugin._workbook_info(path=str(path))
    assert info["sheets"][0]["formula_count"] == 1

    updated_again = plugin._update_cells(
        path=str(path),
        sheet="Model",
        updates=[{"cell": "B2", "value": 125}],
    )
    assert updated_again["ok"], updated_again
    assert updated_again["backup_path"] != updated["backup_path"]
    assert Path(updated_again["backup_path"]).is_file()

    workbook = spreadsheets_module.load_workbook(path)
    workbook["Model"].merge_cells("C1:D1")
    workbook.save(path)
    merged_cell = plugin._update_cells(
        path=str(path),
        sheet="Model",
        updates=[{"cell": "D1", "value": "invalid"}],
    )
    assert merged_cell["ok"] is False
    assert "左上角单元格" in merged_cell["error"]


@pytest.mark.skipif(not presentations_module._PPTX_OK, reason="python-pptx unavailable")
def test_presentation_create_extract_replace_roundtrip(tmp_path: Path) -> None:
    plugin = PresentationsPlugin()
    path = tmp_path / "deck.pptx"

    created = plugin._create_pptx(
        path=str(path),
        slides=[
            {"kind": "title", "title": "Echo Age", "subtitle": "Product story"},
            {"title": "Current risks", "bullets": ["Supply", "Schedule"]},
        ],
    )
    assert created["ok"], created
    assert created["slides"] == 2
    extracted = plugin._extract_text(path=str(path))
    assert any("Current risks" in block["text"] for block in extracted["slides"][1]["blocks"])

    edited = plugin._replace_text(
        path=str(path),
        slide=2,
        replacements=[{"old": "Current risks", "new": "Risk matrix"}],
    )
    assert edited["ok"], edited
    assert Path(edited["backup_path"]).is_file()
    extracted_after = plugin._extract_text(path=str(path))
    assert any("Risk matrix" in block["text"] for block in extracted_after["slides"][1]["blocks"])
    assert plugin._presentation_info(path=str(path))["slide_count"] == 2


@pytest.mark.skipif(not presentations_module._PPTX_OK, reason="python-pptx unavailable")
def test_presentation_replace_preserves_unaffected_run_formatting(tmp_path: Path) -> None:
    path = tmp_path / "formatted.pptx"
    deck = presentations_module.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    textbox = slide.shapes.add_textbox(
        presentations_module.Inches(1),
        presentations_module.Inches(1),
        presentations_module.Inches(8),
        presentations_module.Inches(1),
    )
    paragraph = textbox.text_frame.paragraphs[0]
    lead = paragraph.add_run()
    lead.text = "Revenue"
    lead.font.bold = True
    amount = paragraph.add_run()
    amount.text = " 100"
    amount.font.italic = True
    table = slide.shapes.add_table(
        2,
        2,
        presentations_module.Inches(1),
        presentations_module.Inches(3),
        presentations_module.Inches(6),
        presentations_module.Inches(2),
    ).table
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Revenue"
    table.cell(1, 1).text = "100"
    deck.save(path)

    extracted = PresentationsPlugin()._extract_text(path=str(path))
    table_block = next(
        block for block in extracted["slides"][0]["blocks"] if block["type"] == "table"
    )
    assert table_block["rows"] == [["Metric", "Value"], ["Revenue", "100"]]

    edited = PresentationsPlugin()._replace_text(
        path=str(path),
        replacements=[{"old": "Revenue", "new": "Income"}],
    )

    assert edited["ok"], edited
    reopened = presentations_module.Presentation(path)
    runs = reopened.slides[0].shapes[0].text_frame.paragraphs[0].runs
    assert "".join(run.text for run in runs) == "Income 100"
    assert runs[0].text == "Income" and runs[0].font.bold is True
    assert runs[1].text == " 100" and runs[1].font.italic is True


@pytest.mark.skipif(
    not (pdf_module._PYPDF_OK and pdf_module._REPORTLAB_OK),
    reason="PDF dependencies unavailable",
)
def test_pdf_create_extract_merge_split_roundtrip(tmp_path: Path) -> None:
    plugin = PdfPlugin()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    merged = tmp_path / "merged.pdf"

    assert plugin._create(
        path=str(first),
        title="Quarterly Report",
        blocks=[{"type": "paragraph", "text": "First page evidence R&D <plan>"}],
    )["ok"]
    assert plugin._create(
        path=str(second),
        blocks=[{"type": "paragraph", "text": "Second page evidence"}],
    )["ok"]

    merged_result = plugin._merge(paths=[str(first), str(second)], output_path=str(merged))
    assert merged_result["ok"], merged_result
    assert merged_result["pages"] == 2
    extracted = plugin._extract_text(path=str(merged), pages="1-2")
    assert "First page evidence" in extracted["pages"][0]["text"]
    assert "R&D <plan>" in extracted["pages"][0]["text"]
    assert "Second page evidence" in extracted["pages"][1]["text"]

    split = plugin._split(path=str(merged), output_dir=str(tmp_path / "pages"))
    assert split["ok"], split
    assert len(split["files"]) == 2
    assert all(Path(path).is_file() for path in split["files"])
    assert plugin._info(path=str(merged))["pages"] == 2

    missing_output = plugin._split(path=str(merged), output_dir=None)
    assert missing_output["ok"] is False
    assert "output_dir" in missing_output["error"]


@pytest.mark.skipif(not pdf_module._PYPDF_OK, reason="pypdf unavailable")
def test_encrypted_pdf_returns_a_clean_capability_error(tmp_path: Path) -> None:
    path = tmp_path / "encrypted.pdf"
    writer = pdf_module.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    with path.open("wb") as stream:
        writer.write(stream)

    extracted = PdfPlugin()._extract_text(path=str(path))
    info = PdfPlugin()._info(path=str(path))

    assert extracted["ok"] is False
    assert "已加密" in extracted["error"]
    assert info["ok"] is True
    assert info["encrypted"] is True
    assert info["pages"] is None

