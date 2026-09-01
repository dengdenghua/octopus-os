---
name: agnes-video-generate
description: "使用火山引擎(火山方舟, doubao-seedance-1.5-pro)或 Agnes AI 网关异步提交视频生成任务。默认 wait=true 阻塞到完成并返回 video_url；也可 wait=false 立即返回 task_id，用 agnes-video-poll 查询进度。支持文字到视频、图像到视频(首帧/首尾帧)。"
enabled: true
aliases: [agnes_video, generate_video_agnes, volcano_video, generate_video_volcano]
---

# Video Generation（火山 / Agnes）

根据 `base_url` 自动选择 provider：

- **火山方舟**（默认）：`https://ark.cn-beijing.volces.com/api/plan/v3`，模型 `doubao-seedance-1.5-pro`
  - 创建：`POST {base}/contents/generations/tasks`
  - 查询：`GET {base}/contents/generations/tasks/{id}`
  - 成功状态：`succeeded`；视频 URL 位于 `content.video_url`
- **Agnes AI Gateway**：`https://apihub.agnes-ai.com/v1`，模型 `agnes-video-v2.0`
  - 创建：`POST {base}/videos`；查询：`GET {base}/videos/{id}`
  - 成功状态：`completed`

## Models

| Provider | ID | 用途 |
|----------|----|----|
| 火山 | `doubao-seedance-1.5-pro` | 文/图→视频（默认） |
| Agnes | `agnes-video-v2.0` | 文/图→视频（回退） |

## Constraints

- **Agnes**：`num_frames` 必须满足 **8n+1**（49, 81, 121, ...）；`frame_rate` ∈ [1,60]
- **火山 Seedance**：`num_frames/frame_rate` 会换算为 `duration`（秒，≥1）；`width,height` 换算为最接近的 `ratio`；分辨率默认 `1080p`

## Configuration

API Key 按优先级取第一个非空值：
`VOLCENGINE_API_KEY` → `ARK_API_KEY` → `AGNES_API_KEY` → `OPENAI_API_KEY`

```bash
export VOLCENGINE_API_KEY=ark-xxx
# base URL 可选，默认火山 plan/v3
export VOLCENGINE_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
```

## Usage

### 文生视频（等待完成，默认）

```python
from agnes_video_generate import generate_video

result = generate_video(
    prompt="一只戴黑色围巾的拟人化小章鱼在书桌上踱步，皮克斯风，透明背景",
    width=1152,
    height=768,
    num_frames=49,
    frame_rate=24,
    wait=True,          # 默认；阻塞到 succeeded 并返回 video_url
)
# {"task_id": "...", "status": "succeeded", "video_url": "https://..."}
```

### 非阻塞提交（配合 agnes-video-poll）

```python
result = generate_video(
    prompt="...",
    wait=False,          # 立即返回 task_id，不阻塞 ReAct 轮次
)
# {"task_id": "cgt-...", "status": "queued"}
```

### 图生视频（首帧 / 首尾帧）

```python
# 首帧
result = generate_video(
    prompt="The woman slowly turns around and looks back at the camera",
    image="https://example.com/portrait.png",
    num_frames=81,
    frame_rate=24,
)

# 首尾帧（两张图）
result = generate_video(
    prompt="Smooth cinematic transition between the two keyframes",
    image=["https://example.com/kf1.png", "https://example.com/kf2.png"],
    num_frames=81,
)
```

## Returns

完成时：
```json
{"task_id": "...", "status": "succeeded",
 "video_url": "https://...mp4", "progress": 100}
```

提交时（wait=False）：
```json
{"task_id": "cgt-...", "status": "queued", "model": "doubao-seedance-1.5-pro"}
```

## Errors

- `RuntimeError("video task failed: ...")` — 任务终态为 failed/expired
- `TimeoutError("video task did not complete within ...s")` — 等待超时