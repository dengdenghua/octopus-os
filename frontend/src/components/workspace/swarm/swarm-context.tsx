// Placeholder for the removed Swarm provider. The hook signature is
// retained so existing call sites keep compiling, but it always returns
// null — there is no live Swarm context anymore. Callers guard with
// `if (swarm)` so the body becomes a no-op branch.
interface SwarmContextValue {
  setSelectedAgentId: (id: string) => void;
  openPanel: () => void;
}

const removedSwarmContext: SwarmContextValue | null = null;

export function useOptionalSwarm(): SwarmContextValue | null {
  return removedSwarmContext;
}
