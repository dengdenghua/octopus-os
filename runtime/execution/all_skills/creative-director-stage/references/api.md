# 导演台方法契约

插件基础路径：`/api/plugins/director-stage/scenes/{scene_id}`。Agent 使用同名的
`director_stage.*` Skill；所有 ID 都可以使用唯一前缀，不能编造。

## 读取与编辑

- `scene_get`：`view=summary|entities|timeline|full`。需要精确位置、动作或路径时先读。
- `scene_edit`：一次提交 1–50 个操作，成功后作为一个历史步骤；任一步失败整批回滚。
- `scene_history`：`action=undo|redo`，`steps=1..20`。
- `scene_diagnostics`：只检查机械错误，不证明构图和画面质量。

常用编辑操作：

- 环境：`set_scene`、`set_environment`
- 实体：`add_character`、`add_camera`、`add_prop`、`rename`、`remove_entity`/`remove`
- 角色/相机：`set_transform`、`set_pose`、`set_camera`
- 运镜与走位：`add_camera_path`、`set_camera_path`、`add_move_path`、`remove_track`
- 动作：`set_motion`、`remove_motion`、`add_animation_clip`
- 模型：Agent 优先调用 `model_generate`；底层原子操作为 `generate_model`

位置单位为米、旋转为度、时间线单位为秒；动作 DSL 内的时间戳为毫秒。

`add_prop` 当前提供 `chair`、`sofa`、`square_table`、`desk`、`bed`、`wall`、
`platform`、`column`、`tree_small`、`rock`、`car`、`bench`、`crate`、`barrel`。
场景缺少的语义对象仍应使用安全声明式 `model_generate`，不能拿不相干道具冒充。

## 动作与运镜源码

- `motion_read(scene_id, motion_id)` 返回内置或自定义动作的 DSL、循环与默认时长。
- `campath_read(scene_id, path_id)` 返回控制点、时长、缓动、循环模式、注视点和可选 DSL。

修改自定义动作或运镜前先读取源码，再用相同 ID 更新，避免丢失时间线引用。

## 安全程序化模型

- `model_generate(scene_id, parts, model_id?, label?, position?, rotation?, scale?)`
- `model_capture(scene_id, model_id, views?, max_dim?)`
- `model_compare(scene_id, model_id, reference_path, view?)`

`parts` 是 1–64 个声明式部件；支持 `box`、`sphere`、`cylinder`、`cone`，每个部件
提供 `name`、`size`、`position`、`rotation`、`color`，可选 `metalness`、`roughness`。
这是安全边界：不接受或执行任意 JavaScript。传相同 `model_id` 会原位替换并保留场景引用。

生成后先读 `bbox`、`partDetails` 和 `warnings`，再调用 `model_capture` 实际查看多视角 PNG。
`model_compare` 的像素得分只能衡量轮廓、布局和色彩接近度，不能证明语义或美术质量。

## 视觉边界

`scene_snapshot(scene_id, view?)` 读取导演台真实 WebGL 画布同步的 PNG。编辑器未打开或尚未
完成首帧时返回 `PREVIEW_NOT_READY`，此时不能用结构数据替代。成功返回 `frames[].path`
后必须实际查看图片，才能判断遮挡、穿模、构图、材质或灯光。
