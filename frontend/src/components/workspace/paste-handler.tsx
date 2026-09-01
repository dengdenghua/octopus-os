import { useEffect, useCallback } from "react";

interface PasteHandlerProps {
  onImagePaste: (file: File) => void;
  enabled?: boolean;
}

export function usePasteHandler({
  onImagePaste,
  enabled = true,
}: PasteHandlerProps) {
  const handlePaste = useCallback(
    (e: ClipboardEvent) => {
      if (!enabled) return;

      const items = e.clipboardData?.items;
      if (!items) return;

      for (const item of Array.from(items)) {
        if (item.type.startsWith("image/")) {
          e.preventDefault();
          const file = item.getAsFile();
          if (file) {
            // Create a named file for better UX
            const ext = item.type.split("/")[1] || "png";
            const timestamp = new Date()
              .toISOString()
              .replace(/[:.]/g, "-")
              .slice(0, 19);
            const namedFile = new File(
              [file],
              `screenshot-${timestamp}.${ext}`,
              {
                type: file.type,
              },
            );
            onImagePaste(namedFile);
          }
        }
      }
    },
    [onImagePaste, enabled],
  );

  useEffect(() => {
    document.addEventListener("paste", handlePaste);
    return () => document.removeEventListener("paste", handlePaste);
  }, [handlePaste]);
}

/**
 * Visual indicator shown briefly when an image is pasted
 */
export function PasteIndicator({ fileName }: { fileName: string | null }) {
  if (!fileName) return null;

  return (
    <div className="bg-primary/10 text-primary animate-in fade-in slide-in-from-bottom-2 flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs">
      <span>Pasted: {fileName}</span>
    </div>
  );
}
