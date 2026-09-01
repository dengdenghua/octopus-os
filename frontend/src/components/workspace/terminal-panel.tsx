"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { TerminalIcon, XIcon, RotateCcwIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { swallow } from "@/core/utils/log";
import { getToken } from "@/core/auth/api";
import { getBackendBaseURL, getBackendWebSocketBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import "@xterm/xterm/css/xterm.css";

interface TerminalPanelProps {
  sessionId: string;
  cwd?: string;
  className?: string;
  onClose?: () => void;
}

function terminalShellLabel(platform: string): string {
  if (/win/i.test(platform)) return "PowerShell";
  if (/mac|iphone|ipad/i.test(platform)) return "zsh";
  return "shell";
}

export function TerminalPanel({
  sessionId,
  cwd,
  className,
  onClose,
}: TerminalPanelProps) {
  const { t } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasOutput, setHasOutput] = useState(false);
  const [connectionError, setConnectionError] = useState(false);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const wsBase = getBackendWebSocketBaseURL();
    const params = new URLSearchParams();
    if (cwd) params.set("cwd", cwd);
    const token = getToken();
    if (token) params.set("token", token);
    const query = params.size ? `?${params.toString()}` : "";
    const url = `${wsBase}/api/terminal/ws/${sessionId}${query}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setConnectionError(false);
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "output" && termRef.current) {
          if (msg.data) setHasOutput(true);
          termRef.current.write(msg.data);
        } else if (msg.type === "exit") {
          setHasOutput(true);
          termRef.current?.writeln(
            `\r\n[Process exited with code ${msg.code}]`,
          );
          setConnected(false);
        } else if (msg.type === "error") {
          setHasOutput(true);
          termRef.current?.writeln(`\r\n[Error: ${msg.message}]`);
        }
      } catch (err) {
        swallow(err);
        setHasOutput(true);
        termRef.current?.write(e.data);
      }
    };

    ws.onclose = () => setConnected(false);
    ws.onerror = () => {
      setConnected(false);
      setConnectionError(true);
    };
  }, [sessionId, cwd]);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace",
      theme: {
        background: "#ffffff",
        foreground: "#111827",
        cursor: "#111827",
        selectionBackground: "#dbeafe",
      },
      scrollback: 5000,
      convertEol: true,
    });

    const fit = new FitAddon();
    const links = new WebLinksAddon();
    term.loadAddon(fit);
    term.loadAddon(links);
    term.open(containerRef.current);
    fit.fit();

    termRef.current = term;
    fitRef.current = fit;

    term.onData((data) => {
      setHasOutput(true);
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "input", data }));
      }
    });

    connect();

    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch (e) {
        swallow(e);
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      wsRef.current?.close();
      wsRef.current = null;
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [connect]);

  const handleRestart = useCallback(async () => {
    wsRef.current?.close();
    const base = getBackendBaseURL();
    try {
      await fetch(`${base}/api/terminal/kill/${sessionId}`, { method: "POST" });
    } catch (e) {
      swallow(e);
    }
    termRef.current?.clear();
    setHasOutput(false);
    setConnectionError(false);
    setTimeout(connect, 300);
  }, [sessionId, connect]);

  return (
    <div
      className={cn(
        "flex h-full flex-col bg-background text-foreground",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-border bg-background px-3 py-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <TerminalIcon className="size-3.5" />
          <span className="text-sm font-semibold text-foreground">
            {t.codeMode.terminal}
          </span>
          <span className="font-medium">
            {terminalShellLabel(navigator.platform || navigator.userAgent)}
          </span>
          <span
            className={cn(
              "size-1.5 rounded-full",
              connected ? "bg-success" : "bg-destructive",
            )}
          />
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={handleRestart}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={t.codeMode.terminalRestart}
          >
            <RotateCcwIcon className="size-3" />
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title={t.codeMode.terminalClose}
            >
              <XIcon className="size-3" />
            </button>
          )}
        </div>
      </div>
      <div className="relative min-h-0 flex-1 bg-background">
        <div ref={containerRef} className="absolute inset-0" />
        {!hasOutput && (
          <div className="pointer-events-none absolute inset-0 px-6 py-5 font-mono text-sm leading-6 text-muted-foreground/70">
            {connectionError
              ? t.codeMode.terminalConnectionFailed
              : connected
                ? t.codeMode.terminalConnectedHint
                : t.codeMode.terminalConnecting}
          </div>
        )}
      </div>
    </div>
  );
}

export const __testing = { terminalShellLabel };
