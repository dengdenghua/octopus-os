import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { TaskModelScope } from "./task-model-scope";

it("offers an explicit task override and return to the system", async () => {
  const onChange = vi.fn();
  const view = render(
    <TaskModelScope scope="system" supportsOverride onChange={onChange} />,
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "模型作用范围" }),
    "task",
  );
  expect(onChange).toHaveBeenLastCalledWith("task");
  view.rerender(
    <TaskModelScope scope="task" supportsOverride onChange={onChange} />,
  );
  expect(
    screen.getByText("下方选择只用于此任务，系统默认保持不变。"),
  ).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByRole("combobox"), "system");
  expect(onChange).toHaveBeenLastCalledWith("system");
});

it("prevents scope changes while a turn is running", () => {
  render(
    <TaskModelScope
      scope="task"
      supportsOverride
      disabled
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByRole("combobox")).toBeDisabled();
});

it("does not promise overrides for engines that only consume system profiles", () => {
  render(
    <TaskModelScope
      scope="system"
      supportsOverride={false}
      onChange={vi.fn()}
    />,
  );
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(
    screen.getByText("模型使用系统配置；修改会影响后续任务。"),
  ).toBeInTheDocument();
});
