import type { components } from "@/core/api/openapi-types";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

type PickDirectoryResponse = components["schemas"]["FsPickDirectoryResponse"];

export async function pickLocalDirectory(
  defaultPath = "",
): Promise<string | null> {
  if (window.echo?.dialog?.open) {
    const result = await window.echo.dialog.open({
      title: "选择工作区文件夹",
      buttonLabel: "选取",
      message: "请选择一个文件夹作为工作区",
      properties: ["openDirectory", "createDirectory"],
      defaultPath,
    });
    return result.canceled ? null : result.filePaths[0] || null;
  }

  const params = new URLSearchParams();
  if (defaultPath) params.set("default_path", defaultPath);
  const query = params.toString();
  const response = await fetch(
    `${getBackendBaseURL()}/api/fs/pick-directory${query ? `?${query}` : ""}`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    throw new Error(`Folder picker request failed (${response.status})`);
  }

  const result = (await response.json()) as PickDirectoryResponse;
  if (result.error) throw new Error(result.error);
  if (result.canceled || !result.path) return null;
  return result.path;
}
