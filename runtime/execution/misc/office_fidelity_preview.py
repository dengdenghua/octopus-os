"""Render Office/PDF artifacts as self-contained, layout-faithful HTML pages."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from html import escape
from pathlib import Path

from runtime.execution.misc.office_pdf_preview import render_office_pdf

_SUPPORTED = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf"}
_CACHE_ROOT = Path(tempfile.gettempdir()) / "echo-office-fidelity-cache"
_RENDER_LOCK = threading.Lock()
_MAX_PAGES = 100
_MAX_IMAGE_BYTES = 40 * 1024 * 1024
_MAX_QUICKLOOK_ATTACHMENTS = 200
_MAX_QUICKLOOK_HTML_BYTES = 5 * 1024 * 1024
_MAX_QUICKLOOK_CSS_BYTES = 5 * 1024 * 1024
_RENDER_TIMEOUT_SECONDS = 60
_CACHE_VERSION = "6"


def render_office_fidelity_preview(path: Path) -> str | None:
    """Return self-contained HTML page images, or ``None`` for safe fallback."""

    if path.suffix.lower() not in _SUPPORTED:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    digest = hashlib.sha256(
        f"{_CACHE_VERSION}\0{path.resolve()}\0{stat.st_mtime_ns}\0{stat.st_size}".encode()
    ).hexdigest()
    cached = _CACHE_ROOT / f"{digest}.html"
    if cached.is_file():
        try:
            return cached.read_text(encoding="utf-8")
        except OSError:
            pass
    with _RENDER_LOCK:
        if cached.is_file():
            try:
                return cached.read_text(encoding="utf-8")
            except OSError:
                pass
        _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="render-", dir=_CACHE_ROOT) as output_dir:
            pdftoppm = _find_pdftoppm()
            if not pdftoppm:
                return None
            html = None
            if path.suffix.lower() != ".pdf":
                qlmanage = _find_qlmanage()
                if qlmanage:
                    html = _render_quicklook_preview(path, pdftoppm, qlmanage, Path(output_dir))
            if html is None:
                pdf = path if path.suffix.lower() == ".pdf" else render_office_pdf(path)
                if pdf is None or not pdf.is_file():
                    return None
                html = _render_pdf_pages(path.name, pdf, pdftoppm, Path(output_dir))
            if html is None:
                return None
            temporary = _CACHE_ROOT / f".{digest}.{threading.get_ident()}.html"
            temporary.write_text(html, encoding="utf-8")
            temporary.replace(cached)
            return html


def _render_pdf_pages(filename: str, pdf: Path, pdftoppm: str, output_dir: Path) -> str | None:
    prefix = Path(output_dir) / "page"
    try:
        subprocess.run(
            [
                pdftoppm,
                "-jpeg",
                "-r",
                "120",
                "-jpegopt",
                "quality=84,optimize=y",
                "-f",
                "1",
                "-l",
                str(_MAX_PAGES),
                str(pdf),
                str(prefix),
            ],
            check=True,
            capture_output=True,
            timeout=_RENDER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pages = sorted(Path(output_dir).glob("page-*.jpg"), key=_page_number)
    if not pages:
        return None
    try:
        total = sum(page.stat().st_size for page in pages)
    except OSError:
        return None
    if total > _MAX_IMAGE_BYTES:
        return None
    return _page_html(filename, pages)


def _render_quicklook_preview(
    path: Path, pdftoppm: str, qlmanage: str, output_dir: Path
) -> str | None:
    preview_root = output_dir / "quicklook"
    preview_root.mkdir()
    try:
        subprocess.run(
            [qlmanage, "-p", "-o", str(preview_root), str(path)],
            check=True,
            capture_output=True,
            timeout=_RENDER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    previews = list(preview_root.glob("*.qlpreview/Preview.html"))
    if len(previews) != 1:
        return None
    preview_html = previews[0]
    try:
        if preview_html.stat().st_size > _MAX_QUICKLOOK_HTML_BYTES:
            return None
        html = preview_html.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    html = _inline_quicklook_styles(html, preview_html.parent.resolve())
    if html is None:
        return None

    attachment_names = sorted(set(re.findall(r"Attachment\d+\.pdf", html)))
    if len(attachment_names) > _MAX_QUICKLOOK_ATTACHMENTS:
        return None
    total_image_bytes = 0
    preview_dir = preview_html.parent.resolve()
    for index, name in enumerate(attachment_names):
        attachment = (preview_dir / name).resolve()
        if attachment.parent != preview_dir or not attachment.is_file():
            return None
        prefix = output_dir / f"quicklook-attachment-{index}"
        try:
            subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-r",
                    "120",
                    "-singlefile",
                    str(attachment),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                timeout=_RENDER_TIMEOUT_SECONDS,
            )
            image = prefix.with_suffix(".png")
            image_bytes = image.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None
        total_image_bytes += len(image_bytes)
        if total_image_bytes > _MAX_IMAGE_BYTES:
            return None
        encoded = base64.b64encode(image_bytes).decode("ascii")
        html = html.replace(name, f"data:image/png;base64,{encoded}")

    html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.I | re.S)
    # Quick Look emits unitless CSS lengths that its native preview accepts but
    # Chromium correctly treats as invalid. Normalize the positioning/box
    # properties so slides retain their dimensions inside the web workbench.
    length_properties = (
        r"top|right|bottom|left|width|height|min-width|max-width|min-height|"
        r"max-height|font-size|padding(?:-(?:top|right|bottom|left))?|"
        r"margin(?:-(?:top|right|bottom|left))?"
    )
    html = re.sub(
        rf"\b({length_properties})\s*:\s*(-?\d+(?:\.\d+)?)(?=\s*[;\"}}])",
        r"\1:\2px",
        html,
    )
    viewport_tag = re.search(r'<meta\b[^>]*\bname=["\']viewport["\'][^>]*>', html, flags=re.I)
    viewport_match = (
        re.search(r"\bwidth\s*=\s*(\d+)", viewport_tag.group(0), flags=re.I)
        if viewport_tag
        else None
    )
    viewport_width = max(1, int(viewport_match.group(1))) if viewport_match else 960
    csp = (
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; img-src data:; object-src 'none'; "
        "base-uri 'none'; form-action 'none'\">"
    )
    responsive_style = (
        '<style id="echo-office-fidelity">'
        "html{max-width:100%;overflow-x:hidden;scroll-snap-type:y mandatory;"
        "scroll-padding-top:12px;background:#e8ebf1}"
        f"body{{max-width:100%;overflow-x:hidden;zoom:min(1,calc(100vw/{viewport_width}px));"
        "padding:12px 0 28px;background:#e8ebf1!important}"
        "body>div.slide{margin:0 auto 24px!important;scroll-snap-align:start;"
        "scroll-snap-stop:always;border-radius:3px;outline:1px solid #1118271a;"
        "box-shadow:0 12px 32px #1720332e!important}"
        "body>div.slide:last-of-type{margin-bottom:0!important}"
        "</style>"
    )
    if re.search(r"<head\b[^>]*>", html, flags=re.I):
        html = re.sub(
            r"(<head\b[^>]*>)",
            rf"\1{csp}{responsive_style}",
            html,
            count=1,
            flags=re.I,
        )
    else:
        html = csp + responsive_style + html
    return html


def _inline_quicklook_styles(html: str, preview_dir: Path) -> str | None:
    stylesheet_names = sorted(set(re.findall(r"Attachment\d+\.css", html)))
    if len(stylesheet_names) > _MAX_QUICKLOOK_ATTACHMENTS:
        return None
    total = 0
    for name in stylesheet_names:
        stylesheet = (preview_dir / name).resolve()
        if stylesheet.parent != preview_dir or not stylesheet.is_file():
            return None
        try:
            css_bytes = stylesheet.read_bytes()
            css = css_bytes.decode("utf-8")
        except (OSError, UnicodeError):
            return None
        total += len(css_bytes)
        if total > _MAX_QUICKLOOK_CSS_BYTES:
            return None
        link_pattern = rf"<link\b(?=[^>]*\bhref=[\"\']{re.escape(name)}[\"\'])[^>]*>"
        html, replacements = re.subn(
            link_pattern,
            lambda _match, source=name, content=css: (
                f'<style data-quicklook-source="{source}">{content}</style>'
            ),
            html,
            flags=re.I,
        )
        if replacements == 0:
            return None
    return html


def _page_number(path: Path) -> int:
    try:
        return int(path.stem.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _find_pdftoppm() -> str | None:
    configured = os.environ.get("ECHO_PDFTOPPM_PATH", "").strip()
    candidates = [Path(configured) if configured else None, Path("/usr/bin/pdftoppm")]
    discovered = shutil.which("pdftoppm")
    if discovered:
        candidates.insert(0, Path(discovered))
    runtime_root = Path.home() / ".cache" / "codex-runtimes"
    if runtime_root.is_dir():
        candidates.extend(runtime_root.glob("*/dependencies/bin/override/pdftoppm"))
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _find_qlmanage() -> str | None:
    configured = os.environ.get("ECHO_QLMANAGE_PATH", "").strip()
    candidates = [Path(configured) if configured else None, Path("/usr/bin/qlmanage")]
    discovered = shutil.which("qlmanage")
    if discovered:
        candidates.insert(0, Path(discovered))
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _page_html(filename: str, pages: list[Path]) -> str:
    figures = []
    for index, page in enumerate(pages, start=1):
        encoded = base64.b64encode(page.read_bytes()).decode("ascii")
        figures.append(
            '<figure class="page">'
            f'<img alt="Page {index}" src="data:image/jpeg;base64,{encoded}">'
            f"<figcaption>{index}</figcaption></figure>"
        )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>{escape(filename)}</title><style>
:root{{color-scheme:light;background:#e8ebf1;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;scroll-snap-type:y mandatory;scroll-padding-top:46px}}
*{{box-sizing:border-box}}body{{margin:0}}header{{position:sticky;top:0;z-index:2;padding:9px 14px;border-bottom:1px solid #d9dde5;background:rgba(255,255,255,.94);backdrop-filter:blur(10px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:600}}
main{{display:grid;gap:24px;padding:18px}}.page{{position:relative;width:min(100%,1100px);margin:auto;background:white;scroll-snap-align:start;scroll-snap-stop:always;outline:1px solid #1118271a;box-shadow:0 12px 32px #1720332e}}img{{display:block;width:100%;height:auto}}figcaption{{position:absolute;right:8px;bottom:6px;padding:2px 6px;border-radius:999px;background:#11182799;color:white;font-size:10px}}
</style></head><body><header>{escape(filename)} · {len(pages)} pages</header><main>{"".join(figures)}</main></body></html>"""


__all__ = ["render_office_fidelity_preview"]
