import { act, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

const mocks = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  getMe: vi.fn(),
  getToken: vi.fn(),
  getStoredUser: vi.fn(),
  clearTokens: vi.fn(),
}));

vi.mock("@/core/auth/api", () => ({
  getAuthStatus: mocks.getAuthStatus,
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
  const { authError, isLoading, isAuthenticated, user } = useAuth();
  return (
    <div>
      {isLoading ? "loading" : isAuthenticated ? "authenticated" : "anonymous"}
      <span>{user?.actor_id}</span>
      <span>{authError ? "auth unavailable" : "auth ready"}</span>
    </div>
  );
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

it("exposes a retryable error when the auth status endpoint is unavailable", async () => {
  mocks.getAuthStatus.mockRejectedValueOnce(new Error("network unavailable"));

  renderWithProviders(
    <AuthProvider>
      <AuthState />
    </AuthProvider>,
  );

  expect(await screen.findByText("anonymous")).toBeInTheDocument();
  expect(screen.getByText("auth unavailable")).toBeInTheDocument();
  expect(mocks.getMe).not.toHaveBeenCalled();
});
