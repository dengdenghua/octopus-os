# Logo Design Prompt Guide

Specialized guide for constructing high-quality logo generation prompts using the Echo Workflow.

---

## 🚨 CRITICAL EXECUTION PRINCIPLE 🚨

**When generating design assets (Primary Logo + Mockup/Specs/Variations/Custom Assets):**

✅ **CORRECT:** Generate Primary Logo FIRST → Wait for completion → Use its URL as `reference_image` for ALL other assets

❌ **WRONG:** Generate all assets simultaneously in one batch

**Why:** Design coherence requires all derivative assets to reference the same primary logo. Parallel generation creates inconsistent, disconnected designs that cannot be used together.

**Applies to:**
- Standard assets: Design Specs Board, Brand Application Mockup, Logo Variations
- Custom user requests: Social media assets, packaging, stationery, signage, product mockups, etc.

**Reference locations:** Section 2 Case B, Section 6B (Design Assets Templates), Module 3 execution.

---

## 1. Logo Foundation (Core Dimensions)

Before generating a prompt, define these two core dimensions to ensure alignment with brand identity.

### **A. Logo Type Selection**

| Type | Description | Best For |
| :--- | :--- | :--- |
| **Graphic Logo (Default)** | Contains a distinct icon or symbol paired with text. | Building brand IP, symbolic storytelling, memorable visual identity. |
| **Text Logo (Wordmark)** | Focuses purely on custom typography without icon. | Minimalism, strong brand names, Scandinavian aesthetic, short names. |

**Default:** If not specified, always use **Graphic Logo**.

---

### **B. Style Selection Matrix**

| Style Name | Key Characteristics | Best For |
| :--- | :--- | :--- |
| **3D Luxury/Fashion (Default)** | Couture aesthetics, 3D depth (6-10mm), polished metallic textures, refined contours, premium 3-layer lighting. | High-end Fashion, Luxury Branding, Jewelry, Watches, Premium Lifestyle. |
| **Cartoon/Mascot** | Playful characters integrated with typography, flat vector, vibrant solid colors. | F&B, Gaming, Kids' brands, Mobile Apps, IP development. |
| **Minimalist Flat** | Geometric simplicity, heavy use of negative space, clean lines, maximum legibility. | Tech Startups, Corporate, Architecture, Modern Design. |
| **Vintage/Retro** | Badge/Emblem layouts, ornate borders, textured backgrounds, heritage color palettes. | Breweries, Coffee, Handcrafted goods, Traditional trades. |
| **Tech/Futuristic** | Sleek geometry, neon/cyan accents, circuit-inspired motifs, dark "cyber" aesthetic. | AI, Software, Hardware, Cyber-security, Space-tech. |

**Default:** If not specified, always use **3D Luxury**.

---

## 2. Execution Logic & Defaults

### **Default Behavior Rules**

**Logo Type:** Default to Graphic Logo if not specified.
**Style:** Default to 3D Luxury if not specified.
**Quantity:** 
- No quantity specified → Generate 3 distinct styles in batch
- Quantity specified → Generate requested number across different styles
- Design assets requested → SEQUENTIAL: Primary Logo(s) first, WAIT, then assets using primary logo as reference_image (only when user explicitly requests assets)

### **Anti-Infringement Guidelines (CRITICAL)**

#### **A. Request-Level Detection (MUST Check First)**

Before proceeding with any logo generation, detect and handle requests that contain explicit infringement risks.

**Detection Criteria:**
- User explicitly requests to "copy", "clone", "replicate", "imitate", or "use as template" a specific brand's logo
- User requests to take another brand's logo and only replace text/letters/name
- User references a specific trademarked logo and requests minimal modification or direct reproduction

**Response Protocol:**

When infringement risk is detected, you **MUST**:

1. **Acknowledge the limitation clearly** - Explain that directly copying or closely imitating existing brand logos carries legal/trademark risks and cannot be executed as requested
2. **Offer style-based alternative** - Propose to reference the mentioned brand's abstract design principles (color philosophy, typography approach, visual rhythm, geometric style) while creating a **100% original** design
3. **Proceed with original generation** - Generate logos that are inspired by abstract stylistic elements but distinctly original in all specific visual elements

**Key Principle:** Extract and apply **design principles only** (e.g., "bold geometric shapes", "playful primary color palette", "rounded sans-serif typography", "modular block aesthetic") rather than replicating any specific shapes, icons, letterforms, or layouts from the referenced brand.

**Communication Approach:**
- Be direct but constructive - clearly state what cannot be done and immediately offer what CAN be done
- Frame the alternative positively - style-inspired original designs can achieve similar visual impact without legal risk
- Proceed to generation after explaining - do not wait for user re-confirmation unless they request changes

#### **B. Prompt-Level Protection (During Generation)**

When constructing image generation prompts:
- **NEVER copy or closely imitate** specific shapes, icons, or layouts from known brand logos
- Extract **abstract design principles only** (e.g., "geometric minimalism", "warm color palette") - NOT specific visual elements
- **ALWAYS include in image prompts:** "Original design, must not resemble any existing brand logos"
- Reference images are for **style direction only** - the output must be distinctly original
- If a reference logo is too iconic/famous, describe the abstract principle instead of using the image
- Ensure all outputs are **commercially safe and legally distinct** from existing trademarks

---

## 3. Complete Logo Generation Workflow

This section defines the complete user-facing workflow with dynamic modules based on user context.

### **Workflow Structure & Default Execution**

```
┌─────────────────────────────────────────────────────────────┐
│                    Logo Generation Flow                     │
├─────────────────────────────────────────────────────────────┤
│  0. Anti-Infringement Check (MANDATORY - See Section 2)     │
│  ─────────────────────────────────────────────────────────  │
│  1. Brand/Industry Understanding (Recommended)              │
│  2. Design Solutions (Required)                             │
│  3. Next Steps Guidance (Recommended)                       │
└─────────────────────────────────────────────────────────────┘
```

**⚠️ Step 0 - Anti-Infringement Check:** Before starting any module, apply Section 2 "Anti-Infringement Guidelines Part A" to detect explicit copy/clone requests. If detected, respond with explanation and proceed with style-inspired original design approach.

**Default Execution Strategy:**

⚠️ **DEFAULT = Execute Streamlined Workflow (Modules 1+2+3)**

**Execution Rules:**

1. **Full Workflow (1+2+3)** - DEFAULT when user provides brand name + industry/purpose, or query is generic/vague
2. **Quick Generation (Module 2 only)** - ONLY when user explicitly requests to skip analysis

---

### **Module 1: Brand/Industry Understanding (Recommended)**

#### **Objective**
Avoid "beautiful but irrelevant" designs by ensuring outputs align with brand context.

#### **Trigger Logic**
- **Default:** Always output UNLESS user explicitly says "just the image, no analysis"
- **Skip if:** User says "skip analysis", "just logo", "no explanation needed"

#### **Execution Logic**

Use `info_search` to gather brand information (website, description, social media, news). Extract context: Industry, Target Market (B2B/B2C), User Persona, Core Value, Personality. If existing logo provided, use `image_analysis` to parse visual elements, colors, typography. Synthesize findings into actionable design direction.

#### **Output Format**

Output brand insight covering target audience, industry, core value, message. If existing logo provided, analyze current visual language. Conclude with recommended design direction.

---

### **Module 1B: Market Style Analysis (Optional)**

#### **Objective**
Analyze industry visual trends and representative market examples to inform design strategy differentiation.

#### **Trigger Logic**
- **Optional:** Execute when deeper market context is needed
- **Skip if:** User requests quick generation or has clear style direction

#### **Key Requirements**

- **Output:** 3 Design Strategies (1 industry-aligned + 2 innovative)
- **Reference images:** Strategy 1 MAY use `reference_image_list` for **style direction only** (NOT for copying specific elements)
- **Originality (CRITICAL):** Extract **abstract principles only** (color palettes, style moods, typography approaches) - NEVER specific shapes, icons, or layouts
- **Anti-Infringement:** Output must be 100% original and legally distinct from all reference logos

#### **Execution Steps**

Extract industry keywords. Use `info_search` with `image=true` to search market visuals and design trends. Observe visual trends (colors, styles, icons, typography). Use `image_analysis` on 2-3 representative examples. Formulate 3 strategies: Strategy 1 market-aligned (may use reference images for style direction only), Strategies 2-3 innovative.

#### **Output Format**

Output industry visual trends summary with representative examples, visual insights (colors, styles, icons), then formulate 3 strategies: one market-aligned (with abstract principle extracted and original direction) and two innovative strategies (with approach, brand value, innovation angle, and risk/reward assessment).

#### **Image Generation Rules**

Each logo = separate image generation call. Strategy 1 (market-aligned) MAY use reference_image_list for style direction only, never famous brands, always include originality guidance. Strategies 2-3 (innovative) use no direct market references. Total output: 3 strategies × 2 variations = 6 separate calls.

---

### **Module 2: Design Solutions (Required)**

#### **Objective**
Deliver professional, commercial-ready, multi-option visual solutions based on brand understanding and design strategy.

#### **Execution Logic**

**A. Quantity Logic:**

Default: Generate 3 distinct style themes (3 total logos) with clear visual differentiation based on brand context. If user specifies quantity, generate requested number distributed across different styles.

**B. Logo Generation Strategy:**

Batch generation recommended for standard multi-style requests (3 logos in one batch call). Use separate calls only when user requests sequential review, different technical parameters, or complex dependencies.

---

**C. Sequential Generation:** 

Only when user explicitly requests design specs/mockups/variations: Generate Primary Logo FIRST (no reference_image), WAIT for completion, THEN generate requested assets using primary logo URL as reference_image. Default: only generate primary logos (3 in batch).

**D. Technical Requirements:**

Combine brand understanding from Module 1 with templates from Section 4. Honor user requirements (color, size, style). Output PNG with solid color background ensuring high contrast with logo colors (light logos need dark backgrounds, dark logos need light backgrounds). Clean edges, download-ready. Use user-provided reference images to maintain consistency while ensuring originality.

**E. Custom Assets (Conditional):**

Only when user explicitly mentions specific products/use cases: Generate primary logos first, select 1 recommended solution, generate custom mockup/asset using selected logo URL as reference_image. Ensure consistency across all mockup elements.

#### **Output Content**

**Output Structure:**

Total Output: **3 distinct style themes = 3 logos**

#### **Output Content**

For each logo: style theme title, logo image, one paragraph combining design description with design value.

---

### **Module 3: Next Steps Guidance (Recommended)**

#### **Objective**
Proactively guide users toward natural next actions based on their context and results.

#### **Trigger Logic (Simplified)**

**Execute (Default):** Always provide next steps UNLESS user explicitly says "don't suggest anything else"

This module should always run as it helps users understand their options and encourages iteration.

#### **Execution Logic**

Provide 2-3 relevant next steps based on user's situation:
- **Multiple solutions**: Offer refinement/variations, request more context, or high-res export
- **Known product/business**: Suggest mockup rendering, packaging design, or brand collateral
- **User selected solution**: Offer high-res enhancement, complete design system, or application mockups
- **Vague query**: Ask for clarification, offer style exploration, or show industry examples
- **Similarity concerns**: Propose differentiation refinement, alternative directions, or trademark pre-check

---

## 4. Comprehensive Design Style Library

9 professional design styles, each with design principles (Form, Structure, Strokes, Color, Effects, Typography), complete prompt template, and usage scenarios.

---

#### **Style 1: Modern Minimal**

**Best For:** Tech Startups, Corporate, Architecture

**Prompt Principles:**
- Form: clean silhouette, simple geometry, 1–2 distinctive hooks
- Structure: strong alignment, generous whitespace, stable center
- Strokes: uniform or 2-level hierarchy only, crisp edges
- Color: monochrome or neutrals + single accent
- Effects: flat vector, no texture, no heavy shadow
- Typography: geometric sans, precise kerning/tracking

**Template:**
```
Modern minimalist logo for "[BRAND NAME]". ICON (if Graphic Logo): A singular geometric mark (continuous stroke or abstract negative space shape). TYPOGRAPHY: Bold, high-contrast [Sans-Serif] custom lettering with wide letter spacing for premium feel. VISUAL: Extreme reduction to core shapes, high legibility, perfectly balanced proportions, generous negative space. PALETTE: High-contrast duo-tone (e.g., Black & White).
```

---

#### **Style 2: Abstract Geometric / Typographic Experiment**

**Best For:** Editorial, Design Studios, Avant-garde Brands

**Prompt Principles:**
- Form: deconstructed strokes via rectangles/arcs/diagonals, bold negative space. Use simple and few lines and shapes to draw the logo, rather than complex and numerous ones.
- Structure: Based on the ingenious implicit alignment rules/proportions of composition, figure–ground tricks
- Strokes: hard-edge, razor sharp, modular blocks
- Color: black/white or disciplined 2-tone
- Effects: strictly flat, no gradients, no noise
- Typography: custom modular letterforms, editorial spacing

**Template:**
```
Abstract experimental logo for "[BRAND NAME]". FORM: Deconstructed strokes via rectangles/arcs/diagonals, bold negative space. STRUCTURE: Based on ingenious implicit alignment rules/proportions of composition, figure-ground tricks. STROKES: Hard-edge, razor sharp, modular blocks. COLOR: Black/white or disciplined 2-tone. EFFECTS: Strictly flat, no gradients, no noise. TYPOGRAPHY: Custom modular letterforms, editorial spacing.
```

---

#### **Style 3: Retro American / 80s Film Mark**

**Best For:** Entertainment, Media, Vintage-inspired Brands

**Prompt Principles:**
- Form: chunky shapes, dramatic cut lines, iconic readability
- Structure: bold composition, strong straight-vs-curve tension
- Strokes: blocky fills, geometric slicing, confident proportions
- Color: vivid solids (e.g., orange) or limited retro palette
- Effects: mostly flat; optional subtle vintage print feel (very light)
- Typography: bold sans / display, slightly condensed, cinematic vibe

**Template:**
```
Retro 80s inspired logo for "[BRAND NAME]". FORM: Chunky shapes, dramatic cut lines, iconic readability. STRUCTURE: Bold composition, strong straight-vs-curve tension. STROKES: Blocky fills, geometric slicing, confident proportions. COLOR: Vivid solids (e.g., orange) or limited retro palette. EFFECTS: Mostly flat; optional subtle vintage print feel (very light). TYPOGRAPHY: Bold sans/display, slightly condensed, cinematic vibe.
```

---

#### **Style 4: Hand-Drawn Vintage (Modern-Restraint)**

**Best For:** Breweries, Coffee, Traditional Trades, Handcrafted Goods

**Prompt Principles:**
- Form: clear outline, controlled organic imperfections
- Structure: clean badge or simple layout, minimal ornaments
- Strokes: natural pressure variation, consistent brush family
- Color: low-saturation vintage palette, 1–2 inks
- Effects: very light paper/grain only; avoid heavy distress
- Typography: hand-lettered or vintage serif with refined spacing

**Template:**
```
Vintage heritage emblem for "[BRAND NAME]". LAYOUT (if Graphic Logo): Circular badge or ornate frame with decorative filigree. TYPOGRAPHY: Sturdy hand-drawn serif with irregular edges simulating letterpress printing. DETAILS: Hand-drawn stippling effects or textured copper/paper aesthetic. COLORS: Muted palette (Ochre, Forest Green, Deep Burgundy). ATMOSPHERE: Pure craftsmanship, nostalgic warmth.
```

---

#### **Style 5: Fashion / Beauty (Light Luxury)**

**Best For:** High-end Fashion, Cosmetics, Premium Lifestyle, Jewelry

**Prompt Principles:**
- Form: elegant, thin-to-thick contrast (or refined sans), minimal icon
- Structure: editorial layout, lots of whitespace, premium balance
- Strokes: precise, delicate details, no "cute" gimmicks
- Color: black/cream/neutrals + one accent (champagne-gold as flat)
- Effects: flat or extremely subtle sheen cue; no 3D/gloss
- Typography: high-contrast serif or refined sans, perfect kerning

**Template:**
```
Editorial luxury logo for "[BRAND NAME]". FORM: Elegant, thin-to-thick contrast (or refined sans), minimal icon. STRUCTURE: Editorial layout, lots of whitespace, premium balance. STROKES: Precise, delicate details, no "cute" gimmicks. COLOR: Black/cream/neutrals + one accent (champagne-gold as flat). EFFECTS: Flat or extremely subtle sheen cue; no 3D/gloss. TYPOGRAPHY: High-contrast serif or refined sans, perfect kerning.
```

---

#### **Style 6: Modern Tech / Futuristic Tech**

**Best For:** AI, Software, Data Services, Tech Startups, Cyber-security

**Prompt Principles:**
- Form: parametric curves + modular geometry, beveled cuts, cutouts
- Structure: system language (nodes/connections/flow), ordered grid
- Strokes: consistent radius/angle rules, vector precision
- Color: cool neutrals + single cold accent (electric blue/cyan/purple)
- Effects: flat-first; if any gradient, ultra-subtle and justified
- Typography: geometric sans / semi-mono, custom cuts/ink-traps

**Template:**
```
Futuristic tech logo for "[BRAND NAME]". CORE ELEMENT: "Spectrum progress bar" transitioning from [Color A] to [Color B], simulating laser scanning. DETAILS: Scattered pixel blocks at edges, symbolizing digital remnants from movement. TYPOGRAPHY: Bold white sans-serif lowercase letters, retro-futuristic aesthetic. BACKGROUND: Dark (near-black) to emphasize glowing spectrum.
```

---

#### **Style 7: Cute Cartoon but Premium (Not Childish)**

**Best For:** F&B, Gaming, Kids' brands (but mature execution)

**Prompt Principles:**
- Form: rounded geometry, simplified facial cues (optional), stable silhouette
- Structure: generous whitespace, minimal details, mature balance
- Strokes: smooth, thick rounded blocks, clean vector edges
- Color: soft desaturated + small bright accent, avoid candy overload
- Effects: flat only, no sticker outlines, no toy-like gloss
- Typography: rounded sans with refined spacing, subtle playful curves

**Template:**
```
Creative flat vector wordmark for "[BRAND NAME]" featuring [Character/Animal] integration. CONCEPT: Integrated Typography where the [Character] is CLEVERLY FUSED with the letters, OR standalone mascot with symbolic meaning. VISUAL: 2D flat vector, minimalist geometry. NO gradients, NO shading, NO 3D effects. Clean lines, negative space used for details (eyes, mouth). COLORS: 2-3 vibrant solid colors on clean white background. CHARACTER TRAITS: Playful features, rounded shapes for friendliness.
```

---

#### **Style 8: Solid Bold Geometric (Unified)**

**Best For:** Sports, Energy, Bold Consumer Brands

**Prompt Principles:**
- Form: whole geometric masses, minimal internal cutting, solid integrity
- Structure: stable block alignment, avoid mosaic/fragmentation
- Strokes: ultra-thick, indivisible solid shapes
- Color: one element = one color, high-saturation contrast
- Effects: pure flat vector, clean edges, no gradients
- Typography: ultra-bold sans-serif, heavy and monolithic

**Template:**
```
Bold geometric logo for "[BRAND NAME]". FORM: Whole geometric masses, minimal internal cutting, solid integrity. STRUCTURE: Stable block alignment, avoid mosaic/fragmentation. STROKES: Ultra-thick, indivisible solid shapes. COLOR: One element = one color, high-saturation contrast. EFFECTS: Pure flat vector, clean edges, no gradients. TYPOGRAPHY: Ultra-bold sans-serif, heavy and monolithic.
```

---

#### **Style 9: 3D Luxury (Premium Fashion)**

**Best For:** High-end Fashion, Couture, Luxury Branding, Jewelry, Premium Lifestyle

**Prompt Principles:**
- Form: 3D metallic with 6-10mm depth, precision-beveled edges
- Structure: Couture-inspired refined contours
- Strokes: Elegant light-play on polished/brushed surfaces
- Color: Premium metallics (Rose Gold, Champagne Gold, Silver, Gunmetal, Titanium)
- Effects: 3-layer cinematic lighting system, subtle shadows
- Typography: High-contrast serif or refined sans matching metallic finish

**Template:**
```
Premium brand identity for "[BRAND NAME]" featuring a sophisticated 3D [Circle/Hexagon/Abstract] icon. HERO ELEMENT: Crafted from [Metal Material - e.g., Polished Rose Gold] with 8mm depth and precision-beveled edges. Couture-inspired abstract form with refined contours, evoking [Symbolism - e.g., Timeless Elegance/Fluid Movement]. Surface features subtle horizontal brushing or high-mirror polish to capture elegant light-play. ENVIRONMENT: Deep, sophisticated gradient background (e.g., Midnight Silk [#1A1A1A to #050505]). LIGHTING: 3-layer cinematic system - Key Light: Soft 45° upper-left to highlight primary curves; Rim Light: Delicate backlight to define the silhouette against the dark environment; Soft Specular Highlights: Precision points of light on sharp edges to emphasize high-end craftsmanship. TYPOGRAPHY (if Graphic Logo): "[BRAND NAME]" in high-contrast [Modern Serif / Refined Sans], matching the premium metallic finish. 4K photorealistic rendering, museum-quality presentation, high-fashion aesthetic.
```

### **Usage Guide**

Templates are reference frameworks, not ready-to-use prompts. Use Prompt_Principles as technical guide and Template as structure reference. Build final prompt by integrating: brand context from Module 1, user requirements, and technical specifications. Customize templates by replacing [BRAND NAME], adding brand context, applying user constraints, ensuring originality, and using batch generation for multi-style requests.

---

## 6. Visual Component Standards

### **Metal Materials (3D Luxury Only)**
- **Polished Champagne Gold:** Warm, high-fashion glow, ideal for feminine luxury.
- **Mirror-Finished Silver:** Precision and modern edge, ideal for avant-garde couture.
- **Satin Rose Gold:** Romantic, soft-touch premium feel for cosmetics and high-end jewelry.
- **Liquid Gunmetal:** Powerful, sleek, and high-tech masculine luxury.
- **Brushed Titanium:** Industrial yet refined, perfect for modern accessory brands.

### **Lighting System (3D Luxury Only)**
- **Key Light:** Soft from upper-left 45°, primary highlights.
- **Rim Light:** Delicate from right side for edge separation.
- **Drop Shadow:** Subtle (10-15% opacity) to anchor in space.

### **Background Standards**

Use solid color backgrounds (no gradients/textures) with high contrast to logo colors. Light logos need dark backgrounds (e.g., #2C2C2C, #000000), dark logos need light backgrounds (e.g., #FFFFFF, #F5F5F5). Enables clean removal, clear visibility, professional presentation.

---

## 6B. Design Assets Templates (Sequential Generation)

Only when user explicitly requests additional design assets: Generate Primary Logo FIRST, WAIT for completion, THEN generate requested assets using primary logo URL as reference_image.

**Asset Types:**
- **Primary Logo**: Generate first using Section 4 templates, no reference_image
- **Design Specs Board**: Transform logo into specification board with annotations (spacing, sizing, color codes)
- **Brand Application Mockup**: Apply logo to realistic brand materials with photorealistic rendering
- **Logo Variations**: Showcase on different backgrounds (original, light, dark, monochrome)
- **Custom Assets**: Apply logo to user-specified contexts (social media, packaging, stationery, etc.)

---

## 7. Design Principles

High contrast between background and logo (light logos need dark backgrounds, dark logos need light backgrounds, visible at small sizes). Abstract/symbolic icons, not literal objects. Meaningful negative space. Icon-to-Text ratio 3:2, margins 15-20% clear space. Use specific visual terms (e.g., "museum-quality", "precision-cut"), avoid vague terms.

## 8. Optimization Priority

If unsatisfactory, refine in order: (1) Background-Logo Contrast, (2) Symbolic meaning, (3) Material/Color Contrast, (4) Lighting (for 3D styles), (5) Simplicity (remove small details), (6) Typography matching brand vibe.
