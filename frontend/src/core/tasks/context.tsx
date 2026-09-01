import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import type { Subtask } from "./types";

// Compare the scalar fields that updateSubtask can write. `messages` is
// handled separately by the caller (identity changes there are explicit), so
// here we only bail out when every other field is unchanged.
function subtaskShallowEqual(
  a: Subtask | undefined,
  b: Subtask,
): boolean {
  if (!a) return false;
  const keys = Object.keys(b) as (keyof Subtask)[];
  for (const key of keys) {
    if (key === "messages") continue;
    if (a[key] !== b[key]) return false;
  }
  return true;
}

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: (
    tasks:
      | Record<string, Subtask>
      | ((prev: Record<string, Subtask>) => Record<string, Subtask>),
  ) => void;
}

const NOOP_CONTEXT: SubtaskContextValue = {
  tasks: {},
  setTasks: () => {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  },
};

export const SubtaskContext = createContext<SubtaskContextValue>(NOOP_CONTEXT);

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  const value = useMemo(() => ({ tasks, setTasks }), [tasks, setTasks]);
  return (
    <SubtaskContext.Provider value={value}>{children}</SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  return useContext(SubtaskContext);
}

export function useSubtask(id: string) {
  const { tasks } = useSubtaskContext();
  return tasks[id];
}

export function useUpdateSubtask() {
  const { setTasks } = useSubtaskContext();
  const updateSubtask = useCallback(
    (task: Partial<Subtask> & { id: string }) => {
      setTasks((prevTasks) => {
        const existing = prevTasks[task.id];
        const merged = { ...(existing ?? {}), ...task } as Subtask;

        let messagesChanged = false;
        if (task.latestMessage) {
          const prev = existing?.messages ?? [];
          const isDup = prev.some(
            (m) => m.id && m.id === task.latestMessage!.id,
          );
          if (!isDup) {
            merged.messages = [...prev, task.latestMessage];
            messagesChanged = true;
          }
        }

        // Streaming replays this reducer on every token. Returning the
        // previous object identity when nothing actually changed keeps the
        // SubtaskContext value stable and avoids re-rendering every
        // SubtaskCard consumer on each streamed chunk.
        if (!messagesChanged && subtaskShallowEqual(existing, merged)) {
          return prevTasks;
        }

        return { ...prevTasks, [task.id]: merged };
      });
    },
    [setTasks],
  );
  return updateSubtask;
}
