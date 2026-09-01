import { useId, useState } from "react";
import { SlidersHorizontalIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useLocalSettings } from "@/core/settings";
import { cn } from "@/lib/utils";

/** Settings that belong exclusively to the fixed Assistant conversation. */
export function AssistantSettingsMenu() {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useLocalSettings();
  const idleHoursId = useId();
  const hours = settings.session.auto_new_session_hours;
  const enabled = hours > 0;

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          aria-label="助手设置"
          title="助手设置"
          className={cn(
            "flex size-[42px] items-center justify-center rounded-lg border shadow-none transition-all duration-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 sm:size-8",
            open
              ? "border-transparent bg-transparent text-foreground/82 hover:border-border-default hover:bg-muted/55 hover:text-foreground"
              : "border-transparent bg-transparent text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground",
          )}
        >
          <SlidersHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-80 p-0">
        <div className="border-b border-border-subtle px-4 py-3">
          <p className="text-sm font-medium text-foreground">助手设置</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            仅影响当前助手的固定对话窗口。
          </p>
        </div>
        <div className="space-y-3 px-4 py-3">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-sm text-foreground">自动开启新会话</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                长时间未对话后，下一条消息从新会话开始。
              </p>
            </div>
            <Switch
              checked={enabled}
              onCheckedChange={(checked) =>
                setSettings("session", {
                  auto_new_session_hours: checked ? 6 : 0,
                })
              }
              aria-label="自动开启新会话"
            />
          </div>
          <div
            className={cn(
              "grid transition-[grid-template-rows,opacity] duration-base",
              enabled
                ? "grid-rows-[1fr] opacity-100"
                : "pointer-events-none grid-rows-[0fr] opacity-0",
            )}
            aria-hidden={!enabled}
          >
            <div className="min-h-0 overflow-hidden">
              <div className="flex items-center gap-2 rounded-lg bg-muted/35 px-3 py-2">
                <label
                  htmlFor={idleHoursId}
                  className="text-xs text-muted-foreground"
                >
                  空闲
                </label>
                <Input
                  id={idleHoursId}
                  type="number"
                  min={1}
                  max={720}
                  value={hours}
                  onChange={(event) =>
                    setSettings("session", {
                      auto_new_session_hours: Math.max(
                        1,
                        Math.min(
                          720,
                          Math.floor(Number(event.target.value) || 1),
                        ),
                      ),
                    })
                  }
                  aria-label="助手新会话空闲时长"
                  disabled={!enabled}
                  className="h-8 w-20 tabular-nums"
                />
                <span className="text-xs text-muted-foreground">小时后</span>
              </div>
            </div>
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
