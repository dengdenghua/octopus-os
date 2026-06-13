
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from .browser_skills import register_browser_skills
from .crawler_skills import register_crawler_skills
from .fs_search_skills import register_fs_search_skills
from .market_skills import register_market_skills
from .notebook_skills import register_notebook_skills
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase
from .web_skills import register_web_skills
from .write_skills import (
    register_code_quality_skills,
    register_git_network_skills,
    register_git_skills,
    register_write_skills,
)

MAX_READ_BYTES = 100 * 1024  # 100KB hard cap
MAX_READ_LINES_WITHOUT_RANGE = 2000

# Multimodal dispatch · 2026-05-30 · screenshots / PDFs / notebooks no longer
# fall through the text path and explode on UnicodeDecodeError.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_PDF_DEFAULT_MAX_PAGES_WITHOUT_RANGE = 10
# 25 MB cap on image / PDF bytes — these bypass MAX_READ_BYTES (which is the
# 100 KB text cap) but still need an upper bound to keep the wire reasonable.
_BINARY_READ_CAP = 25 * 1024 * 1024


def _parse_pages_spec(spec: str, total_pages: int) -> tuple[list[int] | None, str | None]:
    """Parse ``"1-5"`` / ``"3"`` / ``"1-3,7,10-12"`` to a sorted, deduped, 1-indexed list.

    Returns ``(pages, None)`` on success, ``(None, error_message)`` otherwise.
    Pages outside ``1..total_pages`` are an error so callers can't silently miss content.
    """
    if not isinstance(spec, str) or not spec.strip():
        return None, "pages spec must be a non-empty string"
    out: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                return None, f"invalid pages range: {part!r}"
            if lo < 1 or hi < lo:
                return None, f"invalid pages range: {part!r}"
            for n in range(lo, hi + 1):
                out.add(n)
        else:
            try:
                n = int(part)
            except ValueError:
                return None, f"invalid pages token: {part!r}"
            if n < 1:
                return None, f"invalid pages token: {part!r}"
            out.add(n)
    if not out:
        return None, "pages spec resolved to no pages"
    bad = [n for n in out if n > total_pages]
    if bad:
        return None, f"page out of range (total={total_pages}): {sorted(bad)}"
    return sorted(out), None


def _read_file_image(p: Path) -> dict[str, Any]:
    """Return a structured image record (base64 payload + media type)."""
    size = p.stat().st_size
    if size > _BINARY_READ_CAP:
        return {
            "error": f"image_too_large: {size} bytes (cap {_BINARY_READ_CAP})",
            "error_type": "too_large",
            "path": str(p.resolve()),
            "size_bytes": size,
        }
    media_type = _IMAGE_MEDIA_TYPES.get(p.suffix.lower(), "application/octet-stream")
    raw = p.read_bytes()
    return {
        "ok": True,
        "kind": "image",
        "path": str(p.resolve()),
        "media_type": media_type,
        "data_base64": base64.standard_b64encode(raw).decode("ascii"),
        "size_bytes": size,
    }


def _read_file_pdf(
    p: Path,
    *,
    pages: str | None = None,
    max_pages_without_range: int = _PDF_DEFAULT_MAX_PAGES_WITHOUT_RANGE,
) -> dict[str, Any]:
    """Extract PDF text via pdfplumber, falling back to pypdf.

    Returns a refusal when ``pages`` is omitted and the PDF has more than
    ``max_pages_without_range`` pages.
    """
    size = p.stat().st_size
    if size > _BINARY_READ_CAP:
        return {
            "error": f"pdf_too_large: {size} bytes (cap {_BINARY_READ_CAP})",
            "error_type": "too_large",
            "path": str(p.resolve()),
            "size_bytes": size,
        }

    backend: str
    page_texts: list[tuple[int, str]] = []
    total_pages = 0
    try:
        import pdfplumber  # type: ignore[import-not-found]
        backend = "pdfplumber"
    except ImportError:
        pdfplumber = None  # type: ignore[assignment]
    if pdfplumber is None:
        try:
            import pypdf  # type: ignore[import-not-found]
            backend = "pypdf"
        except ImportError:
            return {
                "error": "no PDF backend available — install pdfplumber or pypdf",
                "error_type": "dependency_missing",
                "path": str(p.resolve()),
                "hint": "pip install pdfplumber  (or)  pip install pypdf",
            }

    try:
        if backend == "pdfplumber":
            with pdfplumber.open(str(p)) as pdf:
                total_pages = len(pdf.pages)
                if pages is None:
                    if total_pages > max_pages_without_range:
                        return {
                            "error": "pdf_too_large_without_pages",
                            "error_type": "invalid_argument",
                            "path": str(p.resolve()),
                            "total_pages": total_pages,
                            "hint": (
                                f"Pass pages='1-{max_pages_without_range}' to read a slice."
                            ),
                        }
                    wanted = list(range(1, total_pages + 1))
                else:
                    parsed, err = _parse_pages_spec(pages, total_pages)
                    if err:
                        return {
                            "error": err,
                            "error_type": "invalid_argument",
                            "path": str(p.resolve()),
                            "total_pages": total_pages,
                        }
                    wanted = parsed or []
                for n in wanted:
                    page = pdf.pages[n - 1]
                    page_texts.append((n, page.extract_text() or ""))
        else:  # pypdf
            import pypdf  # type: ignore[import-not-found]
            reader = pypdf.PdfReader(str(p))
            total_pages = len(reader.pages)
            if pages is None:
                if total_pages > max_pages_without_range:
                    return {
                        "error": "pdf_too_large_without_pages",
                        "error_type": "invalid_argument",
                        "path": str(p.resolve()),
                        "total_pages": total_pages,
                        "hint": (
                            f"Pass pages='1-{max_pages_without_range}' to read a slice."
                        ),
                    }
                wanted = list(range(1, total_pages + 1))
            else:
                parsed, err = _parse_pages_spec(pages, total_pages)
                if err:
                    return {
                        "error": err,
                        "error_type": "invalid_argument",
                        "path": str(p.resolve()),
                        "total_pages": total_pages,
                    }
                wanted = parsed or []
            for n in wanted:
                page_obj = reader.pages[n - 1]
                page_texts.append((n, page_obj.extract_text() or ""))
    except (OSError, ValueError) as exc:
        return {
            "error": f"pdf_parse_failed: {exc}",
            "error_type": "parse_failed",
            "path": str(p.resolve()),
            "backend": backend,
        }

    joined = "\n\n".join(
        f"--- page {n} ---\n{txt}" for n, txt in page_texts
    )
    return {
        "ok": True,
        "kind": "pdf",
        "path": str(p.resolve()),
        "pages_extracted": [n for n, _ in page_texts],
        "total_pages": total_pages,
        "text": joined,
        "backend": backend,
    }


def _read_file_notebook(
    p: Path,
    *,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
) -> dict[str, Any]:
    """Delegate to ``notebook_skills._notebook_read`` so .ipynb stays in one place."""
    from .notebook_skills import _notebook_read

    return _notebook_read(
        path=str(p),
        sandbox_dir=sandbox_dir,
        allow_sensitive=allow_sensitive,
    )




def _list_cwd(path: str = ".", cwd: str | None = None, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if cwd and not p.is_absolute():
        p = Path(cwd) / p
    if not p.exists():
        return {"error": f"not found: {path}"}
    if not p.is_dir():
        return {"error": f"not a directory: {path}"}
    items = sorted(
        (
            {
                "name": e.name,
                "is_dir": e.is_dir(),
                "size": e.stat().st_size if e.is_file() else None,
            }
            for e in p.iterdir()
            if not e.name.startswith(".")
        ),
        key=lambda x: x["name"],
    )
    return {"path": str(p.resolve()), "items": items, "count": len(items)}


def _read_file_text(
    p: Path,
    *,
    max_bytes: int = MAX_READ_BYTES,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """Original UTF-8 text reader — preserved verbatim, just under a new name."""
    if offset < 0:
        return {"error": "offset must be >= 0"}
    if limit is not None and limit < 0:
        return {"error": "limit must be >= 0"}
    size = p.stat().st_size

    if offset or limit is not None:
        collected: list[str] = []
        line_count = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    line_count = idx + 1
                    if idx < offset:
                        continue
                    if limit is not None and len(collected) >= limit:
                        break
                    collected.append(line)
        except UnicodeDecodeError:
            return {"error": "non-utf8 content", "size": size}
        except OSError as exc:
            return {"error": f"read_failed: {exc}", "size": size}
        truncated = bool(limit is not None and line_count > offset + limit)
        return {
            "path": str(p.resolve()),
            "size": size,
            "truncated": truncated,
            "content": "".join(collected),
            "offset": offset,
            "limit": limit,
            "lines_read": len(collected),
        }

    if size > max_bytes:
        try:
            with p.open("r", encoding="utf-8") as f:
                for line_idx, _line in enumerate(f, start=1):
                    if line_idx > MAX_READ_LINES_WITHOUT_RANGE:
                        return {
                            "error": (
                                "file has more than 2000 lines; call read_file "
                                "again with offset and limit"
                            ),
                            "path": str(p.resolve()),
                            "size": size,
                            "line_limit": MAX_READ_LINES_WITHOUT_RANGE,
                        }
        except UnicodeDecodeError:
            return {"error": "non-utf8 content", "size": size}
        except OSError:
            pass

    to_read = min(size, max_bytes)
    content = p.read_bytes()[:to_read]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {"error": "non-utf8 content", "size": size}
    return {
        "path": str(p.resolve()),
        "size": size,
        "truncated": size > max_bytes,
        "content": text,
    }


def _read_file(
    path: str,
    max_bytes: int = MAX_READ_BYTES,
    offset: int = 0,
    limit: int | None = None,
    *,
    pages: str | None = None,
    cwd: str | None = None,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    """Read a file, dispatching to the right handler by extension.

    Supported kinds:
    - text → ``_read_file_text`` (default; honours ``offset`` / ``limit`` / ``max_bytes``)
    - image (.png / .jpg / .jpeg / .webp / .gif) → ``_read_file_image``
    - .pdf → ``_read_file_pdf`` (requires ``pages`` if total > 10)
    - .ipynb → ``_read_file_notebook`` (delegates to notebook_skills)
    """
    if cwd and not Path(path).is_absolute():
        path = str(Path(cwd) / path)
    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(
        path, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive,
    )
    if not verdict.allow:
        return {"error": f"path_blocked: {verdict.reason}", "path": path}

    p = Path(verdict.resolved) if verdict.resolved else Path(path)
    if not p.exists():
        return {"error": f"not found: {path}"}
    if not p.is_file():
        return {"error": f"not a file: {path}"}

    suffix = p.suffix.lower()
    if suffix in _IMAGE_EXTS:
        return _read_file_image(p)
    if suffix == ".pdf":
        return _read_file_pdf(p, pages=pages)
    if suffix == ".ipynb":
        return _read_file_notebook(
            p, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive,
        )
    return _read_file_text(p, max_bytes=max_bytes, offset=offset, limit=limit)


def _count_words(text: str = "", **_kw: Any) -> dict[str, Any]:
    words = text.split()
    return {
        "chars": len(text),
        "words": len(words),
        "lines": len(text.splitlines()),
    }


def _hash_text(text: str = "", algorithm: str = "blake2b", **_kw: Any) -> dict[str, Any]:
    if algorithm == "blake2b":
        h = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
    elif algorithm == "sha256":
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        return {"error": f"unknown algorithm: {algorithm}"}
    return {"algorithm": algorithm, "hash": h, "length": len(text)}


def _file_stats(
    path: str,
    *,
    cwd: str | None = None,
    sandbox_dir: str | None = None,
    allow_sensitive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    if cwd and not Path(path).is_absolute():
        path = str(Path(cwd) / path)
    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(
        path, sandbox_dir=sandbox_dir, allow_sensitive=allow_sensitive,
    )
    if not verdict.allow:
        return {"error": f"path_blocked: {verdict.reason}", "path": path}
    p = Path(verdict.resolved) if verdict.resolved else Path(path)
    if not p.exists():
        return {"error": f"not found: {path}"}
    st = p.stat()
    return {
        "path": str(p.resolve()),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
    }




def register_builtins(registry: SkillRegistry) -> SkillRegistry:
    registry.register(
        Skill(
            name="list_cwd",
            description=(
                "用途: 非递归列出一个目录的直接子项（隐藏项已过滤）；快速浏览结构。\n"
                "何时不用: 需要多层深度用 tree；按 glob 过滤用 glob_files；要文件元数据用 file_stats；按内容找用 grep_text。\n"
                "关键参数: path (默认 \".\"); cwd (相对路径基准, 可选)。\n"
                "示例: list_cwd({\"path\": \"runtime/execution/suckers\"})"
            ),
            affinity=["file", "io"],
            cost_profile="low",
            trusted_source="skill://public/list_cwd",
            handler=_list_cwd,
            tests=[
                SkillTestCase(
                    name="returns_items_and_count_keys",
                    tier="golden",
                    args={"path": "."},
                    expect=SkillExpect(schema_keys=["items", "count", "path"]),
                ),
                SkillTestCase(
                    name="missing_path_returns_error",
                    tier="golden",
                    args={"path": "/definitely/does/not/exist/xyz"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="read_file",
            description=(
                "用途: 读取单个文件，按扩展名自动分发: 文本 (UTF-8, 默认 100KB / 2000 行硬上限) / 图片 (.png/.jpg/.jpeg/.webp/.gif → base64) / PDF (.pdf, 需要 pages 切片当 >10 页) / Notebook (.ipynb → 委托给 notebook_read)。\n"
                "何时不用: 想找某段内容用 grep_text；只看前 N 行或某个范围用 read_file_range；按 pattern 找文件用 glob_files；其他二进制 / 非 UTF-8 内容会拒读。\n"
                "关键参数: path (必填); offset/limit (文本按行切片); max_bytes (文本默认 102400); pages (PDF 切片 \"1-5\" / \"3\" / \"1-3,7,10-12\")。\n"
                "示例: read_file({\"path\": \"runtime/execution/suckers/builtins.py\", \"offset\": 0, \"limit\": 100}) · read_file({\"path\": \"doc.pdf\", \"pages\": \"1-5\"}) · read_file({\"path\": \"shot.png\"})"
            ),
            affinity=["file", "io"],
            cost_profile="low",
            trusted_source="skill://public/read_file",
            handler=_read_file,
            tests=[
                SkillTestCase(
                    name="missing_file_returns_error",
                    tier="golden",
                    args={"path": "/definitely/does/not/exist/xyz.txt"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="count_words",
            description=(
                "用途: 对一段已经在手上的文本计算 chars / words / lines 三项；常用于 demo / 兜底统计。\n"
                "何时不用: 要统计文件用 read_file 取出后再传入；要分词或自然语言分析用 ipython 跑专用库；只是想核对文件大小用 file_stats。\n"
                "关键参数: text (必填, 直接传字符串)。\n"
                "示例: count_words({\"text\": \"hello world\"})"
            ),
            affinity=["text", "demo"],
            cost_profile="low",
            trusted_source="skill://public/count_words",
            handler=_count_words,
            tests=[
                SkillTestCase(
                    name="empty_text_all_zeros",
                    tier="golden",
                    args={"text": ""},
                    expect=SkillExpect(
                        output_equals={"chars": 0, "words": 0, "lines": 0}
                    ),
                ),
                SkillTestCase(
                    name="two_words_one_line",
                    tier="golden",
                    args={"text": "hello world"},
                    expect=SkillExpect(
                        output_equals={"chars": 11, "words": 2, "lines": 1}
                    ),
                ),
                SkillTestCase(
                    name="multiline_words",
                    tier="golden",
                    args={"text": "a b c\nd e\nf"},
                    expect=SkillExpect(schema_keys=["chars", "words", "lines"]),
                    custom_predicate=lambda out: out["words"] == 6 and out["lines"] == 3,
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="hash_text",
            description=(
                "用途: 对一段文本计算 blake2b (16 字节) 或 sha256 摘要；用于校验、去重 key、签名前缀等。\n"
                "何时不用: 要校验文件内容用 read_file 取出后再哈希；需要 HMAC / 密钥派生用 ipython 跑 hashlib/hmac；想加密 (而非摘要) 不要用本工具。\n"
                "关键参数: text (必填); algorithm (\"blake2b\" 默认 / \"sha256\")。\n"
                "示例: hash_text({\"text\": \"abc\", \"algorithm\": \"sha256\"})"
            ),
            affinity=["crypto", "demo"],
            cost_profile="low",
            trusted_source="skill://public/hash_text",
            handler=_hash_text,
            tests=[
                SkillTestCase(
                    name="blake2b_default",
                    tier="golden",
                    args={"text": "abc"},
                    expect=SkillExpect(schema_keys=["algorithm", "hash", "length"]),
                    custom_predicate=lambda out: (
                        out["algorithm"] == "blake2b"
                        and len(out["hash"]) == 32
                        and out["length"] == 3
                    ),
                ),
                SkillTestCase(
                    name="sha256_explicit",
                    tier="golden",
                    args={"text": "abc", "algorithm": "sha256"},
                    expect=SkillExpect(
                        output_contains=["sha256"],
                        schema_keys=["hash"],
                    ),
                ),
                SkillTestCase(
                    name="unknown_algo_returns_error",
                    tier="golden",
                    args={"text": "x", "algorithm": "nopesuch"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    registry.register(
        Skill(
            name="file_stats",
            description=(
                "用途: 不读内容，只取一个文件/目录的元数据 (size, mtime, is_file, is_dir)；判断存在性、大小、最近修改时间。\n"
                "何时不用: 要看内容用 read_file；要看目录里有什么用 list_cwd 或 tree；要批量按 pattern 找用 glob_files。\n"
                "关键参数: path (必填); cwd (相对路径基准, 可选)。\n"
                "示例: file_stats({\"path\": \"README.md\"})"
            ),
            affinity=["file", "io"],
            cost_profile="low",
            trusted_source="skill://public/file_stats",
            handler=_file_stats,
            tests=[
                SkillTestCase(
                    name="missing_path_returns_error",
                    tier="golden",
                    args={"path": "/definitely/does/not/exist/zzz"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        )
    )
    return registry


BUILTIN_NAMES = ["list_cwd", "read_file", "count_words", "hash_text", "file_stats"]


def register_all(registry: SkillRegistry) -> int:
    register_builtins(registry)
    web_count = register_web_skills(registry)
    crawler_count = register_crawler_skills(registry)
    browser_count = register_browser_skills(registry)
    write_count = register_write_skills(registry)
    git_count = register_git_skills(registry)
    git_network_count = register_git_network_skills(registry)
    # File-system search & notebook skills · filling gaps in the
    # existing skill set: Glob / Grep (non-code) / tree / read range / ipynb.
    fs_search_count = register_fs_search_skills(registry)
    notebook_count = register_notebook_skills(registry)
    # Prompt-as-skill catalog · `all_skills/<name>/SKILL.md`
    # (64 entries · pdf / xlsx / pptx / copywriting /
    # 1688-* / shopify-* / etc) were previously unreachable — md_loader
    # only accepts `handler:` fields. register_market_skills walks the
    # directory and wraps each SKILL.md as a prompt-returning handler
    # so agents can actually discover and invoke them.
    market_count = register_market_skills(registry)
    # AST-aware code editing · tree-sitter powered · 2026-04-26
    from .code_edit_skills import register_code_edit_skills
    code_edit_count = register_code_edit_skills(registry)
    # LSP-based code intelligence · 2026-04-26
    from .lsp_skills import register_lsp_skills
    lsp_count = register_lsp_skills(registry)
    # Code quality · lint / test / format
    quality_count = register_code_quality_skills(registry)
    # 扩展点:消费者经 OCTOPUS_SKILL_EXTENSIONS 注册自定义技能,无需 fork agent。
    # octopus-os 的企业版 PM 工具已改由此加载(OCTOPUS_SKILL_EXTENSIONS=
    # appliance.pm_skills:register_pm_skills),builtins.py 与母体重新对齐。
    from runtime.platform.extensions import load_skill_extensions

    extension_count = load_skill_extensions(registry)
    return (
        len(BUILTIN_NAMES)
        + web_count
        + crawler_count
        + browser_count
        + write_count
        + git_count
        + git_network_count
        + fs_search_count
        + notebook_count
        + market_count
        + code_edit_count
        + lsp_count
        + quality_count
        + extension_count
    )
