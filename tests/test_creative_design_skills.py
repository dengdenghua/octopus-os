from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "runtime/execution/all_skills"

EXPECTED = {
    "creative-3d-animation",
    "creative-anime-style-forge",
    "creative-anime-pv",
    "creative-audiobook",
    "creative-beat-sync-edit",
    "creative-brand-film",
    "creative-brand-flow-mg",
    "creative-broll-planner",
    "creative-character-performance",
    "creative-cinematic-style-extractor",
    "creative-cinematic-shot-cards",
    "creative-comfyui-workflow",
    "creative-course-landing-page",
    "creative-digital-product-film",
    "creative-director-stage",
    "creative-documentary",
    "creative-dodge-game-video",
    "creative-dotmatrix-brand-motion",
    "creative-dreamcore-space",
    "creative-dual-player-intro",
    "creative-dynamic-poster",
    "creative-ecommerce-images",
    "creative-editor-export",
    "creative-education-video",
    "creative-fpv-video",
    "creative-film-score",
    "creative-koc-video",
    "creative-h3-prompt-director",
    "creative-line-explainer",
    "creative-lighting-studio",
    "creative-lip-makeup-ad",
    "creative-liveaction-drawing-reveal",
    "creative-localization-dubbing",
    "creative-microdrama-writer",
    "creative-multi-angle-render",
    "creative-multiboard",
    "creative-music-video",
    "creative-paper-collage",
    "creative-paper-stopmotion-explainer",
    "creative-pixel-block-art",
    "creative-podcast-kit",
    "creative-product-ad",
    "creative-promo-video",
    "creative-silkscreen-film",
    "creative-title-sequence",
    "creative-toy-grid-poster",
    "creative-ui-motion",
    "creative-image-inpaint",
    "creative-image-remix",
    "creative-intentional-lowpoly-film",
    "creative-video-deconstruct",
    "creative-video-editor",
    "creative-visual-direction",
    "creative-voice-design",
    "creative-vox-explainer",
    "creative-zodiac-english-series",
    "creative-sleep-audio",
    "creative-storyboard-assets",
}


def _frontmatter(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    _, raw, _ = source.split("---", 2)
    return yaml.safe_load(raw)


def test_design_skill_pack_is_complete_original_and_catalogued() -> None:
    found = {path.parent.name for path in SKILLS.glob("creative-*/SKILL.md")}
    assert found == EXPECTED

    catalog = (ROOT / "frontend/src/app/workspace/design/design-catalog.ts").read_text(
        encoding="utf-8"
    )
    for skill_id in sorted(EXPECTED):
        metadata = _frontmatter(SKILLS / skill_id / "SKILL.md")
        assert metadata["name"] == skill_id
        assert metadata["license"] == "Apache-2.0"
        assert metadata["metadata"]["origin"] == "echo-original"
        assert f'id: "{skill_id}"' in catalog


def test_editor_and_director_craft_references_are_routed_and_present() -> None:
    required = {
        "creative-video-editor": {
            "api.md",
            "verification.md",
            "editorial-judgment.md",
            "text-and-look.md",
        },
        "creative-director-stage": {
            "api.md",
            "blocking.md",
            "camera-language.md",
            "campath-dsl.md",
            "code-model-craft.md",
            "motion-craft.md",
            "motion-dsl.md",
            "verification.md",
        },
        "creative-comfyui-workflow": {
            "api.md",
            "safety.md",
            "verification.md",
            "workflow-craft.md",
        },
    }
    for skill_id, filenames in required.items():
        entrypoint = (SKILLS / skill_id / "SKILL.md").read_text(encoding="utf-8")
        for filename in filenames:
            assert (SKILLS / skill_id / "references" / filename).is_file()
            assert f"references/{filename}" in entrypoint

