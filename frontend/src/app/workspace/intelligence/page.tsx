import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageCirclePlusIcon, PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AutomationConfiguredTab } from "@/components/workspace/automation/automation-configured-tab";
import { AutomationHistoryTab } from "@/components/workspace/automation/automation-history-tab";
import {
  AutomationTemplatesTab,
  type AutomationTemplate,
} from "@/components/workspace/automation/automation-templates-tab";
import { AutomationCreateDialog } from "@/components/workspace/automation/automation-create-dialog";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { PanelHost } from "@/core/panels/panel-host";

export default function IntelligencePage() {
  const [activeTab, setActiveTab] = useState("templates");
  const [createOpen, setCreateOpen] = useState(false);
  const [presetTemplate, setPresetTemplate] =
    useState<AutomationTemplate | null>(null);
  const navigate = useNavigate();

  const openCreate = (template: AutomationTemplate | null = null) => {
    setPresetTemplate(template);
    setCreateOpen(true);
  };

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="pt-0">
        <div className="flex h-full min-h-0 w-full flex-col bg-background">
          <header className="flex min-h-12 shrink-0 items-center justify-between gap-2 border-b border-border bg-muted/24 px-3 py-2 sm:h-12 sm:py-0">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">自动化</div>
              <div className="hidden truncate text-xs text-muted-foreground sm:block">
                配置任务、查看执行历史与管理模板
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <Button
                variant="outline"
                size="sm"
                className="h-8 bg-card/80 px-2 text-xs sm:h-7 sm:px-2.5"
                onClick={() => openCreate()}
              >
                <PlusIcon className="mr-1.5 size-3.5" />
                <span className="hidden sm:inline">手动新建</span>
                <span className="sm:hidden">新建</span>
              </Button>
              <Button
                size="sm"
                className="h-8 px-2 text-xs sm:h-7 sm:px-2.5"
                onClick={() =>
                  navigate(
                    "/workspace/realtime/new?prompt=" +
                      encodeURIComponent("帮我创建一个自动化订阅任务"),
                  )
                }
              >
                <MessageCirclePlusIcon className="mr-1.5 size-3.5" />
                <span className="hidden sm:inline">在对话中创建</span>
                <span className="sm:hidden">对话创建</span>
              </Button>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-auto bg-background p-2.5 sm:p-3">
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
                <TabsTrigger
                  value="panels"
                  className="h-8 px-3 text-xs data-[state=active]:text-primary after:bg-primary"
                >
                  面板
                </TabsTrigger>
              </TabsList>

              <TabsContent value="configured" className="mt-0">
                <AutomationConfiguredTab />
              </TabsContent>

              <TabsContent value="history" className="mt-0">
                <AutomationHistoryTab />
              </TabsContent>

              <TabsContent value="templates" className="mt-0">
                <AutomationTemplatesTab
                  onUseTemplate={openCreate}
                  onCreateCustom={() => openCreate()}
                />
              </TabsContent>

              <TabsContent value="panels" className="mt-0">
                {/* Composition-layer host: every registered workspace panel
                    renders here; new panels are register-and-done. */}
                <PanelHost zone="workspace" />
              </TabsContent>
            </Tabs>
          </div>
        </div>
        <AutomationCreateDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          presetTemplate={presetTemplate}
          onCreated={() => setActiveTab("configured")}
        />
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
