import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IntentDesktopSurface } from "./intent-desktop-surface";
import { toast } from "sonner";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

describe("IntentDesktopSurface", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("renders with initial workspace mode and ivory white theme by default", () => {
    render(<IntentDesktopSurface />);
    expect(screen.getByText("多窗口工作台")).toBeInTheDocument();
    expect(screen.getByText("意图优先 · 让 Agent 驱动执行")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/输入意图/i)).toBeInTheDocument();
    expect(
      screen.getByText(/全网分析 DeepSeek V3 架构/i)
    ).toBeInTheDocument();
  });

  it("toggles between workspace and pure mode on mode button click", () => {
    render(<IntentDesktopSurface initialMode="workspace" />);
    const toggleBtn = screen.getByRole("button", {
      name: /多窗口工作台/i,
    });
    fireEvent.click(toggleBtn);

    expect(screen.getByText("意图纯净态")).toBeInTheDocument();
    expect(toast.info).toHaveBeenCalledWith(
      expect.stringContaining("已进入纯净意图态"),
      expect.anything()
    );

    fireEvent.click(screen.getByRole("button", { name: /意图纯净态/i }));
    expect(screen.getByText("多窗口工作台")).toBeInTheDocument();
    expect(toast.info).toHaveBeenCalledWith(
      expect.stringContaining("已恢复多窗口工作台"),
      expect.anything()
    );
  });

  it("toggles mode on clicking blank backdrop surface", () => {
    render(<IntentDesktopSurface initialMode="workspace" />);
    const surface = screen.getByTestId("intent-desktop-surface");
    fireEvent.click(surface);

    expect(screen.getByText("意图纯净态")).toBeInTheDocument();
  });

  it("switches themes when clicking theme buttons", () => {
    render(<IntentDesktopSurface />);
    const glassBtn = screen.getByRole("button", { name: /液态玻璃/i });
    fireEvent.click(glassBtn);
    expect(toast.success).toHaveBeenCalledWith(
      "桌面主题已切换为：静谧液态玻璃"
    );

    const darkBtn = screen.getByRole("button", { name: /暗夜黑/i });
    fireEvent.click(darkBtn);
    expect(toast.success).toHaveBeenCalledWith(
      "桌面主题已切换为：纯粹暗夜黑"
    );
  });

  it("submits a new intent, creates a task card, and notifies callback", () => {
    const onOpenWorkbench = vi.fn();
    render(
      <IntentDesktopSurface
        initialMode="pure"
        onOpenWorkbench={onOpenWorkbench}
      />
    );

    const input = screen.getByPlaceholderText(/输入意图/i);
    fireEvent.change(input, {
      target: { value: "分析 NAS 存储碎片化并生成优化方案" },
    });

    const submitBtn = screen.getByRole("button", { name: /提交意图/i });
    fireEvent.click(submitBtn);

    expect(
      screen.getByText("分析 NAS 存储碎片化并生成优化方案")
    ).toBeInTheDocument();
    expect(onOpenWorkbench).toHaveBeenCalledWith(
      "分析 NAS 存储碎片化并生成优化方案"
    );
    // Should switch to workspace mode
    expect(screen.getByText("多窗口工作台")).toBeInTheDocument();
  });

  it("handles task card traffic lights: dismiss, pause, and expand", () => {
    render(<IntentDesktopSurface initialMode="workspace" />);

    // Expand details
    const expandButtons = screen.getAllByRole("button", { name: /展开任务详情/i });
    fireEvent.click(expandButtons[0]);

    expect(
      screen.getByText("实时执行流与控制台日志")
    ).toBeInTheDocument();

    // Close details
    const closeBtn = screen.getByRole("button", { name: /关闭详情/i });
    fireEvent.click(closeBtn);
    expect(screen.queryByText("实时执行流与控制台日志")).not.toBeInTheDocument();

    // Pause task
    const pauseButtons = screen.getAllByRole("button", { name: /暂停或继续任务/i });
    fireEvent.click(pauseButtons[0]);
    expect(toast.info).toHaveBeenCalledWith(
      expect.stringContaining("已暂停任务")
    );

    // Dismiss task
    const dismissButtons = screen.getAllByRole("button", { name: /关闭任务/i });
    fireEvent.click(dismissButtons[0]);
    expect(toast.info).toHaveBeenCalledWith("任务已收纳移除");
  });

  it("supports keyboard shortcut Space to toggle mode", () => {
    render(<IntentDesktopSurface initialMode="workspace" />);
    fireEvent.keyDown(window, { code: "Space" });
    expect(screen.getByText("意图纯净态")).toBeInTheDocument();

    fireEvent.keyDown(window, { code: "Space" });
    expect(screen.getByText("多窗口工作台")).toBeInTheDocument();
  });
});
