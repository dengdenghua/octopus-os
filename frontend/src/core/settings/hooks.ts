import { useCallback, useEffect, useLayoutEffect, useState } from "react";

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
  }, [getSettings]);

  // Cross-component subscription: when *any* component calls
  // saveLocalSettings(), every mounted useLocalSettings / useThreadSettings
  // refreshes from storage. Without this, changing chat_font_size in the
  // Appearance page only updated its own useState — MarkdownContent,
  // MessageList, etc. kept rendering with the old value until a reload.
  useEffect(() => {
    return subscribeLocalSettings(() => {
      setState(getSettings());
    });
  }, [getSettings]);

  const [pendingSave, setPendingSave] = useState<LocalSettings | null>(null);

  useEffect(() => {
    if (pendingSave !== null) {
      saveSettings(pendingSave);
      setPendingSave(null);
    }
  }, [pendingSave, saveSettings]);

  const setter = useCallback<LocalSettingsSetter>(
    (key, value) => {
      if (!mounted) return;
      setState((prev) => {
        const newState: LocalSettings = {
          ...prev,
          [key]: {
            ...prev[key],
            ...value,
          },
        };
        setPendingSave(newState);
        return newState;
      });
    },
    [mounted, setPendingSave],
  );

  return [state, setter];
}

export function useLocalSettings(): [LocalSettings, LocalSettingsSetter] {
  return useSettingsState(getLocalSettings, saveLocalSettings);
}

export function useThreadSettings(
  threadId: string,
): [LocalSettings, LocalSettingsSetter] {
  return useSettingsState(
    useCallback(() => getThreadLocalSettings(threadId), [threadId]),
    useCallback(
      (settings: LocalSettings) => saveThreadLocalSettings(threadId, settings),
      [threadId],
    ),
  );
}
