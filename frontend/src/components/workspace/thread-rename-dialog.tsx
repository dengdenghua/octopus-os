import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { isIMEComposing } from "@/lib/ime";

export function ThreadRenameDialog({
  open,
  value,
  onChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  value: string;
  onChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const { t } = useI18n();

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="w-[min(360px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[360px]"
      >
        <DialogHeader className="gap-1 text-left">
          <DialogTitle className="text-base">{t.common.rename}</DialogTitle>
          <DialogDescription className="sr-only">
            {t.common.rename}
          </DialogDescription>
        </DialogHeader>
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !isIMEComposing(event)) {
              event.preventDefault();
              onSubmit();
            }
          }}
          autoFocus
          className="h-8 text-sm"
        />
        <DialogFooter className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!value.trim()}
            onClick={onSubmit}
          >
            {t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
