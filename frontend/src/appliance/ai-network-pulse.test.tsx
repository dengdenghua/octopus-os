import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AiNetworkPulse } from "./ai-network-pulse";
import { toast } from "sonner";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

describe("AiNetworkPulse", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders trigger button with AI Network status pill", () => {
    render(<AiNetworkPulse />);
    const trigger = screen.getByRole("button", {
      name: /打开 AI Network 实时脉搏/i,
    });
    expect(trigger).toBeInTheDocument();
    expect(screen.getByText("AI Network")).toBeInTheDocument();
    expect(screen.getByText("32% Local")).toBeInTheDocument();
  });

  it("opens popover menu and displays Agents, Plans, and Policy sections", () => {
    render(<AiNetworkPulse />);
    const trigger = screen.getByRole("button", {
      name: /打开 AI Network 实时脉搏/i,
    });
    fireEvent.click(trigger);

    expect(screen.getByText("ECHO AI NETWORK")).toBeInTheDocument();
    expect(
      screen.getByText("全局算力宽带、资费套餐与智能路由中枢"),
    ).toBeInTheDocument();

    // Policy section
    expect(screen.getByText("1. 全局调度原则 (Policy)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Local" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Auto" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quality" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cost" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Privacy" })).toBeInTheDocument();

    // Model Switcher section
    expect(
      screen.getByText("2. 状态栏主选模型 (Model Switcher)"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Claude 3.5 Sonnet").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek-V3")).toBeInTheDocument();

    // Agents section
    expect(screen.getByText("3. 数字工作人员 (Agents)")).toBeInTheDocument();
    expect(screen.getByText("Researcher (调研员)")).toBeInTheDocument();
    expect(screen.getByText("Builder (架构师)")).toBeInTheDocument();
    expect(screen.getByText("Operator (管家)")).toBeInTheDocument();

    // Plans section
    expect(screen.getByText("4. 算力套餐池 (Plans)")).toBeInTheDocument();
    expect(screen.getByText("OpenAI Pro")).toBeInTheDocument();
    expect(screen.getByText("Anthropic Max")).toBeInTheDocument();
    expect(screen.getByText("Google AI Pro")).toBeInTheDocument();
    expect(screen.getByText("Echo Station (NPU)")).toBeInTheDocument();
  });

  it("switches model directly from dropdown and triggers callback", () => {
    const onSelectModel = vi.fn();
    render(<AiNetworkPulse onSelectModel={onSelectModel} />);
    const trigger = screen.getByRole("button", {
      name: /打开 AI Network 实时脉搏/i,
    });
    fireEvent.click(trigger);

    const claudeButtons = screen.getAllByText("Claude 3.5 Sonnet");
    const claudeBtn = claudeButtons[0].closest("button")!;
    fireEvent.click(claudeBtn);

    expect(onSelectModel).toHaveBeenCalledWith("claude-3-5-sonnet");
    expect(toast.success).toHaveBeenCalledWith(
      "系统主模型已切换为：Claude 3.5 Sonnet",
      expect.objectContaining({
        description: expect.stringContaining("Anthropic"),
      }),
    );
  });

  it("switches routing policy and triggers toast notification", () => {
    render(<AiNetworkPulse />);
    const trigger = screen.getByRole("button", {
      name: /打开 AI Network 实时脉搏/i,
    });
    fireEvent.click(trigger);

    const costBtn = screen.getByRole("button", { name: "Cost" });
    fireEvent.click(costBtn);

    expect(toast.success).toHaveBeenCalledWith(
      "调度原则已切换为：成本压制",
      expect.objectContaining({
        description: expect.stringContaining("优先采用本地与经济型模型"),
      }),
    );
  });

  it("handles onOpenWorkbench and onOpenSettings callbacks", () => {
    const onOpenWorkbench = vi.fn();
    const onOpenSettings = vi.fn();

    render(
      <AiNetworkPulse
        onOpenWorkbench={onOpenWorkbench}
        onOpenSettings={onOpenSettings}
      />,
    );
    const trigger = screen.getByRole("button", {
      name: /打开 AI Network 实时脉搏/i,
    });
    fireEvent.click(trigger);

    const workbenchBtn = screen.getByText("打开完整 AI Network 工作台 →");
    fireEvent.click(workbenchBtn);
    expect(onOpenWorkbench).toHaveBeenCalledOnce();

    fireEvent.click(trigger);
    const settingsBtn = screen.getByText("连接设置");
    fireEvent.click(settingsBtn);
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });
});
