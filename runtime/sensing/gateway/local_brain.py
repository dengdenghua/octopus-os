"""One-glance local-brain readiness for the work-mode setup wizard.

Gives a non-technical user a single plain-language checklist of whether their
LOCAL stack is wired up, so "run everything on my own machine" is a guided
flow instead of tribal knowledge. Five checks:

  1. 本地模型服务 (Ollama)      — a local model server is running
  2. 对话模型 (chat)            — a chat model is pulled
  3. 记忆/检索模型 (embedding)  — one embedding model shared by code index + storage
  4. 文档库 (echo-storage)   — the File Agent data service is up
  5. 代码索引 (code index)      — the workspace has been indexed

Each item reports ``ok`` + a one-line ``what`` (what it is, for a layperson) +
``detail`` (current state) + ``action`` (the exact next step, copy-pasteable).
Probes are injectable so the whole thing is unit-testable with nothing running.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

_DEFAULT_OLLAMA = "http://127.0.0.1:11434"
_CODE_INDEX_DB = Path("data/code_index.db")
# Embedding models are not chat models — used to label Ollama tags.
_EMBED_HINTS = ("embed", "bge", "nomic", "minilm", "gte", "e5")


def ollama_url() -> str:
    return (os.environ.get("ECHO_OLLAMA_URL") or "").strip().rstrip("/") or _DEFAULT_OLLAMA


def probe_ollama() -> dict[str, Any] | None:
    """List local Ollama models via ``/api/tags``; ``None`` when not running."""
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=3.0) as resp:  # noqa: S310 — local model server  # nosec B310 — audited local Ollama endpoint
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _model_names(tags: dict[str, Any] | None) -> list[str]:
    if not isinstance(tags, dict):
        return []
    return [str(m.get("name") or "") for m in (tags.get("models") or []) if isinstance(m, dict)]


def _is_embed(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _EMBED_HINTS)


def local_brain_status(
    *,
    ollama_probe: Callable[[], dict[str, Any] | None] | None = None,
    storage_probe: Callable[[], dict[str, Any] | None] | None = None,
    embed_info: Callable[[], dict[str, Any]] | None = None,
    index_db: Path | None = None,
) -> dict[str, Any]:
    """Build the plain-language readiness checklist. All external lookups are
    injectable (defaults probe the real services)."""
    if ollama_probe is None:
        ollama_probe = probe_ollama
    if storage_probe is None:
        from runtime.execution.suckers.storage_skills import storage_manifest

        storage_probe = storage_manifest
    if embed_info is None:
        from runtime.memory.hemolymph.embedding_backend import backend_info

        embed_info = backend_info
    db = index_db if index_db is not None else _CODE_INDEX_DB

    tags = ollama_probe()
    models = _model_names(tags)
    chat_models = [m for m in models if not _is_embed(m)]
    embed_models = [m for m in models if _is_embed(m)]
    ollama_up = tags is not None

    items: list[dict[str, Any]] = []

    items.append(
        {
            "id": "ollama",
            "label": "本地模型服务",
            "what": "在你电脑上跑模型的小程序(Ollama),所有本地 AI 的发动机。",
            "ok": ollama_up,
            "detail": (f"已运行,装了 {len(models)} 个模型" if ollama_up else "没检测到"),
            "action": (
                ""
                if ollama_up
                else "去 ollama.com 下载安装,装好后它会自动在后台运行(或终端跑 `ollama serve`)。"
            ),
        }
    )

    items.append(
        {
            "id": "chat",
            "label": "对话模型",
            "what": "负责理解你、回答你、写代码的大脑。",
            "ok": ollama_up and bool(chat_models),
            "detail": (", ".join(chat_models[:3]) if chat_models else "还没有"),
            "action": (
                ""
                if (ollama_up and chat_models)
                else "终端运行:`ollama pull qwen2.5`(下载一个对话模型)。"
            ),
        }
    )

    einfo = embed_info()
    embed_remote = einfo.get("kind") == "remote"
    items.append(
        {
            "id": "embedding",
            "label": "记忆/检索模型",
            "what": "把你的代码和文档变成「可被搜索的记忆」,代码库和文件管家共用这一个。",
            "ok": ollama_up and bool(embed_models) and embed_remote,
            "detail": (
                f"已接本地:{einfo.get('model')}"
                if embed_remote
                else f"当前用内置 {einfo.get('model')}(没接到本地服务,代码库/文档库各用各的)"
            ),
            "action": (
                ""
                if (ollama_up and embed_models and embed_remote)
                else (
                    "终端 `ollama pull bge-m3`,然后设两个环境变量统一到本地:"
                    "ECHO_EMBED_URL=http://127.0.0.1:11434/v1 与 ECHO_EMBED_MODEL=bge-m3。"
                )
            ),
        }
    )

    storage = storage_probe()
    storage_up = storage is not None
    items.append(
        {
            "id": "storage",
            "label": "文档库(文件管家)",
            "what": "管你自己的文档/PDF/笔记,问它「我那份合同在哪」就能答。",
            "ok": storage_up,
            "detail": ("已运行" if storage_up else "没运行(不影响代码,只影响文档问答)"),
            "action": (
                ""
                if storage_up
                else (
                    "启动 echo-storage 服务后,在文件管家里加授权文件夹;"
                    "或设 ECHO_STORAGE_AUTOSTART=1,让 echo 启动时自动把它一起拉起来。"
                )
            ),
        }
    )

    indexed = db.exists()
    items.append(
        {
            "id": "code_index",
            "label": "代码索引",
            "what": "给当前项目建一份「可语义搜索」的目录,聊天时自动用上。",
            "ok": indexed,
            "detail": ("已建立" if indexed else "还没建"),
            "action": ("" if indexed else "在工作区的代码索引面板点「开始索引」。"),
        }
    )

    core_ok = all(i["ok"] for i in items if i["id"] in ("ollama", "chat"))
    ready = all(i["ok"] for i in items)
    if ready:
        summary = "全部就绪:你的 echo 完全跑在本地,一分钱云费都不花。"
    elif core_ok:
        summary = "基本能用了(对话已就绪);按下面几步把记忆/文档也接成本地,体验更完整。"
    else:
        summary = "先装好「本地模型服务」和「对话模型」这两步,就能开始用本地大脑。"

    return {
        "ready": ready,
        "core_ready": core_ok,
        "summary": summary,
        "ollama_url": ollama_url(),
        "items": items,
    }
