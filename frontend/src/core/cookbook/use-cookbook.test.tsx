import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useCookbookPull } from "./use-cookbook";

const mocks = vi.hoisted(() => ({
  update: vi.fn(),
  save: vi.fn(),
  principal: "actor-a",
}));
vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({ user: { actor_id: mocks.principal } }),
}));
vi.mock("@/core/auth/api", () => ({
  jsonAuthHeaders: () => ({ "Content-Type": "application/json" }),
}));
vi.mock("@/core/coder/api", () => ({
  updateCoderModelProfile: mocks.update,
  coderQueryKeys: (key: string) => ({ profile: ["coder", key, "profile"] }),
}));
vi.mock("@/core/settings/local", () => ({
  getLocalSettings: () => ({ context: { model_name: "old" } }),
  saveLocalSettings: mocks.save,
}));

const selection = "echo-custom-model:v1:verified";
it("does not save a model profile under a user who signed in during import", async () => {
  let resolveImport!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveImport = resolve;
        }),
    ),
  );
  const { result, rerender } = mount();
  act(() => result.current.activate("fixture:1b"));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  mocks.principal = "actor-b";
  rerender();
  await act(async () =>
    resolveImport(
      new Response(JSON.stringify({ ok: true, selection_id: selection })),
    ),
  );
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(mocks.update).not.toHaveBeenCalled();
  expect(mocks.save).not.toHaveBeenCalled();
});
beforeEach(() => {
  vi.clearAllMocks();
  mocks.principal = "actor-a";
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, selection_id: selection })),
      ),
  );
  mocks.update.mockResolvedValue({
    source: "follow_system",
    selected_model: selection,
  });
});
afterEach(() => vi.unstubAllGlobals());

function mount() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const hook = renderHook(() => useCookbookPull(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  return { ...hook, client };
}

it("shares the server profile and local default only after activation succeeds", async () => {
  const { result, client } = mount();
  act(() => result.current.activate("fixture:1b"));
  await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  expect(mocks.update).toHaveBeenCalledWith({
    source: "follow_system",
    model: selection,
  });
  expect(mocks.save).toHaveBeenCalledWith({
    context: { model_name: selection },
  });
  expect(client.getQueryData(["coder", "actor-a", "profile"])).toEqual({
    source: "follow_system",
    selected_model: selection,
  });
});

it("keeps the previous default when importing fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "保存失败" }), { status: 503 }),
      ),
  );
  const { result } = mount();
  act(() => result.current.activate("fixture:1b"));
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(mocks.update).not.toHaveBeenCalled();
  expect(mocks.save).not.toHaveBeenCalled();
});

it("keeps the previous default when profile persistence fails", async () => {
  mocks.update.mockRejectedValue(new Error("profile save failed"));
  const { result } = mount();
  act(() => result.current.activate("fixture:1b"));
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(mocks.save).not.toHaveBeenCalled();
});

it("does not apply a completed activation to a different signed-in user", async () => {
  let finish!: (value: any) => void;
  mocks.update.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, rerender } = mount();
  act(() => result.current.activate("fixture:1b"));
  await waitFor(() => expect(mocks.update).toHaveBeenCalled());
  mocks.principal = "actor-b";
  rerender();
  await act(async () =>
    finish({ source: "follow_system", selected_model: selection }),
  );
  expect(mocks.save).not.toHaveBeenCalled();
});

it("surfaces rejected preparation jobs instead of showing successful download", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "error", error: "busy" })),
      ),
  );
  const { result } = mount();
  act(() => result.current.pull("fixture:1b"));
  await waitFor(() => expect(result.current.error?.message).toBe("busy"));
  expect(mocks.update).not.toHaveBeenCalled();
});
