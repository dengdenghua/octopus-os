import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/core/i18n/hooks";
import { ApprovalRulesSection } from "./automation-settings-page";
import SandboxSettingsPage from "./sandbox-settings-page";

/**
 * One destination for controls that affect what Echo may execute and
 * where it may execute it. The two existing panels stay independent so their
 * behavior and persistence remain unchanged.
 */
export default function AutomationSecuritySettingsPage() {
  const { locale } = useI18n();
  const zh = locale.toLowerCase().startsWith("zh");
  const [tab, setTab] = useState("automation");
  return (
    <Tabs value={tab} onValueChange={setTab} className="space-y-4">
      <TabsList variant="line" className="h-8 w-fit">
        <TabsTrigger value="automation" className="h-8 px-3 text-xs">
          {zh ? "审批规则" : "Approval rules"}
        </TabsTrigger>
        <TabsTrigger value="sandbox" className="h-8 px-3 text-xs">
          {zh ? "沙箱与执行" : "Sandbox & execution"}
        </TabsTrigger>
      </TabsList>
      <TabsContent value="automation" className="mt-0 space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">
            {zh ? "执行审批规则" : "Execution approval rules"}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {zh
              ? "为具体工具设置允许或拒绝规则；浏览器与桌面总开关已移至各自设置页。"
              : "Allow or deny specific tools. Browser and desktop capability switches now live in their own settings pages."}
          </p>
        </div>
        <ApprovalRulesSection />
      </TabsContent>
      <TabsContent value="sandbox" className="mt-0">
        <SandboxSettingsPage />
      </TabsContent>
    </Tabs>
  );
}
