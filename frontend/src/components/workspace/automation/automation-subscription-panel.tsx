import { useState } from "react";
import { XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { AutomationConfiguredTab } from "./automation-configured-tab";
import { AutomationHistoryTab } from "./automation-history-tab";
import { AutomationTemplatesTab } from "./automation-templates-tab";
import { AutomationCreateDialog } from "./automation-create-dialog";
import type { AutomationTemplate } from "./automation-templates-tab";

/**
 * 自动化 / 订阅面板 —— 复用现有自动化页面的「已配置 / 执行历史 / 任务模板」
 * 三个 tab，供助理对话右侧面板内嵌展示订阅与定时任务。
 */
export function AutomationSubscriptionPanel({
  className,
  onClose,
}: {
  className?: string;
  onClose?: () => void;
}) {
  const [activeTab, setActiveTab] = useState("configured");
  const [createOpen, setCreateOpen] = useState(false);
  const [presetTemplate, setPresetTemplate] = useState<AutomationTemplate | null>(null);

  const openCreate = (template: AutomationTemplate | null = null) => {
    setPresetTemplate(template);
    setCreateOpen(true);
  };

  return (
    <div
      className={
        "flex h-full min-h-0 w-full flex-col overflow-hidden bg-background " +
        (className ?? "")
      }
    >
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border-default px-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">自动化 / 订阅</div>
          <div className="truncate text-xs text-muted-foreground">
            定时任务、订阅推送与执行记录
          </div>
        </div>
        {onClose && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            aria-label="关闭"
            title="关闭"
            onClick={onClose}
          >
            <XIcon className="size-4" />
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-background p-3">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList variant="line" className="mb-3 h-8 w-fit">
            <TabsTrigger
              value="configured"
              className="h-8 px-3 text-xs data-[state=active]:text-primary after:bg-primary"
            >
              已配置
            </TabsTrigger>
            <TabsTrigger
              value="history"
              className="h-8 px-3 text-xs data-[state=active]:text-primary after:bg-primary"
            >
              执行历史
            </TabsTrigger>
            <TabsTrigger
              value="templates"
              className="h-8 px-3 text-xs data-[state=active]:text-primary after:bg-primary"
            >
              任务模板
            </TabsTrigger>
          </TabsList>

          <TabsContent value="configured" className="mt-0">
            <AutomationConfiguredTab />
          </TabsContent>

          <TabsContent value="history" className="mt-0">
            <AutomationHistoryTab compact />
          </TabsContent>

          <TabsContent value="templates" className="mt-0">
            <AutomationTemplatesTab
              compact
              onUseTemplate={openCreate}
              onCreateCustom={() => openCreate()}
            />
          </TabsContent>
        </Tabs>
      </div>
      <AutomationCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        presetTemplate={presetTemplate}
      />
    </div>
  );
}
