/* Implementation note. */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/harness";
import type * as OctApiModule from "@/core/oct/api";

const navigateMock = vi.fn();
const emailSendMock = vi.fn();
const emailLoginMock = vi.fn();
const getAuthProvidersMock = vi.fn();
const allowRegistrationMock = vi.fn();
const authUnavailableMock = vi.fn();
const retryAuthMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<
    typeof import("react-router-dom") // eslint-disable-line @typescript-eslint/consistent-type-imports
  >("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/core/auth/api", () => ({
  getAuthProviderInfo: () => getAuthProvidersMock(),
  authHeaders: () => ({}),
  jsonAuthHeaders: () => ({}),
}));

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    emailLogin: (email: string, code: string) => emailLoginMock(email, code),
    guestLogin: vi.fn(),
    authStatus: authUnavailableMock()
      ? null
      : { enabled: true, allow_registration: allowRegistrationMock() },
    authError: authUnavailableMock()
      ? new Error("authentication service unavailable")
      : null,
    isLoading: false,
    isAuthenticated: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
    retryAuth: retryAuthMock,
  }),
}));

vi.mock("@/core/oct/api", async (importOriginal) => {
  const actual = await importOriginal<typeof OctApiModule>();
  return {
    octAuthApi: {
      emailSend: (email: string) => emailSendMock(email),
    },
    octErrorMessage: actual.octErrorMessage,
    OctApiError: class OctApiError extends Error {
      status: number;
      constructor(message: string, status = 500) {
        super(message);
        this.status = status;
      }
    },
  };
});

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
  },
}));

import { remainingCooldownSeconds } from "./login-utils";
import LoginPage from "./page";

function renderPage(initialRoute = "/login") {
  // Test strings reference zh-CN copy, so prime the I18nProvider with
  // zh-CN. The harness also pre-seeds the `locale` cookie so the
  // mount effect in `useI18n()` doesn't race back to the jsdom default.
  return renderWithProviders(<LoginPage />, {
    initialRoute,
    locale: "zh-CN",
  });
}

/* Implementation note. */
async function renderPageAtLoginForm(initialRoute?: string) {
  const user = userEvent.setup();
  renderPage(initialRoute);

  // The login form is the landing surface · no onboarding stepper
  // to advance through. We just wait for the auth provider probe to resolve.
  await screen.findByRole("textbox", { name: "邮箱" });

  return user;
}

describe("LoginPage", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    emailSendMock.mockReset();
    emailLoginMock.mockReset();
    getAuthProvidersMock.mockReset();
    allowRegistrationMock.mockReset();
    authUnavailableMock.mockReset();
    retryAuthMock.mockReset();
    getAuthProvidersMock.mockResolvedValue([{ id: "oct" }]);
    allowRegistrationMock.mockReturnValue(false);
    authUnavailableMock.mockReturnValue(false);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("defaults to the email form with email + code fields visible", async () => {
    await renderPageAtLoginForm();
    expect(screen.getByRole("textbox", { name: "邮箱" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "验证码" })).toBeInTheDocument();
    // Legacy password tab was removed upstream · nothing here asks
    // for a password.
    expect(screen.queryByLabelText("密码")).not.toBeInTheDocument();
  });

  it("normalizes pasted verification codes and exposes autofill hints", async () => {
    await renderPageAtLoginForm();
    const code = screen.getByRole("textbox", { name: "验证码" });
    fireEvent.change(code, { target: { value: "12a 34-56" } });

    expect(code).toHaveValue("123456");
    expect(code).toHaveAttribute("inputmode", "numeric");
    expect(code).toHaveAttribute("autocomplete", "one-time-code");
    expect(code).toHaveAttribute("enterkeyhint", "done");
    expect(code).toHaveAttribute("maxlength", "6");
  });

  it("calculates resend cooldown from the deadline instead of timer ticks", () => {
    expect(remainingCooldownSeconds(160_000, 100_000)).toBe(60);
    expect(remainingCooldownSeconds(160_000, 159_001)).toBe(1);
    expect(remainingCooldownSeconds(160_000, 160_000)).toBe(0);
    expect(remainingCooldownSeconds(160_000, 200_000)).toBe(0);
  });

  it("uses email-specific copy when Oct email auth is available", async () => {
    getAuthProvidersMock.mockResolvedValue([{ id: "oct" }]);

    renderPage();

    // Redesigned login surface: email-first copy, no phone form at all.
    expect(
      await screen.findByText("用一封验证码，唤醒你的 ECHO 身份"),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "邮箱" })).toBeInTheDocument();
    expect(screen.queryByText("登录你的 Echo 账户")).not.toBeInTheDocument();
    // No local account provider → no tab strip.
    expect(screen.queryByText("本地账户")).not.toBeInTheDocument();
  });

  it("shows a retryable service error instead of an endless provider spinner", async () => {
    authUnavailableMock.mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();

    expect(
      await screen.findByText("暂时无法连接 EchoAI 服务"),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试连接" }));
    expect(retryAuthMock).toHaveBeenCalledTimes(1);
  });

  it("shows email + local tabs when both providers are available", async () => {
    getAuthProvidersMock.mockResolvedValue([
      { id: "oct" },
      { id: "local", label: "本地账户" },
    ]);

    renderPage();

    expect(await screen.findByText("邮箱登录")).toBeInTheDocument();
    expect(screen.getByText("本地账户")).toBeInTheDocument();
    // Email form is the default tab.
    expect(screen.getByRole("textbox", { name: "邮箱" })).toBeInTheDocument();
  });

  it("获取验证码 is enabled by default; invalid email surfaces toast error", async () => {
    // Current behavior: send button is always enabled while idle ·
    // clicking with a bad email fires a toast rather than disabling
    // the button up front (older versions had a length-gated button).
    const { toast } = await import("sonner");
    emailSendMock.mockResolvedValue({ sent: true });
    const user = await renderPageAtLoginForm();

    const sendBtn = screen.getByRole("button", { name: "获取验证码" });
    expect(sendBtn).not.toBeDisabled();

    // Too short · click produces a toast.error, no API hit.
    await user.type(screen.getByRole("textbox", { name: "邮箱" }), "bad");
    await user.click(sendBtn);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(emailSendMock).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "邮箱" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("请输入有效的邮箱地址");
  });

  it("marks both missing login fields inline and focuses the first error", async () => {
    const user = await renderPageAtLoginForm();
    const email = screen.getByRole("textbox", { name: "邮箱" });
    const code = screen.getByRole("textbox", { name: "验证码" });

    await user.click(screen.getByRole("button", { name: "进入 ECHO" }));

    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAttribute("aria-describedby", "email-error");
    expect(code).toHaveAttribute("aria-invalid", "true");
    expect(code).toHaveAttribute("aria-describedby", "email-code-error");
    expect(screen.getByText("请输入邮箱地址")).toHaveAttribute("role", "alert");
    expect(screen.getByText("请输入验证码")).toHaveAttribute("role", "alert");
    expect(email).toHaveFocus();

    await user.type(email, "alice@example.com");
    expect(email).toHaveAttribute("aria-invalid", "false");
    expect(screen.queryByText("请输入邮箱地址")).not.toBeInTheDocument();
    expect(code).toHaveAttribute("aria-invalid", "true");
  });

  it("rejects incomplete verification codes before calling the gateway", async () => {
    const user = await renderPageAtLoginForm();
    await user.type(
      screen.getByRole("textbox", { name: "邮箱" }),
      "alice@example.com",
    );
    await user.type(screen.getByRole("textbox", { name: "验证码" }), "1234");

    await user.click(screen.getByRole("button", { name: "进入 ECHO" }));

    expect(emailLoginMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "请输入 6 位数字验证码",
    );
    expect(screen.getByRole("textbox", { name: "验证码" })).toHaveFocus();
  });

  it("sends the email code and kicks off cooldown on success", async () => {
    emailSendMock.mockResolvedValue({ sent: true });
    const user = await renderPageAtLoginForm();

    await user.type(
      screen.getByRole("textbox", { name: "邮箱" }),
      "alice@example.com",
    );

    const sendBtn = screen.getByRole("button", { name: "获取验证码" });
    expect(sendBtn).not.toBeDisabled();
    await user.click(sendBtn);

    // API hit with the trimmed email.
    await waitFor(() =>
      expect(emailSendMock).toHaveBeenCalledWith("alice@example.com"),
    );

    // Implementation note.
    // · assert the original label is gone.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "获取验证码" }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole("textbox", { name: "邮箱" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "验证码已发送，请查收邮箱 · alice@example.com",
    );
  });

  it("keeps send failures visible inside the form", async () => {
    emailSendMock.mockRejectedValue(new Error("邮件服务暂时不可用"));
    const user = await renderPageAtLoginForm();
    await user.type(
      screen.getByRole("textbox", { name: "邮箱" }),
      "alice@example.com",
    );

    await user.click(screen.getByRole("button", { name: "获取验证码" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "邮件服务暂时不可用",
    );
    expect(screen.getByRole("textbox", { name: "邮箱" })).not.toBeDisabled();
  });

  it("calls emailLogin and restores the complete returnTo after verify", async () => {
    emailLoginMock.mockResolvedValue(undefined);
    const user = await renderPageAtLoginForm(
      "/login?returnTo=%2Fworkspace%2Fteam%2Fjoin%3Ftoken%3Dsecret%23details",
    );

    await user.type(
      screen.getByRole("textbox", { name: "邮箱" }),
      "alice@example.com",
    );
    await user.type(screen.getByRole("textbox", { name: "验证码" }), "123456");

    // Implementation note.
    // Implementation note.
    await user.click(screen.getByRole("button", { name: "进入 ECHO" }));

    await waitFor(() => {
      expect(emailLoginMock).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
      );
    });
    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith(
        "/workspace/team/join?token=secret#details",
        { replace: true },
      );
    });
  });

  it("keeps upstream login errors visible and returns focus to the code", async () => {
    const { toast } = await import("sonner");
    emailLoginMock.mockRejectedValue(
      new Error('oct 登录失败: gateway rejected: {"detail":"验证码已过期"}'),
    );
    const user = await renderPageAtLoginForm();

    await user.type(
      screen.getByRole("textbox", { name: "邮箱" }),
      "alice@example.com",
    );
    await user.type(screen.getByRole("textbox", { name: "验证码" }), "000000");
    await user.click(screen.getByRole("button", { name: "进入 ECHO" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("验证码已过期");
    });
    expect(screen.getByRole("alert")).toHaveTextContent("验证码已过期");
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "验证码" })).toHaveFocus(),
    );
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("keeps verification single-flight and locks mutable fields while pending", async () => {
    let finishLogin: (() => void) | undefined;
    emailLoginMock.mockReturnValue(
      new Promise<void>((resolve) => {
        finishLogin = resolve;
      }),
    );
    const user = await renderPageAtLoginForm();
    const email = screen.getByRole("textbox", { name: "邮箱" });
    const code = screen.getByRole("textbox", { name: "验证码" });
    const sendCode = screen.getByRole("button", { name: "获取验证码" });
    const submit = screen.getByRole("button", { name: "进入 ECHO" });
    await user.type(email, "alice@example.com");
    await user.type(code, "123456");

    await user.click(submit);
    expect(emailLoginMock).toHaveBeenCalledTimes(1);
    expect(email).toBeDisabled();
    expect(code).toBeDisabled();
    expect(sendCode).toBeDisabled();
    expect(submit).toBeDisabled();
    expect(submit.closest("form")).toHaveAttribute("aria-busy", "true");

    await user.click(submit);
    expect(emailLoginMock).toHaveBeenCalledTimes(1);

    finishLogin?.();
    await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));
  });
});
