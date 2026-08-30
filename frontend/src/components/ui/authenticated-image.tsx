import { useEffect, useMemo, useState, type ReactNode } from "react";

import { swallow } from "@/core/utils/log";
import { authHeaders, getToken } from "@/core/auth/api";
import { getBackendTransportBaseURL } from "@/core/config";

interface AuthenticatedImageProps {
  src: string;
  alt: string;
  className?: string;
  fallback?: ReactNode;
}

function normalizeImageSrc(src: string): string {
  if (
    src.startsWith("http://") ||
    src.startsWith("https://") ||
    src.startsWith("blob:") ||
    src.startsWith("data:")
  ) {
    return src;
  }
  if (src.startsWith("/")) {
    return src;
  }
  return src;
}

function needsAuthenticatedFetch(src: string): boolean {
  // Public endpoints that don't require authentication
  const publicPaths = [
    "/api/agents/", // Agent metadata and visuals
  ];

  if (src.startsWith("/api/")) {
    // Check if it's a public path
    if (publicPaths.some((path) => src.startsWith(path))) {
      return false;
    }
    return true;
  }

  if (!src.startsWith("http://") && !src.startsWith("https://")) {
    return false;
  }

  try {
    const url = new URL(src);
    const backend = new URL(getBackendTransportBaseURL());
    if (url.origin === backend.origin && url.pathname.startsWith("/api/")) {
      // Check if it's a public path
      if (publicPaths.some((path) => url.pathname.startsWith(path))) {
        return false;
      }
      return true;
    }
    return false;
  } catch (e) {
    swallow(e);
    return false;
  }
}

export function AuthenticatedImage({
  src,
  alt,
  className,
  fallback = null,
}: AuthenticatedImageProps) {
  const normalizedSrc = useMemo(() => {
    const normalized = normalizeImageSrc(src);

    if (
      !normalized.startsWith("http://") &&
      !normalized.startsWith("https://")
    ) {
      return normalized;
    }

    try {
      const url = new URL(normalized);
      const backend = new URL(getBackendTransportBaseURL());
      if (url.origin === backend.origin && url.pathname.startsWith("/api/")) {
        return `${url.pathname}${url.search}`;
      }
    } catch (e) {
      swallow(e);
    }

    return normalized;
  }, [src]);
  const [resolvedSrc, setResolvedSrc] = useState<string | null>(
    needsAuthenticatedFetch(normalizedSrc) ? null : normalizedSrc,
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);

    if (!needsAuthenticatedFetch(normalizedSrc)) {
      setResolvedSrc(normalizedSrc);
      return;
    }

    if (!getToken()) {
      setResolvedSrc(null);
      setFailed(true);
      return;
    }

    let revokedUrl: string | null = null;
    let cancelled = false;

    async function loadProtectedImage() {
      try {
        const res = await fetch(normalizedSrc, {
          headers: authHeaders(),
          cache: "no-store",
        });
        if (!res.ok) {
          throw new Error(`Failed to load image: ${res.status}`);
        }
        const blob = await res.blob();
        if (cancelled) return;
        revokedUrl = URL.createObjectURL(blob);
        setResolvedSrc(revokedUrl);
      } catch (e) {
        swallow(e);
        if (!cancelled) {
          setResolvedSrc(null);
          setFailed(true);
        }
      }
    }

    void loadProtectedImage();

    return () => {
      cancelled = true;
      if (revokedUrl) {
        URL.revokeObjectURL(revokedUrl);
      }
    };
  }, [normalizedSrc]);

  if (failed || !resolvedSrc) {
    return <>{fallback}</>;
  }

  return (
    <img
      src={resolvedSrc}
      alt={alt}
      className={className}
      onError={() => setFailed(true)}
    />
  );
}
