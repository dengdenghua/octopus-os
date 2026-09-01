---
name: seedance-video-generate
description: "使用火山引擎 Seedance 模型从文字提示、图像或参考素材生成视频。支持多种视频风格、时长和分辨率。当用户需要生成视频内容、动画、广告片或任何动态视觉内容时调用此技能。"
enabled: true
---

# Seedance 视频生成

## Overview

使用火山引擎 Seedance 模型生成高质量视频。支持：
- 文生视频：从文本描述生成视频
- 图生视频：从静态图像生成动态视频
- 视频延长：基于已有视频继续生成
- 多种视频风格（写实、动漫、3D 等）
- 多种分辨率和时长

## Prerequisites

需要配置火山引擎 API Key：
```bash
export ARK_API_KEY=your-api-key-here
```

或在 Echo 配置中设置：
```yaml
seedance:
  api_key: ${ARK_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/v3
```

## Usage

### 基本文生视频

```python
from seedance_video_generate import generate_video

result = generate_video(
    prompt="一只金毛犬在草地上奔跑，阳光明媚，慢动作镜头",
    duration=5,  # 5秒
    resolution="720p",
    style="realistic"
)
# 返回: {"url": "https://...", "local_path": "/path/to/video.mp4"}
```

### 图生视频

```python
result = generate_video(
    prompt="让这张图片动起来，风吹动头发，微笑",
    image_path="/path/to/input_image.png",  # 输入图片
    duration=4,
    motion_strength=0.7  # 运动强度 0-1
)
```

### 视频延长

```python
result = extend_video(
    video_path="/path/to/existing_video.mp4",
    prompt="继续这个场景，人物走向远方",
    extend_duration=3  # 延长3秒
)
```

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| prompt | str | 是 | 视频描述，建议 50-300 字 |
| image_path | str | 否 | 输入图片路径（图生视频时必填） |
| video_path | str | 否 | 输入视频路径（视频延长时必填） |
| duration | int | 否 | 视频时长（秒），默认 5，最大 10 |
| resolution | str | 否 | 分辨率，默认 "720p" |
| style | str | 否 | 视频风格，默认 "default" |
| motion_strength | float | 否 | 运动强度 0.0-1.0，默认 0.5 |
| seed | int | 否 | 随机种子 |
| fps | int | 否 | 帧率，默认 24 |

## Supported Styles

- `default` - 默认风格
- `realistic` - 写实风格
- `anime` - 日系动漫
- `3d` - 3D 动画
- `cinematic` - 电影感
- `documentary` - 纪录片风格

## Supported Resolutions

- `480p` - 854x480
- `720p` - 1280x720
- `1080p` - 1920x1080

## Output

返回 JSON 格式：
```json
{
  "success": true,
  "video": {
    "url": "https://...",
    "local_path": "/path/to/video_001.mp4",
    "duration": 5,
    "resolution": "720p",
    "fps": 24,
    "seed": 42
  },
  "prompt": "原始提示词",
  "style": "realistic"
}
```

## Error Handling

- `API_KEY_MISSING` - 未配置 ARK_API_KEY
- `PROMPT_TOO_LONG` - 提示词超过 500 字符
- `INVALID_IMAGE` - 输入图片格式不支持或损坏
- `DURATION_EXCEEDED` - 时长超过限制
- `RATE_LIMITED` - 请求频率限制
- `GENERATION_FAILED` - 生成失败

## Examples

### 产品展示视频
```python
generate_video(
    prompt="一款智能手表，360度旋转展示，金属表带反光，科技感背景",
    duration=5,
    resolution="1080p",
    style="cinematic"
)
```

### 动画角色
```python
generate_video(
    prompt="一个可爱的卡通机器人跳舞，色彩鲜艳，循环动作",
    duration=4,
    style="3d",
    fps=30
)
```

### 自然风景
```python
generate_video(
    prompt="瀑布从悬崖倾泻而下，水雾弥漫，彩虹出现，航拍视角",
    duration=8,
    resolution="1080p",
    style="documentary"
)
```
