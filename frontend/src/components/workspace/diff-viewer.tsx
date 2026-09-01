import { unifiedMergeView } from "@codemirror/merge";
import type { Extension } from "@codemirror/state";
import { useTheme } from "next-themes";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import {
  customDarkTheme,
  customLightTheme,
  loadCodeMirrorExtensions,
} from "./codemirror-config";

const LazyCodeMirror = lazy(() => import("./codemirror-host"));

export function DiffViewer({
  className,
  oldValue,
  newValue,
}: {
  className?: string;
  oldValue: string;
  newValue: string;
}) {
  const { resolvedTheme } = useTheme();
  const [languageExtensions, setLanguageExtensions] = useState<Extension[]>([]);

  useEffect(() => {
    let cancelled = false;
    void loadCodeMirrorExtensions().then((loaded) => {
      if (!cancelled) {
        setLanguageExtensions(loaded);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const extensions = useMemo(() => {
    return [
      ...languageExtensions,
      unifiedMergeView({
        original: oldValue,
        mergeControls: true,
        highlightChanges: true,
        gutter: true,
      }),
    ];
  }, [languageExtensions, oldValue]);

  return (
    <div
      className={cn(
        "flex cursor-text flex-col overflow-hidden rounded-lg",
        className,
      )}
    >
      <Suspense
        fallback={
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            Loading diff...
          </div>
        }
      >
        <LazyCodeMirror
          className={cn(
            "h-full overflow-auto font-mono [&_.cm-editor]:h-full [&_.cm-focused]:outline-none!",
            "px-2 py-0! [&_.cm-line]:px-2! [&_.cm-line]:py-0!",
          )}
          theme={resolvedTheme === "dark" ? customDarkTheme : customLightTheme}
          extensions={extensions}
          basicSetup={{
            foldGutter: false,
            highlightActiveLine: false,
            highlightActiveLineGutter: false,
            lineNumbers: true,
          }}
          value={newValue}
        />
      </Suspense>
    </div>
  );
}
