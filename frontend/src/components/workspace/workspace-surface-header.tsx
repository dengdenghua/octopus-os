import { cn } from "@/lib/utils";
import { useRef, type PointerEvent } from "react";
import {
  isEmbeddedWindow,
  sendEmbeddedWindowDrag,
  useEchoDesktopWindowChrome,
} from "./embedded-window-bridge";
import { MacWindowControls } from "./mac-window-controls";
import { WorkspaceSurfaceSwitch } from "./workspace-surface-switch";

export function WorkspaceSurfaceHeader({
  active,
  className,
}: {
  active: "agent" | "browser";
  className?: string;
}) {
  const embedded = isEmbeddedWindow();
  const echoDesktopOwnsChrome = useEchoDesktopWindowChrome();
  const dragPointer = useRef<number | null>(null);
  const onDrag = (phase: "start" | "move" | "end", event: PointerEvent) => {
    if (!embedded) return;
    if (phase === "start") {
      if (event.target !== event.currentTarget) return;
      dragPointer.current = event.pointerId;
      event.currentTarget.setPointerCapture(event.pointerId);
    } else if (dragPointer.current !== event.pointerId) {
      return;
    }
    sendEmbeddedWindowDrag(phase, event.screenX, event.screenY);
    if (phase === "end") dragPointer.current = null;
  };

  return (
    <div
      onPointerDown={(event) => onDrag("start", event)}
      onPointerMove={(event) => onDrag("move", event)}
      onPointerUp={(event) => onDrag("end", event)}
      onPointerCancel={(event) => onDrag("end", event)}
      className={cn(
        // Width follows content: MacWindowControls may render null (non-mac
        // Electron), collapsing the row to just the surface switch.
        "flex h-8 shrink-0 items-center justify-start gap-2",
        // Echo OS overlays a 64px system-control hit area above embedded
        // content. Reserve that full slot (plus the header's existing gap),
        // whether the content lives in an iframe or is mounted directly into
        // an Echo OS window, so neither pointer nor visuals can overlap.
        (embedded || echoDesktopOwnsChrome) && "pl-16",
        className,
      )}
    >
      {!echoDesktopOwnsChrome ? <MacWindowControls /> : null}
      <WorkspaceSurfaceSwitch active={active} />
    </div>
  );
}
