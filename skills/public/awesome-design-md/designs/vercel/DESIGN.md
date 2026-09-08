# Design System: Vercel

## 1. Visual Theme & Atmosphere

**Mood**: Ultra-minimal, precise, developer-focused
**Density**: Low to medium, generous whitespace
**Aesthetic**: Black and white precision, high contrast, technical elegance

Vercel's design philosophy centers on clarity and speed. Every element serves a purpose. The interface feels like a precision instrument—clean, fast, and unobtrusive.

## 2. Color Palette & Roles

### Primary Colors
- **Background Primary**: `#000000` (Pure black, main canvas)
- **Background Secondary**: `#111111` (Subtle elevation)
- **Background Tertiary**: `#1a1a1a` (Cards, inputs)
- **Text Primary**: `#ffffff` (Headlines, primary content)
- **Text Secondary**: `#888888` (Body text, descriptions)
- **Text Tertiary**: `#666666` (Muted text, placeholders)

### Accent Colors
- **Accent Blue**: `#3b82f6` (Primary actions, links)
- **Accent Blue Glow**: `rgba(59, 130, 246, 0.3)` (Hover states)
- **Success**: `#22c55e` (Positive states)
- **Warning**: `#f59e0b` (Caution states)
- **Error**: `#ef4444` (Error states)

### Usage Patterns
- Use pure black backgrounds for immersive developer experience
- White text for maximum readability on dark surfaces
- Blue accent sparingly for primary actions only
- Gray scale for hierarchy and information density

## 3. Typography Rules

### Font Family
- **Primary**: `Geist, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
- **Monospace**: `Geist Mono, "Fira Code", "SF Mono", Monaco, monospace`

### Type Scale
| Level | Size | Weight | Usage |
|-------|------|--------|-------|
| Hero | 72px | 800 | Landing page headlines |
| H1 | 48px | 700 | Page titles |
| H2 | 36px | 600 | Section headers |
| H3 | 24px | 600 | Subsection headers |
| H4 | 18px | 600 | Card titles |
| Body Large | 18px | 400 | Lead paragraphs |
| Body | 16px | 400 | Primary text |
| Body Small | 14px | 400 | Secondary text |
| Caption | 12px | 500 | Labels, metadata (uppercase, tracking-wide) |
| Code | 14px | 400 | Monospace text |

### Typography Patterns
- Use tight letter-spacing for headlines (-0.02em)
- Generous line-height for body text (1.6)
- Uppercase + wide tracking for labels and buttons
- Monospace for code, API references, and technical data

## 4. Component Stylings

### Buttons

**Primary Button**
```
Background: #ffffff
Text: #000000
Border-radius: 6px
Padding: 10px 16px
Font: 14px, weight 500, uppercase
Hover: opacity 0.9, subtle scale(1.02)
Transition: all 0.2s ease
```

**Secondary Button**
```
Background: transparent
Border: 1px solid #333333
Text: #ffffff
Border-radius: 6px
Padding: 10px 16px
Hover: background #1a1a1a
```

**Ghost Button**
```
Background: transparent
Text: #888888
Hover: text #ffffff
```

### Cards

**Standard Card**
```
Background: #1a1a1a
Border: 1px solid #222222
Border-radius: 8px
Padding: 24px
Hover: border-color #333333, subtle shadow
```

**Interactive Card**
```
Background: #1a1a1a
Border: 1px solid #222222
Border-radius: 8px
Padding: 24px
Hover: transform translateY(-2px), border-color #3b82f6
Transition: all 0.2s ease
```

### Inputs

**Text Input**
```
Background: #111111
Border: 1px solid #333333
Border-radius: 6px
Padding: 10px 14px
Font: 14px
Color: #ffffff
Placeholder: #666666
Focus: border-color #3b82f6, subtle glow
```

### Navigation

**Nav Link**
```
Color: #888888
Font: 14px, weight 500
Hover: color #ffffff
Active: color #ffffff
```

## 5. Layout Principles

### Spacing Scale
- `4px` - Tight (icon gaps, inline elements)
- `8px` - Compact (button padding, small gaps)
- `16px` - Default (card padding, section gaps)
- `24px` - Medium (section padding)
- `32px` - Large (major sections)
- `48px` - XL (hero sections)
- `64px` - 2XL (page sections)
- `96px` - 3XL (major page divisions)

### Grid System
- 12-column grid
- Max container width: 1200px
- Gutter: 24px
- Responsive breakpoints: 640px, 768px, 1024px, 1280px

### Layout Patterns
- Centered content with generous side padding
- Asymmetric layouts for visual interest
- Full-bleed sections for impact
- Sticky navigation with blur backdrop

## 6. Depth & Elevation

### Shadow System
- **Level 1**: `0 1px 2px rgba(0,0,0,0.3)` (Subtle elevation)
- **Level 2**: `0 4px 6px rgba(0,0,0,0.4)` (Cards)
- **Level 3**: `0 10px 15px rgba(0,0,0,0.5)` (Modals, dropdowns)
- **Glow**: `0 0 20px rgba(59, 130, 246, 0.3)` (Accent glow)

### Surface Hierarchy
1. **Base**: Pure black (#000000)
2. **Elevated**: Near black (#111111)
3. **Card**: Dark gray (#1a1a1a)
4. **Interactive**: Lighter on hover

## 7. Do's and Don'ts

### Do
- Use pure black backgrounds
- Maintain high contrast ratios (7:1 minimum)
- Use whitespace generously
- Keep animations fast and purposeful (< 300ms)
- Use monospace for code and technical content

### Don't
- Use gradients for backgrounds
- Add decorative elements without purpose
- Use more than one accent color
- Create busy or cluttered layouts
- Use serif fonts for UI elements

## 8. Responsive Behavior

### Breakpoints
- **Mobile**: < 640px (single column, stacked layout)
- **Tablet**: 640px - 1024px (2 columns, adjusted spacing)
- **Desktop**: > 1024px (full layout, max-width containers)

### Mobile Adaptations
- Reduce font sizes by 15-20%
- Stack grid columns
- Increase touch targets to 44px minimum
- Simplify navigation to hamburger menu

## 9. Agent Prompt Guide

### Quick Reference
```
Colors:
- Background: #000000
- Surface: #1a1a1a
- Text: #ffffff
- Muted: #888888
- Accent: #3b82f6

Typography:
- Font: Geist
- Headlines: Tight tracking, bold weight
- Body: Normal tracking, regular weight
- Labels: Uppercase, wide tracking

Components:
- Buttons: Sharp corners (6px), uppercase text
- Cards: Subtle borders, no shadows by default
- Inputs: Dark backgrounds, light borders
```

### Example Prompts
```
"Create a Vercel-style dashboard with pure black background, 
white text, and blue accents. Use Geist font family."

"Design a landing page following Vercel's aesthetic: 
minimalist, high contrast, generous whitespace."

"Build a navigation component with Vercel's style: 
dark theme, subtle hover states, uppercase labels."
```
