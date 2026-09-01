import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/harness";

const navigateMock = vi.fn();
const registerMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<
    typeof import("react-router-dom") // eslint-disable-line @typescript-eslint/consistent-type-imports
  >("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    register: (payload: {
      username: string;
      password: string;
      email?: string;
    }) => registerMock(payload),
    authStatus: { enabled: true, allow_registration: true },
    isLoading: false,
    isAuthenticated: false,
    isGuest: false,
    login: vi.fn(),
    smsLogin: vi.fn(),
    guestLogin: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

import RegisterPage from "./page";

function renderPage() {
  return renderWithProviders(<RegisterPage />, {
    initialRoute: "/register",
    locale: "zh-CN",
  });
}

describe("RegisterPage", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    registerMock.mockReset();
  });

  it("renders the registration form", () => {
    renderPage();
    expect(screen.getByRole("textbox", { name: "用户名" })).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "邮箱（可选）" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
    expect(screen.getByLabelText("确认密码")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "创建账户" }),
    ).toBeInTheDocument();
  });

  it("shows toast when required fields are missing", async () => {
    const { toast } = await import("sonner");
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("请填写用户名和密码");
    });
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("shows toast when passwords do not match", async () => {
    const { toast } = await import("sonner");
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "用户名" }), "alice");
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "different123");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("两次密码输入不一致");
    });
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("shows toast when username is too short", async () => {
    const { toast } = await import("sonner");
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "用户名" }), "ab");
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "secret123");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("用户名至少需要 3 个字符");
    });
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("shows toast when password is too short", async () => {
    const { toast } = await import("sonner");
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "用户名" }), "alice");
    await user.type(screen.getByLabelText("密码"), "12345");
    await user.type(screen.getByLabelText("确认密码"), "12345");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("密码至少需要 6 个字符");
    });
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("submits successfully and navigates to login", async () => {
    const { toast } = await import("sonner");
    registerMock.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "用户名" }), "alice");
    await user.type(
      screen.getByRole("textbox", { name: "邮箱（可选）" }),
      "alice@example.com",
    );
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "secret123");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(registerMock).toHaveBeenCalledWith({
        username: "alice",
        password: "secret123",
        email: "alice@example.com",
      });
    });
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("注册成功，请登录");
      expect(navigateMock).toHaveBeenCalledWith("/login");
    });
  });

  it("submits undefined email when email field is empty", async () => {
    registerMock.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "用户名" }), "alice");
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "secret123");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(registerMock).toHaveBeenCalledWith({
        username: "alice",
        password: "secret123",
        email: undefined,
      });
    });
  });

  it("shows upstream error message without navigation", async () => {
    const { toast } = await import("sonner");
    registerMock.mockRejectedValue(new Error("用户名已存在"));
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByRole("textbox", { name: "用户名" }), "alice");
    await user.type(screen.getByLabelText("密码"), "secret123");
    await user.type(screen.getByLabelText("确认密码"), "secret123");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("用户名已存在");
    });
    expect(navigateMock).not.toHaveBeenCalledWith("/login");
  });
});
