import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  generateWiki,
  getWikiDocument,
  getWikiStatus,
  listWikiDocs,
  updateWiki,
} from "./api";

export function useWikiStatus(root?: string | null) {
  return useQuery({
    queryKey: ["wiki-status", root ?? "echo"],
    queryFn: () => getWikiStatus(root),
  });
}

export function useWikiDocs(root?: string | null) {
  return useQuery({
    queryKey: ["wiki-docs", root ?? "echo"],
    queryFn: () => listWikiDocs(root),
  });
}

export function useWikiDocument(path: string | null, root?: string | null) {
  return useQuery({
    queryKey: ["wiki-document", root ?? "echo", path],
    queryFn: () => getWikiDocument(path as string, root),
    enabled: Boolean(path),
  });
}

function useWikiMutation(action: "generate" | "update", root?: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      action === "generate" ? generateWiki(root) : updateWiki(root),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["wiki-status", root ?? "echo"],
        }),
        queryClient.invalidateQueries({
          queryKey: ["wiki-docs", root ?? "echo"],
        }),
      ]);
    },
  });
}

export function useGenerateWiki(root?: string | null) {
  return useWikiMutation("generate", root);
}

export function useUpdateWiki(root?: string | null) {
  return useWikiMutation("update", root);
}
