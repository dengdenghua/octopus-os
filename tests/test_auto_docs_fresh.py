"""
CI gate · fail the build if ``docs/auto/`` drifts from the generator.

Pattern mirrors ``tests/test_openapi_snapshot.py``.

The wiki tree is produced by ``scripts/gen_wiki.py``::

    docs/auto/
    ├── index.json                          TOC manifest
    ├── README.md                           index page
    ├── 00-overview.md
    ├── 10-tech-stack.md
    ├── 20-backend/
    │   ├── index.md
    │   ├── 21-runtime/*.md
    │   ├── 22-safety/*.md
    │   ├── 23-memory/*.md
    │   ├── 24-sensing/*.md
    │   ├── 25-adapters/*.md
    │   └── 26-agents/*.md
    ├── 30-skills-graph/skill-map.md
    ├── 40-hooks/hook-surface.md
    └── 50-governance/adr-anchors.md

Regeneration::

    python scripts/gen_wiki.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUTO_DIR = _REPO_ROOT / "docs" / "auto"


def _import_generator():
    # Register the dynamically-loaded module into ``sys.modules``
    # BEFORE ``exec_module`` so that dataclass's annotation resolver
    # (``sys.modules.get(cls.__module__)``) finds the module during
    # class body evaluation. Without this, a ``@dataclass`` class
    # declaration inside the script raises ``AttributeError: 'NoneType'
    # object has no attribute '__dict__'``.
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "gen_wiki",
        _REPO_ROOT / "scripts" / "gen_wiki.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_wiki"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("gen_wiki", None)
        raise
    return mod


class TestAutoDocsFresh:
    def test_docs_auto_dir_exists(self):
        assert _AUTO_DIR.is_dir(), "docs/auto/ missing · run `python scripts/gen_wiki.py`"

    def test_top_level_pages_exist(self):
        """Minimum set of pages every commit should have."""
        for name in (
            "README.md",
            "index.json",
            "00-overview.md",
            "10-tech-stack.md",
            "20-backend/index.md",
            "30-skills-graph/skill-map.md",
            "40-hooks/hook-surface.md",
            "50-governance/adr-anchors.md",
        ):
            assert (_AUTO_DIR / name).is_file(), (
                f"docs/auto/{name} missing · run `python scripts/gen_wiki.py`"
            )

    def test_generator_output_matches_on_disk(self):
        """Every page the generator emits must byte-match the
        committed copy · and no stale files may linger.

        ``index.json`` is skipped from byte-compare because it carries
        ``generated_at`` (a real ISO timestamp the UI needs for
        "generated N min ago"). The separate ``test_manifest_valid``
        asserts the manifest still parses + references real files.
        """
        gen = _import_generator()
        outputs, _tree = gen.generate_all()

        # Files whose content is inherently non-deterministic across
        # regenerations · we check schema elsewhere instead of bytes.
        volatile = {"index.json"}

        drift: list[str] = []
        for rel, expected in outputs.items():
            path = _AUTO_DIR / rel
            if not path.is_file():
                drift.append(f"missing: docs/auto/{rel}")
                continue
            if rel in volatile:
                continue  # timestamp-bearing · byte-compare would always drift
            actual = path.read_text(encoding="utf-8")
            # Exact byte-compare. The generator emits a single deterministic
            # trailing newline for every page (``page_agent`` strips stray
            # blank lines from optional fields before write), so there is no
            # longer an order-dependent trailing-newline delta to tolerate.
            if actual != expected:
                drift.append(f"drift: docs/auto/{rel}")

        # Stale files · on disk but generator doesn't produce them.
        desired = {(_AUTO_DIR / rel).resolve() for rel in outputs}
        for p in _AUTO_DIR.rglob("*"):
            if p.is_file() and p.resolve() not in desired:
                drift.append(f"stale: {p.relative_to(_AUTO_DIR)}")

        if drift:
            msg = "docs/auto/ out of date · run `python scripts/gen_wiki.py`.\n\n" + "\n".join(
                drift[:30]
            )
            if len(drift) > 30:
                msg += f"\n... (+{len(drift) - 30} more)"
            pytest.fail(msg)

    def test_manifest_valid(self):
        """``index.json`` must parse and reference real files."""
        import json

        mf_path = _AUTO_DIR / "index.json"
        assert mf_path.is_file(), "index.json missing"
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))

        assert "tree" in manifest
        assert "version" in manifest
        assert "generated_at" in manifest

        def check(node):
            if node.get("type") == "doc":
                p = _AUTO_DIR / node["path"]
                assert p.is_file(), f"manifest references missing doc: {node['path']}"
            for child in node.get("children", []):
                check(child)

        for top in manifest["tree"]:
            check(top)

    def test_check_mode_accepts_current_timestamped_manifest(self):
        result = subprocess.run(
            [sys.executable, "scripts/gen_wiki.py", "--check"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr

