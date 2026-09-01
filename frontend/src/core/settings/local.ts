import type { AgentThreadContext, ReasoningEffort } from "../threads";
import { emitSettingsChanged, eventBus } from "../events";

export const DEFAULT_LOCAL_SETTINGS: LocalSettings = {
  notification: {
    enabled: true,
  },
  context: {
    // ``auto`` is the shared sentinel for "let the backend ModelRouter pick"
    // (see model-picker.tsx and realtime_turn_lifecycle.py, which skips the
    // model_name override for auto/default). Hardcoding a concrete model here
    // pinned every fresh install to one vendor: a deployment whose provider
    // only serves other model names rejected the turn with an HTTP 400 before
    // the user ever opened the picker.
    model_name: "auto",
    mode: "react",
    permission_mode: "default",
    execution_environment: "sandbox",
    reasoning_effort: undefined,
    // Sandbox default: network denied (only model inference reachable).
    // Users opt in from the sandbox settings page ("network access") to
    // the "common domains" or "full" tiers.
    network_access: "deny",
  },
  layout: {
    sidebar_collapsed: false,
  },
  browser_panel: {
    open: false,
    url: "",
    mode: "mobile",
  },
  display: {
    // Chat message font size. ``medium`` matches the historical default so
    // existing users see no visual change until they opt in.
    chat_font_size: "medium",
    // Conversation detail level controls how much intermediate activity is shown.
    // - "high": Show all tool calls, code blocks, reasoning steps (power users)
    // - "medium": Collapse intermediate steps, show final results (default)
    // - "low": Minimal view, hide tool details and code blocks
    conversation_detail_level: "medium",
  },
  session: {
    auto_new_session_hours: 0,
  },
  personal_space: {
    default_folder: "",
    default_mode: "general",
    remember_last_mode: true,
    custom_instructions: "",
  },
};

const LOCAL_SETTINGS_KEY = "echo.local-settings";
const THREAD_MODEL_KEY_PREFIX = "echo.thread-model.";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export interface LocalSettings {
  notification: {
    enabled: boolean;
  };
  context: Omit<
    AgentThreadContext,
    | "thread_id"
    | "is_plan_mode"
    | "thinking_enabled"
    | "subagent_enabled"
    | "model_name"
    | "reasoning_effort"
  > & {
    model_name?: string | undefined;
    mode: "chat" | "code" | "react" | "deep" | "flash" | "thinking" | undefined;
    reasoning_effort?: ReasoningEffort;
    /** Opt-in independent review for high-risk actions (codex-style
     * guardian). When on, high/critical tool calls get a second opinion
     * from guardian_review_model before escalating to the user; failures
     * degrade to the rule engine, never blocking the task. */
    guardian_review_enabled?: boolean | undefined;
    guardian_review_model?: string | undefined;
  };
  layout: {
    sidebar_collapsed: boolean;
  };
  browser_panel: {
    open: boolean;
    url: string;
    mode: "mobile" | "desktop";
  };
  display: {
    chat_font_size: "small" | "medium" | "large";
    conversation_detail_level: "low" | "medium" | "high";
  };
  session: {
    /** Hours of inactivity before a *new* chat session is auto-started when
     * the user sends the next message. `0` disables the behavior. */
    auto_new_session_hours: number;
  };
  personal_space: {
    /** Default folder used by new personal-space tasks. */
    default_folder: string;
    /** Default operating contract for unbound personal-space tasks. */
    default_mode: "general" | "build" | "research";
    /** When enabled, a composer mode pick becomes the next task's default. */
    remember_last_mode: boolean;
    /** User-authored operating preferences appended to personal turns. */
    custom_instructions: string;
  };
}

function mergeLocalSettings(settings?: Partial<LocalSettings>): LocalSettings {
  const context = {
    ...DEFAULT_LOCAL_SETTINGS.context,
    ...settings?.context,
  };
  const persistedMode = (context as { mode?: unknown }).mode;
  if (persistedMode === "chat" || persistedMode === "swarm") {
    context.mode = "react";
  }
  // Legacy hardcoded default: bare ``claude-opus`` was never a deployable
  // model id (real ones are versioned, e.g. claude-opus-4-7-20250805) — it was
  // the old DEFAULT_LOCAL_SETTINGS value that pinned every install to one
  // vendor and made providers serving other names reject the turn. Persisted
  // copies must fall back to ``auto`` (router picks); an explicit versioned
  // pick the user made in the picker is left untouched.
  if ((context as { model_name?: unknown }).model_name === "claude-opus") {
    context.model_name = "auto";
  }
  const rawPersonalMode = settings?.personal_space?.default_mode;
  const personalMode =
    rawPersonalMode === "build" || rawPersonalMode === "research"
      ? rawPersonalMode
      : "general";
  return {
    ...DEFAULT_LOCAL_SETTINGS,
    context,
    layout: {
      ...DEFAULT_LOCAL_SETTINGS.layout,
      ...settings?.layout,
    },
    notification: {
      ...DEFAULT_LOCAL_SETTINGS.notification,
      ...settings?.notification,
    },
    browser_panel: {
      ...DEFAULT_LOCAL_SETTINGS.browser_panel,
      ...settings?.browser_panel,
    },
    display: {
      ...DEFAULT_LOCAL_SETTINGS.display,
      ...settings?.display,
    },
    session: {
      ...DEFAULT_LOCAL_SETTINGS.session,
      ...settings?.session,
    },
    personal_space: {
      ...DEFAULT_LOCAL_SETTINGS.personal_space,
      ...settings?.personal_space,
      default_folder:
        typeof settings?.personal_space?.default_folder === "string"
          ? settings.personal_space.default_folder.trim()
          : "",
      default_mode: personalMode,
      remember_last_mode:
        typeof settings?.personal_space?.remember_last_mode === "boolean"
          ? settings.personal_space.remember_last_mode
          : true,
      custom_instructions:
        typeof settings?.personal_space?.custom_instructions === "string"
          ? settings.personal_space.custom_instructions.slice(0, 2000)
          : "",
    },
  };
}

function getThreadModelStorageKey(threadId: string): string {
  return `${THREAD_MODEL_KEY_PREFIX}${threadId}`;
}

export function getThreadModelName(threadId: string): string | undefined {
  if (!isBrowser()) {
    return undefined;
  }
  return localStorage.getItem(getThreadModelStorageKey(threadId)) ?? undefined;
}

export function saveThreadModelName(
  threadId: string,
  modelName: string | undefined,
) {
  if (!isBrowser()) {
    return;
  }
  const key = getThreadModelStorageKey(threadId);
  if (!modelName) {
    localStorage.removeItem(key);
    return;
  }
  localStorage.setItem(key, modelName);
}

/**
 * Remove per-thread selections that point at a model which no longer exists.
 *
 * Custom models can be deleted from Settings while older threads still carry
 * a model override. Leaving those keys behind makes the picker appear to
 * select a deleted model when the thread is opened again.
 */
export function clearThreadModelReferences(modelName: string): number {
  if (!isBrowser() || !modelName) {
    return 0;
  }

  const keysToRemove: string[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (
      key?.startsWith(THREAD_MODEL_KEY_PREFIX) &&
      localStorage.getItem(key) === modelName
    ) {
      keysToRemove.push(key);
    }
  }
  for (const key of keysToRemove) {
    localStorage.removeItem(key);
  }
  return keysToRemove.length;
}

function applyThreadModelOverride(
  settings: LocalSettings,
  threadId?: string,
): LocalSettings {
  const threadModelName = threadId ? getThreadModelName(threadId) : undefined;
  if (!threadModelName) {
    return settings;
  }
  return {
    ...settings,
    context: {
      ...settings.context,
      model_name: threadModelName,
    },
  };
}

export function getLocalSettings(): LocalSettings {
  if (!isBrowser()) {
    return DEFAULT_LOCAL_SETTINGS;
  }
  const json = localStorage.getItem(LOCAL_SETTINGS_KEY);
  try {
    if (json) {
      const settings = JSON.parse(json) as Partial<LocalSettings>;
      return mergeLocalSettings(settings);
    }
  } catch (e) {
    console.warn(
      "[LocalSettings] Failed to parse stored settings, using defaults.",
      e,
    );
  }
  return DEFAULT_LOCAL_SETTINGS;
}

export function getThreadLocalSettings(threadId: string): LocalSettings {
  return applyThreadModelOverride(getLocalSettings(), threadId);
}

/**
 * Settings change subscription using EventBus.
 * Same-tab useState subscribers rely on this to re-read: the native
 * ``storage`` event only fires on *other* tabs, so without a same-tab
 * broadcast, a settings change made in one component (say the Appearance
 * page) stays invisible to another component already mounted elsewhere
 * (say MarkdownContent), until the page is reloaded.
 */
export function saveLocalSettings(settings: LocalSettings) {
  if (!isBrowser()) {
    return;
  }
  localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(settings));
  emitSettingsChanged();
}

export function subscribeLocalSettings(handler: () => void): () => void {
  if (!isBrowser()) return () => undefined;
  // Both signals: same-tab event bus + cross-tab storage event.
  const unsubscribe = eventBus.on("settings:changed", handler);
  const storageHandler = (e: StorageEvent) => {
    if (e.key === LOCAL_SETTINGS_KEY) handler();
  };
  window.addEventListener("storage", storageHandler);
  return () => {
    unsubscribe();
    window.removeEventListener("storage", storageHandler);
  };
}

export function saveThreadLocalSettings(
  threadId: string,
  settings: LocalSettings,
) {
  // Persist the per-thread override before broadcasting the global settings
  // change. Subscribers synchronously re-read both stores when
  // saveLocalSettings emits; the old order made them observe the previous
  // thread model and immediately roll the picker back.
  saveThreadModelName(threadId, settings.context.model_name);
  saveLocalSettings(settings);
}
