"""Implementation note."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.write_skills import register_git_skills

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH",
)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(
        ["git", "-C", str(r), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(r), "config", "user.email", "t@t.test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(r), "config", "user.name", "Tester"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(r), "config", "commit.gpgsign", "false"],
        check=True,
        capture_output=True,
    )
    # Implementation note.
    (r / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(r), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(r), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    return r


@pytest.fixture
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    register_git_skills(reg)
    return reg


def _invoke(registry: SkillRegistry, name: str, **args):
    skill = registry.get(name)
    return skill.handler(**args)


# ═══════════════════════════════════════════════════════════
# status / diff / log
# ═══════════════════════════════════════════════════════════


class TestReadOps:
    def test_status_clean(self, registry, repo):
        out = _invoke(registry, "git_status", repo_dir=str(repo))
        assert out["clean"] is True
        assert out["branch"] == "main"
        assert out["files"] == []

    def test_status_dirty(self, registry, repo):
        (repo / "new.txt").write_text("x", encoding="utf-8")
        out = _invoke(registry, "git_status", repo_dir=str(repo))
        assert out["clean"] is False
        paths = {f["path"] for f in out["files"]}
        assert "new.txt" in paths

    def test_diff_unstaged(self, registry, repo):
        (repo / "README.md").write_text("hello v2\n", encoding="utf-8")
        out = _invoke(registry, "git_diff", repo_dir=str(repo))
        assert "hello v2" in out["diff"]
        assert out["staged"] is False

    def test_diff_staged(self, registry, repo):
        (repo / "README.md").write_text("staged\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "README.md"],
            check=True,
            capture_output=True,
        )
        out = _invoke(registry, "git_diff", repo_dir=str(repo), staged=True)
        assert "staged" in out["diff"]
        assert out["staged"] is True

    def test_diff_flag_injection_rejected(self, registry, repo):
        out = _invoke(registry, "git_diff", repo_dir=str(repo), path="-A")
        assert "error" in out

    def test_log_returns_initial(self, registry, repo):
        out = _invoke(registry, "git_log", repo_dir=str(repo), limit=5)
        assert len(out["commits"]) == 1
        c = out["commits"][0]
        assert c["subject"] == "initial"
        assert c["author"] == "Tester"
        assert len(c["sha"]) == 40

    def test_log_limit_out_of_range(self, registry, repo):
        out = _invoke(registry, "git_log", repo_dir=str(repo), limit=0)
        assert "error" in out
        out = _invoke(registry, "git_log", repo_dir=str(repo), limit=10000)
        assert "error" in out


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestWriteOps:
    def test_add_and_commit_cycle(self, registry, repo):
        (repo / "new.txt").write_text("content\n", encoding="utf-8")
        add = _invoke(
            registry,
            "git_add",
            repo_dir=str(repo),
            paths=["new.txt"],
        )
        assert add.get("exit_code") == 0
        assert add["added"] == ["new.txt"]

        cm = _invoke(
            registry,
            "git_commit",
            repo_dir=str(repo),
            message="add new.txt",
        )
        assert "error" not in cm
        assert len(cm["sha"]) == 40

        # Implementation note.
        log = _invoke(registry, "git_log", repo_dir=str(repo))
        assert len(log["commits"]) == 2
        assert log["commits"][0]["subject"] == "add new.txt"

    def test_add_rejects_flag_injection(self, registry, repo):
        out = _invoke(
            registry,
            "git_add",
            repo_dir=str(repo),
            paths=["-A"],
        )
        assert "error" in out
        assert "flag-like" in out["error"]

    def test_add_rejects_broad_paths(self, registry, repo):
        for bad in [".", "*", "../escape"]:
            out = _invoke(
                registry,
                "git_add",
                repo_dir=str(repo),
                paths=[bad],
            )
            assert "error" in out, f"should reject {bad}"

    def test_add_rejects_empty_and_non_string(self, registry, repo):
        assert "error" in _invoke(
            registry,
            "git_add",
            repo_dir=str(repo),
            paths=[],
        )
        assert "error" in _invoke(
            registry,
            "git_add",
            repo_dir=str(repo),
            paths=[""],
        )
        assert "error" in _invoke(
            registry,
            "git_add",
            repo_dir=str(repo),
            paths=None,
        )

    def test_commit_empty_message_rejected(self, registry, repo):
        out = _invoke(
            registry,
            "git_commit",
            repo_dir=str(repo),
            message="   ",
        )
        assert "error" in out

    def test_commit_bad_author_rejected(self, registry, repo):
        (repo / "x.txt").write_text("x", encoding="utf-8")
        _invoke(registry, "git_add", repo_dir=str(repo), paths=["x.txt"])
        out = _invoke(
            registry,
            "git_commit",
            repo_dir=str(repo),
            message="x",
            author="just a name",
        )
        assert "error" in out
        assert "Name <email>" in out["error"]

    def test_commit_with_author(self, registry, repo):
        (repo / "x.txt").write_text("x", encoding="utf-8")
        _invoke(registry, "git_add", repo_dir=str(repo), paths=["x.txt"])
        out = _invoke(
            registry,
            "git_commit",
            repo_dir=str(repo),
            message="x",
            author="Alice <a@b.test>",
        )
        assert "error" not in out

        log = _invoke(registry, "git_log", repo_dir=str(repo))
        assert log["commits"][0]["author"] == "Alice"

    def test_commit_fails_with_nothing_staged(self, registry, repo):
        out = _invoke(
            registry,
            "git_commit",
            repo_dir=str(repo),
            message="empty",
        )
        assert "error" in out


# ═══════════════════════════════════════════════════════════
# branch
# ═══════════════════════════════════════════════════════════


class TestBranch:
    def test_branch_list(self, registry, repo):
        out = _invoke(registry, "git_branch", repo_dir=str(repo))
        names = {b["name"] for b in out["branches"]}
        assert "main" in names
        current = [b for b in out["branches"] if b["current"]]
        assert current and current[0]["name"] == "main"

    def test_branch_create(self, registry, repo):
        out = _invoke(
            registry,
            "git_branch",
            repo_dir=str(repo),
            create="feature/x",
        )
        assert out["created"] == "feature/x"

        listed = _invoke(registry, "git_branch", repo_dir=str(repo))
        names = {b["name"] for b in listed["branches"]}
        assert "feature/x" in names

    def test_branch_invalid_name(self, registry, repo):
        out = _invoke(
            registry,
            "git_branch",
            repo_dir=str(repo),
            create="-bad",
        )
        assert "error" in out
        out = _invoke(
            registry,
            "git_branch",
            repo_dir=str(repo),
            create="has space",
        )
        assert "error" in out


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSandbox:
    def test_repo_outside_sandbox_rejected(self, registry, repo, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        out = _invoke(
            registry,
            "git_status",
            repo_dir=str(outside),
            sandbox_dir=str(repo),
        )
        assert "error" in out

    def test_missing_repo_dir(self, registry):
        assert "error" in _invoke(registry, "git_status", repo_dir="")
        assert "error" in _invoke(
            registry,
            "git_status",
            repo_dir="/nonexistent/xyz",
        )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRegisterAllIntegration:
    def test_register_all_includes_git(self):
        from runtime.execution.suckers.builtins import register_all

        reg = SkillRegistry()
        register_all(reg)
        names = set(reg.all_names())
        for n in [
            "git_status",
            "git_diff",
            "git_log",
            "git_add",
            "git_commit",
            "git_branch",
        ]:
            assert n in names, f"missing skill: {n}"
