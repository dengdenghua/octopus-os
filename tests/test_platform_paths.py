from __future__ import annotations

from pathlib import Path

from runtime.platform.process.paths import app_paths, project_root, resources_root


def _make_project(root: Path) -> Path:
    (root / "runtime").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'echo-agent'\n", encoding="utf-8")
    return root


def test_app_paths_follow_echo_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "echo-data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("ECHO_HOME", raising=False)

    paths = app_paths()

    assert paths.root == data_dir.resolve().parent
    assert paths.data_dir == data_dir.resolve()
    assert paths.threads_path == data_dir.resolve() / "threads.jsonl"
    assert paths.agent_trace_path == data_dir.resolve() / "agent_trace.sqlite"
    assert paths.permissions_path == data_dir.resolve() / "permissions.json"


def test_echo_data_dir_wins_over_echo_home(monkeypatch, tmp_path):
    data_dir = tmp_path / "data-dir"
    home = tmp_path / "home"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ECHO_HOME", str(home))

    paths = app_paths()

    assert paths.root == data_dir.resolve().parent
    assert paths.data_dir == data_dir.resolve()


def test_app_paths_follow_echo_home_when_data_dir_missing(monkeypatch, tmp_path):
    home = tmp_path / "echo-home"
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.setenv("ECHO_HOME", str(home))

    paths = app_paths()

    assert paths.root == home.resolve()
    assert paths.data_dir == home.resolve() / "data"
    assert paths.permissions_path == home.resolve() / "data" / "permissions.json"


def test_project_root_discovers_repo_from_child_directory(monkeypatch, tmp_path):
    repo = _make_project(tmp_path / "repo")
    child = repo / "runtime" / "platform"
    child.mkdir(parents=True)
    monkeypatch.chdir(child)
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)

    assert project_root() == repo.resolve()
    assert app_paths().root == repo.resolve()
    assert app_paths().data_dir == repo.resolve() / "data"


def test_project_root_falls_back_to_cwd_without_sentinels(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(scratch)
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)

    assert project_root() == scratch.resolve()
    assert app_paths().data_dir == scratch.resolve() / "data"


def test_resources_root_honours_env(monkeypatch, tmp_path):
    # The Docker image pip-installs the code and runs from /data, so
    # project_root() can't find bundled skills/prompts/protocols. The
    # image sets ECHO_RESOURCES_DIR=/app/resources; resources_root()
    # must honour it so the 87+ file-backed skills actually load.
    res = tmp_path / "resources"
    res.mkdir()
    monkeypatch.setenv("ECHO_RESOURCES_DIR", str(res))
    assert resources_root() == res.resolve()


def test_resources_root_falls_back_to_packaged_source_root(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(scratch)
    monkeypatch.delenv("ECHO_RESOURCES_DIR", raising=False)
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)
    # Resource discovery is independent of the launch cwd in an editable
    # install, while project_root() intentionally remains scratch-local.
    assert project_root() == scratch.resolve()
    assert resources_root() == Path(__file__).resolve().parents[1]
