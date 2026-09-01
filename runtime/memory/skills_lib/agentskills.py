"""Install agentskills.io skills into Echo — safely.

The `agentskills.io <https://agentskills.io/specification>`_ open standard
(originally Anthropic's SKILL.md format, now adopted by Claude Code, Codex,
Cursor, Gemini CLI, Goose, OpenHands and dozens more) packages a skill as a
folder with a ``SKILL.md`` (``name`` + ``description`` frontmatter required)
plus optional ``scripts/`` / ``references/`` / ``assets/``.

Echo already speaks this format — its ``all_skills/`` catalog *is* a tree
of conformant SKILL.md folders consumed via progressive disclosure. This
module closes the last gap: pulling an *external* standard skill into that
catalog so the whole open ecosystem's skills run in Echo.

The differentiator is the safety gate. A downloaded skill's SKILL.md
instructions and bundled scripts run on the user's machine, so before
installing we scan them for high-signal, low-false-positive dangerous
patterns (``rm -rf`` of a broad path, piped-curl-to-shell, fork bombs, disk
wipes/formats, private-key reads, secret exfiltration). Flagged skills are
refused unless the caller explicitly opts in. "Install any skill in the
ecosystem — but only Echo installs it behind an immune gate."
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.memory.skills_lib.skill_library import _parse_frontmatter

# High-signal dangerous shell patterns (substring / regex). Curated for low
# false-positive rate — each one is rarely legitimate inside a skill.
_DANGER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"rm\s+-rf\s+(/|~|\$HOME|\*)", "recursive force-delete of a broad path"),
    (r":\(\)\s*\{\s*:\|\s*:&\s*\}\s*;\s*:", "fork bomb"),
    (r"(curl|wget)\s+[^\n|]*\|\s*(sudo\s+)?(sh|bash|zsh)", "pipe remote script to a shell"),
    (r"\bdd\s+if=.*of=/dev/", "raw write to a block device"),
    (r"\bmkfs\b", "format a filesystem"),
    (r"chmod\s+-R\s+777\s+/", "world-writable on a broad path"),
    (r">\s*/dev/sd[a-z]", "overwrite a disk device"),
    (r"\.ssh/id_(rsa|ed25519)", "read a private SSH key"),
    (
        r"(AWS_SECRET|OPENAI_API_KEY|ANTHROPIC_API_KEY)[^\n]*\|\s*(curl|wget|nc)",
        "exfiltrate a secret",
    ),
)

_SCANNED_SUFFIXES = {".md", ".sh", ".bash", ".zsh", ".py", ".js", ".ts", ".rb", ".pl", ".ps1"}


@dataclass
class SafetyFinding:
    file: str
    line: int
    reason: str
    excerpt: str


@dataclass
class InstallResult:
    ok: bool
    name: str = ""
    description: str = ""
    dest: str = ""
    findings: list[SafetyFinding] = field(default_factory=list)
    error: str = ""

    @property
    def dangerous(self) -> bool:
        return bool(self.findings)


def validate_skill_dir(skill_dir: Path) -> tuple[bool, str, str, str]:
    """Check a folder is an agentskills.io-conformant skill.

    Returns ``(ok, name, description, error)``. Conformance = a ``SKILL.md``
    with non-empty ``name`` and ``description`` frontmatter (the spec's only
    required fields). Extra frontmatter keys (``license``, ``enabled``,
    ``allowed-tools``, ...) are tolerated.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return False, "", "", "no SKILL.md in skill folder"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return False, "", "", f"cannot read SKILL.md: {exc}"
    meta, _body = _parse_frontmatter(text)
    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    if not name:
        return False, "", "", "SKILL.md frontmatter missing required 'name'"
    if not description:
        return False, name, "", "SKILL.md frontmatter missing required 'description'"
    return True, name, description, ""


def scan_skill_safety(skill_dir: Path) -> list[SafetyFinding]:
    """Scan a skill's SKILL.md + bundled text/scripts for dangerous patterns.

    Best-effort static scan — not a sandbox. Catches the obvious foot-guns a
    malicious or careless skill would carry; the runtime immunity layer is
    still the hard floor when the skill actually runs.
    """
    findings: list[SafetyFinding] = []
    compiled = [(re.compile(pat, re.IGNORECASE), why) for pat, why in _DANGER_PATTERNS]
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SCANNED_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(skill_dir))
        for lineno, line in enumerate(content.splitlines(), start=1):
            for rx, why in compiled:
                if rx.search(line):
                    findings.append(SafetyFinding(rel, lineno, why, line.strip()[:160]))
    return findings


def install_skill(
    src_dir: Path,
    dest_root: Path,
    *,
    allow_dangerous: bool = False,
    overwrite: bool = False,
) -> InstallResult:
    """Validate + safety-scan an agentskills.io skill, then copy it into
    ``dest_root/<name>/``.

    Refuses (``ok=False``, ``findings`` populated) when the safety scan trips
    unless ``allow_dangerous=True``. Refuses to clobber an existing skill
    unless ``overwrite=True``.
    """
    src_dir = Path(src_dir)
    dest_root = Path(dest_root)
    ok, name, description, err = validate_skill_dir(src_dir)
    if not ok:
        return InstallResult(ok=False, error=err)

    findings = scan_skill_safety(src_dir)
    if findings and not allow_dangerous:
        return InstallResult(
            ok=False,
            name=name,
            description=description,
            findings=findings,
            error=(
                f"refused: {len(findings)} safety finding(s) — pass "
                "allow_dangerous=True to install anyway"
            ),
        )

    dest = dest_root / name
    if dest.exists():
        if not overwrite:
            return InstallResult(
                ok=False,
                name=name,
                description=description,
                dest=str(dest),
                error=f"skill '{name}' already installed (pass overwrite=True)",
            )
        shutil.rmtree(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_dir, dest)
    return InstallResult(
        ok=True,
        name=name,
        description=description,
        dest=str(dest),
        findings=findings,
    )


def default_catalog_dir() -> Path:
    """The default skill catalog Echo auto-merges.

    Prefers the external ``skills/public/`` location (preferred post-migration
    home). Falls back to the legacy in-package ``all_skills/`` directory when
    the external resources root is unavailable (e.g. bare wheel install).
    """
    from runtime.platform.process.paths import resources_root

    external = resources_root() / "skills" / "public"
    if external.is_dir():
        return external
    return Path(__file__).resolve().parents[2] / "execution" / "all_skills"


_GH_TREE_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+?)/?$")
_GH_REPO_RE = re.compile(r"^https?://github\.com/[^/]+/[^/]+?(?:\.git)?/?$")


def _git_clone(url: str, branch: str | None, dest: Path) -> None:
    import subprocess

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as exc:  # git not installed
        raise RuntimeError("git is not installed") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:300]}")


def resolve_skill_source(source: str, workdir: Path) -> Path:
    """Resolve a skill source to a local folder containing ``SKILL.md``.

    Accepts:
    * a local directory path;
    * a GitHub *tree* URL pointing at a skill subfolder
      (``.../tree/<branch>/<path>``);
    * a plain GitHub repo or ``.git`` URL (clones, then finds the skill).

    Clones into ``workdir`` (caller owns its lifetime). Raises on failure.
    """
    p = Path(source).expanduser()
    if p.is_dir():
        return p

    m = _GH_TREE_RE.match(source)
    if m:
        owner, repo, branch, subpath = m.groups()
        _git_clone(f"https://github.com/{owner}/{repo}.git", branch, workdir)
        target = workdir / subpath
        if not (target / "SKILL.md").is_file():
            raise ValueError(f"no SKILL.md at '{subpath}' in {owner}/{repo}")
        return target

    if _GH_REPO_RE.match(source) or source.endswith(".git"):
        url = source if source.endswith(".git") else source.rstrip("/") + ".git"
        _git_clone(url, None, workdir)
        if (workdir / "SKILL.md").is_file():
            return workdir
        found = next(iter(sorted(workdir.rglob("SKILL.md"))), None)
        if found is None:
            raise ValueError(f"no SKILL.md found in {source}")
        return found.parent

    raise ValueError(
        f"unrecognized skill source: {source!r} (use a local directory or a GitHub URL)"
    )


def install_from_source(
    source: str,
    dest_root: Path | None = None,
    *,
    allow_dangerous: bool = False,
    overwrite: bool = False,
) -> InstallResult:
    """Resolve a skill source (local dir or GitHub URL) and install it into
    the catalog behind the safety gate. Temp clones are cleaned up."""
    import tempfile

    root = Path(dest_root) if dest_root is not None else default_catalog_dir()
    with tempfile.TemporaryDirectory(prefix="echo-skill-") as td:
        try:
            skill_dir = resolve_skill_source(source, Path(td) / "clone")
        except (ValueError, RuntimeError, OSError) as exc:
            return InstallResult(ok=False, error=f"fetch failed: {exc}")
        return install_skill(
            skill_dir,
            root,
            allow_dangerous=allow_dangerous,
            overwrite=overwrite,
        )


def to_payload(result: InstallResult) -> dict[str, Any]:
    """Serialise an InstallResult for an API/CLI response."""
    return {
        "ok": result.ok,
        "name": result.name,
        "description": result.description,
        "dest": result.dest,
        "dangerous": result.dangerous,
        "error": result.error or None,
        "findings": [
            {"file": f.file, "line": f.line, "reason": f.reason, "excerpt": f.excerpt}
            for f in result.findings
        ],
    }
