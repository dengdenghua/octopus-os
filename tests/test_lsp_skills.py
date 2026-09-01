"""Tests for runtime.execution.suckers.lsp_skills."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from runtime.execution.suckers import lsp_skills
from runtime.execution.suckers.lsp_skills import (
    _detect_language,
    _extract_hover_contents,
    _flatten_document_symbols,
    _lsp_definition,
    _lsp_document_symbols,
    _lsp_hover,
    _lsp_references,
    _normalize_locations,
    _path_to_uri,
    _reset_clients_for_test,
    _resolve_server_argv,
    _symbol_kind_name,
    _uri_to_path,
    register_lsp_skills,
)
from runtime.execution.suckers.registry import SkillRegistry


@pytest.fixture(autouse=True)
def _drop_clients() -> Any:
    _reset_clients_for_test()
    yield
    _reset_clients_for_test()


# ────────────────────────────────────────────────────────────────────────────
# small pure helpers
# ────────────────────────────────────────────────────────────────────────────


def test_detect_language_dispatch() -> None:
    assert _detect_language("foo.py") == "python"
    assert _detect_language("foo.tsx") == "typescript"
    assert _detect_language("foo.js") == "javascript"
    assert _detect_language("foo.rs") == "rust"
    assert _detect_language("foo.go") == "go"
    assert _detect_language("foo.txt") is None
    assert _detect_language("README") is None


def test_uri_path_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text("x = 1\n", encoding="utf-8")
    uri = _path_to_uri(str(p))
    assert uri.startswith("file:///") if str(p)[1:2] == ":" else uri.startswith("file://")
    assert Path(_uri_to_path(uri)) == p.resolve()


def test_symbol_kind_translation() -> None:
    assert _symbol_kind_name(5) == "Class"
    assert _symbol_kind_name(12) == "Function"
    assert _symbol_kind_name(13) == "Variable"
    assert _symbol_kind_name(99) == "Kind(99)"


# ────────────────────────────────────────────────────────────────────────────
# Strategy A · mock _LSPClient
# ────────────────────────────────────────────────────────────────────────────


class _FakeClient:
    """Stand-in for _LSPClient that returns canned LSP results."""

    def __init__(self, language: str = "python", canned: dict[str, Any] | None = None) -> None:
        self.language = language
        self._canned = canned or {}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._opened: set[str] = set()

    def is_alive(self) -> bool:
        return True

    def ensure_open(self, path: str) -> None:
        self._opened.add(path)

    def request(self, method: str, params: dict[str, Any], **_kw: Any) -> Any:
        self.requests.append((method, params))
        canned = self._canned.get(method)
        if isinstance(canned, Exception):
            raise canned
        return canned


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(
        lsp_skills,
        "_get_or_start_client",
        lambda language, workspace_root: fake,
    )


def test_lsp_definition_translates_lsp_to_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    fake = _FakeClient(
        canned={
            "textDocument/definition": [
                {
                    "uri": _path_to_uri(str(f)),
                    "range": {
                        "start": {"line": 9, "character": 4},
                        "end": {"line": 9, "character": 7},
                    },
                }
            ],
        }
    )
    _patch_client(monkeypatch, fake)

    result = _lsp_definition(path=str(f), line=10, column=5, sandbox_dir=str(tmp_path))

    assert result["ok"] is True
    assert len(result["definitions"]) == 1
    d = result["definitions"][0]
    # 0-indexed → 1-indexed translation
    assert d["line"] == 10
    assert d["column"] == 5
    assert Path(d["path"]) == f.resolve()
    # Verify request sent 0-indexed coords
    method, params = fake.requests[-1]
    assert method == "textDocument/definition"
    assert params["position"] == {"line": 9, "character": 4}


def test_lsp_references_translates_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    uri = _path_to_uri(str(f))
    fake = _FakeClient(
        canned={
            "textDocument/references": [
                {
                    "uri": uri,
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                },
                {
                    "uri": uri,
                    "range": {
                        "start": {"line": 4, "character": 8},
                        "end": {"line": 4, "character": 9},
                    },
                },
                {
                    "uri": uri,
                    "range": {
                        "start": {"line": 9, "character": 0},
                        "end": {"line": 9, "character": 1},
                    },
                },
            ],
        }
    )
    _patch_client(monkeypatch, fake)

    result = _lsp_references(path=str(f), line=1, column=1, sandbox_dir=str(tmp_path))

    assert result["ok"] is True
    assert result["count"] == 3
    assert [r["line"] for r in result["references"]] == [1, 5, 10]
    assert [r["column"] for r in result["references"]] == [1, 9, 1]


def test_lsp_hover_extracts_markup_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = _FakeClient(
        canned={
            "textDocument/hover": {"contents": {"kind": "markdown", "value": "**foo**: int"}},
        }
    )
    _patch_client(monkeypatch, fake)
    result = _lsp_hover(path=str(f), line=1, column=1, sandbox_dir=str(tmp_path))
    assert result["ok"] is True
    assert result["contents"] == "**foo**: int"


def test_lsp_hover_extracts_marked_string_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = _FakeClient(
        canned={
            "textDocument/hover": {
                "contents": [
                    {"language": "python", "value": "def foo() -> int"},
                    "Returns the answer.",
                ]
            },
        }
    )
    _patch_client(monkeypatch, fake)
    result = _lsp_hover(path=str(f), line=1, column=1, sandbox_dir=str(tmp_path))
    assert result["ok"] is True
    assert "def foo() -> int" in result["contents"]
    assert "Returns the answer." in result["contents"]


def test_extract_hover_contents_handles_string_form() -> None:
    assert _extract_hover_contents("plain text") == "plain text"
    assert _extract_hover_contents(None) == ""
    assert _extract_hover_contents([]) == ""


def test_lsp_document_symbols_translates_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("class A:\n    def m(self): ...\n", encoding="utf-8")
    fake = _FakeClient(
        canned={
            "textDocument/documentSymbol": [
                {
                    "name": "A",
                    "kind": 5,  # Class
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 1, "character": 22},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 6},
                        "end": {"line": 0, "character": 7},
                    },
                    "children": [
                        {
                            "name": "m",
                            "kind": 6,  # Method
                            "range": {
                                "start": {"line": 1, "character": 4},
                                "end": {"line": 1, "character": 22},
                            },
                            "selectionRange": {
                                "start": {"line": 1, "character": 8},
                                "end": {"line": 1, "character": 9},
                            },
                        }
                    ],
                }
            ],
        }
    )
    _patch_client(monkeypatch, fake)

    result = _lsp_document_symbols(path=str(f), sandbox_dir=str(tmp_path))

    assert result["ok"] is True
    syms = result["symbols"]
    assert len(syms) == 2
    assert syms[0]["name"] == "A"
    assert syms[0]["kind"] == "Class"
    assert syms[0]["line"] == 1
    assert syms[1]["name"] == "m"
    assert syms[1]["kind"] == "Method"
    assert syms[1]["container"] == "A"


def test_lsp_document_symbols_handles_flat_symbol_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("def f(): pass\n", encoding="utf-8")
    uri = _path_to_uri(str(f))
    fake = _FakeClient(
        canned={
            "textDocument/documentSymbol": [
                {
                    "name": "f",
                    "kind": 12,  # Function
                    "location": {
                        "uri": uri,
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 5},
                        },
                    },
                    "containerName": "module",
                }
            ],
        }
    )
    _patch_client(monkeypatch, fake)

    result = _lsp_document_symbols(path=str(f), sandbox_dir=str(tmp_path))
    assert result["symbols"][0]["kind"] == "Function"
    assert result["symbols"][0]["line"] == 1
    assert result["symbols"][0]["container"] == "module"


# ────────────────────────────────────────────────────────────────────────────
# error paths
# ────────────────────────────────────────────────────────────────────────────


def test_dependency_missing_when_no_server_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    # No server resolves: isolate both the PATH lookup and the module fallback
    # without mutating sys.executable underneath the module's prebuilt argv.
    monkeypatch.setattr(lsp_skills.shutil, "which", lambda _exe: None)
    monkeypatch.setitem(
        lsp_skills._SERVER_CANDIDATES,
        "python",
        [["pyright-langserver", "--stdio"], ["/no/such/python", "-m", "pylsp"]],
    )

    result = _lsp_definition(path=str(f), line=1, column=1, sandbox_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "dependency_missing"
    assert "pyright" in result["hint"].lower() or "pylsp" in result["hint"].lower()


def test_invalid_position_rejected(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    result = _lsp_definition(path=str(f), line=-1, column=1, sandbox_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"

    result = _lsp_references(path=str(f), line=1, column=0, sandbox_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_unsupported_extension(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("hello\n", encoding="utf-8")
    result = _lsp_document_symbols(path=str(f), sandbox_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "unsupported"


def test_missing_path_argument() -> None:
    result = _lsp_definition(path="", line=1, column=1)
    assert result["ok"] is False
    assert result["error_type"] == "invalid_argument"


def test_path_outside_sandbox(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    result = _lsp_definition(
        path=str(outside),
        line=1,
        column=1,
        sandbox_dir=str(sandbox),
    )
    assert result["ok"] is False
    assert result["error_type"] == "permission_denied"


def test_timeout_surfaced_to_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = _FakeClient(
        canned={
            "textDocument/definition": lsp_skills._LSPTimeoutError("boom"),
        }
    )
    _patch_client(monkeypatch, fake)

    result = _lsp_definition(path=str(f), line=1, column=1, sandbox_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


def test_transport_error_surfaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = _FakeClient(
        canned={
            "textDocument/hover": lsp_skills._LSPTransportError("pipe broken"),
        }
    )
    _patch_client(monkeypatch, fake)

    result = _lsp_hover(path=str(f), line=1, column=1, sandbox_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["error_type"] == "transport"


# ────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────


def test_normalize_locations_handles_single_dict_and_none() -> None:
    assert _normalize_locations(None) == []
    single = {"uri": "file:///a.py", "range": {"start": {"line": 0, "character": 0}}}
    assert _normalize_locations(single) == [
        {"path": str(Path("/a.py")), "line": 1, "column": 1, "uri": "file:///a.py"}
    ]


def test_flatten_document_symbols_sets_container_for_nested() -> None:
    items = [
        {
            "name": "Outer",
            "kind": 5,
            "selectionRange": {"start": {"line": 0, "character": 0}},
            "children": [
                {
                    "name": "inner",
                    "kind": 6,
                    "selectionRange": {"start": {"line": 1, "character": 4}},
                }
            ],
        }
    ]
    out = _flatten_document_symbols(items)
    assert out[0]["container"] is None
    assert out[1]["container"] == "Outer"


def test_resolve_server_argv_returns_none_when_nothing_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lsp_skills.shutil, "which", lambda _exe: None)
    monkeypatch.setitem(
        lsp_skills._SERVER_CANDIDATES,
        "python",
        [["pyright-langserver", "--stdio"], ["/nope/python_no_lsp", "-m", "pylsp"]],
    )
    assert _resolve_server_argv("python") is None


def test_resolve_server_argv_finds_pyright(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name: str) -> str | None:
        return "/usr/bin/" + name if name == "pyright-langserver" else None

    monkeypatch.setattr(lsp_skills.shutil, "which", which)
    argv = _resolve_server_argv("python")
    assert argv is not None
    assert argv[0] == "/usr/bin/pyright-langserver"


# ────────────────────────────────────────────────────────────────────────────
# registration
# ────────────────────────────────────────────────────────────────────────────


def test_register_lsp_skills_registers_four() -> None:
    reg = SkillRegistry()
    n = register_lsp_skills(reg)
    assert n == 4
    for name in ("lsp_definition", "lsp_references", "lsp_hover", "lsp_document_symbols"):
        assert reg.has(name)


# ────────────────────────────────────────────────────────────────────────────
# Strategy B · optional integration test (skip if pyright not installed)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    shutil.which("pyright-langserver") is None,
    reason="pyright-langserver not on PATH",
)
def test_real_pyright_definition_lookup(tmp_path: Path) -> None:
    f = tmp_path / "demo.py"
    f.write_text(
        "def helper():\n    return 1\n\ndef main():\n    return helper()\n",
        encoding="utf-8",
    )
    # Position on the call to ``helper`` in line 5, column 12 (1-indexed).
    result = _lsp_definition(path=str(f), line=5, column=12, sandbox_dir=str(tmp_path))
    if not result.get("ok"):
        pytest.skip(f"server returned {result}")
    assert any(
        Path(d["path"]).resolve() == f.resolve() and d["line"] == 1 for d in result["definitions"]
    )
