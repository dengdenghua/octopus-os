import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { I18nContext } from "@/core/i18n/context";
import type { Translations } from "@/core/i18n/locales";

const FALLBACK_ERROR_BOUNDARY_TRANSLATIONS: Translations["errorBoundary"] = {
  title: "Something went wrong",
  description:
    "An error occurred while loading this component. Try refreshing the page.",
  chunkTitle: "Page resources were updated",
  chunkDescription:
    "The frontend bundle changed. Refresh the page to load the latest version.",
  unexpectedDescription: "An unexpected error occurred.",
  retry: "Retry",
  refreshPage: "Refresh page",
};

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  // Error reporting integration point: callers (or a future diagnostics /
  // telemetry channel) can inject onError to receive render errors. The
  // existing stream-telemetry channel only covers streaming turn metrics,
  // not render errors, so an injectable callback is the minimal hook;
  // defaults to console.error when not provided.
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * React error boundary that catches render errors in its subtree
 * and displays a friendly error card instead of a blank screen.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    if (this.isHmrRefreshError(error, errorInfo)) {
      setTimeout(() => this.setState({ hasError: false, error: null }), 0);
      return;
    }
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    } else {
      console.error("[ErrorBoundary] Uncaught error:", error, errorInfo);
    }
    if (this.isChunkLoadError(error)) {
      const key = "echo:chunk-reload-once";
      if (window.sessionStorage.getItem(key) !== "1") {
        window.sessionStorage.setItem(key, "1");
        window.location.reload();
      }
    }
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  // Chunk-load failures (deploy rolled forward, CDN blip, offline) surface as
  // a render error with a distinctive message — offer a hard reload instead
  // of the generic retry, which just re-renders the same broken subtree.
  private isChunkLoadError(err: Error | null): boolean {
    if (!err) return false;
    const msg = err.message || "";
    return (
      err.name === "ChunkLoadError" ||
      /Loading chunk .* failed/i.test(msg) ||
      /Failed to fetch dynamically imported module/i.test(msg) ||
      /Importing a module script failed/i.test(msg)
    );
  }

  private isHmrRefreshError(err: Error, errorInfo: ErrorInfo): boolean {
    if (import.meta.env.PROD) return false;
    const stack = errorInfo.componentStack || "";
    const combined = `${err.message}\n${err.stack || ""}\n${stack}`;
    return (
      /@react-refresh|performReactRefresh|scheduleRefresh/i.test(combined) &&
      /must be used within an|Context\.Provider/i.test(combined)
    );
  }

  handleReload = (): void => {
    window.sessionStorage.removeItem("echo:chunk-reload-once");
    window.location.reload();
  };

  private renderErrorCard(t: Translations["errorBoundary"]): ReactNode {
    const chunkFailed = this.isChunkLoadError(this.state.error);
    return (
      <div className="flex items-center justify-center p-8">
        <Card className="max-w-lg border-border-default bg-background/92 shadow-[var(--shadow-xs)]">
          <CardHeader>
            <CardTitle className="text-base text-foreground">
              {chunkFailed ? t.chunkTitle : t.title}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              {chunkFailed
                ? t.chunkDescription
                : this.state.error?.message || t.unexpectedDescription}
            </p>
          </CardContent>
          <CardFooter className="gap-3">
            {chunkFailed ? (
              <Button variant="default" size="sm" onClick={this.handleReload}>
                {t.refreshPage}
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={this.handleReset}>
                {t.retry}
              </Button>
            )}
          </CardFooter>
        </Card>
      </div>
    );
  }

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <I18nContext.Consumer>
          {(context) =>
            this.renderErrorCard(
              context?.t.errorBoundary ?? FALLBACK_ERROR_BOUNDARY_TRANSLATIONS,
            )
          }
        </I18nContext.Consumer>
      );
    }

    return this.props.children;
  }
}
