import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { getLocalSettings } from "@/core/settings";
import { renderWithProviders } from "@/test/harness";

import SandboxSettingsPage from "./sandbox-settings-page";

describe("SandboxSettingsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders three independent axes with defaults highlighted", () => {
    renderWithProviders(<SandboxSettingsPage />);

    // Execution environment axis.
    expect(
      screen.getByRole("button", { name: /^Sandbox/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Local/ })).toBeInTheDocument();

    // Permission level axis.
    expect(
      screen.getByRole("button", { name: /^Default/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /^Accept edits/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Full access/ }),
    ).toBeInTheDocument();

    // Network access axis — three tiers, deny highlighted by default.
    expect(
      screen.getByRole("button", { name: /^Blocked/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /^Common domains/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Allowed/ }),
    ).toBeInTheDocument();
  });

  it("switches the execution environment without touching the other axes", () => {
    renderWithProviders(<SandboxSettingsPage />);

    fireEvent.click(screen.getByRole("button", { name: /^Local/ }));

    const persisted = getLocalSettings();
    expect(persisted.context.execution_environment).toBe("local");
    expect(persisted.context.sandbox_mode).toBe("full");
    // The permission axis is untouched.
    expect(persisted.context.permission_mode).toBe("default");
    expect(persisted.context.approval_policy).toBeUndefined();
    // The network axis is untouched.
    expect(persisted.context.network_access).toBe("deny");

    expect(screen.getByRole("button", { name: /^Local/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("switches the permission level without touching the other axes", () => {
    renderWithProviders(<SandboxSettingsPage />);

    fireEvent.click(screen.getByRole("button", { name: /^Full access/ }));

    const persisted = getLocalSettings();
    expect(persisted.context.permission_mode).toBe("bypassPermissions");
    expect(persisted.context.approval_policy).toBe("never");
    // The environment axis is untouched (still sandbox by default).
    expect(persisted.context.execution_environment).toBe("sandbox");
    // The network axis is untouched.
    expect(persisted.context.network_access).toBe("deny");

    expect(
      screen.getByRole("button", { name: /^Full access/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("switches network access to the common-domains tier without touching the other axes", () => {
    renderWithProviders(<SandboxSettingsPage />);

    fireEvent.click(screen.getByRole("button", { name: /^Common domains/ }));

    const persisted = getLocalSettings();
    expect(persisted.context.network_access).toBe("common");
    // The other axes are untouched.
    expect(persisted.context.permission_mode).toBe("default");
    expect(persisted.context.execution_environment).toBe("sandbox");

    expect(
      screen.getByRole("button", { name: /^Common domains/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("switches network access to the full tier", () => {
    renderWithProviders(<SandboxSettingsPage />);

    fireEvent.click(screen.getByRole("button", { name: /^Allowed/ }));

    const persisted = getLocalSettings();
    expect(persisted.context.network_access).toBe("full");
    expect(screen.getByRole("button", { name: /^Allowed/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps all three axes independent when re-rendering an existing combination", () => {
    window.localStorage.setItem(
      "echo.local-settings",
      JSON.stringify({
        context: {
          permission_mode: "acceptEdits",
          execution_environment: "local",
          sandbox_mode: "full",
          approval_policy: "on-request",
          // Legacy boolean storage normalizes to the "full" tier.
          network_access: true,
        },
      }),
    );

    renderWithProviders(<SandboxSettingsPage />);
    expect(screen.getByRole("button", { name: /^Local/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("button", { name: /^Accept edits/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Allowed/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Change only the permission axis; environment and network stay.
    fireEvent.click(screen.getByRole("button", { name: /^Default/ }));

    const persisted = getLocalSettings();
    expect(persisted.context.permission_mode).toBe("default");
    expect(persisted.context.execution_environment).toBe("local");
    // Unchanged axes keep their raw stored value (legacy true).
    expect(persisted.context.network_access).toBe(true);
  });
});

  it("toggles the guardian independent review switch and persists it", () => {
    renderWithProviders(<SandboxSettingsPage />);

    // Off by default.
    expect(
      screen.queryByLabelText(/Review model/),
    ).not.toBeInTheDocument();

    const toggle = screen.getByRole("switch", {
      name: /Enable independent review/i,
    });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("data-state", "checked");

    // Enabling reveals the review-model input, empty by default
    // (empty = follow the conversation's own model).
    const modelInput = screen.getByLabelText(/Review model/);
    expect(modelInput).toHaveValue("");

    // Persisted to local settings.
    const saved = getLocalSettings();
    expect(saved.context.guardian_review_enabled).toBe(true);

    fireEvent.change(modelInput, { target: { value: "agnes-2.5-flash" } });
    expect(getLocalSettings().context.guardian_review_model).toBe(
      "agnes-2.5-flash",
    );
    // Clearing the input resets to "follow conversation model".
    fireEvent.change(modelInput, { target: { value: "" } });
    expect(getLocalSettings().context.guardian_review_model).toBeUndefined();

    // Toggling off hides the model input and clears the flag.
    fireEvent.click(toggle);
    expect(
      screen.queryByLabelText(/Review model/),
    ).not.toBeInTheDocument();
    expect(getLocalSettings().context.guardian_review_enabled).toBe(false);
  });
