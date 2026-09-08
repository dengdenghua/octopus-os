import type { ReactNode } from "react";
import { UNSAFE_LocationContext, UNSAFE_RouteContext } from "react-router-dom";

const DETACHED_ROUTE_CONTEXT = {
  outlet: null,
  matches: [],
  isDataRoute: false,
};

export function DetachedRouterContext({ children }: { children: ReactNode }) {
  // Echo itself already runs in a HashRouter. The Agent window needs its own
  // history so links, thread changes and redirects stay inside that window.
  // Reset only the inherited router contexts, then mount a normal MemoryRouter
  // backed by the exact same route tree as the top-level Agent workspace.
  return (
    <UNSAFE_LocationContext.Provider value={null!}>
      <UNSAFE_RouteContext.Provider value={DETACHED_ROUTE_CONTEXT}>
        {children}
      </UNSAFE_RouteContext.Provider>
    </UNSAFE_LocationContext.Provider>
  );
}
