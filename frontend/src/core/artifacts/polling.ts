export const ACTIVE_ARTIFACT_REFRESH_MS = 3000;

export function getWorkspaceArtifactRefetchInterval(
  isThreadLoading: boolean,
): number | false {
  return isThreadLoading ? ACTIVE_ARTIFACT_REFRESH_MS : false;
}
