import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { useSystemControls } from "./use-system-controls";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() },
}));

type SystemMock = {
  getCapabilities: ReturnType<typeof vi.fn>;
  runAction: ReturnType<typeof vi.fn>;
};

function installSystem(capabilities: Record<string, boolean>): SystemMock {
  const system: SystemMock = {
    getCapabilities: vi.fn().mockResolvedValue({
      nativeShell: true,
      lock: true,
      logout: true,
      suspend: true,
      restart: true,
      shutdown: true,
      ...capabilities,
    }),
    runAction: vi.fn().mockResolvedValue({ ok: true }),
  };
  (window as unknown as { echo: unknown }).echo = { system };
  return system;
}

beforeEach(() => {
  delete (window as unknown as { echo?: unknown }).echo;
});

it("reports every action as unavailable when there is no native shell", async () => {
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(false));
  expect(result.current.availableSystemActions).toMatchObject({
    lock: false,
    logout: false,
    suspend: false,
    restart: false,
    shutdown: false,
  });
});

it("adopts the capabilities reported by the native shell", async () => {
  installSystem({ restart: false, shutdown: false });
  const { result } = renderHook(() => useSystemControls());

  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));
  expect(result.current.availableSystemActions.restart).toBe(false);
  expect(result.current.availableSystemActions.shutdown).toBe(false);
  expect(result.current.availableSystemActions.lock).toBe(true);
});

it("refuses to queue a power action the shell does not expose", async () => {
  installSystem({ restart: false });
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  act(() => result.current.requestSystemAction("restart"));

  // 不可用的动作必须连确认弹窗都不弹，否则会给用户一个必然失败的按钮。
  expect(result.current.pendingSystemAction).toBeNull();
});

it("collects transient panels before asking for confirmation", async () => {
  installSystem({});
  const closeTransientPanels = vi.fn();
  const { result } = renderHook(() => useSystemControls({ closeTransientPanels }));
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  act(() => result.current.requestSystemAction("shutdown"));

  expect(closeTransientPanels).toHaveBeenCalledTimes(1);
  expect(result.current.pendingSystemAction).toBe("shutdown");
  expect(result.current.systemActionError).toBeNull();
});

it("clears the pending action once the shell accepts it", async () => {
  const system = installSystem({});
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  act(() => result.current.requestSystemAction("suspend"));
  await act(async () => {
    await result.current.confirmSystemAction();
  });

  expect(system.runAction).toHaveBeenCalledWith("suspend");
  expect(result.current.pendingSystemAction).toBeNull();
  expect(result.current.systemActionBusy).toBe(false);
});

it("surfaces a shell refusal instead of closing the dialog silently", async () => {
  const system = installSystem({});
  system.runAction.mockResolvedValue({ ok: false, error: "polkit 拒绝了请求" });
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  act(() => result.current.requestSystemAction("restart"));
  await act(async () => {
    await result.current.confirmSystemAction();
  });

  expect(result.current.systemActionError).toBe("polkit 拒绝了请求");
  // 失败时必须留在弹窗里，让用户看到原因。
  expect(result.current.pendingSystemAction).toBe("restart");
});

it("keeps the dialog open and explains when the session is no longer native", async () => {
  installSystem({});
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  act(() => result.current.requestSystemAction("logout"));
  // 用户在确认前丢了原生会话。
  delete (window as unknown as { echo?: unknown }).echo;

  await act(async () => {
    await result.current.confirmSystemAction();
  });

  expect(result.current.systemActionError).toBe("当前不是 Echo OS 原生系统会话");
  expect(result.current.pendingSystemAction).toBe("logout");
});

it("locks immediately without a confirmation round trip", async () => {
  const system = installSystem({});
  const closeTransientPanels = vi.fn();
  const { result } = renderHook(() => useSystemControls({ closeTransientPanels }));
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  await act(async () => {
    await result.current.lockScreen();
  });

  // 锁屏是低风险反向动作，不该弹确认框。
  expect(system.runAction).toHaveBeenCalledWith("lock");
  expect(result.current.pendingSystemAction).toBeNull();
  expect(closeTransientPanels).toHaveBeenCalled();
});

it("does nothing on lock when the shell cannot lock", async () => {
  const system = installSystem({ lock: false });
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  await act(async () => {
    await result.current.lockScreen();
  });

  expect(system.runAction).not.toHaveBeenCalled();
});

it("resets the confirmation error when the user cancels", async () => {
  const system = installSystem({});
  system.runAction.mockResolvedValue({ ok: false, error: "失败" });
  const { result } = renderHook(() => useSystemControls());
  await waitFor(() => expect(result.current.systemCapabilities.nativeShell).toBe(true));

  act(() => result.current.requestSystemAction("shutdown"));
  await act(async () => {
    await result.current.confirmSystemAction();
  });
  expect(result.current.systemActionError).toBe("失败");

  act(() => result.current.cancelSystemAction());
  expect(result.current.pendingSystemAction).toBeNull();
  expect(result.current.systemActionError).toBeNull();
});
