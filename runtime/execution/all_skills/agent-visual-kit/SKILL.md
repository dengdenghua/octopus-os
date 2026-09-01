---
name: agent-visual-kit
description: Metaskill workflow for generating Echo Agent character artwork. Use when creating or regenerating Agent portraits, three-view character sheets, transparent-background standees, separate close-up avatars, or visual assets from HUD character profile data; constrains prompt assembly, generation, post-processing, quality checks, and retry reasons.
---

# Agent Visual Kit

## Purpose

Use this metaskill to generate Agent visual assets that are usable inside the HUD, not just attractive images. The target output is a consistent character asset pack:

- `visuals/front.png`
- `visuals/side.png`
- `visuals/back.png`
- `avatar.png` generated as a separate fixed-size close-up headshot and used as the Agent's replacement avatar

## Required Workflow

1. Read the Agent character profile:
   - display name
   - role/category
   - background
   - personality
   - temperament
   - apparent age
   - visual keywords
   - user custom prompt additions

2. Assemble the prompt in this order:
   - Agent identity and role
   - readable character background
   - appearance, outfit, palette, and temperament
   - three-view consistency rules
   - composition rules
   - background/transparency rules
   - quality rules
   - negative constraints

3. Generate three separate full-body views:
   - front view, facing the viewer
   - side profile view, facing screen right
   - back view, showing rear silhouette and equipment

4. Generate one separate avatar portrait:
   - square 1:1 image, normally 512x512
   - Zero-like close-up large headshot, face and eyes readable at small list size
   - same identity, hairstyle, palette, outfit collar, and temperament as the full-body views
   - transparent background or flat chroma-key background
   - face should fill most of the icon, with only light collar or shoulder context

5. Post-process every generated full-body view:
   - prefer real alpha transparency
   - if alpha is absent, remove flat chroma-key or flat edge-connected background
   - keep only the primary connected character component
   - crop to subject while adding transparent headroom and footroom
   - keep the character visually large enough for Hub preview cards
   - save as PNG when possible

6. Post-process the avatar:
   - remove flat background if needed
   - keep a fixed 512x512 square output
   - make it a Zero-like large-face headshot with only slight shoulder/collar context, not a half-body crop
   - keep the face readable and centered
   - save as 512x512 `avatar.png`
   - replace the Agent avatar with this new file so lists, switchers, and HUD thumbnails show the new face
   - if separate avatar generation fails, fall back to cropping a close-up from `front.png`

7. Return or persist file URLs for all views and the avatar.

## Hard Quality Rules

- The image must be a single full-body character standee, not an infographic, card, UI panel, poster, or character stat sheet.
- The head, hair, hands, and feet must remain inside the canvas.
- Add generous transparent padding above the head and below the feet.
- The three views must keep the same face, hairstyle, outfit, palette, proportions, and role-readable design language.
- Transparent background is preferred. If the generator cannot output true alpha, require one perfectly flat `#00ff00` chroma-key background.
- Do not include labels, logos, borders, floating icons, particle effects, code glyphs, UI frames, decorative HUD elements, or text.
- Any tool, code, circuitry, or domain motif must be integrated into clothing or equipment, not floating around the character.

## Retry Reasons

Retry or regenerate with a stricter prompt when any of these are detected or reported:

- cropped head, hair, feet, or hands
- non-transparent busy background
- low resolution, blurry edges, or muddy face
- half-body image instead of full-body standee
- front/side/back identity mismatch
- text, labels, watermark, UI card, or infographic elements
- extra limbs, distorted hands, asymmetrical eyes, duplicate character, or detached artifacts

## Prompt Template

Use this compact shape when composing prompts:

```text
Use agent-visual-kit metaskill.
Create a high-resolution three-view character asset pack for {agent_name}.
Identity: {role/background/personality}.
Appearance: {visual_keywords/user_additions}.
Views: generate one {view} only.
Composition: for views use full body, centered, generous headroom and footroom; for avatar use square close-up large headshot.
Background: true transparent background; otherwise perfectly flat #00ff00 chroma-key.
Consistency: same face, hairstyle, outfit, palette, proportions across all views.
Quality: crisp edges, detailed outfit, sharp eyes, no blur.
Negative: no text, no UI frame, no card, no poster, no watermark, no floating icons, no particles, no cropped head, no cropped feet, no duplicate character.
```
