---
name: "awesome-design-md"
description: "58个大厂设计系统DESIGN.md集合。使用 $awesome-design-md 触发，为项目安装指定品牌的设计规范（如 Vercel、Apple、Stripe 等）。"
---

# Awesome DESIGN.md

## 简介

Awesome DESIGN.md 是一个精选的设计系统集合，包含 58 个顶级大厂（Vercel、Apple、Stripe、Linear 等）的 DESIGN.md 文件。

## 使用方法

### 1. 自然语言触发

在对话框中输入 `$awesome-design-md` 或直接使用自然语言：

```
"Use $awesome-design-md to install a vercel-style DESIGN.md"
"使用 awesome-design-md 安装 Apple 风格的设计规范"
"给我推荐 3 个适合 B2B 产品的设计风格"
```

### 2. 查看所有品牌

```bash
# 列出所有支持的品牌
ls skills/local/awesome-design-md/designs/
```

## 推荐风格

### 开发者工具 / SaaS
- **Vercel** - 极简黑白，高对比度，Geist 字体
- **Linear** - 超极简，精准，紫色强调
- **Supabase** - 深翠绿主题，代码优先
- **Raycast** - 深色 Chrome，鲜艳渐变强调

### 消费级应用
- **Apple** - 磨砂玻璃质感，大留白，精致圆角
- **Airbnb** - 温暖友好，大图展示
- **Stripe** - 标志性紫色渐变，排版优雅

### 生产力工具
- **Notion** - 呼吸感强，图标克制
- **Figma** - 鲜艳多彩， playful 但专业
- **Framer** - 大胆黑蓝，动效优先

### AI 产品
- **Claude** - 温暖陶土色强调，干净编辑布局
- **Cursor** - AI 优先代码编辑器，深色界面
- **Mistral AI** - 法式极简，紫色调

## DESIGN.md 结构

每个 DESIGN.md 包含以下部分：

1. **Visual Theme & Atmosphere** - 视觉主题和氛围
2. **Color Palette & Roles** - 调色板和角色
3. **Typography Rules** - 排版规则
4. **Component Stylings** - 组件样式
5. **Layout Principles** - 布局原则
6. **Depth & Elevation** - 深度和层级
7. **Do's and Don'ts** - 设计准则
8. **Responsive Behavior** - 响应式行为
9. **Agent Prompt Guide** - AI 提示指南

## 安装设计规范

### 方式一：复制文件

```bash
# 复制 Vercel 风格到项目根目录
cp skills/local/awesome-design-md/designs/vercel/DESIGN.md ./DESIGN.md

# 或复制到 docs 目录
cp skills/local/awesome-design-md/designs/vercel/DESIGN.md ./docs/VERCEL-STYLE.md
```

### 方式二：使用脚本

```bash
# 安装指定品牌的设计规范
skills/local/awesome-design-md/scripts/add-design.sh vercel

# 安装到指定路径
skills/local/awesome-design-md/scripts/add-design.sh vercel ./docs/
```

## 使用 DESIGN.md

安装后，告诉 AI 助手遵守设计规范：

```
"请始终遵守根目录 DESIGN.md 中的视觉约束"
"按照 DESIGN.md 的规范重新设计这个页面"
"使用 DESIGN.md 中的调色板和组件样式"
```

## 完整品牌列表

### AI & Machine Learning
- Claude, Cohere, ElevenLabs, Minimax, Mistral AI, Ollama, OpenCode AI, Replicate, RunwayML, Together AI, VoltAgent, xAI

### Developer Tools
- Cursor, Expo, Linear, Lovable, Mintlify, PostHog, Raycast, Resend, Sentry, Supabase, Superhuman, Vercel, Warp, Zapier

### Infrastructure
- ClickHouse, Composio, HashiCorp, MongoDB, Sanity, Stripe

### Design & Productivity
- Airtable, Cal.com, Clay, Figma, Framer, Intercom, Notion, Pitch, Read.cv

### E-commerce
- Shopify, Squarespace, Webflow

### Social & Community
- Discord, Reddit, Twitter/X

### 更多
- GitHub, GitLab, Sourcegraph, Vercel Commerce

## 最佳实践

1. **选择匹配的风格** - 根据产品类型选择合适的设计系统
2. **局部微调** - 在 DESIGN.md 中修改特定值以适应需求
3. **保持一致性** - 整个项目使用同一套设计规范
4. **定期更新** - 关注设计系统的更新和演变

## 文件结构

```
skills/local/awesome-design-md/
├── SKILL.md                    # 本文件
├── designs/                    # 设计规范集合
│   ├── vercel/
│   │   ├── DESIGN.md
│   │   ├── preview.html
│   │   └── preview-dark.html
│   ├── apple/
│   ├── stripe/
│   └── ... (58个品牌)
└── scripts/
    ├── add-design.sh           # 安装脚本
    └── list-brands.sh          # 列出品牌
```

