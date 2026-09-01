import { EchoClient } from "./client";
import { getEchoBaseURL } from "../config";
import { getToken } from "../auth/api";

const _clients = new Map<string, EchoClient>();

export function getAPIClient(isMock?: boolean): EchoClient {
  const cacheKey = isMock ? "mock" : "default";
  let client = _clients.get(cacheKey);
  if (!client) {
    client = new EchoClient({
      apiUrl: getEchoBaseURL(isMock),
      getToken,
    });
    _clients.set(cacheKey, client);
  }
  return client;
}

// Singleton instance for direct import
export const apiClient = getAPIClient();
