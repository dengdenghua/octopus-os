// Slash-command catalog client. Backend assembles global
// (~/.echo/commands/) + project-local entries; this just
// fetches the merged list so the composer typeahead can render
// it. Body text never crosses the wire — the picker only needs
// metadata (name / description / argument hint).

import type { components } from "@/core/api/openapi-types";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export type SlashCommand = components["schemas"]["SlashCommandWire"];

export async function listSlashCommands(): Promise<{
  commands: SlashCommand[];
}> {
  const response = await fetch(`${getBackendBaseURL()}/api/slash-commands`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    return { commands: [] };
  }
  return response.json();
}
