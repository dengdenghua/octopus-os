# ComfyUI Bridge 调用顺序

## 探测与选择

- `comfyui_bridge.status`：读取本机服务在线状态、版本和基础能力。
- `comfyui_bridge.dependencies`：读取托管引擎、Python、模型目录和扩展依赖状态。
- `comfyui_bridge.workflows`：列出内置与用户工作流。
- `comfyui_bridge.workflow_get`：按工作流 ID 读取真实 API-prompt、来源和 revision。
- `comfyui_bridge.workflow_diagnostics`：以本机 `object_info` 和模型盘点检查节点、输入、枚举与资源。

## 修改与运行

- `comfyui_bridge.workflow_save`：保存工作流 ID、API-prompt JSON 和期望 revision。冲突后重新读取再合并。
- `comfyui_bridge.queue`：按工作流 ID 和经用户确认的输入覆盖提交本机队列，返回 `prompt_id`。
- `comfyui_bridge.result`：按 `prompt_id` 读取队列状态、错误和真实输出列表。

先诊断后排队。排队成功只证明 ComfyUI 接受了请求，只有结果状态完成且输出可读取才算生成成功。
