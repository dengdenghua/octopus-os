import { createContext, useContext, type ReactNode } from "react";

export type WorkbenchSurface = "workspace" | "browser";

const WorkbenchSurfaceContext = createContext<WorkbenchSurface>("workspace");

export function WorkbenchSurfaceProvider({
  surface,
  children,
}: {
  surface: WorkbenchSurface;
  children: ReactNode;
}) {
  return (
    <WorkbenchSurfaceContext.Provider value={surface}>
      {children}
    </WorkbenchSurfaceContext.Provider>
  );
}

export function useWorkbenchSurface(): WorkbenchSurface {
  return useContext(WorkbenchSurfaceContext);
}
