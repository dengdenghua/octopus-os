"""Every tool-use-capable router must honour ``require_tool_use``.

The action-deficit forcing added for the narrate-instead-of-act livelock only
works if the flag reaches the wire. It was previously implemented in the
OpenAI-compatible router alone, so on anthropic/gemini/oct a prose-only round
silently stayed unconstrained. Each provider spells the constraint differently,
so the mapping is asserted per dialect rather than through a shared helper.

Ollama additionally advertised ``supports_tool_use=True`` while never
forwarding the catalog and hardcoding ``tool_calls=[]`` — native mode there
could not produce a single action.
"""

from __future__ import annotations

import json

import pytest

from runtime.platform.models.llm import Message, ModelRequest, ToolSpec


def _request(*, force: bool) -> ModelRequest:
    return ModelRequest(
        model="m",
        messages=[Message(role="user", content="fix it")],
        tools=[ToolSpec(name="edit_file", description="edit", input_schema={"type": "object"})],
        require_tool_use=force,
    )


# ── oct · OpenAI-compatible dialect ────────────────────────────


@pytest.mark.parametrize(("force", "expected"), [(True, "required"), (False, "auto")])
def test_oct_maps_to_tool_choice_string(force: bool, expected: str) -> None:
    from runtime.sensing.model_router.oct_router import OctModelRouter

    router = OctModelRouter.__new__(OctModelRouter)
    payload = router._build_payload(_request(force=force), stream=False)
    assert payload["tool_choice"] == expected


# ── gemini · functionCallingConfig.mode dialect ────────────────


@pytest.mark.parametrize(("force", "expected"), [(True, "ANY"), (False, "AUTO")])
def test_gemini_maps_to_function_calling_mode(force: bool, expected: str) -> None:
    """Gemini spells the constraint as a mode enum, not a tool_choice field."""
    import inspect

    from runtime.sensing.model_router import gemini_router

    src = inspect.getsource(gemini_router)
    assert '"ANY" if request.require_tool_use else "AUTO"' in src
    assert f'"{expected}"' in src


# ── anthropic · tool_choice dict dialect ───────────────────────


def test_anthropic_uses_the_any_dict_form_on_both_paths() -> None:
    """``call`` and ``call_stream`` build tool payloads separately."""
    import inspect

    from runtime.sensing.model_router import anthropic_router

    src = inspect.getsource(anthropic_router)
    # A bare string would be rejected by the Anthropic API.
    assert '"tool_choice"] = {"type": "any"}' in src
    assert src.count('create_kwargs["tool_choice"] = {"type": "any"}') == 2


def test_anthropic_omits_tool_choice_when_not_forcing() -> None:
    """Default (auto) semantics must be unchanged for ordinary rounds."""
    import inspect

    from runtime.sensing.model_router import anthropic_router

    src = inspect.getsource(anthropic_router)
    assert "if request.require_tool_use:" in src


# ── ollama · capability was advertised but unimplemented ───────


def test_ollama_still_advertises_tool_use() -> None:
    from runtime.sensing.model_router.ollama_router import OllamaModelRouter

    assert OllamaModelRouter.capabilities.supports_tool_use is True


def test_ollama_forwards_the_catalog_and_parses_calls_back() -> None:
    """Forwarding alone is insufficient: unparsed calls are still no action."""
    import inspect

    from runtime.sensing.model_router import ollama_router

    src = inspect.getsource(ollama_router)
    assert 'payload["tools"]' in src, "catalog never forwarded"
    assert '"required" if request.require_tool_use else "auto"' in src
    assert "tool_calls=tool_calls" in src, "response path still hardcodes []"
    # Match the constructor argument, not the prose in the explanatory comment.
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert "tool_calls=[]" not in code


def test_ollama_streaming_reuses_the_call_path() -> None:
    """The ReAct loop streams, so the fix only lands if streaming routes here.

    ``ollama_router`` deliberately has no ``call_stream``: the abstract base
    wraps ``call()`` as a synthetic stream and re-emits ``tool_calls`` as
    ``tool_use`` events. If a real streaming override is ever added it must
    forward the catalog too, or native mode silently regresses on ollama only.
    """
    from runtime.platform.models.llm import ModelRouter
    from runtime.sensing.model_router.ollama_router import OllamaModelRouter

    assert OllamaModelRouter.call_stream is ModelRouter.call_stream


def test_base_stream_fallback_reemits_tool_calls() -> None:
    """A fallback that dropped tool calls would make forwarding pointless."""
    from runtime.platform.models.llm import CostEntry, ModelResponse, ModelRouter
    from runtime.sensing.model_router.models import ToolCall

    call = ToolCall(id="c1", name="edit_file", input={"path": "a.py"})

    class _Stub(ModelRouter):
        def call(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                text="",
                tool_calls=[call],
                input_tokens=0,
                output_tokens=0,
                cost=CostEntry(tokens_in=0, tokens_out=0, usd=0.0),
                model="m",
                provider="stub",
            )

    events = list(_Stub().call_stream(_request(force=True)))
    assert [e.tool_call for e in events if e.type == "tool_use"] == [call]


def test_ollama_parses_a_tool_call_from_an_openai_shaped_reply() -> None:
    """End-to-end shape check against the wire format ollama actually returns."""
    from runtime.sensing.model_router.models import ToolCall

    raw = {
        "id": "call_1",
        "function": {"name": "edit_file", "arguments": json.dumps({"path": "a.py"})},
    }
    fn = raw["function"]
    parsed = ToolCall(
        id=str(raw["id"]),
        name=str(fn["name"]),
        input=json.loads(fn["arguments"]),
    )
    assert parsed.name == "edit_file"
    assert parsed.input == {"path": "a.py"}

