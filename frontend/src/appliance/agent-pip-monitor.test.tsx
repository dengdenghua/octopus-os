import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentPipMonitor } from "./agent-pip-monitor";
import { toast } from "sonner";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

describe("AgentPipMonitor (Codex 画中画)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the floating PiP monitor with live step and progress", () => {
    render(<AgentPipMonitor />);
    expect(screen.getByText(/Codex PiP 画中画/i)).toBeInTheDocument();
    expect(screen.getByText("RK3576 驱动装配与编译")).toBeInTheDocument();
    expect(screen.getByText("68%")).toBeInTheDocument();
    expect(
      screen.getByText(/正在执行交叉编译: aarch64-linux-gnu-gcc/i)
    ).toBeInTheDocument();
  });

  it("collapses into a compact pill and expands back", () => {
    render(<AgentPipMonitor />);
    const minimizeBtn = screen.getByRole("button", { name: /最小化/i });
    fireEvent.click(minimizeBtn);

    expect(screen.getByText(/Builder PiP · 68%/i)).toBeInTheDocument();

    const pill = screen.getByText(/Builder PiP · 68%/i);
    fireEvent.click(pill);
    expect(screen.getByText(/Codex PiP 画中画/i)).toBeInTheDocument();
  });

  it("approves high risk operation and fires notification", () => {
    render(<AgentPipMonitor />);
    const approveBtn = screen.getByRole("button", { name: /批准放行/i });
    fireEvent.click(approveBtn);

    expect(toast.success).toHaveBeenCalledWith(
      "已批准执行高危操作",
      expect.anything()
    );
  });

  it("rejects high risk operation and fires error notification", () => {
    render(<AgentPipMonitor />);
    const rejectBtn = screen.getByRole("button", { name: /拦截/i });
    fireEvent.click(rejectBtn);

    expect(toast.error).toHaveBeenCalledWith("已拦截并拒绝高危操作");
  });

  it("calls onExpandToWindow callback when clicking expand button", () => {
    const onExpand = vi.fn();
    render(<AgentPipMonitor onExpandToWindow={onExpand} />);

    const expandBtn = screen.getByRole("button", { name: /放大为全屏窗口/i });
    fireEvent.click(expandBtn);

    expect(onExpand).toHaveBeenCalledOnce();
  });
});
