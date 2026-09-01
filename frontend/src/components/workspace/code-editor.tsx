import type { Extension } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";
import { CheckIcon, SaveIcon, RotateCcwIcon, SearchIcon } from "lucide-react";
import { useTheme } from "next-themes";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  customDarkTheme,
  customLightTheme,
  loadCodeMirrorExtensions,
} from "./codemirror-config";
import { useThread } from "./messages/context";

const LazyCodeMirror = lazy(() => import("./codemirror-host"));

async function saveFile(
  path: string,
  content: string,
  expectedContent: string,
  threadId?: string,
  workspacePath?: string,
) {
  const baseURL = getBackendBaseURL();
  const expectedSha256 = await sha256Text(expectedContent);
  const response = await fetch(`${baseURL}/api/fs/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path,
      content,
      expected_sha256: expectedSha256,
      thread_id: threadId,
      workspace_path: workspacePath,
    }),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Save failed" }));
    const detail = error.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : "Save failed",
    );
  }
  return response.json();
}

async function sha256Text(content: string): Promise<string | undefined> {
  if (!globalThis.crypto?.subtle) return undefined;
  const bytes = new TextEncoder().encode(content);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

async function runDiagnostics(path: string, workspacePath?: string) {
  const baseURL = getBackendBaseURL();
  const response = await fetch(`${baseURL}/api/lsp/diagnostics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, workspace: workspacePath }),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Diagnostics failed" }));
    throw new Error(error.detail ?? "Diagnostics failed");
  }
  return response.json();
}

async function runReferences(
  path: string,
  symbol: string,
  workspacePath?: string,
) {
  const baseURL = getBackendBaseURL();
  const response = await fetch(`${baseURL}/api/lsp/references`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, symbol, workspace: workspacePath }),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "References failed" }));
    throw new Error(error.detail ?? "References failed");
  }
  return response.json();
}

async function runDefinition(
  path: string,
  symbol: string,
  workspacePath?: string,
) {
  const baseURL = getBackendBaseURL();
  const response = await fetch(`${baseURL}/api/lsp/definition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, symbol, workspace: workspacePath }),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Definition failed" }));
    throw new Error(error.detail ?? "Definition failed");
  }
  return response.json();
}

function extractSymbolAtSelection(
  text: string,
  from: number,
  to: number,
): string | null {
  const ident = /^[A-Za-z_][A-Za-z0-9_]*$/;
  if (to > from) {
    const selected = text.slice(from, to).trim();
    return ident.test(selected) ? selected : null;
  }
  let start = from;
  let end = from;
  while (start > 0 && /[A-Za-z0-9_]/.test(text[start - 1]!)) start -= 1;
  while (end < text.length && /[A-Za-z0-9_]/.test(text[end]!)) end += 1;
  const word = text.slice(start, end).trim();
  return ident.test(word) ? word : null;
}

export function CodeEditor({
  className,
  placeholder,
  value: initialValue,
  readonly,
  disabled,
  autoFocus,
  settings,
  filePath,
  workspacePath,
  threadId,
  onSave,
  onOpenLocation,
}: {
  className?: string;
  placeholder?: string;
  value: string;
  readonly?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  settings?: unknown;
  filePath?: string;
  workspacePath?: string;
  threadId?: string;
  onSave?: (content: string) => void;
  onOpenLocation?: (path: string, line?: number) => void;
}) {
  const { t } = useI18n();
  const {
    thread: { isLoading },
  } = useThread();
  const { resolvedTheme } = useTheme();
  const isEditable = !readonly && !disabled && !!filePath;
  const [localValue, setLocalValue] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isSaved, setIsSaved] = useState(false);
  const [isDiagnosing, setIsDiagnosing] = useState(false);
  const [diagnosticCount, setDiagnosticCount] = useState<number | null>(null);
  const [currentSymbol, setCurrentSymbol] = useState<string | null>(null);
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const previousFilePathRef = useRef(filePath);
  const editorViewRef = useRef<EditorView | null>(null);

  const currentValue = localValue ?? initialValue;
  const isDirty = localValue !== null && localValue !== initialValue;
  const fallbackTextareaClassName = cn(
    "h-full overflow-auto font-mono [&_.cm-editor]:h-full [&_.cm-focused]:outline-none!",
    "resize-none border-none p-4! [&_.cm-line]:px-2! [&_.cm-line]:py-0!",
  );

  useEffect(() => {
    let cancelled = false;
    void loadCodeMirrorExtensions(filePath).then(async (loaded) => {
      if (cancelled) return;
      if (!readonly && filePath) {
        try {
          const { inlineCompletion } = await import("./inline-completion");
          loaded = [...loaded, inlineCompletion(filePath)];
        } catch (e) {
          swallow(e);
        }
      }
      setExtensions(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, [filePath, readonly]);

  useEffect(() => {
    if (previousFilePathRef.current !== filePath) {
      previousFilePathRef.current = filePath;
      setLocalValue(null);
      setIsSaved(false);
    }
  }, [filePath]);

  useEffect(() => {
    if (localValue !== null && localValue === initialValue) {
      setLocalValue(null);
    }
  }, [initialValue, localValue]);

  const handleChange = useCallback(
    (val: string) => {
      if (isEditable) {
        setLocalValue(val);
        setIsSaved(false);
      }
    },
    [isEditable],
  );

  const handleSave = useCallback(async () => {
    if (!filePath || localValue === null) return;
    setIsSaving(true);
    try {
      await saveFile(
        filePath,
        localValue,
        initialValue,
        threadId,
        workspacePath,
      );
      setIsSaved(true);
      onSave?.(localValue);
      toast.success(t.codeEditor.fileSaved);
      setTimeout(() => setIsSaved(false), 2000);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.codeEditor.fileSaveFailed,
      );
    } finally {
      setIsSaving(false);
    }
  }, [
    filePath,
    initialValue,
    localValue,
    onSave,
    threadId,
    workspacePath,
    t.codeEditor,
  ]);

  const handleReset = useCallback(() => {
    setLocalValue(null);
    setIsSaved(false);
  }, []);

  const handleDiagnose = useCallback(async () => {
    if (!filePath) return;
    setIsDiagnosing(true);
    try {
      const result = await runDiagnostics(filePath, workspacePath);
      const count = Array.isArray(result.errors) ? result.errors.length : 0;
      setDiagnosticCount(count);
      if (count === 0) {
        toast.success(t.codeEditor.diagnosticsClean);
      } else {
        const firstFew = result.errors
          .slice(0, 3)
          .map(
            (e: { line?: number; message?: string }) =>
              `L${e.line ?? 0}: ${e.message ?? ""}`,
          )
          .join("\n");
        toast.error(`${count} ${t.codeEditor.diagnosticsIssues}`, {
          description: firstFew,
        });
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.codeEditor.diagnosticsFailed,
      );
    } finally {
      setIsDiagnosing(false);
    }
  }, [filePath, workspacePath, t.codeEditor]);

  const handleGoToDefinition = useCallback(async () => {
    if (!filePath || !currentSymbol) return;
    try {
      const result = await runDefinition(
        filePath,
        currentSymbol,
        workspacePath,
      );
      if (result.found) {
        toast.success(
          t.codeEditor.definitionFound(currentSymbol, result.file, result.line),
          {
            description: result.context,
          },
        );
        if (onOpenLocation && result.file) {
          onOpenLocation(result.file, result.line);
        }
      } else {
        toast.info(t.codeEditor.definitionNotFound(currentSymbol));
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.codeEditor.definitionLookupFailed,
      );
    }
  }, [filePath, currentSymbol, workspacePath, onOpenLocation, t.codeEditor]);

  const handleFindReferences = useCallback(async () => {
    if (!filePath || !currentSymbol) return;
    try {
      const result = await runReferences(
        filePath,
        currentSymbol,
        workspacePath,
      );
      if (result.found && result.count > 0) {
        const firstFew = (
          result.references as { file: string; line: number; context: string }[]
        )
          .slice(0, 5)
          .map((r) => `L${r.line}: ${r.context}`)
          .join("\n");
        toast.success(
          t.codeEditor.referencesFound(result.count, currentSymbol),
          {
            description: firstFew,
          },
        );
      } else {
        toast.info(t.codeEditor.referencesNotFound(currentSymbol));
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.codeEditor.referencesLookupFailed,
      );
    }
  }, [filePath, currentSymbol, workspacePath, t.codeEditor]);

  const handleEditorUpdate = useCallback(
    (viewUpdate: {
      state: {
        doc: { toString: () => string };
        selection: { main: { from: number; to: number } };
      };
    }) => {
      const { from, to } = viewUpdate.state.selection.main;
      const text = viewUpdate.state.doc.toString();
      const sym = extractSymbolAtSelection(text, from, to);
      setCurrentSymbol(sym);
    },
    [],
  );

  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ filePath?: string; line?: number }>;
      if (!editorViewRef.current || !filePath) return;
      if (ce.detail?.filePath && ce.detail.filePath !== filePath) return;
      const line = ce.detail?.line;
      if (!line || line < 1) return;
      const view = editorViewRef.current;
      const lineInfo = view.state.doc.line(line);
      view.dispatch({
        selection: { anchor: lineInfo.from },
        scrollIntoView: true,
      });
      view.focus();
    };
    window.addEventListener("echo:editor-go-to-line", handler);
    return () =>
      window.removeEventListener("echo:editor-go-to-line", handler);
  }, [filePath]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s" && isEditable && isDirty) {
        e.preventDefault();
        void handleSave();
      }
    },
    [isEditable, isDirty, handleSave],
  );

  return (
    <div
      className={cn(
        "relative flex cursor-text flex-col overflow-hidden rounded-lg",
        className,
      )}
      onKeyDownCapture={handleKeyDown}
    >
      {filePath && (
        <div className="absolute top-2 right-2 z-10 flex items-center gap-1">
          {currentSymbol && (
            <>
              <Button
                variant="secondary"
                size="sm"
                className="h-7 gap-1 px-2 text-xs shadow-[var(--shadow-xs)]"
                onClick={() => void handleGoToDefinition()}
                title={t.codeEditor.goToDefinitionTitle(currentSymbol)}
              >
                {t.codeEditor.definitionButton}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-7 gap-1 px-2 text-xs shadow-[var(--shadow-xs)]"
                onClick={() => void handleFindReferences()}
                title={t.codeEditor.findReferencesTitle(currentSymbol)}
              >
                {t.codeEditor.referencesButton}
              </Button>
            </>
          )}
          <Button
            variant="secondary"
            size="sm"
            className="h-7 gap-1 px-2 text-xs shadow-[var(--shadow-xs)]"
            disabled={isDiagnosing}
            onClick={() => void handleDiagnose()}
            title={t.codeEditor.diagnose}
          >
            <SearchIcon className="size-3" />
            {diagnosticCount !== null && diagnosticCount > 0
              ? `${diagnosticCount}`
              : t.codeEditor.diagnose}
          </Button>
        </div>
      )}
      {isEditable && isDirty && (
        <div className="bg-muted/50 flex items-center justify-between border-b px-3 py-1.5">
          <span className="text-muted-foreground text-xs">
            {t.codeEditor.modified}
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-xs"
              onClick={handleReset}
            >
              <RotateCcwIcon className="size-3" />
              {t.codeEditor.reset}
            </Button>
            <Button
              variant="default"
              size="sm"
              className="h-6 gap-1 px-2 text-xs"
              disabled={isSaving}
              onClick={() => void handleSave()}
            >
              {isSaved ? (
                <CheckIcon className="size-3" />
              ) : (
                <SaveIcon className="size-3" />
              )}
              {isSaving
                ? t.codeEditor.saving
                : isSaved
                  ? t.codeEditor.saved
                  : t.codeEditor.save}
            </Button>
          </div>
        </div>
      )}
      {isLoading ? (
        <Textarea
          className={fallbackTextareaClassName}
          readOnly
          value={currentValue}
        />
      ) : (
        <Suspense
          fallback={
            <Textarea
              className={fallbackTextareaClassName}
              readOnly
              value={currentValue}
            />
          }
        >
          <LazyCodeMirror
            readOnly={readonly ?? disabled}
            placeholder={placeholder}
            className={cn(
              "h-full overflow-auto font-mono [&_.cm-editor]:h-full [&_.cm-focused]:outline-none!",
              "px-2 py-0! [&_.cm-line]:px-2! [&_.cm-line]:py-0!",
            )}
            theme={
              resolvedTheme === "dark" ? customDarkTheme : customLightTheme
            }
            extensions={extensions}
            basicSetup={{
              foldGutter:
                (settings as { foldGutter?: boolean })?.foldGutter ?? false,
              highlightActiveLine: isEditable,
              highlightActiveLineGutter: isEditable,
              lineNumbers:
                (settings as { lineNumbers?: boolean })?.lineNumbers ?? false,
            }}
            autoFocus={autoFocus}
            value={currentValue}
            onChange={isEditable ? handleChange : undefined}
            onUpdate={handleEditorUpdate}
            onCreateEditor={(view) => {
              editorViewRef.current = view;
            }}
          />
        </Suspense>
      )}
    </div>
  );
}
