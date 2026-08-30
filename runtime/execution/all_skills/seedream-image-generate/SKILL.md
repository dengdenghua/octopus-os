---
name: seedream-image-generate
description: "使用火山引擎 Seedream 模型从文字描述生成高质量图像，支持多种艺术风格和宽高比。当用户需要生成图片、插画、设计素材或任何视觉内容时调用此技能。"
enabled: true
---

# Seedream 图像生成

## Overview

使用火山引擎 Seedream 模型生成高质量图像。支持：
- 文生图：从文本描述生成图像
- 多种艺术风格
- 多种宽高比（1:1, 16:9, 9:16, 4:3, 3:4）
- 高质量输出（最高 4K）

## Prerequisites

需要配置火山引擎 API Key：
```bash
export ARK_API_KEY=your-api-key-here
```

或在 Echo 配置中设置：
```yaml
seedream:
  api_key: ${ARK_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/v3
```

## Usage

### 基本文生图

```python
from seedream_image_generate import generate_image

result = generate_image(
    prompt="一只可爱的橘猫在樱花树下睡觉，阳光透过花瓣洒下，日系动漫风格",
    width=1024,
    height=1024,
    seed=42
)
# 返回: {"url": "https://...", "local_path": "/path/to/image.png"}
```

### 指定风格

```python
result = generate_image(
    prompt="未来城市夜景，霓虹灯闪烁，赛博朋克风格",
    style="cyberpunk",  # 可选: anime, realistic, oil_painting, watercolor, 3d, pixel_art
    width=1920,
    height=1080
)
```

### 指定宽高比

```python
result = generate_image(
    prompt="山水风景，中国传统水墨画",
    ratio="16:9",  # 可选: 1:1, 16:9, 9:16, 4:3, 3:4
)
```

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| prompt | str | 是 | 图像描述，建议 100-500 字，越详细效果越好 |
| width | int | 否 | 图像宽度，默认 1024 |
| height | int | 否 | 图像高度，默认 1024 |
| ratio | str | 否 | 快捷宽高比，覆盖 width/height |
| style | str | 否 | 艺术风格，默认 "default" |
| seed | int | 否 | 随机种子，相同种子可复现结果 |
| negative_prompt | str | 否 | 负面描述，排除不需要的元素 |
| num_images | int | 否 | 生成数量，默认 1，最大 4 |

## Supported Styles

- `default` - 默认风格
- `anime` - 日系动漫
- `realistic` - 写实照片
- `oil_painting` - 油画
- `watercolor` - 水彩
- `3d` - 3D 渲染
- `pixel_art` - 像素艺术
- `chinese_ink` - 中国水墨
- `ukiyo_e` - 浮世绘
- `sketch` - 铅笔素描

## Output

返回 JSON 格式：
```json
{
  "success": true,
  "images": [
    {
      "url": "https://...",
      "local_path": "/path/to/image_001.png",
      "width": 1024,
      "height": 1024,
      "seed": 42
    }
  ],
  "prompt": "原始提示词",
  "style": "anime"
}
```

## Error Handling

- `API_KEY_MISSING` - 未配置 ARK_API_KEY
- `PROMPT_TOO_LONG` - 提示词超过 1000 字符
- `RATE_LIMITED` - 请求频率限制
- `GENERATION_FAILED` - 生成失败，建议重试或调整提示词

## Examples

### 产品图生成
```python
generate_image(
    prompt="一瓶高端香水，玻璃材质，金色瓶盖，放置在白色大理石台面上，柔和的自然光，产品摄影风格，背景虚化",
    style="realistic",
    ratio="4:3"
)
```

### 头像生成
```python
generate_image(
    prompt="年轻女性，短发，微笑，穿着白色衬衫，纯色背景，证件照风格",
    style="realistic",
    ratio="1:1"
)
```

### 插画生成
```python
generate_image(
    prompt="魔法森林中的小精灵，发光翅膀，周围有萤火虫，梦幻氛围",
    style="anime",
    ratio="9:16"
)
```
