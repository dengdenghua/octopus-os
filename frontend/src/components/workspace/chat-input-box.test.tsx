import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import { queueComposerImageEntry } from "@/core/composer-image-inbox";

import type * as UploadsApiModule from "@/core/uploads/api";

import { ChatInputBox } from "./chat-input-box";
import type { GroupTaskStrategy } from "./group-task-strategy";

const uploadFilesMock = vi.fn();
const uploadWithProgressMock = vi.fn();
const modelCatalog = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
const capabilityCatalog = vi.hoisted(() => ({
  pluginOptions: [] as Array<{ enabled?: boolean } | undefined>,
  skillOptions: [] as Array<{ enabled?: boolean } | undefined>,
  plugins: [
    {
      id: "seedance",
      name: "Seedance",
      description: "Generate videos",
      enabled: true,
      state: "running",
    },
    {
      id: "disabled-plugin",
      name: "Disabled plugin",
      description: "Hidden",
      enabled: false,
      state: "stopped",
    },
  ],
  skills: [
    {
      name: "video-generate",
      description: "Generate a video from a prompt",
      enabled: true,
      category: "media",
    },
    {
      name: "disabled-skill",
      description: "Hidden",
      enabled: false,
      category: "other",
    },
  ],
}));

// Only the transport is stubbed — ``useAttachmentUploads`` runs for real so the
// progress/gating tests exercise the actual state machine. The hook imports
// from ``./api`` directly, so that module is what has to be mocked; the barrel
// re-exports it.
vi.mock("@/core/uploads/api", async (importOriginal) => {
  const actual = await importOriginal<UploadsApiModule>();
  return {
    ...actual,
    uploadFiles: (...args: unknown[]) => uploadFilesMock(...args),
    uploadFilesWithProgress: (...args: unknown[]) =>
      uploadWithProgressMock(...args),
  };
});

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: modelCatalog.current,
  }),
}));

vi.mock("@/core/plugins/hooks", () => ({
  usePlugins: (options?: { enabled?: boolean }) => {
    capabilityCatalog.pluginOptions.push(options);
    return {
      plugins: capabilityCatalog.plugins,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    };
  },
}));

vi.mock("@/core/skills/hooks", () => ({
  useSkills: (options?: { enabled?: boolean }) => {
    capabilityCatalog.skillOptions.push(options);
    return {
      skills: capabilityCatalog.skills,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    };
  },
}));

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    isLoading: false,
    authStatus: { enabled: false, allow_registration: false },
    user: null,
    isAuthenticated: false,
    login: vi.fn(),
    smsLogin: vi.fn(),
    guestLogin: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("./evolution-indicator", () => ({
  EvolutionIndicator: () => null,
}));

vi.mock("./file-activity-indicator", () => ({
  FileActivityIndicator: () => null,
}));

vi.mock("./preview-refresh-indicator", () => ({
  PreviewRefreshIndicator: () => null,
}));

function textarea(): HTMLTextAreaElement {
  const el = document.querySelector("textarea");
  if (!el) throw new Error("textarea not found");
  return el as HTMLTextAreaElement;
}

async function openAgentSettings() {
  const trigger = screen.getByLabelText("Insert into input");
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByText("Research settings"));
}

async function openToolsMenu() {
  const trigger = screen.getByTestId("chat-tools-trigger");
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  fireEvent.click(trigger);
  return screen.findByRole("menu");
}

it("keeps the plus entry visually lightweight when focused", () => {
  renderWithProviders(<ChatInputBox mode="react" threadId="thread-plus" />);

  expect(screen.getByTestId("chat-tools-trigger")).toHaveClass(
    "focus-visible:outline-none",
  );
});

function uploadedInfo(file: File) {
  return {
    filename: file.name,
    size: file.size,
    path: `/artifacts/${file.name}`,
    virtual_path: `uploads/${file.name}`,
    artifact_url: `https://example.test/${file.name}`,
    content_type: file.type,
  };
}

beforeEach(() => {
  // Composer drafts intentionally persist across reloads, but not across
  // independent tests. A full-suite predecessor may otherwise leave a draft
  // under a reused thread id and make send-failure isolation assertions flaky.
  window.localStorage.clear();
  modelCatalog.current = [
    {
      id: "test-model",
      name: "test-model",
      model: "test-model",
      display_name: "Test Model",
    },
  ];
  uploadFilesMock.mockReset();
  uploadWithProgressMock.mockReset();
  capabilityCatalog.pluginOptions = [];
  capabilityCatalog.skillOptions = [];
  // Attaching now uploads immediately, so every test needs a transport.
  // The default resolves at once; progress-specific tests override it.
  uploadWithProgressMock.mockImplementation(
    async (
      _threadId: string,
      files: File[],
      options?: { onProgress?: (p: number) => void },
    ) => {
      options?.onProgress?.(100);
      return { files: files.map(uploadedInfo) };
    },
  );
});

describe("<ChatInputBox /> cowork materials", () => {
  it("places the automation target picker inside the plus menu", async () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-target-menu"
        automationTarget={{
          kind: "desktop_window",
          source: "computer",
          id: "window-7",
          title: "Project notes",
          app_name: "Notes",
        }}
        onAutomationTargetChange={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("automation-target-trigger")).toBeNull();
    expect(
      screen.getByTestId("automation-target-active-indicator"),
    ).toBeInTheDocument();

    const menu = await openToolsMenu();
    expect(
      within(menu).getByTestId("automation-target-submenu-trigger"),
    ).toHaveTextContent("Window · Project notes");
    expect(within(menu).queryByTestId("chat-add-appshot")).toBeNull();
  });

  it("renders the shared model profile control independently of agent role", () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-unified-model-control"
        modelProfileControl
      />,
    );

    expect(screen.getByTestId("coder-engine-trigger")).toBeInTheDocument();
    expect(screen.queryByTestId("model-picker-trigger")).toBeNull();
  });

  it("opens the Teach & Repeat library for /record without sending a message", async () => {
    const user = userEvent.setup();
    const onSwitchPanel = vi.fn();
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-record"
        onSwitchPanel={onSwitchPanel}
        onSubmit={onSubmit}
      />,
    );

    await user.type(textarea(), "/record");
    await user.click(screen.getByLabelText("Send"));

    expect(onSwitchPanel).toHaveBeenCalledWith("teach-repeat");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("replaces Inspiration with the response strategy in collaboration", () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-response-mode"
        showInspirationToggle
        responseModeControl={
          <div data-testid="response-mode-control">Conversation type</div>
        }
      />,
    );

    const control = screen.getByTestId("response-mode-control");
    expect(control.closest(".composer-footer")).toBeInTheDocument();
    expect(control.closest(".composer-footer__response")).toBeInTheDocument();
    expect(
      screen
        .getByTestId("model-picker-trigger")
        .closest(".composer-footer__model"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("chat-mode-toggle")).toBeNull();
    expect(screen.getByTestId("chat-send-button")).toBeInTheDocument();
  });

  it("moves restore-auto into + and removes it after use", async () => {
    const onStrategyChange = vi.fn();
    const onSubmit = vi.fn();

    function ControlledGroupComposer() {
      const [strategy, setStrategy] = useState<GroupTaskStrategy>("auto");
      return (
        <ChatInputBox
          mode="react"
          threadId="thread-group-strategy"
          isGroupConversation
          showWorkDirSelector
          groupTaskStrategy={strategy}
          onGroupTaskStrategyChange={(next) => {
            onStrategyChange(next);
            setStrategy(next);
          }}
          onSubmit={onSubmit}
        />
      );
    }

    renderWithProviders(<ControlledGroupComposer />);

    expect(
      screen.getByRole("button", { name: "Add content" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Auto" }));
    fireEvent.click(screen.getByRole("option", { name: /Deep research/ }));

    expect(onStrategyChange).toHaveBeenLastCalledWith("research");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByTestId("group-task-strategy-chip")).toBeNull();
    expect(screen.queryByTestId("group-task-strategy-indicator")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Deep research" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("chat-composer")).queryByText("Deep research"),
    ).toBeNull();

    const menu = await openToolsMenu();
    fireEvent.click(within(menu).getByTestId("group-task-clear-action"));

    expect(onStrategyChange).toHaveBeenLastCalledWith("auto");
    expect(screen.queryByTestId("group-task-strategy-chip")).toBeNull();
    expect(screen.queryByTestId("group-task-strategy-indicator")).toBeNull();
    const reopenedMenu = await openToolsMenu();
    expect(
      within(reopenedMenu).queryByTestId("group-task-clear-action"),
    ).toBeNull();
  });

  it("offers create-deliverable without a folder and develop with a folder", async () => {
    const first = renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-group-personal"
        isGroupConversation
        showWorkDirSelector
        groupTaskStrategy="auto"
        onGroupTaskStrategyChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Auto" }));
    expect(
      screen.getByRole("option", { name: /Create deliverable/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Develop/ })).toBeNull();
    first.unmount();

    renderWithProviders(
      <ChatInputBox
        mode="code"
        threadId="thread-group-project"
        workDir="/workspace/project"
        isGroupConversation
        showWorkDirSelector
        groupTaskStrategy="auto"
        onGroupTaskStrategyChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Develop/ }));
    expect(screen.getByRole("option", { name: /Develop/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /Create deliverable/ }),
    ).toBeNull();
  });

  it("keeps project planning separate from the per-turn task strategy", async () => {
    const onProjectCapabilityAction = vi.fn();
    const onStrategyChange = vi.fn();
    const unbound = renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-group-project-plan"
        isGroupConversation
        showWorkDirSelector
        groupTaskStrategy="auto"
        onGroupTaskStrategyChange={onStrategyChange}
        onProjectCapabilityAction={onProjectCapabilityAction}
      />,
    );

    let menu = await openToolsMenu();
    fireEvent.click(
      within(menu).getByTestId("group-project-capability-action"),
    );
    expect(onProjectCapabilityAction).toHaveBeenCalledTimes(1);
    expect(onStrategyChange).not.toHaveBeenCalled();
    expect(screen.queryByTestId("group-task-strategy-chip")).toBeNull();
    unbound.unmount();

    const onBoundStrategyChange = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="code"
        threadId="thread-group-bound-project"
        workDir="/workspace/project"
        isGroupConversation
        showWorkDirSelector
        groupTaskStrategy="auto"
        onGroupTaskStrategyChange={onBoundStrategyChange}
        projectCapabilityEnabled
        onProjectCapabilityAction={onProjectCapabilityAction}
        responseModeControl={
          <div data-testid="bound-project-response-mode">AI participation</div>
        }
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Develop/ }));
    fireEvent.click(screen.getByRole("option", { name: /Read-only audit/ }));
    menu = await openToolsMenu();
    expect(
      within(menu).getByText("Open project workbench"),
    ).toBeInTheDocument();
    expect(within(menu).queryByText("Create project plan")).toBeNull();
    expect(within(menu).queryByTestId("group-task-strategy-audit")).toBeNull();
    expect(onBoundStrategyChange).toHaveBeenCalledWith("audit");
    expect(onProjectCapabilityAction).toHaveBeenCalledTimes(1);
    expect(
      screen.getByTestId("bound-project-response-mode"),
    ).toBeInTheDocument();
  });

  it("keeps personal/project status visible in groups and hides only default permission chrome", () => {
    const onGroupTaskStrategyChange = vi.fn();
    const group = renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-group-clean-footer"
        isGroupConversation
        groupTaskStrategy="auto"
        onGroupTaskStrategyChange={onGroupTaskStrategyChange}
        showWorkDirSelector
        statusTrailing={<span data-testid="group-roster-inline">avatars</span>}
        permissionMode="default"
      />,
    );

    expect(screen.getByTestId("chat-status-strip")).toBeInTheDocument();
    expect(
      screen
        .getByTestId("chat-status-strip")
        .contains(screen.getByTestId("group-roster-inline")),
    ).toBe(true);
    expect(screen.getByTitle("Personal space")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Auto" }));
    fireEvent.click(screen.getByRole("option", { name: /Create deliverable/ }));
    expect(onGroupTaskStrategyChange).toHaveBeenCalledWith("build");
    expect(screen.queryByTestId("permission-mode-trigger")).toBeNull();
    group.unmount();

    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-group-risk-warning"
        isGroupConversation
        permissionMode="bypassPermissions"
      />,
    );

    expect(screen.getByTestId("permission-mode-trigger")).toHaveAccessibleName(
      "Permissions: Full access",
    );
  });

  it("keeps the existing private composer controls unchanged", async () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-private-controls"
        showWorkDirSelector
        permissionMode="default"
      />,
    );

    expect(screen.getByTestId("chat-status-strip")).toBeInTheDocument();
    expect(screen.getByTestId("permission-mode-trigger")).toHaveAccessibleName(
      "Permissions: Default",
    );
    expect(
      screen.getByRole("button", { name: "Insert into input" }),
    ).toBeInTheDocument();

    const menu = await openToolsMenu();
    expect(within(menu).queryByText("Start a task")).toBeNull();
    expect(within(menu).queryByTestId("group-task-strategy-auto")).toBeNull();
  });

  it("returns the selected row id when two endpoints share one wire model", async () => {
    modelCatalog.current = [
      {
        id: "deepseek-v4-flash",
        name: "deepseek-v4-flash",
        model: "deepseek-v4-flash",
        display_name: "DeepSeek primary",
        entry_id: "deepseek-primary",
        selection_id: "selection-deepseek-primary-default",
        reasoning_efforts: ["off", "high"],
        context_window: 256_000,
        context_profile: "default",
        supports_thinking: true,
        supports_vision: false,
        supports_tool_use: true,
      },
      {
        id: "deepseek-v4-flash",
        name: "deepseek-v4-flash",
        model: "deepseek-v4-flash",
        display_name: "DeepSeek backup",
        entry_id: "deepseek-backup",
        selection_id: "selection-deepseek-backup-default",
        reasoning_efforts: ["off", "high", "xhigh"],
        context_window: 128_000,
        context_profile: "default",
        supports_thinking: true,
        supports_vision: true,
        supports_tool_use: false,
      },
    ];
    const user = userEvent.setup();
    const onModelChange = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-duplicate-models"
        modelName="selection-deepseek-primary-default"
        onModelChange={onModelChange}
      />,
    );

    await user.click(screen.getByTestId("model-picker-trigger"));
    const menu = await screen.findByTestId("model-picker-menu");
    await user.click(
      within(menu).getByText("DeepSeek backup").closest("button")!,
    );

    expect(onModelChange).toHaveBeenCalledWith(
      "selection-deepseek-backup-default",
    );
  });

  it("gives the composer a persistent accessible name", () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-accessible-name"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    expect(screen.getByTestId("chat-composer-input")).toHaveAttribute(
      "aria-label",
      "How can I assist you today?",
    );
  });

  it("keeps the add menu focused on user-facing context and task controls", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ChatInputBox
        mode="deep"
        threadId="thread-1"
        allowAgentModes
        onDeepResearch={vi.fn()}
      />,
    );

    // Scope the negative assertions to the menu: the composer status strip
    // always renders a permission-mode label ("Default"), so a document-wide
    // queryByText would fail on chrome that has nothing to do with the menu.
    const menu = await openToolsMenu();
    const inMenu = within(menu);

    expect(screen.getByText("Research settings")).toBeInTheDocument();
    expect(screen.getByText("Upload images")).toBeInTheDocument();
    expect(screen.getByText("Project files")).toBeInTheDocument();
    expect(screen.getByText("Commands")).toBeInTheDocument();
    expect(screen.getByText("Plugins")).toBeInTheDocument();
    expect(screen.getByText("Skills")).toBeInTheDocument();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    expect(await screen.findByText("Spec")).toBeInTheDocument();
    expect(screen.getByText("Plan")).toBeInTheDocument();
    expect(
      screen.getByText("Goal").closest('[role="menuitem"]'),
    ).toHaveTextContent("🎯Goal");
    expect(screen.getByText("Milestone")).toBeInTheDocument();
    expect(screen.getByText("Browser")).toBeInTheDocument();
    expect(screen.getByText("Chrome")).toBeInTheDocument();
    expect(screen.queryByText("Add material")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Add image (paste / drag / select)"),
    ).not.toBeInTheDocument();
    expect(inMenu.queryByText("Default")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Web search")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Create PPT")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Create page")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Format table")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Generate image")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Scheduled Task")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Project Files")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Research context")).not.toBeInTheDocument();
    expect(inMenu.queryByText("Web Search Research")).not.toBeInTheDocument();
  });

  it("inserts user-facing plan and goal modes without switching the model mode", async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        allowAgentModes
        onModeChange={onModeChange}
        onDeepResearch={vi.fn()}
      />,
    );

    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Plan"));

    expect(textarea().value).toBe("");
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "Plan",
    );
    expect(screen.getByTestId("composer-command-prefix")).toHaveClass(
      "font-bold",
      "text-sky-600",
    );
    expect(onModeChange).not.toHaveBeenCalled();

    fireEvent.change(textarea(), {
      target: { value: "Audit this repo" },
    });
    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Goal"));

    expect(textarea().value).toBe("Audit this repo");
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "Goal",
    );
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "🎯",
    );
    expect(screen.getByTestId("composer-command-prefix")).toHaveClass(
      "font-bold",
      "text-violet-600",
    );
    expect(screen.getByTestId("composer-long-task-indicator")).toHaveAttribute(
      "aria-label",
      "Goal 模式 · 点击退出",
    );
    expect(screen.getByTestId("composer-long-task-indicator")).toHaveClass(
      "text-muted-foreground",
    );
    expect(
      screen.getByTestId("composer-long-task-indicator"),
    ).toHaveTextContent("🎯");
    const longTaskIndicator = screen.getByTestId(
      "composer-long-task-indicator",
    );
    const permissionTrigger = screen.getByTestId("permission-mode-trigger");
    expect(longTaskIndicator.closest(".ml-auto")).toBeNull();
    expect(
      permissionTrigger.compareDocumentPosition(longTaskIndicator) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(onModeChange).not.toHaveBeenCalled();
  });

  it("keeps marker-only Codex drafts unsent until the task is written", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={onSubmit}
        onDeepResearch={vi.fn()}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "/mode plan\n" } });

    expect(screen.getByTitle("Send")).toBeDisabled();
    fireEvent.click(screen.getByTitle("Send"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(textarea().value).toBe("");
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "Plan",
    );
  });

  it("shows Milestone as a mode and sends through the Project OS command", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-project-mode"
        onSubmit={onSubmit}
      />,
    );

    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Milestone"));

    expect(textarea()).toHaveValue("");
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "Milestone",
    );
    expect(screen.getByTestId("composer-command-prefix")).toHaveClass(
      "font-bold",
      "text-rose-600",
    );
    expect(textarea()).toHaveClass("pl-[7.5rem]");
    expect(screen.getByTitle("Send")).toBeDisabled();

    fireEvent.change(textarea(), { target: { value: "Ship the release" } });
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: "/project run\nShip the release",
      }),
    );
    expect(screen.getByTestId("composer-long-task-indicator")).toHaveAttribute(
      "aria-label",
      "里程碑模式 · 点击退出",
    );
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "Milestone",
    );
    expect(textarea()).toHaveValue("");

    fireEvent.click(screen.getByTestId("composer-long-task-indicator"));
    expect(screen.queryByTestId("composer-long-task-indicator")).toBeNull();
    expect(screen.queryByTestId("composer-command-prefix")).toBeNull();
  });

  it("lazily exposes plugins and skills as removable colored references", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-capability-menu"
        onSubmit={onSubmit}
      />,
    );

    expect(capabilityCatalog.pluginOptions.at(-1)).toEqual({ enabled: false });
    expect(capabilityCatalog.skillOptions.at(-1)).toEqual({ enabled: false });

    await openToolsMenu();
    expect(capabilityCatalog.pluginOptions.at(-1)).toEqual({ enabled: true });
    expect(capabilityCatalog.skillOptions.at(-1)).toEqual({ enabled: true });
    await user.hover(screen.getByTestId("chat-plugins-submenu"));
    fireEvent.click(await screen.findByText("Seedance"));

    expect(
      screen.getByTestId("composer-capability-plugin-seedance"),
    ).toHaveClass("text-violet-700");
    expect(screen.getByTitle("Send")).toBeDisabled();

    fireEvent.change(textarea(), { target: { value: "Create launch clip" } });
    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-skills-submenu"));
    const search = await screen.findByTestId("chat-skill-search");
    fireEvent.change(search, { target: { value: "video" } });
    expect(screen.queryByText("disabled-skill")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("video-generate"));

    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Browser"));
    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Chrome"));
    expect(
      screen.getByTestId("composer-capability-surface-chrome"),
    ).toHaveTextContent("Chrome");
    expect(
      screen.queryByTestId("composer-capability-surface-browser"),
    ).not.toBeInTheDocument();
    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Browser"));
    await openToolsMenu();
    await user.hover(screen.getByTestId("chat-commands-submenu"));
    fireEvent.click(await screen.findByText("Goal"));

    expect(
      screen.getByTestId("composer-capability-skill-video-generate"),
    ).toHaveClass("text-blue-700");
    expect(
      screen.getByTestId("composer-capability-surface-browser"),
    ).toHaveClass("text-cyan-700");
    expect(screen.getByTestId("composer-command-prefix")).toHaveTextContent(
      "Goal",
    );

    fireEvent.click(screen.getByTitle("Send"));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: "/mode goal\n@plugin:seedance @skill:video-generate @Browser\nCreate launch clip",
      }),
    );
  });

  it("removes an empty highlighted command with Backspace", () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        defaultValue={"/mode goal\n"}
      />,
    );

    fireEvent.keyDown(screen.getByTestId("chat-composer-input"), {
      key: "Backspace",
    });

    expect(
      screen.queryByTestId("composer-command-prefix"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-composer-input")).toHaveValue("");
  });

  it("sends default execution mode through the normal message path", async () => {
    const onSubmit = vi.fn();
    const onDeepResearch = vi.fn().mockResolvedValue(true);
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={onSubmit}
        onDeepResearch={onDeepResearch}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "Run the agent" } });
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ text: "Run the agent" }),
    );
    expect(onDeepResearch).not.toHaveBeenCalled();
  });

  it("sends vague tasks through so the model can decide whether to clarify", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox mode="react" threadId="thread-1" onSubmit={onSubmit} />,
    );

    fireEvent.change(textarea(), {
      target: { value: "Research a promising niche market" },
    });
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: "Research a promising niche market",
      }),
    );
    expect(textarea().value).toBe("");
  });

  it("suppresses duplicate submissions before the parent status updates", () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox mode="react" threadId="thread-1" onSubmit={onSubmit} />,
    );

    fireEvent.change(textarea(), { target: { value: "Run once" } });
    const send = screen.getByTitle("Send");
    fireEvent.click(send);
    fireEvent.click(send);

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("allows sending a pasted image without typed text", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox mode="react" threadId="thread-1" onSubmit={onSubmit} />,
    );

    const image = new File(["img"], "screen.png", { type: "image/png" });
    fireEvent.paste(textarea(), {
      clipboardData: {
        items: [
          {
            kind: "file",
            type: "image/png",
            getAsFile: () => image,
          },
        ],
      },
    });

    // Send stays blocked until the attachment finishes uploading.
    await waitFor(() => expect(screen.getByTitle("Send")).toBeEnabled());
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: "",
        images: [image],
        uploaded: [uploadedInfo(image)],
      }),
    );
  });

  it("adds clicked workspace files to the composer and sends them as turn context", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        workDir="/repo/echo"
        onSubmit={onSubmit}
      />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:open-file", {
          detail: {
            threadId: "thread-1",
            path: "src/app.tsx",
            workDir: "/repo/echo",
          },
        }),
      );
    });

    expect(await screen.findByText("app.tsx")).toBeInTheDocument();
    expect(screen.getByTitle("Send")).toBeEnabled();
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: expect.stringContaining(
          "path=src/app.tsx workspace=/repo/echo",
        ),
      }),
    );
  });

  it("sends selected local files through the normal attachment path", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox mode="react" threadId="thread-1" onSubmit={onSubmit} />,
    );

    const file = new File(["brief"], "brief.md", { type: "text/markdown" });
    const contextInput = screen.getByTestId(
      "chat-device-file-input",
    ) as HTMLInputElement;
    fireEvent.change(contextInput, {
      target: { files: [file] },
    });

    expect(await screen.findByText("brief.md")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTitle("Send")).toBeEnabled());
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: expect.stringContaining("upload=brief.md"),
        files: [file],
        uploaded: [uploadedInfo(file)],
      }),
    );
  });

  it("treats legacy deep Agent state as a normal message until research settings open", async () => {
    const onSubmit = vi.fn();
    const onDeepResearch = vi.fn().mockResolvedValue(true);
    renderWithProviders(
      <ChatInputBox
        mode="deep"
        threadId="thread-1"
        onSubmit={onSubmit}
        onDeepResearch={onDeepResearch}
      />,
    );

    fireEvent.change(textarea(), {
      target: { value: "Continue in agent mode" },
    });
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        text: "Continue in agent mode",
      }),
    );
    expect(onDeepResearch).not.toHaveBeenCalled();
  });

  it("prefills the composer and switches to cowork from a thinking plan event", async () => {
    const onModeChange = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        allowAgentModes
        onModeChange={onModeChange}
        onDeepResearch={vi.fn()}
      />,
    );

    window.dispatchEvent(
      new CustomEvent("echo:start-deep-research", {
        detail: { threadId: "other-thread", topic: "wrong topic" },
      }),
    );
    expect(textarea().value).toBe("");

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("echo:start-deep-research", {
          detail: { threadId: "thread-1", topic: "NAS market research" },
        }),
      );
    });

    await waitFor(() => {
      expect(textarea().value).toBe("NAS market research");
    });
    expect(onModeChange).toHaveBeenCalledWith("deep");
  });

  it("can expose Inspiration as a right-side toggle without an Agent menu", async () => {
    const onModeChange = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onModeChange={onModeChange}
        showInspirationToggle
        onDeepResearch={vi.fn()}
      />,
    );

    window.dispatchEvent(
      new CustomEvent("echo:start-deep-research", {
        detail: { threadId: "thread-1", topic: "NAS market research" },
      }),
    );

    await waitFor(() => {
      expect(textarea().value).toBe("NAS market research");
    });
    expect(onModeChange).not.toHaveBeenCalled();

    expect(screen.queryByText("Swarm")).toBeNull();
    expect(screen.queryByText("Add Research Material")).toBeNull();
    expect(screen.queryByTestId("reasoning-mode-trigger")).toBeNull();

    const inspiration = screen.getByRole("button", {
      name: "Discuss ideas without running tools",
    });
    expect(inspiration).toHaveAttribute("aria-pressed", "false");
    expect(inspiration).toHaveAttribute(
      "title",
      "Discuss ideas without running tools",
    );
    expect(inspiration).not.toHaveTextContent("Inspiration");

    fireEvent.click(inspiration);

    expect(onModeChange).toHaveBeenCalledWith("chat", "NAS market research");
  });

  it("marks the Inspiration toggle active in discussion-only mode", () => {
    renderWithProviders(
      <ChatInputBox
        mode="chat"
        threadId="thread-1"
        showInspirationToggle
        onModeChange={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", {
        name: "Discuss ideas without running tools",
      }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("lets users select the reasoning effort", async () => {
    const onReasoningEffortChange = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        reasoningEffort="medium"
        onReasoningEffortChange={onReasoningEffortChange}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Select Model" });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    expect(
      await screen.findByRole("radiogroup", { name: "Reasoning effort" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Ultra" }));

    expect(onReasoningEffortChange).toHaveBeenCalledWith("xhigh");
  });

  it("shows the context compressor as a persistent input control", () => {
    const { rerender } = renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        contextTokens={500}
        maxContextTokens={1000}
      />,
    );

    expect(screen.getByLabelText(/Context Usage: 50%/)).toBeInTheDocument();

    rerender(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        contextTokens={600}
        maxContextTokens={1000}
      />,
    );

    expect(screen.getByLabelText(/Context Usage: 60%/)).toBeInTheDocument();
  });

  it("submits only enabled URL/text materials", async () => {
    const onDeepResearch = vi.fn().mockResolvedValue(true);
    renderWithProviders(
      <ChatInputBox
        mode="deep"
        threadId="thread-1"
        allowAgentModes
        onDeepResearch={onDeepResearch}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "Research NAS market" } });
    await openAgentSettings();
    fireEvent.change(
      screen.getByPlaceholderText("https://example.com, https://..."),
      {
        target: { value: "https://www.synology.com/" },
      },
    );
    fireEvent.change(screen.getByPlaceholderText("Material Note"), {
      target: { value: "official site" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^URL$/i }));

    fireEvent.change(screen.getByPlaceholderText("Text Title"), {
      target: { value: "Internal notes" },
    });
    fireEvent.change(screen.getByPlaceholderText("Paste text material"), {
      target: { value: "Users care about backup." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Text$/i }));

    fireEvent.click(screen.getAllByTitle("Toggle Material")[0]);
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() => expect(onDeepResearch).toHaveBeenCalledTimes(1));
    // Sending clears the optimistic draft asynchronously. Wait for that
    // contract before unmounting so the following test cannot observe a late
    // state write when the full suite is under load.
    await waitFor(() => expect(textarea().value).toBe(""));
    const [, options] = onDeepResearch.mock.calls[0];
    expect(options.materials).toEqual([
      expect.objectContaining({
        kind: "text",
        title: "Internal notes",
        text: "Users care about backup.",
      }),
    ]);
  });

  it("uploads files and submits them as file materials", async () => {
    uploadFilesMock.mockResolvedValue({
      success: true,
      message: "ok",
      files: [
        {
          filename: "brief.md",
          path: "F:/uploads/thread-1/brief.md",
          virtual_path: "/uploads/brief.md",
          artifact_url: "/api/artifacts/brief.md",
          size: 123,
          modified: 1,
          extension: ".md",
        },
      ],
    });
    const onDeepResearch = vi.fn().mockResolvedValue(true);
    renderWithProviders(
      <ChatInputBox
        mode="deep"
        threadId="thread-1"
        allowAgentModes
        onDeepResearch={onDeepResearch}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "Research NAS market" } });
    await openAgentSettings();
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["hello"], "brief.md", { type: "text/markdown" })],
      },
    });

    await screen.findByText("brief.md");
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() => expect(onDeepResearch).toHaveBeenCalledTimes(1));
    const [, options] = onDeepResearch.mock.calls[0];
    expect(uploadFilesMock).toHaveBeenCalledWith("thread-1", [
      expect.objectContaining({ name: "brief.md" }),
    ]);
    expect(options.materials).toEqual([
      expect.objectContaining({
        kind: "file",
        title: "brief.md",
        path: "F:/uploads/thread-1/brief.md",
      }),
    ]);
  });

  it("does not expose raw upload errors in the composer", async () => {
    uploadFilesMock.mockRejectedValueOnce(
      new Error("S3 credential token leaked from upstream"),
    );
    renderWithProviders(
      <ChatInputBox
        mode="deep"
        threadId="thread-1"
        allowAgentModes
        onDeepResearch={vi.fn()}
      />,
    );

    await openAgentSettings();
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["hello"], "brief.md", { type: "text/markdown" })],
      },
    });

    expect(await screen.findByText("Upload failed")).toBeInTheDocument();
    expect(
      screen.queryByText("S3 credential token leaked from upstream"),
    ).not.toBeInTheDocument();
  });

  it("lets the planner choose research roles instead of sending a fixed template", async () => {
    const onDeepResearch = vi.fn().mockResolvedValue(true);
    renderWithProviders(
      <ChatInputBox
        mode="deep"
        threadId="thread-1"
        allowAgentModes
        onDeepResearch={onDeepResearch}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "Research NAS market" } });
    await openAgentSettings();
    fireEvent.click(screen.getByTitle("Send"));

    await waitFor(() => expect(onDeepResearch).toHaveBeenCalledTimes(1));
    const [, options] = onDeepResearch.mock.calls[0];
    expect(options.roles).toBeUndefined();
    expect(options.maxSubagents).toBeUndefined();
  });
});

describe("<ChatInputBox /> live steering", () => {
  it("keeps text input sendable while a turn is streaming", () => {
    const onSubmit = vi.fn();
    const onStop = vi.fn();
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-live"
        status="streaming"
        onSubmit={onSubmit}
        onStop={onStop}
      />,
    );

    const input = screen.getByTestId("chat-composer-input");
    expect(input).not.toBeDisabled();
    fireEvent.change(input, { target: { value: "先暂停修改，核对根因" } });
    fireEvent.click(screen.getByTestId("chat-steer-button"));

    expect(onSubmit).toHaveBeenCalledWith({
      text: "先暂停修改，核对根因",
      images: undefined,
      files: undefined,
    });
  });
});

describe("<ChatInputBox /> send-failure draft restore", () => {
  function dispatchSendFailed(detail: {
    threadId?: string | null;
    text?: string | null;
    images?: File[] | null;
    sourceLabel?: string | null;
  }) {
    act(() => {
      window.dispatchEvent(new CustomEvent("echo:send-failed", { detail }));
    });
  }

  it("restores the draft when a send fails after optimistic clear", async () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "hello agent" } });
    fireEvent.click(screen.getByTitle("Send"));
    await waitFor(() => expect(textarea().value).toBe(""));

    dispatchSendFailed({ threadId: "thread-1", text: "hello agent" });

    await waitFor(() => expect(textarea().value).toBe("hello agent"));
  });

  it("ignores failures from other threads", () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    dispatchSendFailed({ threadId: "thread-other", text: "not mine" });

    expect(textarea().value).toBe("");
  });

  it("does not clobber text the user already retyped", () => {
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    fireEvent.change(textarea(), { target: { value: "new attempt" } });
    dispatchSendFailed({ threadId: "thread-1", text: "old failed text" });

    expect(textarea().value).toBe("new attempt");
  });

  it("restores failed screenshots when the composer had been cleared", async () => {
    const image = new File(["img"], "failed-shot.png", { type: "image/png" });
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    dispatchSendFailed({
      threadId: "thread-1",
      images: [image],
      sourceLabel: "浏览器截图",
    });

    await waitFor(() =>
      expect(
        document.querySelector('img[alt="failed-shot.png"]'),
      ).toBeInTheDocument(),
    );
    expect(screen.getByTitle("Send")).toBeEnabled();
    expect(screen.getByText("浏览器截图")).toBeInTheDocument();
  });

  it("accepts externally injected browser screenshots for the active thread", async () => {
    const image = new File(["img"], "browser-shot.png", { type: "image/png" });
    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:inject-composer-images", {
          detail: {
            threadId: "thread-1",
            images: [image],
            sourceLabel: "浏览器截图",
          },
        }),
      );
    });

    await waitFor(() =>
      expect(
        document.querySelector('img[alt="browser-shot.png"]'),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("浏览器截图")).toBeInTheDocument();
  });

  it("hydrates queued browser screenshots when the composer mounts", async () => {
    const pngDataUrl =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnR5WQAAAAASUVORK5CYII=";
    queueComposerImageEntry({
      dataUrl: pngDataUrl,
      filename: "queued-browser-shot.png",
      sourceLabel: "浏览器截图",
    });

    renderWithProviders(
      <ChatInputBox
        mode="react"
        threadId="thread-1"
        onSubmit={vi.fn()}
        onDeepResearch={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(
        document.querySelector('img[alt="queued-browser-shot.png"]'),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("浏览器截图")).toBeInTheDocument();
  });
});

// ── upload on attach · the chip is the upload, not a promise of one ─
//
// Attachments used to upload inside the send handler. Nothing was in flight
// while the chip sat in the composer, so a progress bar was impossible and the
// only completion signal was a toast that appeared, detached, after send.
describe("<ChatInputBox /> upload on attach", () => {
  /** aria-label is stable; the title explains *why* send is blocked. */
  const sendButton = () => screen.getByLabelText("Send");

  function pasteImage(name = "shot.png") {
    const image = new File(["img"], name, { type: "image/png" });
    fireEvent.paste(textarea(), {
      clipboardData: {
        items: [{ kind: "file", type: "image/png", getAsFile: () => image }],
      },
    });
    return image;
  }

  /** A transport whose completion and progress the test drives by hand. */
  function deferredTransport() {
    let resolve!: (value: { files: ReturnType<typeof uploadedInfo>[] }) => void;
    let reject!: (err: Error) => void;
    let emit: ((percent: number) => void) | undefined;
    uploadWithProgressMock.mockImplementation(
      (
        _threadId: string,
        _files: File[],
        options?: { onProgress?: (p: number) => void },
      ) => {
        emit = options?.onProgress;
        return new Promise((res, rej) => {
          resolve = res;
          reject = rej;
        });
      },
    );
    return {
      progress: (percent: number) => act(() => emit?.(percent)),
      finish: (files: File[]) =>
        act(async () => resolve({ files: files.map(uploadedInfo) })),
      fail: async (message: string) => {
        await act(async () => {
          reject(new Error(message));
        });
      },
    };
  }

  it("starts uploading as soon as an image is attached", async () => {
    renderWithProviders(<ChatInputBox mode="react" threadId="thread-1" />);
    const image = pasteImage();

    await waitFor(() =>
      expect(uploadWithProgressMock).toHaveBeenCalledTimes(1),
    );
    expect(uploadWithProgressMock.mock.calls[0][0]).toBe("thread-1");
    expect(uploadWithProgressMock.mock.calls[0][1]).toEqual([image]);
  });

  it("shows byte progress on the chip while the upload runs", async () => {
    const transport = deferredTransport();
    renderWithProviders(<ChatInputBox mode="react" threadId="thread-1" />);
    pasteImage();

    await waitFor(() => expect(uploadWithProgressMock).toHaveBeenCalled());
    transport.progress(42);

    const bar = await screen.findByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("data-upload-status", "uploading");
    expect(screen.getByText("42%")).toBeInTheDocument();
  });

  it("blocks send until the progress bar completes", async () => {
    const transport = deferredTransport();
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox mode="react" threadId="thread-1" onSubmit={onSubmit} />,
    );
    const image = pasteImage();

    await waitFor(() => expect(uploadWithProgressMock).toHaveBeenCalled());
    transport.progress(70);
    expect(sendButton()).toBeDisabled();
    // A disabled button that says nothing looks broken.
    expect(sendButton()).toHaveAttribute(
      "title",
      "Waiting for attachments to finish uploading",
    );
    fireEvent.click(sendButton());
    expect(onSubmit).not.toHaveBeenCalled();

    await transport.finish([image]);
    await waitFor(() => expect(sendButton()).toBeEnabled());
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("carries the server-side upload info into the sent message", async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <ChatInputBox mode="react" threadId="thread-1" onSubmit={onSubmit} />,
    );
    const image = pasteImage("into-chat.png");

    await waitFor(() => expect(sendButton()).toBeEnabled());
    fireEvent.click(sendButton());

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          images: [image],
          uploaded: [uploadedInfo(image)],
        }),
      ),
    );
  });

  it("marks a failed attachment and keeps send blocked", async () => {
    const transport = deferredTransport();
    renderWithProviders(<ChatInputBox mode="react" threadId="thread-1" />);
    const image = pasteImage("broken.png");

    await waitFor(() => expect(uploadWithProgressMock).toHaveBeenCalled());
    await transport.fail("disk full");

    const bar = await screen.findByRole("progressbar");
    expect(bar).toHaveAttribute("data-upload-status", "error");
    expect(sendButton()).toBeDisabled();
    expect(screen.getByLabelText("Retry upload")).toBeInTheDocument();
    expect(bar).toHaveAttribute("aria-label", "Upload failed");
    expect(image.name).toBe("broken.png");
  });

  it("retries a failed upload from the chip", async () => {
    const transport = deferredTransport();
    renderWithProviders(<ChatInputBox mode="react" threadId="thread-1" />);
    const image = pasteImage("retry-me.png");

    await waitFor(() => expect(uploadWithProgressMock).toHaveBeenCalled());
    await transport.fail("network down");
    const retry = await screen.findByLabelText("Retry upload");
    expect(image.name).toBe("retry-me.png");

    uploadWithProgressMock.mockResolvedValue({ files: [uploadedInfo(image)] });
    fireEvent.click(retry);

    await waitFor(() => expect(sendButton()).toBeEnabled());
    expect(uploadWithProgressMock).toHaveBeenCalledTimes(2);
  });

  it("stops tracking an attachment that is removed mid-upload", async () => {
    deferredTransport();
    // Own thread id: the composer persists drafts per thread, and a leaked
    // draft from another test would keep Send enabled for the wrong reason.
    renderWithProviders(<ChatInputBox mode="react" threadId="thread-remove" />);
    pasteImage("discarded.png");

    await waitFor(() => expect(uploadWithProgressMock).toHaveBeenCalled());
    fireEvent.click(screen.getByTitle("Remove"));

    // Removing the chip must also clear its upload, or an abandoned transfer
    // would keep the send button disabled forever.
    await waitFor(() => expect(sendButton()).toBeDisabled());
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
