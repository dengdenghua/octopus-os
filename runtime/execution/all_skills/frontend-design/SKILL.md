---
name: "frontend-design"
description: "前端设计技能，包含 UI/UX 设计原则、响应式设计、设计系统构建。在设计界面或实现设计时调用。"
---

# Frontend Design

## UI Design Principles

### 1. Visual Hierarchy
- Size and scale
- Color and contrast
- Typography
- Spacing and layout
- Position and alignment

### 2. Consistency
- Design tokens
- Component library
- Style guide
- Pattern library

### 3. Accessibility
- WCAG 2.1 compliance
- Color contrast (4.5:1 minimum)
- Keyboard navigation
- Screen reader support
- Focus indicators

## Design System

### Color Palette
```css
:root {
  --primary: #3b82f6;
  --primary-dark: #2563eb;
  --secondary: #64748b;
  --success: #22c55e;
  --warning: #f59e0b;
  --error: #ef4444;
  --background: #ffffff;
  --foreground: #0f172a;
}
```

### Typography Scale
- **Display**: 48px / 600 weight
- **H1**: 36px / 600 weight
- **H2**: 24px / 600 weight
- **H3**: 20px / 500 weight
- **Body**: 16px / 400 weight
- **Small**: 14px / 400 weight
- **Caption**: 12px / 400 weight

### Spacing Scale
- xs: 4px
- sm: 8px
- md: 16px
- lg: 24px
- xl: 32px
- 2xl: 48px
- 3xl: 64px

## Responsive Design

### Breakpoints
- Mobile: < 640px
- Tablet: 640px - 1024px
- Desktop: > 1024px

### Mobile-First Approach
```css
/* Base styles for mobile */
.component {
  padding: 16px;
}

/* Tablet */
@media (min-width: 640px) {
  .component {
    padding: 24px;
  }
}

/* Desktop */
@media (min-width: 1024px) {
  .component {
    padding: 32px;
  }
}
```

## Component Patterns

### Button States
- Default
- Hover
- Active
- Disabled
- Loading

### Form Patterns
- Input with label
- Error states
- Helper text
- Required indicators

### Card Patterns
- Image + content
- Header + body + footer
- Action cards
- Selection cards

## Animation Guidelines

### Timing
- Micro: 150ms
- Standard: 300ms
- Complex: 500ms

### Easing
- Default: ease-in-out
- Enter: ease-out
- Exit: ease-in
- Bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55)

### Performance
- Use transform and opacity
- Avoid animating layout properties
- Use will-change sparingly

## Design Tools

### Recommended
- Figma for UI design
- Tailwind CSS for styling
- shadcn/ui for components
- Radix UI for primitives
