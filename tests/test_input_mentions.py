from __future__ import annotations

from runtime.core.cerebrum.input_mentions import (
    InputMention,
    InputMentions,
    parse_input_mentions,
)


def test_parse_input_mentions_extracts_all_three_types() -> None:
    text = (
        "Please use @skill:deep-research and @plugin:web-search to "
        "investigate the topic, then ask @agent:researcher_v1 for "
        "review."
    )
    result = parse_input_mentions(text)

    assert result.skills == ("deep-research",)
    assert result.plugins == ("web-search",)
    assert result.agents == ("researcher_v1",)
    assert result.has_any
    assert len(result.raw_mentions) == 3


def test_parse_input_mentions_dedupes_within_bucket() -> None:
    text = "@skill:deep-research and again @skill:deep-research and @skill:web_search"
    result = parse_input_mentions(text)
    assert result.skills == ("deep-research", "web_search")


def test_parse_input_mentions_returns_empty_for_plain_text() -> None:
    result = parse_input_mentions("just a regular message with no mentions")
    assert not result.has_any
    assert result.skills == ()
    assert result.plugins == ()
    assert result.agents == ()


def test_parse_input_mentions_ignores_unknown_types() -> None:
    text = "@thing:foo @user:bar @skill:real_one"
    result = parse_input_mentions(text)
    # Only the @skill: token should match — the others use unrecognized
    # bucket names.
    assert result.skills == ("real_one",)
    assert result.plugins == ()
    assert result.agents == ()


def test_parse_input_mentions_ignores_at_without_colon() -> None:
    # Bare @mentions without a type prefix are not routing tokens —
    # they're handled by the markdown user-mention layer (or just as
    # casual text). They MUST NOT be treated as agent pins.
    result = parse_input_mentions("@alice please look")
    assert not result.has_any


def test_parse_input_mentions_records_span() -> None:
    text = "Hi @skill:foo there"
    result = parse_input_mentions(text)
    assert len(result.raw_mentions) == 1
    mention = result.raw_mentions[0]
    assert isinstance(mention, InputMention)
    assert mention.type == "skill"
    assert mention.id == "foo"
    assert text[mention.span[0] : mention.span[1]] == "@skill:foo"


def test_render_hint_mentions_each_bucket() -> None:
    mentions = InputMentions(
        plugins=("web-search",),
        skills=("deep-research",),
        agents=("researcher_v1",),
        packs=(),
        raw_mentions=(),
    )
    hint = mentions.render_hint()
    assert "<input-mentions>" in hint
    assert "deep-research" in hint
    assert "web-search" in hint
    assert "researcher_v1" in hint
    assert "</input-mentions>" in hint


def test_render_hint_empty_for_no_mentions() -> None:
    mentions = InputMentions((), (), (), (), ())
    assert mentions.render_hint() == ""


def test_capability_router_promotes_pinned_skill_to_front() -> None:
    """Pinned skills should lead the priority list."""
    from runtime.core.cerebrum.capability_router import activate_capabilities

    class _Registry:
        def has(self, _name: str) -> bool:
            return True

        def is_enabled(self, _name: str) -> bool:
            return True

    activation = activate_capabilities(
        "research the market and use @skill:deep-research",
        registry=_Registry(),
    )
    assert "deep-research" in activation.priority_skills
    assert activation.priority_skills.index("deep-research") < 3
    assert activation.pinned_skills == ("deep-research",)
    assert "pinned" in activation.labels


def test_capability_router_pin_only_activates_even_without_keywords() -> None:
    """A pure mention-driven prompt still activates the router."""
    from runtime.core.cerebrum.capability_router import activate_capabilities

    class _Registry:
        def has(self, _name: str) -> bool:
            return True

        def is_enabled(self, _name: str) -> bool:
            return True

    activation = activate_capabilities(
        "@plugin:my-tool please",
        registry=_Registry(),
    )
    # No keyword/mode rule fires, but mentions alone should make it
    # active so the input-mentions hint reaches the prompt.
    assert activation.active
    assert activation.pinned_plugins == ("my-tool",)
    assert "input-mentions" in activation.render_prompt()
