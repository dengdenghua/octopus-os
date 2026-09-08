# Design System: Apple

## 1. Visual Theme & Atmosphere

**Mood**: Premium, refined, human-centric
**Density**: Medium, breathable layouts
**Aesthetic**: Frosted glass, soft shadows, generous curves

Apple's design language emphasizes clarity, deference, and depth. Interfaces feel tactile and responsive, with subtle animations that feel physical and satisfying.

## 2. Color Palette & Roles

### Light Mode
- **Background Primary**: `#ffffff` (Pure white)
- **Background Secondary**: `#f5f5f7` (Light gray, secondary surfaces)
- **Background Tertiary**: `#e8e8ed` (Inputs, elevated cards)
- **Text Primary**: `#1d1d1f` (Near black, maximum contrast)
- **Text Secondary**: `#86868b` (Gray, secondary content)
- **Text Tertiary**: `#a1a1a6` (Light gray, placeholders)

### Dark Mode
- **Background Primary**: `#000000` (Pure black)
- **Background Secondary**: `#1c1c1e` (Dark gray)
- **Background Tertiary**: `#2c2c2e` (Elevated surfaces)
- **Text Primary**: `#ffffff` (Pure white)
- **Text Secondary**: `#98989d` (Gray)
- **Text Tertiary**: `#6e6e73` (Muted)

### Accent Colors
- **Blue**: `#007aff` (Primary actions, links)
- **Green**: `#34c759` (Success, positive)
- **Orange**: `#ff9500` (Warning)
- **Red**: `#ff3b30` (Error, destructive)
- **Purple**: `#af52de` (Creative)
- **Pink**: `#ff2d55` (Love, health)
- **Teal**: `#5ac8fa` (Information)
- **Yellow**: `#ffcc00` (Caution)

### Usage Patterns
- Use system background colors for native feel
- Blue for primary actions across all contexts
- Semantic colors for status and feedback
- Subtle gradients for premium elements

## 3. Typography Rules

### Font Family
- **System**: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif`
- **Display**: `"SF Pro Display", -apple-system, sans-serif`
- **Monospace**: `"SF Mono", "SFMono-Regular", Consolas, monospace`

### Type Scale
| Level | Size | Weight | Line Height | Usage |
|-------|------|--------|-------------|-------|
| Large Title | 34px | 700 | 1.2 | Hero headlines |
| Title 1 | 28px | 700 | 1.2 | Page titles |
| Title 2 | 22px | 700 | 1.2 | Section headers |
| Title 3 | 20px | 600 | 1.3 | Subsection headers |
| Headline | 17px | 600 | 1.3 | Card titles |
| Body | 17px | 400 | 1.5 | Primary text |
| Callout | 16px | 400 | 1.5 | Emphasized body |
| Subheadline | 15px | 400 | 1.5 | Secondary text |
| Footnote | 13px | 400 | 1.4 | Captions |
| Caption 1 | 12px | 400 | 1.3 | Small labels |
| Caption 2 | 11px | 400 | 1.3 | Metadata |

### Typography Patterns
- Use SF Pro Display for large titles and headlines
- Use SF Pro Text for body and UI elements
- Medium weight (500) for emphasis, not bold
- Generous line height for readability

## 4. Component Stylings

### Buttons

**Filled Button**
```
Background: #007aff
Text: #ffffff
Border-radius: 10px
Padding: 12px 20px
Font: 17px, weight 500
Hover: brightness(1.1)
Active: scale(0.98)
```

**Tinted Button**
```
Background: rgba(0, 122, 255, 0.15)
Text: #007aff
Border-radius: 10px
Padding: 12px 20px
Font: 17px, weight 500
```

**Plain Button**
```
Background: transparent
Text: #007aff
Font: 17px, weight 400
Hover: opacity 0.8
```

### Cards

**Standard Card**
```
Background: #ffffff (light) / #1c1c1e (dark)
Border-radius: 12px
Padding: 20px
Shadow: 0 2px 8px rgba(0,0,0,0.08)
```

**Inset Group**
```
Background: #f5f5f7 (light) / #1c1c1e (dark)
Border-radius: 10px
Overflow: hidden
```

### Inputs

**Text Field**
```
Background: #f5f5f7 (light) / #1c1c1e (dark)
Border: none
Border-radius: 10px
Padding: 12px 16px
Font: 17px
Placeholder: #a1a1a6
```

**Search Field**
```
Background: #e8e8ed (light) / #2c2c2e (dark)
Border-radius: 10px
Padding: 8px 12px
Icon: Search magnifying glass
```

### Navigation

**Tab Bar**
```
Background: rgba(255,255,255,0.7) with backdrop-blur
Border-top: 0.5px solid rgba(0,0,0,0.1)
Height: 49px + safe area
```

**Navigation Bar**
```
Background: rgba(255,255,255,0.7) with backdrop-blur
Border-bottom: 0.5px solid rgba(0,0,0,0.1)
Height: 44px
Title: 17px, weight 600, centered
```

## 5. Layout Principles

### Spacing Scale
- `4px` - Micro (tight gaps)
- `8px` - Small (related elements)
- `12px` - Compact (internal padding)
- `16px` - Default (card padding)
- `20px` - Medium (section gaps)
- `24px` - Large (major sections)
- `32px` - XL (page sections)
- `48px` - 2XL (hero sections)

### Corner Radius
- `6px` - Small elements (buttons, badges)
- `10px` - Medium (inputs, small cards)
- `12px` - Standard (cards, sheets)
- `16px` - Large (modals, alerts)
- `20px` - XL (full-screen sheets)
- `50%` - Circular (avatars, icons)

### Layout Patterns
- Centered content with readable max-width
- Full-bleed images with rounded corners
- Sticky headers with blur effect
- Bottom sheets for secondary content

## 6. Depth & Elevation

### Layering
- **Base**: Solid background color
- **Elevated**: Slightly lighter/darker with shadow
- **Floating**: Prominent shadow, above content
- **Modal**: Highest elevation, dimmed background

### Shadows
- **Card**: `0 2px 8px rgba(0,0,0,0.08)`
- **Elevated**: `0 4px 16px rgba(0,0,0,0.12)`
- **Floating**: `0 8px 24px rgba(0,0,0,0.16)`
- **Modal**: `0 16px 48px rgba(0,0,0,0.24)`

### Blur Effects
- **Navigation**: `backdrop-filter: blur(20px)`
- **Tab Bar**: `backdrop-filter: blur(20px)`
- **Context Menus**: `backdrop-filter: blur(20px)`

## 7. Do's and Don'ts

### Do
- Use generous corner radius (10-12px minimum)
- Apply frosted glass effects for overlays
- Use semantic colors consistently
- Provide haptic feedback for interactions
- Animate with spring physics

### Don't
- Use sharp corners (0-4px radius)
- Create flat designs without depth
- Use pure black in light mode
- Overuse bold text
- Ignore safe areas on mobile

## 8. Responsive Behavior

### Device Adaptations
- **iPhone**: Compact layout, full-width cards
- **iPad**: Sidebar navigation, multi-column grids
- **Mac**: Full window chrome, hover states

### Dynamic Type
- Support accessibility text sizes
- Scale layouts gracefully
- Maintain readability at all sizes

## 9. Agent Prompt Guide

### Quick Reference
```
Colors (Light):
- Background: #ffffff, #f5f5f7
- Text: #1d1d1f, #86868b
- Accent: #007aff

Colors (Dark):
- Background: #000000, #1c1c1e
- Text: #ffffff, #98989d
- Accent: #0a84ff

Typography:
- Font: SF Pro / system-ui
- Large Titles: 34px, bold
- Body: 17px, regular
- Rounded: 10-12px radius

Effects:
- Backdrop blur for overlays
- Soft shadows for elevation
- Spring animations
```

### Example Prompts
```
"Create an Apple-style settings page with grouped cells, 
frosted glass navigation, and SF Pro typography."

"Design a card component following Apple's Human Interface Guidelines: 
rounded corners, subtle shadows, generous padding."

"Build a music player interface with Apple's aesthetic: 
blur effects, large artwork, clean typography."
```
