import { useState, useRef, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";
import { SparklesIcon, XIcon, Loader2Icon } from "lucide-react";

interface SelectionEditorProps {
  onSubmit: (selection: string, instruction: string) => void;
  isLoading?: boolean;
  className?: string;
}

export function SelectionEditor({
  onSubmit,
  isLoading,
  className,
}: SelectionEditorProps) {
  const { t } = useI18n();
  const [isVisible, setIsVisible] = useState(false);
  const [selection, setSelection] = useState("");
  const [instruction, setInstruction] = useState("");
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Handle text selection
  const handleSelectionChange = useCallback(() => {
    const selectedText = window.getSelection()?.toString() || "";

    if (selectedText.length > 0) {
      const selectionObj = window.getSelection();
      if (selectionObj && selectionObj.rangeCount > 0) {
        const range = selectionObj.getRangeAt(0);
        const rect = range.getBoundingClientRect();

        // Position the editor below the selection
        setPosition({
          x: rect.left + rect.width / 2,
          y: rect.bottom + 8,
        });
        setSelection(selectedText);
        setIsVisible(true);
      }
    } else {
      // Don't hide immediately to allow clicking the editor
      setTimeout(() => {
        if (!containerRef.current?.matches(":hover")) {
          setIsVisible(false);
          setInstruction("");
        }
      }, 200);
    }
  }, []);

  useEffect(() => {
    document.addEventListener("selectionchange", handleSelectionChange);
    return () =>
      document.removeEventListener("selectionchange", handleSelectionChange);
  }, [handleSelectionChange]);

  useEffect(() => {
    if (isVisible && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isVisible]);

  const handleSubmit = useCallback(() => {
    if (instruction.trim() && selection) {
      onSubmit(selection, instruction.trim());
      setInstruction("");
      setIsVisible(false);
      window.getSelection()?.removeAllRanges();
    }
  }, [instruction, selection, onSubmit]);

  const handleClose = useCallback(() => {
    setIsVisible(false);
    setInstruction("");
    window.getSelection()?.removeAllRanges();
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
      if (e.key === "Escape") {
        handleClose();
      }
    },
    [handleSubmit, handleClose],
  );

  if (!isVisible) return null;

  return (
    <div
      ref={containerRef}
      className={cn(
        "fixed z-50 animate-in fade-in zoom-in-95 duration-base",
        className,
      )}
      style={{
        left: position.x,
        top: position.y,
        transform: "translateX(-50%)",
      }}
    >
      <div className="flex items-center gap-2 rounded-lg border border-border-default bg-popover shadow-[var(--shadow-md)] px-3 py-2 min-w-[280px]">
        <SparklesIcon className="size-4 text-chart-1 shrink-0" />
        <input
          ref={inputRef}
          type="text"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t.selectionEditor.placeholder}
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60 min-w-0"
          disabled={isLoading}
        />
        {isLoading ? (
          <Loader2Icon className="size-4 animate-spin text-chart-1 shrink-0" />
        ) : (
          <button
            onClick={handleClose}
            className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <XIcon className="size-3.5" />
          </button>
        )}
      </div>
      {/* Arrow */}
      <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-popover border-l border-t border-border-default rotate-45" />
    </div>
  );
}

// Hook to use selection editor
export function useSelectionEditor() {
  const [selectedText, setSelectedText] = useState("");
  const [instruction, setInstruction] = useState("");

  const handleSelection = useCallback((text: string, instr: string) => {
    setSelectedText(text);
    setInstruction(instr);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedText("");
    setInstruction("");
  }, []);

  return {
    selectedText,
    instruction,
    handleSelection,
    clearSelection,
    hasSelection: !!selectedText && !!instruction,
  };
}
