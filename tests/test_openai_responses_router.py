from __future__ import annotations

import json

import httpx

from runtime.sensing.model_router.models import Message, ModelRequest, ToolSpec
from runtime.sensing.model_router.openai_responses_router import (
    OpenAIResponsesModelRouter,
)


def _sse(*events: dict[str, object]) -> bytes:
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def test_responses_provider_streams_with_api_key_and_keeps_it_out_of_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        completed = {
            "id": "resp_zen",
            "object": "response",
            "status": "completed",
            "model": "muse-spark-1.2-contributor-free",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "OK"}],
                }
            ],
            "usage": {"input_tokens": 7, "output_tokens": 1},
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"type": "response.output_text.delta", "delta": "OK"},
                {"type": "response.completed", "response": completed},
            ),
        )

    router = OpenAIResponsesModelRouter(
        base_url="https://opencode.ai/zen/v1",
        api_key="zen-secret",
        default_model="muse-spark-1.2-contributor-free",
        provider_name="opencode_zen",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = router.call(
        ModelRequest(
            model="muse-spark-1.2-contributor-free",
            messages=[Message(role="user", content="Reply OK")],
            tools=[ToolSpec(name="read_file", description="Read a file")],
        )
    )

    assert response.text == "OK"
    assert response.provider == "opencode_zen"
    assert seen["url"] == "https://opencode.ai/zen/v1/responses"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer zen-secret"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "muse-spark-1.2-contributor-free"
    assert payload["tools"][0]["name"] == "read_file"
    assert "zen-secret" not in json.dumps(payload)

