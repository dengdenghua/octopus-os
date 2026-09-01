from __future__ import annotations

from pathlib import Path

from runtime.execution.suckers.code_navigation import dependency_graph, find_symbol


def test_find_symbol_locates_python_definitions(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "VALUE = 1\n\nclass Worker:\n    pass\n\ndef run(arg):\n    return arg\n",
        encoding="utf-8",
    )

    assert find_symbol("Worker", directory=str(tmp_path))["definitions"] == [
        {"path": "sample.py", "line": 3, "kind": "class"}
    ]
    function = find_symbol("run", directory=str(tmp_path))["definitions"][0]
    assert function["signature"] == "def run(arg)"


def test_dependency_graph_links_local_python_imports(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("import alpha\n", encoding="utf-8")

    graph = dependency_graph(directory=str(tmp_path))

    assert graph["node_count"] == 2
    assert graph["edges"] == [{"source": "beta.py", "target": "alpha.py", "import": "alpha"}]


def test_code_navigation_rejects_missing_inputs(tmp_path: Path) -> None:
    assert find_symbol(directory=str(tmp_path)) == {"error": "missing symbol name"}
    assert dependency_graph(directory=str(tmp_path / "missing")) == {
        "error": f"directory not found: {tmp_path / 'missing'}"
    }

