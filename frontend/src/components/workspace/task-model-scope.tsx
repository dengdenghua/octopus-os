export function TaskModelScope({
  scope,
  supportsOverride,
  disabled,
  onChange,
}: {
  scope: "system" | "task";
  supportsOverride: boolean;
  disabled?: boolean;
  onChange: (scope: "system" | "task") => void;
}) {
  if (!supportsOverride) {
    return (
      <p className="mb-2 text-xs text-muted-foreground">
        模型使用系统配置；修改会影响后续任务。
      </p>
    );
  }
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <label className="flex items-center gap-2">
        模型作用范围
        <select
          value={scope}
          disabled={disabled}
          onChange={(event) =>
            onChange(event.target.value as "system" | "task")
          }
          className="rounded-md border bg-background px-2 py-1 text-foreground"
        >
          <option value="system">跟随系统</option>
          <option value="task">仅此任务</option>
        </select>
      </label>
      <span>
        {scope === "task"
          ? "下方选择只用于此任务，系统默认保持不变。"
          : "下方修改会同步系统默认，影响后续任务。"}
      </span>
    </div>
  );
}
