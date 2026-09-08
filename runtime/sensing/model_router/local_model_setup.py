"""Loopback-only Ollama discovery and synthetic capability verification.

No NAS content is read. A successful text canary is required before importing;
tool and image capabilities are enabled only when their own canaries pass.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import threading
import time
import zlib
from typing import Any
from urllib.parse import urlsplit

import httpx

from runtime.safety.privacy import is_loopback_endpoint

_lock = threading.Lock()
_verified: dict[str, dict[str, Any]] = {}
_TTL = 1200


def valid_tag(tag: str) -> bool:
    return (
        bool(re.fullmatch(r"[A-Za-z0-9._/:-]{1,120}", tag or ""))
        and not any(part in {".", ".."} for part in tag.split("/"))
        and "cloud" not in tag.lower()
    )


def base_url() -> str:
    from .managed_ollama import BASE, enabled

    default = BASE if enabled() else "http://127.0.0.1:11434"
    value = os.environ.get("OLLAMA_BASE_URL", default).rstrip("/")
    if not is_loopback_endpoint(value) or urlsplit(value).path:
        raise ValueError("本地模型设置只接受本机 Ollama 根地址")
    return value


def client(*, timeout: float = 4.0, base: str | None = None) -> httpx.Client:
    target = base or base_url()
    if not is_loopback_endpoint(target) or urlsplit(target).path:
        raise ValueError("需要本机 Ollama 地址")
    return httpx.Client(base_url=target, timeout=timeout, trust_env=False, follow_redirects=False)


def request_json(connection: httpx.Client, method: str, path: str, **kwargs: Any) -> dict:
    with connection.stream(method, path, **kwargs) as response:
        if response.status_code != 200:
            raise ValueError(f"本机服务返回 HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError("本机服务响应过大")
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("本机服务返回无效结果")
    return data


def model_catalog(connection: httpx.Client) -> list[dict]:
    data = request_json(connection, "GET", "/api/tags")
    rows = data.get("models")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows
    ):
        raise ValueError("未识别为 Ollama 模型目录")
    return rows


def _model(rows: list[dict], tag: str) -> dict:
    normalized = tag if ":" in tag.rsplit("/", 1)[-1] else f"{tag}:latest"
    for row in rows:
        if row.get("name") in {tag, normalized}:
            if not row.get("digest") or row.get("remote_model") or row.get("remote_host"):
                break
            return row
    raise ValueError("模型尚未安装，或无法确认本机模型版本")


def _red_image() -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 32) * 32))
    return "data:image/png;base64," + base64.b64encode(png + chunk(b"IEND", b"")).decode()


def _chat(connection: httpx.Client, tag: str, content: Any, **extra: Any) -> dict:
    response = request_json(
        connection,
        "POST",
        "/v1/chat/completions",
        json={
            "model": tag,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "max_tokens": 128,
            "temperature": 0,
            **extra,
        },
    )
    return response["choices"][0]["message"]


def _check_memory_budget(connection: httpx.Client, row: dict) -> None:
    from .hwfit import detect_hardware

    size = row.get("size")
    if not isinstance(size, (int, float)) or size <= 0:
        raise ValueError("无法确认模型权重大小，请检查 Ollama 模型信息")
    hardware = detect_hardware()
    if size / (1024**3) + 0.9 <= hardware.vram_gb:
        return
    # Already resident weights do not need to be allocated a second time.
    try:
        running = request_json(connection, "GET", "/api/ps").get("models", [])
        if any(
            isinstance(model, dict) and model.get("digest") == row["digest"] for model in running
        ):
            return
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    raise ValueError("当前可用内存不足或无法估算，请释放资源后重新验证")


def clear_verification(tag: str) -> None:
    with _lock:
        _verified.pop(tag, None)


def verification_states() -> dict[str, dict[str, Any]]:
    with _lock:
        return {
            tag: dict(result)
            for tag, result in _verified.items()
            if time.time() - result["verified_at"] < _TTL
        }


def verify_model(tag: str, *, base: str | None = None) -> dict[str, Any]:
    clear_verification(tag)
    if not valid_tag(tag):
        raise ValueError("无效标签或云端模型，不能作为本机模型启用")
    target = base or base_url()
    with client(timeout=120, base=target) as connection:
        row = _model(model_catalog(connection), tag)
        details = request_json(connection, "POST", "/api/show", json={"model": tag})
        if details.get("remote_model") or details.get("remote_host"):
            raise ValueError("这是经 Ollama 转发的远端模型，不能作为本机模型启用")
        if not details.get("model_info") or details.get("details", {}).get("format") != "gguf":
            raise ValueError("无法确认本机模型权重，请检查 Ollama 模型信息")
        _check_memory_budget(connection, row)
        started = time.monotonic()
        message = _chat(connection, tag, "Reply with exactly ECHO_READY, without explanation.")
        latency = round((time.monotonic() - started) * 1000)
        if "ECHO_READY" not in str(message.get("content", "")):
            raise ValueError("文本验证未通过；模型需要支持普通对话")

        tools_ok = False
        vision_ok = False
        try:
            message = _chat(
                connection,
                tag,
                "Call echo_check with value 7. Do not answer in text.",
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "echo_check",
                            "description": "Synthetic capability check",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
            )
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                args = function.get("arguments")
                args = json.loads(args) if isinstance(args, str) else args
                if function.get("name") == "echo_check" and args == {"value": 7}:
                    tools_ok = True
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            pass
        if "vision" in details.get("capabilities", []):
            try:
                message = _chat(
                    connection,
                    tag,
                    [
                        {
                            "type": "text",
                            "text": "Name the image's single color in one English word.",
                        },
                        {"type": "image_url", "image_url": {"url": _red_image()}},
                    ],
                )
                vision_ok = str(message.get("content", "")).strip().lower().strip(".! ") == "red"
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
                pass
        # Catch a concurrent replacement/removal during the probes.
        if _model(model_catalog(connection), tag)["digest"] != row["digest"]:
            raise ValueError("验证期间模型版本发生变化，请重试")
    result = {
        "tag": tag,
        "base_url": target,
        "digest": row["digest"],
        "supports_tool_use": tools_ok,
        "supports_vision": vision_ok,
        "latency_ms": latency,
        "verified_at": time.time(),
    }
    with _lock:
        if len(_verified) >= 64:
            _verified.pop(next(iter(_verified)))
        _verified[tag] = result
    return dict(result)


def verified_model(tag: str) -> dict[str, Any]:
    result = verification_states().get(tag)
    if not result or result["base_url"] != base_url():
        raise ValueError("请先完成本机验证；结果过期或连接变更后需要重新验证")
    with client() as connection:
        if _model(model_catalog(connection), tag)["digest"] != result["digest"]:
            clear_verification(tag)
            raise ValueError("模型版本已变化，请重新验证")
    return result
