/**
 * React hooks for app authorization.
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { currentActorId } from "@/core/auth/api";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";

import {
  listProviders,
  getProviderTypes,
  listAuthorizations,
  createApiKeyAuth,
  createTokenAuth,
  createCookieAuth,
  deleteAuthorization,
  testAuthorization,
  refreshAuthorization,
  initOAuthFlow,
  startBrowserCapture,
  getBrowserCaptureStatus,
  cancelBrowserCapture,
  type ProviderInfo,
  type Authorization,
  type BrowserCaptureSession,
} from "./api";

const QUERY_KEY = ["app-authorizations"];
const PROVIDERS_KEY = ["app-providers"];

export function authorizationsQueryKey(actor = currentActorId()) {
  return [...QUERY_KEY, actor] as const;
}

// Query hooks
export function useProviders(providerType?: string) {
  return useQuery<ProviderInfo[]>({
    queryKey: [...PROVIDERS_KEY, providerType],
    queryFn: () => listProviders(providerType),
  });
}

export function useProviderTypes() {
  return useQuery<Array<{ value: string; label: string }>>({
    queryKey: [...PROVIDERS_KEY, "types"],
    queryFn: getProviderTypes,
  });
}

export function useAuthorizations() {
  return useQuery<Authorization[]>({
    queryKey: authorizationsQueryKey(),
    queryFn: listAuthorizations,
  });
}

// Mutation hooks
export function useCreateApiKeyAuth() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation({
    mutationFn: ({
      provider,
      apiKey,
      displayName,
      scopes,
    }: {
      provider: string;
      apiKey: string;
      displayName?: string;
      scopes?: string[];
    }) => createApiKeyAuth(provider, apiKey, displayName, scopes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: authorizationsQueryKey() });
      toast.success(t.appAuth.toastAuthCreated);
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

export function useCreateTokenAuth() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation({
    mutationFn: ({
      provider,
      token,
      displayName,
      scopes,
    }: {
      provider: string;
      token: string;
      displayName?: string;
      scopes?: string[];
    }) => createTokenAuth(provider, token, displayName, scopes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: authorizationsQueryKey() });
      toast.success(t.appAuth.toastAuthCreated);
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

export function useCreateCookieAuth() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation({
    mutationFn: ({
      provider,
      cookieValue,
      displayName,
    }: {
      provider: string;
      cookieValue: string;
      displayName?: string;
    }) => createCookieAuth(provider, cookieValue, displayName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: authorizationsQueryKey() });
      toast.success(t.appAuth.toastAuthCreated);
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

export function useDeleteAuthorization() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation({
    mutationFn: deleteAuthorization,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: authorizationsQueryKey() });
      toast.success(t.appAuth.toastAuthDeleted);
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

export function useTestAuthorization() {
  return useMutation({
    mutationFn: testAuthorization,
    onSuccess: (result) => {
      if (result.success) {
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

export function useRefreshAuthorization() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation({
    mutationFn: refreshAuthorization,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: authorizationsQueryKey() });
      toast.success(t.appAuth.toastAuthRefreshed);
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

export function useInitOAuth() {
  return useMutation({
    mutationFn: initOAuthFlow,
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });
}

/**
 * Drives the Playwright browser-capture flow:
 *   start() -> backend opens a Chromium window
 *   poll status every second until success/failed/cancelled
 *   refresh authorizations on success
 */
export function useBrowserCapture() {
  const queryClient = useQueryClient();
  const { t } = useI18n();
  const [session, setSession] = useState<BrowserCaptureSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => {
    return () => stopPolling();
  }, []);

  const start = async (providerId: string) => {
    setError(null);
    stopPolling();
    try {
      const initial = await startBrowserCapture(providerId);
      setSession(initial);
      pollRef.current = window.setInterval(async () => {
        try {
          const next = await getBrowserCaptureStatus(initial.session_id);
          setSession(next);
          if (next.status !== "running") {
            stopPolling();
            if (next.status === "success") {
              toast.success(t.appAuth.toastBrowserAuthSuccess);
              queryClient.invalidateQueries({
                queryKey: authorizationsQueryKey(),
              });
            } else if (next.status === "failed") {
              toast.error(next.message || t.appAuth.toastBrowserAuthFailed);
            } else if (next.status === "cancelled") {
              toast.message(t.appAuth.toastBrowserAuthCancelled);
            }
          }
        } catch (err) {
          swallow(err);
          stopPolling();
          setError((err as Error).message);
        }
      }, 1000);
    } catch (err) {
      setError((err as Error).message);
      toast.error((err as Error).message);
    }
  };

  const cancel = async () => {
    if (!session) return;
    try {
      await cancelBrowserCapture(session.session_id);
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const reset = () => {
    stopPolling();
    setSession(null);
    setError(null);
  };

  return { session, error, start, cancel, reset };
}
