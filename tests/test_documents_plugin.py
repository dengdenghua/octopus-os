"""Tests for the bundled ``documents`` plugin (独立自研 docx 处理)。

覆盖:
  1. 插件可发现、可加载(bundled)
  2. 注册 5 个技能进 SkillRegistry
  3. create_docx -> extract_text / to_markdown / docx_info 的真实本地文件流
  4. create_docx 写操作安全门:文件已存在且未传 overwrite 时拒绝
  5. 缺失文件返回干净错误
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.platform.plugins.bundled import documents as documents_module
from runtime.platform.plugins.bundled.documents import DocumentsPlugin
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub

PLUGIN_ID = "documents"
REQUIRES_DOCX = pytest.mark.skipif(
    not documents_module._DOCX_OK,
    reason="python-docx optional dependency not installed",
)


def test_bundled_documents_is_discoverable_and_loadable() -> None:
    hub = PluginHub()
    matches = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(matches) == 1
    assert matches[0]["bundled"] is True
    assert hub.load(PLUGIN_ID) is not None


def test_documents_registers_five_skills() -> None:
    plugin = DocumentsPlugin()
    registered: list[str] = []
    plugin.ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir="",
        manifest=None,
        skill_registry=MagicMock(register=lambda s, verify_tests=False: registered.append(s.name)),
    )
    plugin.register_skills()

    assert set(registered) == {
        "documents.create_docx",
        "documents.replace_text",
        "documents.extract_text",
        "documents.to_markdown",
        "documents.docx_info",
    }


@REQUIRES_DOCX
def test_replace_text_edits_paragraphs_and_tables_with_backup(tmp_path) -> None:
    plugin = DocumentsPlugin()
    out = tmp_path / "editable.docx"
    assert plugin._create_docx(path=str(out), sections=_sample_sections())["ok"]

    edited = plugin._replace_text(
        path=str(out),
        replacements=[
            {"old": "本季度整体进展顺利。", "new": "本季度已完成交付。"},
            {"old": "100", "new": "120"},
        ],
    )

    assert edited["ok"], edited
    assert edited["total_replacements"] == 2
    assert Path(edited["backup_path"]).is_file()
    extracted = plugin._extract_text(path=str(out))
    assert any(p["text"] == "本季度已完成交付。" for p in extracted["paragraphs"])
    assert extracted["tables"][0]["data"][1][1] == "120"


@REQUIRES_DOCX
def test_replace_text_preserves_unaffected_run_formatting(tmp_path) -> None:
    out = tmp_path / "formatted.docx"
    doc = documents_module.Document()
    paragraph = doc.add_paragraph()
    lead = paragraph.add_run("Revenue")
    lead.bold = True
    amount = paragraph.add_run(" 100")
    amount.italic = True
    doc.save(out)

    edited = DocumentsPlugin()._replace_text(
        path=str(out),
        replacements=[{"old": "Revenue", "new": "Income"}],
    )

    assert edited["ok"], edited
    reopened = documents_module.Document(out)
    runs = reopened.paragraphs[0].runs
    assert reopened.paragraphs[0].text == "Income 100"
    assert runs[0].text == "Income" and runs[0].bold is True
    assert runs[1].text == " 100" and runs[1].italic is True


@REQUIRES_DOCX
def test_replace_text_no_match_does_not_touch_file(tmp_path) -> None:
    plugin = DocumentsPlugin()
    out = tmp_path / "unchanged.docx"
    assert plugin._create_docx(path=str(out), sections=_sample_sections())["ok"]
    before = out.read_bytes()

    result = plugin._replace_text(path=str(out), replacements=[{"old": "不存在的文字", "new": "x"}])

    assert result["ok"] is False
    assert out.read_bytes() == before


def _sample_sections() -> list[dict]:
    return [
        {"type": "heading", "text": "季度总结", "level": 1},
        {"type": "paragraph", "text": "本季度整体进展顺利。"},
        {"type": "list", "items": ["完成 A", "完成 B"], "ordered": False},
        {
            "type": "table",
            "headers": ["指标", "数值"],
            "rows": [["收入", "100"], ["利润", "20"]],
        },
    ]


@REQUIRES_DOCX
def test_create_then_extract_roundtrip(tmp_path) -> None:
    plugin = DocumentsPlugin()
    out = tmp_path / "report.docx"
    created = plugin._create_docx(path=str(out), title="季度报告", sections=_sample_sections())
    assert created["ok"], created
    assert out.exists()
    assert created["num_headings"] == 1
    assert created["num_tables"] == 1

    extracted = plugin._extract_text(path=str(out))
    assert extracted["ok"], extracted
    assert extracted["num_paragraphs"] >= 3
    assert extracted["num_tables"] == 1
    # 标题层级保留
    levels = [p["level"] for p in extracted["paragraphs"] if p["level"]]
    assert levels == [1]
    texts = [p["text"] for p in extracted["paragraphs"]]
    assert "季度总结" in texts and "完成 A" in texts


@REQUIRES_DOCX
def test_create_docx_requires_overwrite_for_existing_file(tmp_path) -> None:
    plugin = DocumentsPlugin()
    out = tmp_path / "exists.docx"
    first = plugin._create_docx(path=str(out), sections=[{"type": "paragraph", "text": "v1"}])
    assert first["ok"]

    # 不传 overwrite -> 拒绝
    rejected = plugin._create_docx(path=str(out), sections=[{"type": "paragraph", "text": "v2"}])
    assert rejected["ok"] is False
    assert "overwrite" in rejected["error"]

    # 传 overwrite=true -> 覆盖成功
    overwritten = plugin._create_docx(
        path=str(out), sections=[{"type": "paragraph", "text": "v2"}], overwrite=True
    )
    assert overwritten["ok"]
    extracted = plugin._extract_text(path=str(out))
    assert any(p["text"] == "v2" for p in extracted["paragraphs"])


@REQUIRES_DOCX
def test_to_markdown_preserves_structure(tmp_path) -> None:
    plugin = DocumentsPlugin()
    out = tmp_path / "doc.md.docx"
    assert plugin._create_docx(path=str(out), sections=_sample_sections())["ok"]

    converted = plugin._to_markdown(path=str(out))
    assert converted["ok"], converted
    md = converted["markdown"]
    assert "# 季度总结" in md
    assert "- 完成 A" in md
    assert "| 指标" in md and "|---|---|" in md


@REQUIRES_DOCX
def test_docx_info_counts(tmp_path) -> None:
    plugin = DocumentsPlugin()
    out = tmp_path / "info.docx"
    assert plugin._create_docx(path=str(out), sections=_sample_sections())["ok"]

    info = plugin._docx_info(path=str(out))
    assert info["ok"], info
    assert info["num_tables"] == 1
    assert info["num_headings"] == 1
    assert info["size_bytes"] > 0
    assert info["tables"][0]["rows"] == 3  # 表头 + 2 行


def test_missing_file_returns_clean_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(documents_module, "_DOCX_OK", False)
    plugin = DocumentsPlugin()
    missing = tmp_path / "nope.docx"
    for operation in (plugin._extract_text, plugin._to_markdown, plugin._docx_info):
        out = operation(path=str(missing))
        assert out["ok"] is False
        assert "文件不存在" in out["error"]


def test_non_docx_path_rejected_without_optional_dependency(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(documents_module, "_DOCX_OK", False)
    plugin = DocumentsPlugin()
    txt = tmp_path / "a.txt"
    txt.write_text("hi")
    for operation in (
        plugin._create_docx,
        plugin._extract_text,
        plugin._to_markdown,
        plugin._docx_info,
    ):
        out = operation(path=str(txt))
        assert out["ok"] is False
        assert ".docx" in out["error"]


def test_create_existing_file_requires_overwrite_without_optional_dependency(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(documents_module, "_DOCX_OK", False)
    out = tmp_path / "exists.docx"
    out.write_bytes(b"not a real docx")

    rejected = DocumentsPlugin()._create_docx(path=str(out), sections=[])

    assert rejected["ok"] is False
    assert "overwrite" in rejected["error"]


@REQUIRES_DOCX
def test_create_docx_rejects_malformed_structured_blocks(tmp_path) -> None:
    plugin = DocumentsPlugin()
    malformed_section = plugin._create_docx(
        path=str(tmp_path / "bad-section.docx"),
        sections=["not an object"],
    )
    malformed_table = plugin._create_docx(
        path=str(tmp_path / "bad-table.docx"),
        sections=[{"type": "table", "rows": ["not a row"]}],
    )

    assert malformed_section["ok"] is False
    assert "sections[0]" in malformed_section["error"]
    assert malformed_table["ok"] is False
    assert "二维数组" in malformed_table["error"]


def test_python_docx_is_an_optional_runtime_capability(tmp_path, monkeypatch) -> None:
    """The core wheel remains usable when the optional document library is absent."""

    monkeypatch.setattr(documents_module, "_DOCX_OK", False)

    out = DocumentsPlugin()._create_docx(path=str(tmp_path / "report.docx"), sections=[])

    assert out["ok"] is False
    assert "python-docx" in out["error"]

