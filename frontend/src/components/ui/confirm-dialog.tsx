import { useCallback, useRef, useState } from "react";
import { Trash2Icon } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Promise-based replacement for ``window.confirm``. Renders the app's
 * design-language dialog instead of the blocking native chrome, so
 * destructive-action confirmations look and behave identically across
 * panels.
 *
 * Usage:
 * ```tsx
 * const { confirm, confirmDialog } = useConfirmDialog();
 * const handleDelete = async () => {
 *   if (!(await confirm({ title, description }))) return;
 *   doDelete();
 * };
 * return (<>{rows}{confirmDialog}</>);
 * ```
 */
export type ConfirmDialogOptions = {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Pending label is the confirm label; the spinner communicates progress. */
  pending?: boolean;
  /** Non-destructive confirms (e.g. clearing site data) use a plain primary
   *  button instead of the red destructive treatment. */
  destructive?: boolean;
};

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  pending = false,
  destructive = true,
  onOpenChange,
  onConfirm,
}: ConfirmDialogOptions & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[360px]"
      >
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-[15px]">{title}</DialogTitle>
          {description ? (
            <DialogDescription className="text-caption leading-5">
              {description}
            </DialogDescription>
          ) : null}
        </DialogHeader>
        <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            disabled={pending}
            onClick={() => onOpenChange(false)}
            className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-background px-3 text-caption font-medium text-foreground/80 transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-60"
          >
            {cancelLabel ?? t.common.cancel}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onConfirm}
            className={
              destructive
                ? "inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-destructive/25 bg-destructive/[0.07] px-3 text-caption font-medium text-destructive transition-colors hover:border-destructive/35 hover:bg-destructive/[0.11] disabled:pointer-events-none disabled:opacity-60"
                : "inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border bg-foreground px-3 text-caption font-medium text-background transition-colors hover:bg-foreground/90 disabled:pointer-events-none disabled:opacity-60"
            }
          >
            {pending ? (
              <span className="size-3 animate-spin rounded-full border border-current border-t-transparent" />
            ) : destructive ? (
              <Trash2Icon className="size-3.5" />
            ) : null}
            {confirmLabel ?? t.common.delete}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function useConfirmDialog() {
  const [options, setOptions] = useState<ConfirmDialogOptions | null>(null);
  const resolverRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((next: ConfirmDialogOptions) => {
    resolverRef.current?.(false);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
      setOptions(next);
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    resolverRef.current?.(value);
    resolverRef.current = null;
    setOptions(null);
  }, []);

  const confirmDialog = (
    <ConfirmDialog
      open={options !== null}
      title={options?.title ?? ""}
      description={options?.description}
      confirmLabel={options?.confirmLabel}
      cancelLabel={options?.cancelLabel}
      pending={options?.pending ?? false}
      destructive={options?.destructive ?? true}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) settle(false);
      }}
      onConfirm={() => settle(true)}
    />
  );

  return { confirm, confirmDialog };
}
