// Copy keys are English source strings. Each locale maps the English
// source to its translation. When a locale omits a key, the English
// source is displayed as-is (graceful fallback).
export type WorkspaceComputerCopy = Record<string, string>;

export const workspaceComputerEnUS: WorkspaceComputerCopy = {};

export const workspaceComputerZhCN: WorkspaceComputerCopy = {
  "Local computer automation": "本地电脑自动化页",
  "Confirmation expired": "确认队列已过期",
  "The server cleared the token. Preview the action again.":
    "token 已在服务端清除，请重新预演动作",
  "Failed to read status": "状态读取失败",
  "Live screen started": "实时屏幕已启动",
  "The computer view is now live. Clicking the view only selects a point; it does not execute anything.":
    "电脑画面进入工位观察区；点击画面只会选点，不会直接执行。",
  "Failed to start live screen": "实时屏幕启动失败",
  "Live screen stopped": "实时屏幕已停止",
  "The current workspace layout is preserved. Restart the live screen when needed.":
    "保留当前工位布局；需要继续观察时可重新启动实时屏幕。",
  "Failed to stop live screen": "实时屏幕停止失败",
  "Current screen captured": "已观察当前屏幕",
  "Screenshot failed": "截图失败",
  "Screenshot request failed": "截图请求失败",
  "Action added to confirmation queue": "动作已进入确认队列",
  "Action preview failed": "动作预演失败",
  "Screen point added to confirmation queue": "截图坐标已进入确认队列",
  "Point-click preview failed": "坐标点击预演失败",
  "Next-step plan generated": "已生成下一步计划",
  "No plan available": "没有可用计划",
  "Describe the goal more specifically.": "请补充更明确的任务目标",
  "Failed to generate plan": "计划生成失败",
  "Candidate action added to confirmation queue": "候选动作已放入确认队列",
  "Agent preview complete": "Agent 已完成预演",
  "The first step is awaiting your confirmation before it runs.":
    "第一步已进入确认队列，需要你确认后才会执行。",
  "Agent has no executable next step": "Agent 暂无可执行下一步",
  "No candidate action was generated. Describe the goal more specifically.":
    "没有生成候选动作，请补充更明确的目标。",
  "Agent loop preview failed": "Agent 循环预演失败",
  "Vision output validated": "视觉输出已校验",
  "No action found": "没有解析到动作",
  "Paste a valid JSON action.": "请粘贴标准 JSON 动作",
  "Failed to parse vision output": "视觉输出解析失败",
  "Vision model returned actions": "视觉模型已返回动作",
  "Vision model is not ready": "视觉模型未就绪",
  "Configure a vision model.": "请配置视觉模型",
  "Vision model request failed": "视觉模型调用失败",
  "Point selected from screenshot": "已从截图选中坐标",
  "Point selected from live screen": "已从实时屏幕选中坐标",
  "Action executed": "动作已执行",
  "Action failed": "动作执行失败",
  "Execution request failed": "执行请求失败",
  "Computer control released": "已释放电脑接管",
  "Other projects can now take control of this computer.":
    "其他项目现在可以接管这台电脑。",
  "Failed to release control": "释放接管失败",
  "Connection error": "连接异常",
  "frames": "帧",
  "Waiting for screen": "等待画面",
  "Not started": "未启动",
  "Computer assistant": "本机助手",
  "Let the Agent see and operate this computer. Every step is previewed before you confirm it.":
    "让 Agent 看见并操作这台电脑。每一步先预演，再由你确认。",
  "Refresh status": "刷新状态",
  "Capture screen": "观察屏幕",
  "Runtime health": "运行健康",
  "Confirmation mode": "确认方式",
  "Preview, then confirm": "先预演再确认",
  "Loading": "加载中",
  "Screen": "屏幕",
  "Cursor position": "鼠标位置",
  "Computer control": "电脑控制",
  "Ready": "已就绪",
  "Not ready": "未就绪",
  "Semantic targeting": "语义定位",
  "Available with limits": "降级可用",
  "Control lease": "接管租约",
  "Local runtime is blocked": "本机运行时被阻塞",
  "The backend is running, but required automation capabilities are not ready. Screenshots, previews, mouse, and keyboard actions are temporarily unavailable.":
    "后端已启动，但关键电脑自动化能力还没有就绪，暂时不能截图、预演或执行鼠标键盘动作。",
  "Screen observation": "屏幕观察",
  "Snapshot": "截图",
  "Live": "实时",
  "Stop live": "停止实时",
  "Start live": "启动实时",
  "Click the live screen to select a point. Press Enter to select the center.":
    "点击实时屏幕选择坐标；按 Enter 可选择屏幕中心点。",
  "Live computer screen": "实时电脑屏幕",
  "Waiting for the live screen": "等待实时屏幕画面",
  'Select “Start live” to open the computer view.':
    "点击“启动实时”打开电脑工位画面",
  "Click the screenshot to select a point. Press Enter to select the center.":
    "点击截图选择坐标；按 Enter 可选择屏幕中心点。",
  "Current screen screenshot": "当前屏幕截图",
  'Select “Capture screen” to take a desktop screenshot.':
    "点击“观察屏幕”获取当前桌面截图",
  "Task plan": "任务计划",
  "For example: open Edge and visit https://gemini.google.com":
    "例如：打开 Edge 并访问 https://gemini.google.com",
  "Preview agent loop": "Agent 循环预演",
  "Observe and plan next step": "观察并生成下一步",
  "Add for confirmation": "加入确认",
  "candidate actions awaiting confirmation": "个候选动作等待确认",
  "actions awaiting confirmation": "个动作等待确认",
  "vision models": "个视觉模型",
  "Use selected point": "填入坐标",
  "Vision output": "视觉输出",
  "Select a vision model": "选择视觉模型",
  "Vision model ID, for example glm-vision": "视觉模型 ID，例如 glm-vision",
  "Run model": "调用模型",
  "Current: ": "当前：",
  "Could not load the model list. You can enter a model ID manually.":
    "模型列表读取失败，可手动输入模型 ID。",
  "No model is marked supports_vision. Enable vision for a custom model in Settings.":
    "暂无标记 supports_vision 的模型，可先在设置里给自定义模型开启视觉能力。",
  "Open model settings": "去模型设置",
  "The current screenshot is sent to the selected vision model. Returned actions still require confirmation.":
    "调用模型会把当前屏幕截图发送到所选视觉模型；返回动作仍需确认后才执行。",
  "Parse and add candidates": "解析并加入候选",
  "Action preview": "动作预演",
  "Click point": "点击坐标",
  "Move cursor": "移动鼠标",
  "Type text": "输入文字",
  "Keyboard shortcut": "快捷键",
  "Wait": "等待",
  "Text to type into the focused control": "要输入到当前焦点的文字",
  "ctrl+l or enter": "ctrl+l 或 enter",
  "Milliseconds": "毫秒",
  "Generate confirmation": "生成确认",
  "Confirmation queue": "确认队列",
  "Risk: ": "风险：",
  "Confirm and run": "确认执行",
  "Mouse and keyboard actions awaiting confirmation appear here. Nothing touches the system before confirmation.":
    "这里会显示待确认的鼠标、键盘动作。确认前不会操作系统。",
  "Activity log": "操作记录",
  "No activity yet": "还没有记录",
  "Replay evidence": "回放证据",
  "Runtime": "运行时",
  "Idle": "空闲",
  "No project currently controls the physical mouse or keyboard.":
    "当前没有项目接管真实鼠标键盘。",
  "Another project": "其他项目",
  "This project": "本项目",
  "This project controls the physical input; ": "本项目正在接管真实鼠标键盘，",
  "remaining before another project can take over.": "内会阻止其他项目抢占。",
  " controls the physical input; ": "正在接管真实鼠标键盘，",
  "remaining before automatic release.": "后自动释放。",
  "Checking": "正在检查",
  "Checking whether the Agent can observe and operate this computer.":
    "正在确认这台电脑是否可以被 Agent 观察和操作。",
  "Unavailable": "不可用",
  "This environment cannot read the screen or perform computer actions.":
    "当前环境还不能读取屏幕或执行电脑动作。",
  "Capabilities required": "需要补能力",
  "The backend responded, but computer control capabilities are not ready.":
    "后端已响应，但电脑控制能力还没有就绪。",
  "Screen connected": "屏幕已连接",
  "Connected": "已连接",
  "The screen can be observed and actions can run after confirmation. ":
    "可以观察当前屏幕，并在确认后执行操作。",
  "Checking computer assistant": "正在检查本机助手",
  "Required computer automation capabilities failed runtime checks.":
    "关键电脑自动化能力未通过运行时检查。",
  "pyautogui is unavailable": "pyautogui 不可用",
  "Blocked": "阻塞",
  "Computer assistant blocked": "本机助手被阻塞",
  "Computer assistant available with limits": "本机助手降级可用",
  " unavailable; observation, preview, and confirmed execution still work.":
    "暂不可用；基础观察、预演和确认执行仍可继续。",
  "Some optional capabilities are unavailable. Observation, preview, and confirmed execution still work.":
    "部分非关键能力暂不可用；基础观察、预演和确认执行仍可继续。",
  "Computer assistant ready": "本机助手已就绪",
  "Required capabilities passed runtime checks. ":
    "关键能力已通过运行时检查。",
  "Waiting for confirmation": "等待你确认",
  "until expiry": "后过期",
  "Check connection": "检查连接",
  "Parse actions": "解析动作",
  "Request vision model": "请求视觉模型",
  "Generate plan": "生成计划",
  "Execute action": "执行操作",
  "Release control": "释放接管",
  "Switch live screen": "切换实时屏幕",
  "Working": "正在处理",
  "New computer actions wait until the current action finishes.":
    "当前动作完成前，新的电脑动作会先排队等待。",
  "Candidate actions available": "有候选动作",
  "candidate actions. Select one to add it to the confirmation queue.":
    "个候选动作，选择后会进入确认队列。",
  "Screen observed": "已观察屏幕",
  "Select a point on the screenshot or ask the vision model for the next step.":
    "可以点击截图选坐标，或让视觉模型生成下一步。",
  "Waiting for a task": "等待任务",
  "Capture the screen or describe what you want the Agent to do.":
    "先观察屏幕，或描述你希望 Agent 在电脑上完成什么。",
  "Action": "动作",
  "Click": "点击",
  "Move": "移动",
  "Current cursor": "当前鼠标",
  "Selected point": "手动选点",
  "Unnamed control": "未命名控件",
  "UIA match: ": "UIA 命中：",
  "Center": "中心",
  "Query": "查询",
  "This computer": "这台电脑",
  "Observe": "观察",
  "Screen screenshot captured": "已获取屏幕截图",
  "Only observes the screen when you request it": "只在你点击后观察屏幕",
  "Confirm": "确认",
  "An action is awaiting confirmation": "有动作等待确认",
  "Mouse and keyboard actions never run automatically": "鼠标键盘不会直接执行",
  "Expiry": "过期",
  "until automatic removal": "后自动清除",
  "Confirmation tokens are short-lived": "确认令牌短时有效",
  "Control": "接管",
  "Permission guard": "权限护栏",
  "Owner": "所有者",
  "Target": "目标",
  "Current action": "当前动作",
  "Control session": "控制会话",
  "Previews, executions, and screenshots are retained as control evidence.":
    "预演、执行、截图会沉淀为控制证据。",
  "Running": "执行中",
  "Paused": "暂停",
  "Expired": "已过期",
};

// Japanese and Korean currently inherit the English source strings (en-US is
// empty, so the key itself is displayed). This avoids leaking Chinese strings
// while those locale packs are completed.
export const workspaceComputerJaJP = workspaceComputerEnUS;
export const workspaceComputerKoKR = workspaceComputerEnUS;
