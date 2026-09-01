---
name: creative-director-stage
description: 使用 Echo 3D 导演台编排角色、场景、机位、姿态、走位和运镜；适用于分镜预演、镜头构图和生成视频前的空间验证。
license: Apache-2.0
metadata:
  category: 平台工具
  origin: echo-original
---

# 3D 导演台编排

把自然语言镜头要求转换为可检查的场景树、角色变换、相机参数、姿态片段和时间线。导演台用于低成本验证空间与镜头，不替代高精度 CG 制作。

## 工作流

1. 明确镜头目标、叙事重点、画幅、主要角色和必要道具。未知信息用低成本默认值，不伪造外部模型。
2. 先做空间 blockout：场景尺度、角色站位、视线、前后景和机位；布局未通过前不添加装饰细节。
3. 选择标准男、标准女或儿童素体，使用导演台支持的姿态建立关键动作。坐姿和驾驶姿态必须同时校正角色高度与朝向。
4. 建立相机与运镜片段，逐段检查起点、落点、遮挡、穿模、焦段和动作连续性。
5. 导出关键视角作为分镜证据，并把场景参数随项目交付物保存。

空间调度见 [references/blocking.md](references/blocking.md)，镜头设计见 [references/camera-language.md](references/camera-language.md)，动作设计见 [references/motion-craft.md](references/motion-craft.md)。编写源码时分别读取 [references/motion-dsl.md](references/motion-dsl.md) 与 [references/campath-dsl.md](references/campath-dsl.md)；搭建目录外对象时读取 [references/code-model-craft.md](references/code-model-craft.md)。视觉验收见 [references/verification.md](references/verification.md)。只在对应任务需要时读取。
调用导演台的读取、原子编辑、历史、动作 DSL 或运镜路径方法时，读取 [references/api.md](references/api.md)。

## 数据约定

- 使用右手坐标系、Y 轴向上；位置以米、旋转以度、时间以毫秒或明确标注的秒数表示。
- 场景数据至少保存 revision、环境、相机、角色、道具、路径和时间线引用。
- 角色可用姿态包括站立、T Pose、行走、奔跑、跳跃、坐下、深蹲、跪姿、躺卧、驾驶、挥手、举手、鞠躬、叉腰、思考、格斗、瞄准、持剑和施法。
- 修改前读取当前状态；修改后再次读取并导出视角验证。结构数据不是视觉证据。

这是 Echo 原创技能，不包含 MiniMax 私有提示词、模型或未授权插件源码。
