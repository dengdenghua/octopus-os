"""Keep the frontend on one package-manager contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def test_frontend_declares_pnpm_package_manager() -> None:
    package_json = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package_json["packageManager"].startswith("pnpm@")


def test_frontend_has_single_lockfile_policy() -> None:
    assert (FRONTEND / "pnpm-lock.yaml").is_file()
    assert not (FRONTEND / "package-lock.json").exists()
    assert not (FRONTEND / "yarn.lock").exists()


def test_frontend_docs_use_pnpm_commands() -> None:
    docs = (
        "README.md",
        "README.en.md",
        "QUICKSTART.md",
        "docs/GOLDEN_PATH.md",
    )
    forbidden = {
        r"(?<!p)\bnpm\s+install\b": "npm install",
        r"(?<!p)\bnpm\s+run\b": "npm run",
        r"(?<!p)\bnpm\s+ci\b": "npm ci",
        r"\bnpx\s+": "npx",
        r"\bpackage-lock\.json\b": "package-lock.json",
    }
    compiled = [(re.compile(pattern), label) for pattern, label in forbidden.items()]
    failures: list[str] = []
    for rel in docs:
        text = (ROOT / rel).read_text(encoding="utf-8")
        hits = [label for pattern, label in compiled if pattern.search(text)]
        if hits:
            failures.append(f"{rel}: {', '.join(hits)}")
    assert failures == []
