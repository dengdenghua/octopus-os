from __future__ import annotations

from runtime.core.cerebrum.react_guards import _path_verification_policy_guard
from runtime.core.cerebrum.react_types import ReActStep
from runtime.core.cerebrum.verification_policy import (
    classify_path,
    command_satisfies_requirement,
    project_verification_profile,
    verification_requirements_for_paths,
)


def _step(iteration: int, action: str, observation: str = "") -> ReActStep:
    return ReActStep(iteration=iteration, action=action, observation=observation)


def test_classify_python_frontend_schema_and_docs() -> None:
    assert classify_path("runtime/core/foo.py") == "python"
    assert classify_path("frontend/src/App.tsx") == "frontend"
    assert classify_path("output/final/snake-game.html") == "static-web"
    assert classify_path("frontend/package.json") == "frontend"
    assert classify_path("runtime/platform/schema/models.py") == "python-schema"
    assert classify_path("migrations/001_init.sql") == "schema"
    assert classify_path("README.md") is None


def test_requirements_are_deduplicated_by_bucket() -> None:
    reqs = verification_requirements_for_paths(
        [
            "runtime/foo.py",
            "runtime/bar.py",
            "frontend/src/App.tsx",
        ]
    )

    assert [req.key for req in reqs] == ["python-checks", "frontend-typecheck"]
    assert reqs[0].paths == ("runtime/foo.py", "runtime/bar.py")
    assert reqs[1].paths == ("frontend/src/App.tsx",)


def test_project_verification_profile_discovers_local_commands(tmp_path) -> None:
    (tmp_path / "Makefile").write_text(
        "test-fast:\n\tpytest -q\nfrontend-typecheck:\n\tcd frontend && pnpm typecheck\n",
        encoding="utf-8",
    )
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (frontend / "package.json").write_text(
        '{"scripts":{"typecheck":"tsc --noEmit","build":"vite build"}}',
        encoding="utf-8",
    )

    profile = project_verification_profile(tmp_path)

    assert "make test-fast" in profile.python_hints
    assert "make frontend-typecheck" in profile.frontend_hints
    assert "cd frontend && pnpm typecheck" in profile.frontend_hints


def test_requirements_include_project_specific_commands(tmp_path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (frontend / "package.json").write_text(
        '{"scripts":{"typecheck":"tsc --noEmit"}}',
        encoding="utf-8",
    )

    (req,) = verification_requirements_for_paths(
        ["frontend/src/App.tsx"],
        project_root=tmp_path,
    )

    assert "cd frontend && pnpm typecheck" in req.command_hints
    assert command_satisfies_requirement(
        'exec_shell({"command": "cd frontend && pnpm typecheck"})',
        req,
    )


def test_command_satisfies_frontend_requirement() -> None:
    (req,) = verification_requirements_for_paths(["frontend/src/App.tsx"])

    assert command_satisfies_requirement(
        'exec_shell({"command": "pnpm typecheck"})',
        req,
    )
    assert not command_satisfies_requirement(
        'exec_shell({"command": "python -m pytest"})',
        req,
    )


def test_static_html_uses_smoke_check_not_typescript_requirement() -> None:
    (req,) = verification_requirements_for_paths(["output/final/snake-game.html"])

    assert req.key == "static-web-artifact"
    assert "Static web artifact" in req.label
    assert "npx tsc --noEmit" not in req.command_hints
    assert command_satisfies_requirement(
        'read_file({"path": "output/final/snake-game.html"})',
        req,
    )
    assert command_satisfies_requirement(
        'browser_screenshot({"url": "http://127.0.0.1:3000/snake-game.html"})',
        req,
    )
    assert not command_satisfies_requirement(
        'exec_shell({"command": "python -m pytest"})',
        req,
    )


def test_path_guard_blocks_wrong_verifier_after_frontend_edit() -> None:
    steps = [
        _step(
            1,
            'edit_file({"path": "frontend/src/App.tsx", "old_string": "x", "new_string": "y"})',
        ),
        _step(2, 'exec_shell({"command": "python -m pytest"})', "passed"),
    ]

    msg = _path_verification_policy_guard(steps, "Done.", is_code_mode=True)

    assert msg is not None
    assert "Frontend typecheck" in msg
    assert "pnpm typecheck" in msg


def test_path_guard_accepts_matching_verifier_after_latest_edit() -> None:
    steps = [
        _step(
            1,
            'edit_file({"path": "frontend/src/App.tsx", "old_string": "x", "new_string": "y"})',
        ),
        _step(2, 'exec_shell({"command": "pnpm typecheck"})', "No errors"),
    ]

    assert _path_verification_policy_guard(steps, "Done.", is_code_mode=True) is None


def test_path_guard_requires_verifier_after_latest_edit() -> None:
    steps = [
        _step(1, 'exec_shell({"command": "pnpm typecheck"})', "No errors"),
        _step(
            2,
            'edit_file({"path": "frontend/src/App.tsx", "old_string": "x", "new_string": "y"})',
        ),
    ]

    msg = _path_verification_policy_guard(steps, "Done.", is_code_mode=True)

    assert msg is not None
    assert "after the latest edit" in msg


def test_path_guard_ignores_docs_only_edits() -> None:
    steps = [
        _step(
            1,
            'edit_file({"path": "README.md", "old_string": "x", "new_string": "y"})',
        ),
    ]

    assert _path_verification_policy_guard(steps, "Done.", is_code_mode=True) is None


def test_path_guard_accepts_static_html_readback_after_latest_edit() -> None:
    steps = [
        _step(
            1,
            'write_text_file({"path": "output/final/snake-game.html", "content": "<!doctype html>"})',
        ),
        _step(
            2,
            'read_file({"path": "output/final/snake-game.html"})',
            "<!doctype html>",
        ),
    ]

    assert _path_verification_policy_guard(steps, "Done.", is_code_mode=True) is None


def test_path_guard_blocks_unrelated_check_after_static_html_edit() -> None:
    steps = [
        _step(
            1,
            'write_text_file({"path": "output/final/snake-game.html", "content": "<!doctype html>"})',
        ),
        _step(2, 'exec_shell({"command": "python -m pytest"})', "passed"),
    ]

    msg = _path_verification_policy_guard(steps, "Done.", is_code_mode=True)

    assert msg is not None
    assert "Static web artifact" in msg
    assert "read_file" in msg
