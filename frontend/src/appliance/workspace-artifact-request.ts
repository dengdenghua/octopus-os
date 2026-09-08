import { createContext } from "react";

export type WorkspaceArtifactRequest = { path: string; revision: number };
export const WorkspaceArtifactRequestContext = createContext<
  WorkspaceArtifactRequest | undefined
>(undefined);
