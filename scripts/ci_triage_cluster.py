"""Cluster CI / local JUnit failures into actionable buckets.

The full-suite job is deliberately wired as a non-blocking observation gate.
A raw list of N failures is not actionable; what we need is:

    how many distinct root causes are there, and which ones are
    platform artefacts (Windows-only) versus genuine defects?

Usage:
    python scripts/ci_triage_cluster.py "junit/*.xml" --md report.md
    python scripts/ci_triage_cluster.py batch_000.xml batch_001.xml --top 20

Exit code is always 0 - this is a reporting tool, not a gate.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

# --- signature normalisation -------------------------------------------------
# Anything volatile (temp dirs, pids, hex ids, timings, absolute paths) is
# scrubbed so that the *same* root cause collapses into one bucket.

_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NUM = re.compile(r"\b\d+\b")
_TMPDIR = re.compile(
    r"(?i)([A-Z]:)?[\\/](?:Users|home)[\\/][^\\/\s]+[\\/]"
    r"(?:AppData[\\/]Local[\\/]Temp|tmp)[\\/][^\s\"']*"
)
_PTEST_TMP = re.compile(r"pytest-of-[^\\/\s]+")
_WINPATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']*")
_POSIXPATH = re.compile(r"(?<![\w])/[^\s\"']*")
_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|ms|seconds|secs)\b")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

VERDICT_WIN = "WIN-ONLY"
VERDICT_POSIX = "POSIX-ASSUMPTION"
VERDICT_REAL = "REAL"

# Ordered: first match wins.
WIN_PATTERNS = [
    r"\[WinError",
    r"WinError \d+",
    r"Windows ACL",
    r"requires? PowerShell",
    r"icacls",
    r"nt\.path",
    r"On Windows",
    r"windows only",
    r"WindowsPath",
    r"pywin32",
    r"NotImplementedError.*Windows",
    r"\\Users\\",
]
POSIX_PATTERNS = [
    r"/bin/(?:ba)?sh",
    r"/usr/bin/env",
    r"posix",
    r"signal\.SIG(?:ALRM|KILL|TERM)",
    r"os\.fork",
    r"fcntl",
    r"termios",
    r"resource\.",
    r"grp\.|pwd\.",
    r"chmod",
]


def scrub(text: str) -> str:
    """Collapse volatile tokens so identical causes share a signature."""
    out = _TMPDIR.sub("<TMP>", text)
    out = _PTEST_TMP.sub("<PYTEST_TMP>", out)
    out = _WINPATH.sub("<PATH>", out)
    out = _POSIXPATH.sub("<PATH>", out)
    out = _HEX.sub("<HEX>", out)
    out = _QUOTED.sub("<Q>", out)
    out = _NUM.sub("<N>", out)
    out = _DURATION.sub("<DUR>", out)
    return out.strip()


def signature(message: str) -> str:
    """First two meaningful lines of the failure, normalised."""
    lines = [ln.strip() for ln in (message or "").splitlines() if ln.strip()]
    # Drop the pytest boilerplate banner ("assert X == Y" prelude) if present.
    lines = [ln for ln in lines if not ln.startswith("=" * 5)]
    head = lines[:3] if lines else ["<no message>"]
    return " | ".join(scrub(ln)[:160] for ln in head)


def classify(message: str, nodeid: str) -> str:
    blob = f"{message}\n{nodeid}"
    for pat in WIN_PATTERNS:
        if re.search(pat, blob, re.IGNORECASE):
            return VERDICT_WIN
    for pat in POSIX_PATTERNS:
        if re.search(pat, blob, re.IGNORECASE):
            return VERDICT_POSIX
    return VERDICT_REAL


# --- junit parsing -----------------------------------------------------------


def iter_failures(paths: list[str]):
    """Yield (nodeid, kind, message) for every failing/erroring testcase."""
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:  # pragma: no cover - malformed artifact
            print(f"  ! skipping unparsable {path}: {exc}", file=sys.stderr)
            continue
        for case in root.iter("testcase"):
            cls = case.get("classname") or ""
            name = case.get("name") or ""
            nodeid = f"{cls}::{name}" if cls else name
            if cls:
                # classname is dotted (tests.appliance.test_auth.TestBootstrap);
                # rebuild a file-ish, readable id.
                nodeid = f"{cls.replace('.', '/')}::{name}"
            for child in case:
                if child.tag in ("failure", "error"):
                    msg = (child.get("message") or "").strip()
                    if not msg:
                        body = (child.text or "").strip().splitlines()
                        msg = body[0] if body else ""
                    yield nodeid, child.tag, msg


def expand(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for pat in patterns:
        if os.path.isdir(pat):
            files.extend(sorted(glob.glob(os.path.join(pat, "*.xml"))))
        else:
            hits = sorted(glob.glob(pat))
            files.extend(hits or [pat])
    # stable, de-duplicated
    seen, out = set(), []
    for f in files:
        rp = os.path.realpath(f)
        if rp not in seen and os.path.exists(f):
            seen.add(rp)
            out.append(f)
    return out


# --- reporting ---------------------------------------------------------------

VERDICT_ORDER = [VERDICT_WIN, VERDICT_POSIX, VERDICT_REAL]

GUIDANCE = {
    VERDICT_WIN: "Windows-only artefact. Add `pytest.mark.skipif(sys.platform == 'win32')` "
    "or make the fixture portable; must NOT block the Linux gate.",
    VERDICT_POSIX: "Asserts POSIX semantics. Should be skipped on Windows; on Linux CI it "
    "is expected to pass - verify before treating as a defect.",
    VERDICT_REAL: "No platform signal found. Treat as a genuine failure candidate until "
    "triaged by hand.",
}


def render(clusters: dict, totals: Counter, top: int) -> str:
    lines: list[str] = []
    total = sum(totals.values())
    lines.append(f"Total failing/erroring cases: {total}")
    lines.append("  " + "  ".join(f"{v}={totals.get(v, 0)}" for v in VERDICT_ORDER))
    lines.append("")

    for verdict in VERDICT_ORDER:
        bucket = [c for c in clusters.values() if c["verdict"] == verdict]
        bucket.sort(key=lambda c: -c["count"])
        if not bucket:
            continue
        lines.append(f"## {verdict}  ({len(bucket)} clusters) — {GUIDANCE[verdict]}")
        lines.append("")
        lines.append("| count | signature | modules | sample |")
        lines.append("|---|---|---|---|")
        for c in bucket[:top]:
            mods = ", ".join(sorted(c["modules"])[:3])
            if len(c["modules"]) > 3:
                mods += f" +{len(c['modules']) - 3}"
            lines.append(f"| {c['count']} | `{c['signature'][:120]}` | {mods} | `{c['sample']}` |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("paths", nargs="+", help="junit xml files, globs or directories")
    ap.add_argument("--top", type=int, default=40, help="clusters per verdict")
    ap.add_argument("--md", help="also write the report to this markdown file")
    args = ap.parse_args(argv)

    files = expand(args.paths)
    if not files:
        print("no junit inputs found", file=sys.stderr)
        return 2

    clusters: dict[str, dict] = {}
    totals: Counter = Counter()

    for nodeid, kind, message in iter_failures(files):
        sig = signature(message)
        verdict = classify(message, nodeid)
        key = sig
        c = clusters.setdefault(
            key,
            {
                "signature": sig,
                "verdict": verdict,
                "count": 0,
                "modules": set(),
                "sample": nodeid,
                "kinds": Counter(),
            },
        )
        c["count"] += 1
        c["kinds"][kind] += 1
        mod = nodeid.split("::")[0]
        if mod:
            c["modules"].add(mod)
        totals[verdict] += 1

    report = render(clusters, totals, args.top)
    print(report)

    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write("# Full-suite triage\n\n")
            fh.write(report)
            fh.write("\n")
        print(f"\nwritten -> {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
