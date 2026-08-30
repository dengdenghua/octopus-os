import { CheckIcon, CopyIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
} from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { copyTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";

import { Tooltip } from "./tooltip";

export function CopyButton({
  clipboardData,
  ...props
}: ComponentProps<typeof Button> & {
  clipboardData: string;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await copyTextToClipboard(clipboardData);
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t.clipboard.failedToCopyToClipboard);
    }
  }, [clipboardData, t.clipboard.failedToCopyToClipboard]);
  return (
    <Tooltip content={t.clipboard.copyToClipboard}>
      <Button
        size="icon-sm"
        type="button"
        variant="ghost"
        aria-label={t.clipboard.copyToClipboard}
        className="rounded-lg border border-transparent text-muted-foreground transition-colors hover:border-border-default hover:bg-muted/60 hover:text-foreground"
        onClick={handleCopy}
        {...props}
      >
        {copied ? (
          <CheckIcon className="text-success" size={12} />
        ) : (
          <CopyIcon size={12} />
        )}
      </Button>
    </Tooltip>
  );
}
