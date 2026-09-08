import { useCallback, useEffect, useLayoutEffect, useState } from "react";

import { currentActorId } from "../auth/api";
import {
  getLocalSettings,
  getThreadLocalSettings,
  saveLocalSettings,
  saveThreadLocalSettings,
  subscribeLocalSettings,
  type LocalSettings,
} from "./local";

type LocalSettingsSetter = (
  key: keyof LocalSettings,
  value: Partial<LocalSettings[keyof LocalSettings]>,
) => void;

function useSettingsState(
  getSettings: () => LocalSettings,
  saveSettings: (settings: LocalSettings) => void,
  scopeKey: string,
): [LocalSettings, LocalSettingsSetter] {
  // Lazy initializer reads localStorage on first render so children that
  // consume settings during their own initial render (e.g. MarkdownContent
  // wrapped in React.memo(MessageResponse)) pick up the persisted value
  // right away. Using DEFAULT_LOCAL_SETTINGS then patching in a layout
  // effect meant the very first render leaked the default into any memo
  // downstream, and later effect-driven updates were invisible because
  // memo didn't see its className prop change.
  const [state, setState] = useState<LocalSettings>(getSettings);

  const [mounted, setMounted] = useState(false);
  useLayoutEffect(() => {
    setState(getSettings());
    setMounted(true);
  }, [getSettings, scopeKey]);

  // Cross-component subscription: when *any* component calls
  // saveLocalSettings(), every mounted useLocalSettings / useThreadSettings
  // refreshes from storage. Without this, changing chat_font_size in the
  // Appearance page only updated its own useState — MarkdownContent,
  // MessageList, etc. kept rendering with the old value until a reload.
  useEffect(() => {
    return subscribeLocalSettings(() => {
      setState(getSettings());
    });
  }, [getSettings, scopeKey]);

  const setter = useCallback<LocalSettingsSetter>(
    (key, value) => {
      if (!mounted) return;
      // Merge against the latest shared store, not this window's render
      // snapshot. Commit through this callback's thread identity before a
      // navigation can replace it; deferred effects could save into the next
      // thread and overwrite another window's intervening setting changes.
      const latest = getSettings();
      const next: LocalSettings = {
        ...latest,
        [key]: { ...latest[key], ...value },
      };
      saveSettings(next);
      setState(getSettings());
    },
    [mounted, getSettings, saveSettings],
  );

  return [state, setter];
}

export function useLocalSettings(): [LocalSettings, LocalSettingsSetter] {
  const actor = currentActorId();
  const saveActorSettings = useCallback(
    (settings: LocalSettings) => {
      // A queued click from the previous session must never commit into the
      // newly active account after an actor switch.
      if (currentActorId() !== actor) return;
      saveLocalSettings(settings);
    },
    [actor],
  );
  return useSettingsState(getLocalSettings, saveActorSettings, actor);
}

export function useThreadSettings(
  threadId: string,
): [LocalSettings, LocalSettingsSetter] {
  const actor = currentActorId();
  return useSettingsState(
    useCallback(() => getThreadLocalSettings(threadId), [threadId]),
    useCallback(
      (settings: LocalSettings) => {
        if (currentActorId() !== actor) return;
        saveThreadLocalSettings(threadId, settings);
      },
      [actor, threadId],
    ),
    `${actor}:${threadId}`,
  );
}
