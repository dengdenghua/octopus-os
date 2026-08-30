---
name: agnes-image-generate
description: "使用火山引擎(火山方舟)或 Agnes AI 网关从文字描述生成图像。默认火山 doubao-seedream-5.0-lite（文生图+图生图），未配置火山 key 时自动回退 Agnes agnes-image-2.1-flash。当用户需要生成图片、插画或封面时调用此技能。"
enabled: true
aliases: [agnes_image, generate_image_agnes, volcano_image, generate_image_volcano]
---

# Image Generation（火山 / Agnes）

根据 `base_url` 自动选择 provider：

- **火山方舟**（默认）：`https://ark.cn-beijing.volces.com/api/plan/v3`，模型 `doubao-seedream-5.0-lite`
- **Agnes AI Gateway**：`https://apihub.agnes-ai.com/v1`，模型 `agnes-image-2.1-flash`

## Models

| Provider | ID | 用途 |
|----------|----|----|
| 火山 | `doubao-seedream-5.0-lite` | text→image + image→image（默认） |
| Agnes | `agnes-image-2.1-flash` | text→image + image→image（回退） |

## Configuration

API Key 按优先级取第一个非空值：
`VOLCENGINE_API_KEY` → `ARK_API_KEY` → `AGNES_API_KEY` → `OPENAI_API_KEY`

```bash
# 火山（推荐）
export VOLCENGINE_API_KEY=ark-xxx
# base URL 可选，默认火山 plan/v3
export VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3

# Agnes 回退
export AGNES_API_KEY=sk-...
```

## Usage

### 文生图

```python
from agnes_image_generate import generate_image

result = generate_image(
    prompt="a tiny red panda holding a paintbrush, soft studio lighting, 4k",
)
# {"url": "https://...", "model": "doubao-seedream-5.0-lite"}
```

### 指定尺寸 + 数量

```python
result = generate_image(
    prompt="cinematic dragon over Hong Kong skyline at dusk",
    size="2048x2048",   # 火山推荐 2048x2048 / 2304x1728 / 2560x1440 ...
    n=2,
)
# {"urls": ["...", "..."], ...}
```

### 图生图（参考图）

```python
result = generate_image(
    prompt="same cat but in oil painting style",
    image="https://example.com/cat.png",
)
```

## Returns

```json
{
  "url": "https://...",
  "urls": ["..."],
  "model": "doubao-seedream-5.0-lite",
  "created": 1780826984,
  "usage": {}
}
```

## Errors

- `ValueError("No API key found")` — 未配置任何 provider 的 key。
- `RuntimeError("image API error: ...")` — 非 200 响应；底层信息保留。