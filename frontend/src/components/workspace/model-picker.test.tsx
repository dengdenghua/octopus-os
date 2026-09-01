/**
 * Tests for the chat-input model picker — compact dropdown variant.
 *
 * Focus: one flat model list + selection plumbing. The Official/Custom
 * tab split was removed — with a handful of configured endpoints it cost
 * two clicks to reach a neighbouring model and hid the selected row behind
 * whichever tab opened by default. We don't re-test Radix DropdownMenu
 * internals.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AllProviders } from "@/test/harness";

import { ModelPicker, type PickerModel } from "./model-picker";

// Stub useOctLink — picker reads `link.oct_user_id` to auto-enable
// unconfigured models.
vi.mock("@/core/oct/hooks", () => ({
  useOctLink: () => ({ data: null }),
}));

function withProviders(node: React.ReactNode) {
  // Test assertions reference zh-CN copy, so prime the I18nProvider to zh-CN.
  return <AllProviders locale="zh-CN">{node}</AllProviders>;
}

// Backwards-compat alias used in older tests.
const withRouter = withProviders;

// MODELS includes echo-mix (official) plus several custom models.
// The picker opens on the category that contains the current selection.
const MODELS: PickerModel[] = [
  { name: "echo-mix", display_name: "mix", provider: "echo" },
  { name: "kimi-k2.5", display_name: "Kimi K2.5", model: "kimi" },
  { name: "minimax-m2.7", display_name: "MiniMax M2.7", model: "minimax" },
  { name: "glm-5", display_name: "GLM-5", model: "glm" },
  { name: "deepseek-v3.2", display_name: "DeepSeek-V3.2", model: "deepseek" },
  { name: "qwen3-max", display_name: "Qwen3-Max", model: "qwen" },
  {
    name: "claude-opus-4-6-mirror",
    display_name: "Claude Opus 4.6 (mirror)",
    model: "claude-opus",
    description: "Anthropic Opus via mirror",
  },
];

describe("<ModelPicker />", () => {
  function setup(value = "kimi-k2.5") {
    const onChange = vi.fn();
    const utils = render(
      withRouter(
        <ModelPicker models={MODELS} value={value} onChange={onChange} />,
      ),
    );
    return { ...utils, onChange };
  }

  it("renders the default trigger with the resolved model name", () => {
    setup();
    expect(screen.getByRole("button", { name: "选择模型" })).toHaveTextContent(
      "Kimi K2.5",
    );
  });

  it("renders OpenCode Zen free model names in green without a duplicate badge", async () => {
    const user = userEvent.setup();
    render(
      withRouter(
        <ModelPicker
          models={[
            {
              name: "big-pickle",
              display_name: "big-pickle",
              model: "big-pickle",
              entry_id: "opencode-zen",
              selection_id: "zen-big-pickle",
            },
            {
              name: "mimo-v2.5-free",
              display_name: "mimo-v2.5-free",
              model: "mimo-v2.5-free",
              entry_id: "opencode-zen",
              selection_id: "zen-mimo-v2.5-free",
            },
          ]}
          value="zen-big-pickle"
          onChange={vi.fn()}
        />,
      ),
    );

    const trigger = screen.getByTestId("model-picker-trigger");
    expect(within(trigger).getByText("big-pickle")).toHaveClass(
      "text-emerald-600",
    );

    await user.click(trigger);
    const menu = await screen.findByTestId("model-picker-menu");
    expect(within(menu).getByText("big-pickle")).toHaveClass(
      "text-emerald-600",
    );
    expect(within(menu).getByText("mimo-v2.5-free")).toHaveClass(
      "text-emerald-600",
    );
    expect(within(menu).queryByText("FREE")).not.toBeInTheDocument();
  });

  it("lists every model in one flat list, no tabs", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu = await screen.findByTestId("model-picker-menu");
    expect(menu).toBeInTheDocument();
    // No tab strip at all — every model is reachable without a category hop.
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    for (const label of [
      "mix",
      "Kimi K2.5",
      "GLM-5",
      "Claude Opus 4.6 (mirror)",
    ]) {
      expect(within(menu).getByText(label)).toBeInTheDocument();
    }
  });

  it("folds the 1M variant into its base row instead of a second row", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      withRouter(
        <ModelPicker
          models={[
            {
              name: "deepseek-v4-pro",
              model: "deepseek-v4-pro",
              display_name: "DeepSeek V4 Pro",
              context_window: 256_000,
              context_profile: "default",
            },
            {
              name: "deepseek-v4-pro::1m",
              model: "deepseek-v4-pro",
              display_name: "DeepSeek V4 Pro",
              context_window: 1_000_000,
              context_profile: "1m",
            },
          ]}
          value="deepseek-v4-pro"
          onChange={onChange}
        />,
      ),
    );

    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu = await screen.findByTestId("model-picker-menu");
    expect(menu).toHaveClass("w-56");
    expect(menu.querySelector("button button")).toBeNull();
    // One row for the model, not two near-identical ones. Scoped to the menu
    // because the trigger also renders the selected model's label.
    expect(within(menu).getAllByText("DeepSeek V4 Pro")).toHaveLength(1);
    // Context length is a first-class quick setting instead of a tiny row badge.
    const context = within(menu).getByRole("radiogroup", {
      name: "上下文长度",
    });
    expect(
      within(context).getByRole("radio", { name: "标准 · 256K" }),
    ).toHaveAttribute("aria-checked", "true");
    await user.click(within(context).getByRole("radio", { name: "Max · 1M" }));
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenLastCalledWith("deepseek-v4-pro::1m");
    expect(menu).toBeVisible();
  });

  it("selecting the row itself keeps the default context window", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      withRouter(
        <ModelPicker
          models={[
            {
              name: "deepseek-v4-pro",
              model: "deepseek-v4-pro",
              display_name: "DeepSeek V4 Pro",
              context_profile: "default",
            },
            {
              name: "deepseek-v4-pro::1m",
              model: "deepseek-v4-pro",
              display_name: "DeepSeek V4 Pro",
              context_profile: "1m",
            },
          ]}
          value="deepseek-v4-pro"
          onChange={onChange}
        />,
      ),
    );

    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu = await screen.findByTestId("model-picker-menu");
    await user.click(
      within(menu).getByText("DeepSeek V4 Pro").closest("button")!,
    );
    expect(onChange).toHaveBeenCalledWith("deepseek-v4-pro");
  });

  it("distinguishes two custom entries sharing the same wire model id", async () => {
    // A primary provider and its backup can both advertise the same wire
    // model id ("deepseek-v4-flash") while carrying distinct entry_ids.
    // Selecting one must send its own entry_id and resolve its own label —
    // never highlight or echo the other.
    const user = userEvent.setup();
    const onChange = vi.fn();
    const DUP_MODELS: PickerModel[] = [
      {
        name: "deepseek-v4-flash",
        model: "deepseek-v4-flash",
        display_name: "deepseek-v4-flash",
        entry_id: "deepseek-v4-flash",
        context_profile: "default",
      },
      {
        name: "deepseek-v4-flash",
        model: "deepseek-v4-flash",
        display_name: "deepseek-v4-flash (api.b.ai)",
        entry_id: "deepseek-v4-flash-bai",
        context_profile: "default",
      },
    ];
    const { rerender } = render(
      withRouter(
        <ModelPicker
          models={DUP_MODELS}
          value="deepseek-v4-flash"
          onChange={onChange}
        />,
      ),
    );

    // Both rows render side by side even though their wire id is identical.
    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu = await screen.findByTestId("model-picker-menu");
    expect(within(menu).getByText("deepseek-v4-flash")).toBeInTheDocument();
    expect(
      within(menu).getByText("deepseek-v4-flash (api.b.ai)"),
    ).toBeInTheDocument();

    // Selecting the backup row sends its unique entry_id, not the shared
    // wire id.
    await user.click(
      within(menu).getByText("deepseek-v4-flash (api.b.ai)").closest("button")!,
    );
    expect(onChange).toHaveBeenLastCalledWith("deepseek-v4-flash-bai");

    // Selecting the primary row sends its own entry_id.
    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu2 = await screen.findByTestId("model-picker-menu");
    await user.click(
      within(menu2).getByText("deepseek-v4-flash").closest("button")!,
    );
    expect(onChange).toHaveBeenLastCalledWith("deepseek-v4-flash");

    // With the backup selected, the trigger resolves the label via
    // entry_id instead of falling back to the raw model string.
    rerender(
      withRouter(
        <ModelPicker
          models={DUP_MODELS}
          value="deepseek-v4-flash-bai"
          onChange={onChange}
        />,
      ),
    );
    expect(screen.getByRole("button", { name: "选择模型" })).toHaveTextContent(
      "deepseek-v4-flash (api.b.ai)",
    );
  });

  it("collapses exact duplicate catalog rows without hiding distinct endpoints", async () => {
    const user = userEvent.setup();
    const duplicate: PickerModel = {
      name: "deepseek-v4-flash",
      display_name: "DeepSeek V4 Flash",
      entry_id: "deepseek-v4-flash",
      context_profile: "default",
    };
    render(
      withRouter(
        <ModelPicker
          models={[duplicate, { ...duplicate }]}
          value="deepseek-v4-flash"
          onChange={vi.fn()}
        />,
      ),
    );

    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu = await screen.findByTestId("model-picker-menu");
    expect(within(menu).getAllByText("DeepSeek V4 Flash")).toHaveLength(1);
  });

  it("uses row selection ids for variants, duplicate endpoints, and 1M siblings", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const rows: PickerModel[] = [
      {
        name: "economy-model",
        model: "economy-model",
        display_name: "Primary economy",
        entry_id: "primary",
        selection_id: "selection-primary-economy-default",
        context_profile: "default",
      },
      {
        name: "economy-model::1m",
        model: "economy-model",
        display_name: "Primary economy",
        entry_id: "primary",
        selection_id: "selection-primary-economy-1m",
        context_profile: "1m",
      },
      {
        name: "shared-model",
        model: "shared-model",
        display_name: "Primary performance",
        entry_id: "primary",
        selection_id: "selection-primary-shared-default",
        context_profile: "default",
      },
      {
        name: "shared-model::1m",
        model: "shared-model",
        display_name: "Primary performance",
        entry_id: "primary",
        selection_id: "selection-primary-shared-1m",
        context_profile: "1m",
      },
      {
        name: "shared-model",
        model: "shared-model",
        display_name: "Backup performance",
        entry_id: "backup",
        selection_id: "selection-backup-shared-default",
        context_profile: "default",
      },
      {
        name: "shared-model::1m",
        model: "shared-model",
        display_name: "Backup performance",
        entry_id: "backup",
        selection_id: "selection-backup-shared-1m",
        context_profile: "1m",
      },
    ];
    const { rerender } = render(
      withRouter(
        <ModelPicker
          models={rows}
          value="selection-primary-economy-default"
          onChange={onChange}
        />,
      ),
    );

    await user.click(screen.getByTestId("model-picker-trigger"));
    const backupDefault = screen.getByRole("button", {
      name: "Backup performance",
    });
    backupDefault.focus();
    expect(backupDefault).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenLastCalledWith(
      "selection-backup-shared-default",
    );
    expect(onChange).toHaveBeenCalledOnce();
    onChange.mockClear();

    rerender(
      withRouter(
        <ModelPicker
          models={rows}
          value="selection-primary-shared-default"
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByTestId("model-picker-trigger"));
    const primaryLong = screen.getByRole("radio", { name: "Max · 1M" });
    primaryLong.focus();
    expect(primaryLong).toHaveFocus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenLastCalledWith("selection-primary-shared-1m");
    expect(onChange).toHaveBeenCalledOnce();
    onChange.mockClear();

    rerender(
      withRouter(
        <ModelPicker
          models={rows}
          value="selection-backup-shared-default"
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("radio", { name: "Max · 1M" }));
    expect(onChange).toHaveBeenLastCalledWith("selection-backup-shared-1m");
    expect(onChange).toHaveBeenCalledOnce();
  });

  it("keeps reasoning effort inside the model dropdown", async () => {
    const user = userEvent.setup();
    const onReasoningEffortChange = vi.fn();
    render(
      withRouter(
        <ModelPicker
          models={MODELS}
          value="kimi-k2.5"
          onChange={vi.fn()}
          reasoningEffort="medium"
          onReasoningEffortChange={onReasoningEffortChange}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "选择模型" }));
    const group = await screen.findByRole("radiogroup", {
      name: "推理等级",
    });

    expect(within(group).getByRole("radio", { name: "中" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(within(group).getByRole("radio", { name: "超高" }));

    expect(onReasoningEffortChange).toHaveBeenCalledWith("xhigh");
  });

  it("only offers the effort tiers the selected model genuinely supports", async () => {
    const user = userEvent.setup();
    render(
      withRouter(
        <ModelPicker
          models={[
            {
              name: "deepseek-v4",
              model: "deepseek-v4",
              display_name: "DeepSeek V4",
              reasoning_efforts: ["off", "high", "xhigh"],
            },
          ]}
          value="deepseek-v4"
          onChange={vi.fn()}
          reasoningEffort="medium"
          onReasoningEffortChange={vi.fn()}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "选择模型" }));
    const group = await screen.findByRole("radiogroup", {
      name: "推理等级",
    });
    const radios = within(group).getAllByRole("radio");
    // Only off / high / xhigh — the low/medium tiers DeepSeek collapses
    // onto "high" on the wire are not offered.
    expect(radios.map((r) => r.textContent)).toEqual(["关闭", "高", "超高"]);
  });

  it("hides the effort control when the model has no meaningful tiers", async () => {
    const user = userEvent.setup();
    render(
      withRouter(
        <ModelPicker
          models={[
            {
              name: "minimax-m2",
              model: "minimax-m2",
              display_name: "MiniMax M2",
              reasoning_efforts: [],
            },
          ]}
          value="minimax-m2"
          onChange={vi.fn()}
          reasoningEffort="medium"
          onReasoningEffortChange={vi.fn()}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "选择模型" }));
    expect(
      screen.queryByRole("radiogroup", { name: "推理等级" }),
    ).not.toBeInTheDocument();
  });

  it("shows a mapping hint when the current effort is not offered", async () => {
    const user = userEvent.setup();
    render(
      withRouter(
        <ModelPicker
          models={[
            {
              name: "deepseek-v4",
              model: "deepseek-v4",
              display_name: "DeepSeek V4",
              reasoning_efforts: ["off", "high", "xhigh"],
            },
          ]}
          value="deepseek-v4"
          onChange={vi.fn()}
          reasoningEffort="medium"
          onReasoningEffortChange={vi.fn()}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "选择模型" }));
    const menu = await screen.findByTestId("model-picker-menu");
    // "中" isn't offered for DeepSeek — it maps to "高" on the wire, and the
    // UI surfaces that instead of silently rewriting it.
    expect(within(menu).getByText(/将按.*高.*发送/)).toBeInTheDocument();
    expect(within(menu).getByRole("radio", { name: "高" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("selecting the official mix row invokes onChange with its backend name", async () => {
    const user = userEvent.setup();
    const { onChange } = setup("kimi-k2.5");
    await user.click(screen.getByRole("button", { name: "选择模型" }));

    const menu = await screen.findByRole("menu");
    const root = menu.parentElement ?? menu;
    const mixBtn = Array.from(root.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("mix"),
    );
    expect(mixBtn).toBeTruthy();
    await user.click(mixBtn!);

    expect(onChange).toHaveBeenCalledWith("echo-mix");
  });

  it("shows a bare model name with no guessed vendor label", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "选择模型" }));

    const menu = await screen.findByRole("menu");
    expect(
      within(menu).getByText("Claude Opus 4.6 (mirror)"),
    ).toBeInTheDocument();
    // The old right column guessed a vendor from the model name ("claude" →
    // "Claude"), which restated what the label already said.
    expect(within(menu).queryByText("Claude")).not.toBeInTheDocument();
  });

  it("shows the 添加模型 CTA", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "选择模型" }));

    const menu = await screen.findByRole("menu");
    expect(
      within(menu).getByRole("button", { name: /添加模型/ }),
    ).toBeInTheDocument();
  });

  it("clicking a custom row fires onChange", async () => {
    const user = userEvent.setup();
    const { onChange } = setup();
    await user.click(screen.getByRole("button", { name: "选择模型" }));

    const menu = await screen.findByRole("menu");
    await user.click(
      within(menu).getByText("Claude Opus 4.6 (mirror)").closest("button")!,
    );
    expect(onChange).toHaveBeenCalledWith("claude-opus-4-6-mirror");
  });

  it("trigger shows mix label when that model is selected", () => {
    setup("echo-mix");
    expect(screen.getByRole("button", { name: "选择模型" })).toHaveTextContent(
      "mix",
    );
  });

  it("keeps a stored-but-unavailable model instead of snapping to mix", () => {
    // A thread override may point at a model the current catalog no longer
    // advertises (removed/renamed custom model). The trigger must keep showing
    // the stored value — silently swapping to models[0] (mix) would make the
    // picker lie about what the thread actually uses after a reload.
    setup("glm-5.3");
    expect(screen.getByRole("button", { name: "选择模型" })).toHaveTextContent(
      "glm-5.3",
    );
    expect(
      screen.getByRole("button", { name: "选择模型" }),
    ).not.toHaveTextContent("mix");
  });

  it("supports a renderTrigger override", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      withRouter(
        <ModelPicker
          models={MODELS}
          value="kimi-k2.5"
          onChange={onChange}
          renderTrigger={(sel) => (
            <button type="button">CUSTOM-TRIGGER {sel?.name}</button>
          )}
        />,
      ),
    );
    const trigger = screen.getByRole("button", {
      name: /CUSTOM-TRIGGER kimi-k2.5/,
    });
    await user.click(trigger);
    expect(await screen.findByRole("menu")).toBeInTheDocument();
  });

  it("surfaces echo-mix in the Official tab when advertised", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      withProviders(
        <ModelPicker
          models={[
            {
              name: "echo-mix",
              display_name: "mix",
              provider: "echo",
            },
            { name: "minimax-m2.5", display_name: "MiniMax M2.5" },
          ]}
          value="echo-mix"
          onChange={onChange}
        />,
      ),
    );
    await user.click(screen.getByRole("button", { name: "选择模型" }));
    const menu = await screen.findByRole("menu");
    const root = menu.parentElement ?? menu;
    // models advertises echo-mix → it classifies as Official (not Custom)
    expect(root.textContent ?? "").toContain("mix");
  });
});
