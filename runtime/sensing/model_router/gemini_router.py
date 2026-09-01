# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import os
from typing import Any

from runtime.adapters.instrumentation import record_gen_ai_cost, trace_stage
from runtime.platform.models import CostEntry

from .models import (
    DEFAULT_USER_AGENT,
    LLMResponseFormatError,
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
)

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_DEFAULT_INPUT_USD_PER_TOKEN = 1.25e-7
_DEFAULT_OUTPUT_USD_PER_TOKEN = 5e-7


class GeminiRouterError(LLMResponseFormatError):
    pass


from .provider import Provider, ProviderCapabilities


class GeminiModelRouter(Provider, ModelRouter):
    provider_name = "gemini"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=False,  # Gemini has explicit cache API but not auto
        supports_structured_output=True,
        default_model="gemini-2.5-flash",
        pricing_hint="low",
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = "gemini-2.5-flash",
        base_url: str = DEFAULT_API_BASE,
        env_var_name: str = "GEMINI_API_KEY",
        timeout_seconds: float = 60.0,
        pricing_per_1k: dict[str, tuple[float, float]] | None = None,
        extra_headers: dict[str, str] | None = None,
        client: Any = None,
    ) -> None:
        if not HTTPX_AVAILABLE:
            raise GeminiRouterError(
                "httpx not installed · `pip install httpx` (or install extras: '.[web]')",
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get(env_var_name, "")
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.pricing_per_1k = pricing_per_1k or {}
        self.extra_headers = dict(extra_headers or {})
        self._client = client

    def call(self, request: ModelRequest) -> ModelResponse:
        model = request.model or self.default_model

        with trace_stage(
            "eyes.gemini_router.call",
            **{"echo.model": model, "echo.provider": "gemini"},
        ) as span:
            payload = self._build_payload(request)
            url = f"{self.base_url}/models/{model}:generateContent"
            params = {"key": self.api_key} if self.api_key else None

            client = (
                self._client
                if self._client is not None
                else httpx.Client(timeout=self.timeout_seconds)
            )
            try:
                resp = client.post(
                    url,
                    json=payload,
                    params=params,
                    headers=self._build_headers(),
                )
            except Exception as e:  # noqa: BLE001
                raise GeminiRouterError(
                    f"http_error: {type(e).__name__}: {e}",
                ) from e
            finally:
                if self._client is None:
                    client.close()

            status = getattr(resp, "status_code", 200)
            if status >= 400:
                body = getattr(resp, "text", "")[:500]
                raise GeminiRouterError(f"http_{status}: {body}")

            try:
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                raise GeminiRouterError(f"invalid_json: {e}") from e

            text, finish_reason = self._extract_text(data)
            tool_calls = self._extract_tool_calls(data)
            usage = data.get("usageMetadata") or {}
            input_tokens = int(usage.get("promptTokenCount", 0) or 0)
            output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
            cost_usd = self._estimate_cost(model, input_tokens, output_tokens)

            cost = CostEntry(
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                usd=cost_usd,
            )
            record_gen_ai_cost(
                span,
                system="gemini",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usd=cost_usd,
            )

            return ModelResponse(
                text=text,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                finish_reason=finish_reason,
                model=model,
                provider="gemini",
            )

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        system_parts, contents = _split_system_and_contents(request.messages)

        if request.images_b64 and contents:
            _attach_images_to_last_user_gemini(contents, request.images_b64)

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}],
            }
        # Native function calling · Gemini's shape is different
        # from OpenAI/Anthropic: a single ``tools`` entry with a
        # ``functionDeclarations`` array of tool specs. The
        # ``parameters`` field follows a subset of JSON Schema;
        # our permissive ``{type:"object"}`` fits fine.
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        }
                        for t in request.tools
                    ],
                }
            ]
            # toolConfig.functionCallingConfig.mode = "AUTO" is the
            # default when tools are present; we set it explicitly
            # to match Anthropic's ``tool_choice=auto`` semantics.
            # ``ANY`` is Gemini's spelling of "must call some function" —
            # the agentic loop asks for it after a prose-only round so a
            # text-only reply is not an available decode. See
            # ``_LoopState.zero_action_rounds``.
            payload["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "ANY" if request.require_tool_use else "AUTO",
                },
            }
        return payload

    def _build_headers(self) -> dict[str, str]:
        # See models.DEFAULT_USER_AGENT: a default library UA gets
        # rejected by bot-protection layers in front of some relays.
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        headers.update(self.extra_headers)
        return headers

    def _extract_text(self, data: dict[str, Any]) -> tuple[str, str]:
        candidates = data.get("candidates") or []
        if not candidates:
            raise GeminiRouterError(
                f"no candidates in response · keys={list(data.keys())}",
            )
        first = candidates[0]
        if not isinstance(first, dict):
            raise GeminiRouterError("candidate[0] not a dict")

        finish = str(first.get("finishReason", "stop")).lower()
        if finish == "max_tokens":
            finish = "length"
        content = first.get("content") or {}
        parts = content.get("parts") or []
        texts: list[str] = []
        for p in parts:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str) and t:
                    texts.append(t)
        return "".join(texts), finish

    def _extract_tool_calls(self, data: dict[str, Any]) -> list[Any]:
        """Gemini function calls live in ``candidates[0].content.parts``
        as ``{"functionCall": {"name": "...", "args": {...}}}``
        entries. Gemini doesn't assign a stable id per call (unlike
        OpenAI / Anthropic), so we synthesize ``gemfn_<name>_<idx>``
        · sufficient for echoing back in the next-turn
        ``functionResponse`` block."""
        from .models import ToolCall

        candidates = data.get("candidates") or []
        if not candidates:
            return []
        first = candidates[0]
        if not isinstance(first, dict):
            return []
        parts = (first.get("content") or {}).get("parts") or []
        out: list[ToolCall] = []
        for idx, p in enumerate(parts):
            if not isinstance(p, dict):
                continue
            fn = p.get("functionCall")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name") or ""
            args = fn.get("args") or {}
            out.append(
                ToolCall(
                    id=f"gemfn_{name}_{idx}",
                    name=str(name),
                    input=args if isinstance(args, dict) else {},
                )
            )
        return out

    def _estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        pricing = self.pricing_per_1k.get(model)
        if pricing is not None:
            in_usd, out_usd = pricing
            return (input_tokens / 1000) * in_usd + (output_tokens / 1000) * out_usd
        return (
            input_tokens * _DEFAULT_INPUT_USD_PER_TOKEN
            + output_tokens * _DEFAULT_OUTPUT_USD_PER_TOKEN
        )


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _split_system_and_contents(
    messages: list[Message],
) -> tuple[list[str], list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            sys_text = m.content if isinstance(m.content, str) else _flatten_text_blocks(m.content)
            system_parts.append(sys_text)
            continue

        role = "model" if m.role == "assistant" else "user"

        # Plain-string fast path.
        if isinstance(m.content, str):
            contents.append(
                {
                    "role": role,
                    "parts": [{"text": m.content}],
                }
            )
            continue

        # Block-list content · translate per-block to Gemini parts.
        parts: list[dict[str, Any]] = []
        for b in m.content if isinstance(m.content, list) else []:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                txt = str(b.get("text", ""))
                if txt:
                    parts.append({"text": txt})
            elif btype == "tool_use":
                # Assistant calling a function.
                parts.append(
                    {
                        "functionCall": {
                            "name": b.get("name") or "",
                            "args": b.get("input") or {},
                        },
                    }
                )
            elif btype in ("image_url", "image"):
                # User uploads arrive as OpenAI-shaped ``image_url`` blocks
                # (built by ``_react_context_attachments``). Without this
                # branch they matched no case and were dropped, so an
                # uploaded picture never reached Gemini at all.
                part = _image_block_to_gemini_part(b)
                if part is not None:
                    parts.append(part)
            elif btype == "tool_result":
                # User returning function result. Gemini wants
                # ``response`` as a JSON-serializable object, so
                # stringify the content if it's not already dict.
                raw = b.get("content")
                if isinstance(raw, str):
                    response_obj: Any = {"result": raw}
                elif isinstance(raw, dict):
                    response_obj = raw
                else:
                    response_obj = {"result": str(raw)}
                parts.append(
                    {
                        "functionResponse": {
                            # Gemini identifies by name, not by id;
                            # our synthesized ``gemfn_<name>_<idx>``
                            # id from ``_extract_tool_calls`` encodes
                            # the name so we peel it back out here.
                            "name": (
                                (b.get("tool_use_id") or "")
                                .removeprefix("gemfn_")
                                .rsplit("_", 1)[0]
                            )
                            or "unknown",
                            "response": response_obj,
                        },
                    }
                )
        if not parts:
            parts = [{"text": ""}]
        contents.append({"role": role, "parts": parts})
    return system_parts, contents


def _image_block_to_gemini_part(block: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one image block to a Gemini ``inlineData`` / ``fileData`` part.

    Handles the OpenAI ``image_url`` shape we build internally and the
    Anthropic ``image``/``source`` variant. Remote https URLs become
    ``fileData``; data URLs and raw base64 become ``inlineData``. Returns
    ``None`` for a block with no usable reference.
    """
    url: str | None = None
    media_type = "image/png"
    if block.get("type") == "image_url":
        raw = block.get("image_url")
        candidate = raw.get("url") if isinstance(raw, dict) else raw
        if isinstance(candidate, str) and candidate:
            url = candidate
    else:
        source = block.get("source")
        if not isinstance(source, dict):
            return None
        if source.get("type") == "url":
            candidate = source.get("url")
            if isinstance(candidate, str) and candidate:
                url = candidate
        else:
            data = source.get("data")
            if not isinstance(data, str) or not data:
                return None
            return {
                "inlineData": {
                    "mimeType": str(source.get("media_type") or media_type),
                    "data": data,
                },
            }
    if not url:
        return None
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        if not data:
            return None
        media_type = header[len("data:") :].split(";", 1)[0] or media_type
        return {"inlineData": {"mimeType": media_type, "data": data}}
    return {"fileData": {"mimeType": media_type, "fileUri": url}}


def _flatten_text_blocks(content: Any) -> str:
    """Helper · extract concatenated text from a block list."""
    if not isinstance(content, list):
        return ""
    return "".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def _attach_images_to_last_user_gemini(
    contents: list[dict[str, Any]],
    images_b64: list[str],
) -> None:
    for i in range(len(contents) - 1, -1, -1):
        if contents[i].get("role") == "user":
            existing_parts = list(contents[i].get("parts") or [])
            for b64 in images_b64:
                existing_parts.append(
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": b64,
                        },
                    }
                )
            contents[i]["parts"] = existing_parts
            return
