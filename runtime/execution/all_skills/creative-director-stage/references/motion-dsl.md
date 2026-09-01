# 动作 DSL

Echo 动作源码采用逐行时间事件，时间单位为毫秒。使用 `motion_read` 读取相近内置动作，再以相同结构创建或更新自定义动作。

```text
0ms pose stand
240ms step left
500ms pose stand
760ms step right
1000ms pose stand
```

当前可视预演识别四类事件：

- `Tms pose POSE`：切换到导演台支持的标准姿态。
- `Tms step left|right`：行走步态事件。
- `Tms lean DEG`：前倾动作，适合跑步准备。
- `Tms torso DEG`：躯干俯仰，适合鞠躬。

时间必须单调递增。循环动作的最后事件应回到首姿态，`cycleMs` 与最后时间一致；`defaultMs` 是时间线片段省略时长时采用的默认值。更新已有动作要沿用原 ID，避免时间线片段失去引用。
