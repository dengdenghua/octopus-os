import { MessageSquareTextIcon } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useI18n } from "@/core/i18n/hooks";
import { useLocalSettings } from "@/core/settings";

function SettingRow({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0 space-y-1">
        <div className="text-sm font-medium">{title}</div>
        <p className="max-w-xl text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export default function ConversationSettingsPage() {
  const { t, locale } = useI18n();
  const [settings, setSetting] = useLocalSettings();
  const zh = locale.toLowerCase().startsWith("zh");

  return (
    <div className="space-y-6">
      <header className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
          <MessageSquareTextIcon className="size-5" />
        </div>
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-tight">
            {t.settings.sections.conversation}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
            {zh
              ? "控制对话里展示多少执行过程，以及聊天文字的阅读尺寸。三档细节只改变呈现，不改变 Agent 的实际执行。"
              : "Choose how much execution detail appears in conversations and the reading size of chat text. Detail levels change presentation, not agent behavior."}
          </p>
        </div>
      </header>

      <div className="divide-y rounded-xl border border-border-default bg-card/35">
        <SettingRow
          title={t.settings.appearance.conversationDetailLevelTitle}
          description={t.settings.appearance.conversationDetailLevelDescription}
        >
          <Select
            value={settings.display.conversation_detail_level ?? "medium"}
            onValueChange={(value) => {
              if (value === "low" || value === "medium" || value === "high") {
                setSetting("display", { conversation_detail_level: value });
              }
            }}
          >
            <SelectTrigger
              aria-label={t.settings.appearance.conversationDetailLevelTitle}
              className="w-full sm:w-[220px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="low">
                {t.settings.appearance.conversationDetailLevelLow}
              </SelectItem>
              <SelectItem value="medium">
                {t.settings.appearance.conversationDetailLevelMedium}
              </SelectItem>
              <SelectItem value="high">
                {t.settings.appearance.conversationDetailLevelHigh}
              </SelectItem>
            </SelectContent>
          </Select>
        </SettingRow>

        <SettingRow
          title={t.settings.appearance.chatFontSizeTitle}
          description={t.settings.appearance.chatFontSizeDescription}
        >
          <Select
            value={settings.display.chat_font_size}
            onValueChange={(value) => {
              if (
                value === "small" ||
                value === "medium" ||
                value === "large"
              ) {
                setSetting("display", { chat_font_size: value });
              }
            }}
          >
            <SelectTrigger
              aria-label={t.settings.appearance.chatFontSizeTitle}
              className="w-full sm:w-[220px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="small">
                {t.settings.appearance.chatFontSizeSmall}
              </SelectItem>
              <SelectItem value="medium">
                {t.settings.appearance.chatFontSizeMedium}
              </SelectItem>
              <SelectItem value="large">
                {t.settings.appearance.chatFontSizeLarge}
              </SelectItem>
            </SelectContent>
          </Select>
        </SettingRow>
      </div>
    </div>
  );
}
