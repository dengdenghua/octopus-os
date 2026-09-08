import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { applianceLogin } from "./auth";
import { ApplianceLogin } from "./login";

vi.mock("./auth", () => ({
  ApplianceSecondFactorRequiredError: class extends Error {},
  applianceLogin: vi.fn(),
}));

const systemCapabilities = {
  suspend: false,
  restart: false,
  shutdown: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(applianceLogin).mockResolvedValue(undefined);
});

describe("appliance login", () => {
  it("submits a password silently populated by a password manager", async () => {
    const user = userEvent.setup();
    const onSuccess = vi.fn();
    render(
      <ApplianceLogin
        onSuccess={onSuccess}
        systemCapabilities={systemCapabilities}
        onSystemAction={vi.fn()}
      />,
    );

    const password = screen.getByLabelText("密码") as HTMLInputElement;
    const valueSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    valueSetter?.call(password, "password-manager-secret");

    const submit = screen.getByRole("button", { name: "进入桌面" });
    expect(submit).toBeEnabled();
    await user.click(submit);

    expect(applianceLogin).toHaveBeenCalledWith(
      "admin",
      "password-manager-secret",
      undefined,
    );
    expect(onSuccess).toHaveBeenCalledOnce();
  });

  it("keeps native required validation for an empty password", async () => {
    const user = userEvent.setup();
    render(
      <ApplianceLogin
        onSuccess={vi.fn()}
        systemCapabilities={systemCapabilities}
        onSystemAction={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "进入桌面" }));

    expect(applianceLogin).not.toHaveBeenCalled();
  });
});
