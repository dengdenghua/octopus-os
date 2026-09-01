import { useCallback, useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";

export type PromptDialogOptions = {
  title: string;
  label?: string;
  defaultValue?: string;
  placeholder?: string;
  submitLabel?: string;
  cancelLabel?: string;
};

export function PromptDialog({
  open,
  title,
  label,
  defaultValue,
  placeholder,
  submitLabel,
  cancelLabel,
  onOpenChange,
  onSubmit,
}: PromptDialogOptions & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (value: string) => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(defaultValue ?? "");

  useEffect(() => {
    if (open) setValue(defaultValue ?? "");
  }, [open, defaultValue]);

  const trimmed = value.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[360px]"
      >
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-[15px]">{title}</DialogTitle>
          {label ? (
            <DialogDescription className="text-caption leading-5">
              {label}
            </DialogDescription>
          ) : null}
        </DialogHeader>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!trimmed) return;
            onSubmit(trimmed);
          }}
        >
          <Input
            autoFocus
            value={value}
            placeholder={placeholder}
            onChange={(event) => setValue(event.target.value)}
            onFocus={(event) => event.target.select()}
            className="h-9 text-[13px]"
          />
          <DialogFooter className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-background px-3 text-caption font-medium text-foreground/80 transition-colors hover:bg-muted"
            >
              {cancelLabel ?? t.common.cancel}
            </button>
            <button
              type="submit"
              disabled={!trimmed}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border bg-foreground px-3 text-caption font-medium text-background transition-colors hover:bg-foreground/90 disabled:pointer-events-none disabled:opacity-60"
            >
              {submitLabel ?? t.common.confirm}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function usePromptDialog() {
  const [options, setOptions] = useState<PromptDialogOptions | null>(null);
  const resolverRef = useRef<((value: string | null) => void) | null>(null);

  const prompt = useCallback((next: PromptDialogOptions) => {
    resolverRef.current?.(null);
    return new Promise<string | null>((resolve) => {
      resolverRef.current = resolve;
      setOptions(next);
    });
  }, []);

  const settle = useCallback((value: string | null) => {
    resolverRef.current?.(value);
    resolverRef.current = null;
    setOptions(null);
  }, []);

  const promptDialog = (
    <PromptDialog
      open={options !== null}
      title={options?.title ?? ""}
      label={options?.label}
      defaultValue={options?.defaultValue}
      placeholder={options?.placeholder}
      submitLabel={options?.submitLabel}
      cancelLabel={options?.cancelLabel}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) settle(null);
      }}
      onSubmit={(value) => settle(value)}
    />
  );

  return { prompt, promptDialog };
}
