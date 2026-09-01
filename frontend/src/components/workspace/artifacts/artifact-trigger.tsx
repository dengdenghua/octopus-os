import { FilesIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { useArtifacts } from "./context";

export const ArtifactTrigger = ({ className }: { className?: string }) => {
  const { t } = useI18n();
  const { artifacts, open, setOpen: setArtifactsOpen } = useArtifacts();
  const count = artifacts?.length ?? 0;
  return (
    <Button
      className={cn(
        "relative size-8 border text-muted-foreground transition-colors",
        open
          ? "border-border bg-muted text-foreground"
          : "border-transparent hover:border-border-subtle hover:bg-muted/60 hover:text-foreground",
        className,
      )}
      variant="ghost"
      size="icon"
      title={t.common.artifacts}
      aria-label={t.common.artifacts}
      onClick={() => {
        setArtifactsOpen(!open);
      }}
    >
      <FilesIcon className="size-4" />
      {count > 0 && (
        <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-foreground px-1 text-xs font-medium leading-none text-background">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Button>
  );
};
