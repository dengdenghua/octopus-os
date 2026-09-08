import { useEffect, useState } from "react";

import { loadNASAssetURL } from "@/core/storage/api";

export type NASAssetState = {
  url: string | null;
  loading: boolean;
  error: string | null;
};

const EMPTY_STATE: NASAssetState = {
  url: null,
  loading: false,
  error: null,
};

/** Load a temporary media preview URL and revoke it on every transition. */
export function useNASAssetState(path: string | undefined): NASAssetState {
  const [state, setState] = useState<NASAssetState>(EMPTY_STATE);

  useEffect(() => {
    if (!path) {
      setState(EMPTY_STATE);
      return;
    }
    setState({ url: null, loading: true, error: null });

    let disposed = false;
    let objectUrl: string | null = null;
    void loadNASAssetURL(path)
      .then((next) => {
        if (disposed) {
          URL.revokeObjectURL(next);
          return;
        }
        objectUrl = next;
        setState({ url: next, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setState({
            url: null,
            loading: false,
            error: error instanceof Error ? error.message : "无法读取预览",
          });
        }
      });

    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  return state;
}

export function useNASAsset(path: string | undefined): string | null {
  return useNASAssetState(path).url;
}
