import { Download, FileJson, FileText } from "lucide-react";
import { useCallback } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
} from "@/core/threads/export";
import type { AgentThread } from "@/core/threads/types";

import { useThread } from "./messages/context";
import { Tooltip } from "./tooltip";

export function ExportTrigger({ threadId }: { threadId: string }) {
  const { t } = useI18n();
  const { thread } = useThread();

  const messages = thread.messages;

  const handleExport = useCallback(
    (format: "markdown" | "json") => {
      if (messages.length === 0) {
        toast.error(t.conversation.noMessages);
        return;
      }
      const agentThread = {
        thread_id: threadId,
        updated_at: new Date().toISOString(),
        values: thread.values,
      } as AgentThread;

      if (format === "markdown") {
        exportThreadAsMarkdown(agentThread, messages);
      } else {
        exportThreadAsJSON(agentThread, messages);
      }
      toast.success(t.common.exportSuccess);
    },
    [messages, thread.values, threadId, t],
  );

  if (messages.length === 0) {
    return null;
  }

  return (
    <DropdownMenu>
      <Tooltip content={t.common.export}>
        <DropdownMenuTrigger asChild>
          <Button
            className="size-8 rounded-lg border border-transparent text-muted-foreground transition-colors hover:border-border-default hover:bg-muted/60 hover:text-foreground"
            variant="ghost"
            size="icon"
            aria-label="Export"
          >
            <Download className="size-4" />
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent align="end" className="rounded-lg">
        <DropdownMenuItem onSelect={() => handleExport("markdown")}>
          <FileText className="text-muted-foreground" />
          <span>{t.common.exportAsMarkdown}</span>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => handleExport("json")}>
          <FileJson className="text-muted-foreground" />
          <span>{t.common.exportAsJSON}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
