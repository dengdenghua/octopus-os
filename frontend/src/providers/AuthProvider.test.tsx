import { act, fireEvent, screen } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  getMe: vi.fn(),
  getToken: vi.fn(),
  getStoredUser: vi.fn(),
  clearTokens: vi.fn(),
}));

function backendStartingError(retryAfterMs = 3_000) {
  return Object.assign(new Error("Echo OS is starting"), {
    code: "appliance_starting",
    retryAfterMs,
  });
}

vi.mock("@/core/auth/api", () => ({
  getAuthStatus: mocks.getAuthStatus,
  isBackendStartingError: (error: unknown) =>
    error instanceof Error &&
    "code" in error &&
    error.code === "appliance_starting",
  getMe: mocks.getMe,
  getToken: mocks.getToken,
  getUser: mocks.getStoredUser,
  login: vi.fn(),
  logout: vi.fn(),
  refreshToken: vi.fn(),
  register: vi.fn(),
  _writeToken: vi.fn(),
  _clearTokens: mocks.clearTokens,
}));

vi.mock("@/core/oct/api", () => ({
  octAuthApi: { emailLogin: vi.fn() },
}));

import { AuthProvider, useAuth } from "./AuthProvider";

function AuthState() {
  const { authError, isBackendStarting, isLoading, isAuthenticated, user } =
    useAuth();
  return (
    <div>
      {isLoading ? "loading" : isAuthenticated ? "authenticated" : "anonymous"}
      <span>{user?.actor_id}</span>
      <span>{authError ? "auth unavailable" : "auth ready"}</span>
      <span>{isBackendStarting ? "backend starting" : "backend settled"}</span>
    </div>
  );
}

function AuthControls() {
  const { retryAuth } = useAuth();
  return <button onClick={() => void retryAuth()}>重新读取账号</button>;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getToken.mockReturnValue(null);
  mocks.getStoredUser.mockReturnValue(null);
  mocks.getAuthStatus.mockResolvedValue({
    enabled: true,
    jwt_available: true,
    allow_registration: false,
    exempt_paths: [],
  });
  mocks.getMe.mockResolvedValue({
    user_id: "oct:user@example.com",
    actor_id: "oct:user@example.com",
    username: "user@example.com",
    roles: ["user", "oct"],
    permissions: [],
    is_active: true,
  });
  vi.spyOn(window, "fetch").mockResolvedValue(
    new Response(null, { status: 204 }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("recovers an authenticated actor from the HttpOnly cookie after browser restart", async () => {
  renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );

  expect(await screen.findByText("authenticated")).toBeInTheDocument();
  expect(screen.getByText("oct:user@example.com")).toBeInTheDocument();
  expect(mocks.getToken).toHaveBeenCalled();
  expect(mocks.getMe).toHaveBeenCalledTimes(1);
});

it("drops the in-memory actor when a protected request reports expiry", async () => {
  renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );
  expect(await screen.findByText("authenticated")).toBeInTheDocument();

  act(() => {
    window.dispatchEvent(new CustomEvent("echo:auth-expired"));
  });

  expect(await screen.findByText("anonymous")).toBeInTheDocument();
  expect(mocks.clearTokens).toHaveBeenCalled();
  expect(window.fetch).toHaveBeenCalledWith("/api/auth/logout", {
    method: "POST",
    credentials: "include",
  });
});

it("clears shared query data when the authenticated actor changes", async () => {
  const queryClient = new QueryClient();
  renderWithProviders(
    <AuthProvider>
      <AuthState />
      <AuthControls />
    </AuthProvider>,
    { queryClient },
  );
  expect(await screen.findByText("authenticated")).toBeInTheDocument();
  queryClient.setQueryData(["account-private"], {
    owner: "oct:user@example.com",
  });
  mocks.getMe.mockResolvedValueOnce({
    user_id: "oct:other@example.com",
    actor_id: "oct:other@example.com",
    username: "other@example.com",
    roles: ["user", "oct"],
    permissions: [],
    is_active: true,
  });

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "重新读取账号" }));
  });
  expect(await screen.findByText("oct:other@example.com")).toBeInTheDocument();
  expect(queryClient.getQueryData(["account-private"])).toBeUndefined();
});

it("exposes a retryable error when the auth status endpoint is unavailable", async () => {
  mocks.getAuthStatus.mockRejectedValueOnce(new Error("network unavailable"));

  renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );

  expect(await screen.findByText("anonymous")).toBeInTheDocument();
  expect(screen.getByText("auth unavailable")).toBeInTheDocument();
  expect(mocks.getAuthStatus).toHaveBeenCalledOnce();
  expect(mocks.getMe).not.toHaveBeenCalled();
});

it("automatically recovers while the appliance backend is starting", async () => {
  vi.useFakeTimers();
  mocks.getAuthStatus
    .mockRejectedValueOnce(backendStartingError())
    .mockResolvedValueOnce({
      enabled: true,
      jwt_available: true,
      allow_registration: false,
      exempt_paths: [],
    });

  renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );

  expect(screen.getByText("loading")).toBeInTheDocument();
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getByText("backend starting")).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_000);
  });

  expect(screen.getByText("authenticated")).toBeInTheDocument();
  expect(screen.getByText("auth ready")).toBeInTheDocument();
  expect(screen.getByText("backend settled")).toBeInTheDocument();
  expect(mocks.getAuthStatus).toHaveBeenCalledTimes(2);
});

it("stops automatic startup retries after the bounded recovery window", async () => {
  vi.useFakeTimers();
  mocks.getAuthStatus.mockRejectedValue(backendStartingError(10_000));

  renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );

  await act(async () => {
    await vi.advanceTimersByTimeAsync(120_000);
  });

  expect(screen.getByText("anonymous")).toBeInTheDocument();
  expect(screen.getByText("auth unavailable")).toBeInTheDocument();
  expect(screen.getByText("backend settled")).toBeInTheDocument();
  expect(mocks.getAuthStatus).toHaveBeenCalledTimes(13);
  expect(mocks.getMe).not.toHaveBeenCalled();
});

it("cancels a pending startup retry when the provider unmounts", async () => {
  vi.useFakeTimers();
  mocks.getAuthStatus.mockRejectedValue(backendStartingError());

  const view = renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(mocks.getAuthStatus).toHaveBeenCalledOnce();

  view.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });

  expect(mocks.getAuthStatus).toHaveBeenCalledOnce();
});
