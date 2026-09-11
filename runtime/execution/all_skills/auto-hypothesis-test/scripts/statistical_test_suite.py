#!/usr/bin/env python3
"""Portable compatibility entry point for the canonical statistics skill.

This file used to be a Git symlink. Windows checkouts without symlink support
materialized the link target as plain text, which produced an invalid ``.py``
file inside wheels built on Windows. Keep the legacy skill path executable while
sharing the canonical implementation without relying on archive symlink rules.
"""

from __future__ import annotations

import runpy
from pathlib import Path

_TARGET = (
    Path(__file__).resolve().parents[2] / "auto-stat-test" / "scripts" / "statistical_test_suite.py"
)
if not _TARGET.is_file():
    raise RuntimeError("canonical auto-stat-test implementation is unavailable")

_namespace = runpy.run_path(str(_TARGET), run_name=__name__)
if __name__ != "__main__":
    globals().update(_namespace)
