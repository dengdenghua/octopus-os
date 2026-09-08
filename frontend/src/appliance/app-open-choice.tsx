import { useEffect, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { AppPresentation } from "@/core/apps/app-presentation";
import { supportsPresentation } from "@/core/workbench/apps";

export function AppOpenChoice({
  name,
  presentation,
  supportedPresentations,
  onClose,
  onStandalone,
  onWorkbench,
}: {
  name?: string;
  /** Primary presentation for legacy registrations without an explicit list. */
  presentation?: AppPresentation;
  supportedPresentations?: readonly AppPresentation[];
  onClose: () => void;
  onStandalone: () => void;
  onWorkbench: () => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const presentationContract = presentation
    ? { presentation, supportedPresentations }
    : null;
  const canOpenStandalone = presentationContract
    ? supportsPresentation(presentationContract, "standalone")
    : (supportedPresentations?.includes("standalone") ?? true);
  const canOpenWorkbench = presentationContract
    ? supportsPresentation(presentationContract, "workbench")
    : (supportedPresentations?.includes("workbench") ?? true);
  const presentationDescription =
    canOpenStandalone && canOpenWorkbench
      ? "两种方式共用应用、权限和数据。"
      : canOpenWorkbench
        ? "应用会嵌入工作台，并复用当前任务与上下文。"
        : canOpenStandalone
          ? "应用会以独立窗口打开，并复用同一份应用数据。"
          : "当前应用暂未提供可用的打开方式。";
  useEffect(() => setContainer(hostRef.current), []);

  return (
    <div ref={hostRef} data-app-open-choice-host>
      {container ? (
        <Dialog
          open={Boolean(name)}
          onOpenChange={(open) => {
            if (!open) onClose();
          }}
        >
          <DialogContent container={container} className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle>打开{name}</DialogTitle>
              <DialogDescription>{presentationDescription}</DialogDescription>
            </DialogHeader>
            {canOpenStandalone || canOpenWorkbench ? (
              <div className="flex flex-col gap-2">
                {canOpenStandalone && (
                  <button
                    className="rounded-md border px-4 py-3 text-left hover:bg-muted"
                    onClick={onStandalone}
                  >
                    独立窗口打开
                  </button>
                )}
                {canOpenWorkbench && (
                  <button
                    className="rounded-md border px-4 py-3 text-left hover:bg-muted"
                    onClick={onWorkbench}
                  >
                    在工作台中打开
                  </button>
                )}
              </div>
            ) : (
              <p
                role="status"
                className="rounded-md border border-dashed px-4 py-3 text-sm text-muted-foreground"
              >
                当前应用暂未提供可用的打开方式，请稍后重试。
              </p>
            )}
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}
