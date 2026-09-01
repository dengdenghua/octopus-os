# 运镜路径 DSL 与控制点

当前导演台以三维控制点作为路径真值，并可在 `source` 保存可编辑的文本来源。每条路径至少两个点，位置单位为米，时间线单位为秒。

建议源码格式：

```text
duration 5s
easing easeInOut
lookAt 0 1.2 0
0s point 0 1.6 5
2.5s point 3.5 2.1 2.8
5s point 0 1.6 -5
```

提交时把解析后的坐标写入 `add_camera_path.points` 或 `set_camera_path.points`，同时将原文保存在 `source`。更新路径时先用 `campath_read` 读取原 ID，再原位修改，保证时间线引用稳定。

- 两点路径适合稳定推拉或横移；三点以上用于绕行和揭示。
- 控制点间距决定速度感，连续点过密会造成停顿。
- `lookAt` 应落在主体胸口或动作中心附近，不能机械锁定世界原点。
- 修改后拖动播放头检查全程，而不是只看起止帧。
