---
name: creative-comfyui-workflow
description: 使用 Echo ComfyUI 工作流桥接诊断、编辑、保存和运行本机节点工作流，并核对真实生成结果；适用于文生图、图生图、放大和用户已安装扩展支持的图像、视频或音频流程。
license: Apache-2.0
metadata:
  category: 平台工具
  origin: echo-original
---

# ComfyUI 工作流编排

把用户的创作目标转换成可检查、可保存、可运行的 ComfyUI 图。先确认本机能力，再修改工作流；不要从模板名称推断节点、模型或扩展已经存在。

## 工作流

1. 调用 `comfyui_bridge.status` 和 `comfyui_bridge.dependencies`，确认服务、版本、模型目录和依赖状态。
2. 调用 `comfyui_bridge.workflows` 选择工作流，再用 `comfyui_bridge.workflow_get` 读取真实 API-prompt JSON。优先复用用户指定或已验证模板。
3. 调用 `comfyui_bridge.workflow_diagnostics` 核对节点类型、必填输入、枚举值和本机模型。错误未清零前不得宣称可运行。
4. 仅修改完成目标所需的输入或节点。保存用户工作流时调用 `comfyui_bridge.workflow_save`，保留最新 revision，遇到冲突先重新读取。
5. 调用 `comfyui_bridge.queue` 排队，取得 `prompt_id` 后用 `comfyui_bridge.result` 查询到完成或失败。不要把已排队当成已生成。
6. 检查真实输出文件、媒体类型、尺寸和警告；视觉任务必须查看结果再判断构图、主体、文字、身份一致性和明显伪影。

接口顺序和字段见 [references/api.md](references/api.md)，节点与工作流修改原则见 [references/workflow-craft.md](references/workflow-craft.md)，权限和依赖边界见 [references/safety.md](references/safety.md)，完成标准见 [references/verification.md](references/verification.md)。只读取当前任务需要的参考文件。

## 约束

- 只连接 loopback 本机服务，不把工作流或凭据发送到任意远端 ComfyUI 地址。
- 不由 Agent 安装、更新或卸载 ComfyUI、节点扩展，也不自动下载模型；这些动作必须由用户在界面中逐项确认。
- 不编造 checkpoint、VAE、LoRA、ControlNet 或自定义节点名称。所有值来自工作流、`object_info` 或模型盘点。
- 不覆盖用户工作流的新 revision，不将结构 JSON、空预览或队列 ID 冒充生成结果。

这是 Echo 原创技能，不包含 MiniMax 私有提示词、工作流、模型或未授权插件源码。
