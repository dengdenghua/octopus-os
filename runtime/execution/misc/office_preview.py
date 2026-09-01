"""Safe, dependency-light HTML previews for OOXML office artifacts."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from runtime.execution.misc.document_text_extractor import extract_document_path

_SUPPORTED = {".csv", ".docx", ".tsv", ".xlsx", ".pptx"}
_MAX_PREVIEW_CHARS = 60_000
_MAX_PREVIEW_FILE_BYTES = 64 * 1024 * 1024


def supports_office_preview(path: Path) -> bool:
    return path.suffix.lower() in _SUPPORTED


def render_office_preview(path: Path, *, script_nonce: str | None = None) -> str | None:
    """Render an OOXML file as inert, escaped HTML suitable for an iframe."""

    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED:
        return None
    try:
        too_large = path.stat().st_size > _MAX_PREVIEW_FILE_BYTES
    except OSError:
        too_large = True
    extracted = None if too_large else extract_document_path(path, max_chars=_MAX_PREVIEW_CHARS)
    text = extracted.text if extracted is not None else ""
    if suffix in {".csv", ".tsv"}:
        body = _render_xlsx(f"--- sheet 1: {path.name} ---\n{text}")
        label = "Spreadsheet"
    elif suffix == ".xlsx":
        body = _render_xlsx(text)
        label = "Spreadsheet"
    elif suffix == ".pptx":
        body = _render_pptx(text)
        label = "Presentation"
    else:
        body = _render_docx(text)
        label = "Document"
    if not text:
        body = '<div class="empty">This file has no extractable text preview.</div>'
    return _page(path.name, label, body, script_nonce=script_nonce)


def _split_sections(text: str, marker: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = ""
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(marker) and line.endswith(" ---"):
            if title or lines:
                sections.append((title, "\n".join(lines)))
            title = line.removeprefix("--- ").removesuffix(" ---")
            lines = []
        else:
            lines.append(line)
    if title or lines:
        sections.append((title, "\n".join(lines)))
    return sections


def _render_docx(text: str) -> str:
    paragraphs = [line for line in text.splitlines() if line.strip()]
    return (
        '<main class="document-page">'
        + "".join(
            f'<p data-office-node="paragraph:{index}" '
            f'data-office-label="Paragraph {index}" tabindex="0">'
            f"{escape(line)}</p>"
            for index, line in enumerate(paragraphs, start=1)
        )
        + "</main>"
    )


def _render_pptx(text: str) -> str:
    slides = _split_sections(text, "--- slide ")
    if not slides and text:
        slides = [("slide 1", text)]
    cards: list[str] = []
    for index, (_title, content) in enumerate(slides, start=1):
        lines = [line for line in content.splitlines() if line.strip()]
        heading = escape(lines[0]) if lines else f"Slide {index}"
        rest = "".join(f"<p>{escape(line)}</p>" for line in lines[1:])
        cards.append(
            f'<section class="slide" data-office-node="slide:{index}" '
            f'data-office-label="Slide {index}" tabindex="0">'
            f'<span class="slide-number">{index}</span><h1>{heading}</h1>{rest}'
            "</section>"
        )
    return '<main class="slides">' + "".join(cards) + "</main>"


def _render_xlsx(text: str) -> str:
    sheets = _split_sections(text, "--- sheet ")
    if not sheets and text:
        sheets = [("sheet 1", text)]
    rendered: list[str] = []
    for index, (title, content) in enumerate(sheets, start=1):
        sheet_label = (
            re.sub(r"^(?:sheet\s+)?\d+:\s*", "", title, flags=re.IGNORECASE).strip()
            or f"Sheet {index}"
        )
        rows: list[tuple[int, list[str]]] = []
        for fallback_row, line in enumerate(
            (line for line in content.splitlines() if line.strip()), start=1
        ):
            values = line.split("\t")
            marker = re.fullmatch(r"Row (\d+)", values[0]) if values else None
            row_number = int(marker.group(1)) if marker else fallback_row
            rows.append((row_number, values[1:] if marker else values))
        width = max((len(values) for _row_number, values in rows), default=0)
        header = "".join(f"<th>{_column_name(i)}</th>" for i in range(width))
        body = []
        for row_number, row in rows:
            cells = "".join(
                f'<td data-office-node="sheet:{index}:cell:{_column_name(i)}{row_number}" '
                f'data-office-label="{escape(sheet_label)} · {_column_name(i)}{row_number}" tabindex="0">'
                f"{escape(row[i]) if i < len(row) else ''}</td>"
                for i in range(width)
            )
            body.append(f"<tr><th>{row_number}</th>{cells}</tr>")
        rendered.append(
            '<section class="sheet">'
            f"<h2>{escape(sheet_label)}</h2>"
            f"<table><thead><tr><th></th>{header}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></section>"
        )
    return '<main class="sheets">' + "".join(rendered) + "</main>"


def _column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


_INSPECT_SCRIPT = r"""(()=>{let enabled=false;let selected=null;
const clear=()=>{if(selected)selected.classList.remove('office-selected');selected=null};
const setEnabled=(next)=>{enabled=next;document.documentElement.classList.toggle('office-inspecting',enabled);if(!enabled)clear();parent.postMessage({type:'echo:office:state',active:enabled},'*')};
const readable=(node)=>{const blocks=[...node.querySelectorAll('h1,h2,h3,p,li')];const sources=blocks.length?blocks:[node];return sources.map((part)=>(part.innerText||part.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean).join(' · ').slice(0,2000)};
const choose=(target)=>{if(!enabled)return;const node=target.closest('[data-office-node]');if(!node)return;clear();selected=node;node.classList.add('office-selected');parent.postMessage({type:'echo:office:select',payload:{node:node.dataset.officeNode||'',label:node.dataset.officeLabel||'',text:readable(node)}},'*');setEnabled(false)};
addEventListener('message',(event)=>{const type=event.data&&event.data.type;if(type==='echo:office:enable')setEnabled(true);if(type==='echo:office:disable')setEnabled(false)});
addEventListener('click',(event)=>choose(event.target));addEventListener('keydown',(event)=>{if(enabled&&(event.key==='Enter'||event.key===' ')){choose(event.target);event.preventDefault()}});parent.postMessage({type:'echo:office:ready'},'*')})();"""


def _page(
    filename: str,
    label: str,
    body: str,
    *,
    script_nonce: str | None,
) -> str:
    csp = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src 'nonce-{script_nonce}'; img-src data:; "
        "base-uri 'none'; form-action 'none'"
        if script_nonce
        else "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; form-action 'none'"
    )
    script = (
        f'<script nonce="{escape(script_nonce, quote=True)}">{_INSPECT_SCRIPT}</script>'
        if script_nonce
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}">
<title>{escape(filename)}</title><style>
:root{{color-scheme:light;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202124;background:#eef0f3}}
*{{box-sizing:border-box}}body{{margin:0}}header{{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid #d9dde5;background:rgba(255,255,255,.96);backdrop-filter:blur(12px)}}
.kind{{border-radius:999px;background:#eef2ff;color:#4f46e5;padding:3px 8px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}}.filename{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:600}}
.document-page{{width:min(816px,calc(100% - 32px));min-height:1056px;margin:24px auto;padding:72px 82px;background:white;box-shadow:0 8px 30px #1720331f}}.document-page p{{margin:0 0 12px;line-height:1.75;white-space:pre-wrap}}
.slides{{display:grid;gap:24px;padding:24px}}.slide{{position:relative;aspect-ratio:16/9;max-width:980px;width:100%;margin:auto;padding:8% 9%;overflow:auto;background:linear-gradient(145deg,#fff,#f6f7fb);border:1px solid #d9dde5;border-radius:8px;box-shadow:0 10px 32px #17203326}}.slide h1{{margin:0 0 5%;font-size:clamp(22px,4vw,44px)}}.slide p{{font-size:clamp(13px,2vw,22px);line-height:1.55}}.slide-number{{position:absolute;right:18px;bottom:14px;color:#8b93a5;font-size:12px}}
.sheets{{padding:18px}}.sheet{{margin:0 0 22px;overflow:auto;border:1px solid #d9dde5;border-radius:8px;background:white}}.sheet h2{{position:sticky;left:0;margin:0;padding:9px 12px;border-bottom:1px solid #d9dde5;background:#f8fafc;font-size:13px}}table{{border-collapse:collapse;min-width:100%;font-size:12px}}th,td{{min-width:90px;padding:7px 9px;border-right:1px solid #e3e6eb;border-bottom:1px solid #e3e6eb;text-align:left;white-space:pre-wrap}}th{{min-width:38px;background:#f6f7f9;color:#697184;font-weight:600}}td{{background:#fff}}.empty{{margin:48px auto;padding:24px;max-width:520px;border:1px dashed #c6cad2;border-radius:12px;background:white;color:#697184;text-align:center}}
.office-inspecting [data-office-node]{{cursor:crosshair;outline:1px dashed transparent;outline-offset:2px}}.office-inspecting [data-office-node]:hover{{outline-color:#6366f1;background-color:#eef2ff!important}}.office-selected{{outline:2px solid #6366f1!important;outline-offset:2px}}
</style></head><body><header><span class="kind">{escape(label)}</span><span class="filename">{escape(filename)}</span></header>{body}{script}</body></html>"""


__all__ = ["render_office_preview", "supports_office_preview"]
