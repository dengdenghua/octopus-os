import { cn } from "@/lib/utils";
import { useWorkbenchSurface } from "@/core/workbench/workbench-surface";

/* Implementation note. */
const ELECTRON_TITLE_BAR_HEIGHT = 36;
const inElectron = (): boolean =>
  typeof window !== "undefined" && !!window.echo?.isElectron;

export function WorkspaceContainer({
  className,
  children,
  ...props
}: React.ComponentProps<"div">) {
  const surface = useWorkbenchSurface();
  const embeddedInBrowser = surface === "browser";
  // Implementation note.
  // Implementation note.
  return (
    <div
      className={cn(
        "flex w-full flex-col px-3 pb-3 md:px-4",
        embeddedInBrowser ? "h-full" : "h-screen",
        className,
      )}
      style={
        inElectron() && !embeddedInBrowser
          ? { paddingTop: ELECTRON_TITLE_BAR_HEIGHT }
          : undefined
      }
      {...props}
    >
      {inElectron() && !embeddedInBrowser && (
        <div
          aria-hidden
          className="pointer-events-none fixed left-0 right-0 top-0 z-50 h-9"
          style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
        />
      )}
      {children}
    </div>
  );
}

export function WorkspaceBody({
  className,
  children,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="workspace-body"
      className={cn(
        "relative flex min-h-0 w-full flex-1 flex-col items-center overflow-y-auto overflow-x-hidden pt-3",
        className,
      )}
      {...props}
    >
      {/* ``flex-1 min-h-0`` on the inner wrapper so children using
          ``h-full`` / ``size-full`` get a non-zero parent height.
          Without this, React-Flow-based pages (workflows editor) log
          "The React Flow parent container needs a width and a height
          to render the graph" and render blank. Regression discovered
          2026-04-24 by browser-side regression sweep. */}
      <div className="flex w-full flex-1 min-h-0 flex-col items-center">
        {children}
      </div>
    </div>
  );
}
