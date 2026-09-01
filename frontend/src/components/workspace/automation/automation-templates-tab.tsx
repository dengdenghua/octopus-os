import {
  BookOpenIcon,
  ChevronRightIcon,
  GithubIcon,
  NewspaperIcon,
  SparklesIcon,
  TargetIcon,
  Wand2Icon,
} from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export type AutomationTemplate = {
  id: string;
  icon: React.ReactNode;
  title: string;
  description: string;
  tags: string[];
  topic: string;
  cadence: string;
  schedule_time: string;
  schedule_day?: string;
  instructions?: string;
};

interface AutomationTemplatesTabProps {
  onUseTemplate?: (template: AutomationTemplate) => void;
  onCreateCustom?: () => void;
  /** 紧凑模式：用于窄容器（如助理右侧面板），改用横向紧凑卡片布局。 */
  compact?: boolean;
}

export function AutomationTemplatesTab({
  onUseTemplate,
  onCreateCustom,
  compact = false,
}: AutomationTemplatesTabProps) {
  const { t } = useI18n();

  const templates: AutomationTemplate[] = [
    {
      id: "daily-ai-news",
      icon: <SparklesIcon className="size-5 text-primary" />,
      title: "每日AI资讯摘要",
      description: "每日追踪 AI 领域最新论文、开源项目和产品动态，生成精炼摘要",
      tags: ["每日", "09:00"],
      topic: "AI大模型、机器学习、深度学习最新进展",
      cadence: "每日",
      schedule_time: "09:00",
    },
    {
      id: "github-trending",
      icon: <GithubIcon className="size-5 text-primary" />,
      title: "GitHub Trending 追踪",
      description: "追踪 GitHub Trending 热门项目，发现有趣的开源工具和框架",
      tags: ["每日", "10:00"],
      topic: "GitHub trending open source projects",
      cadence: "每日",
      schedule_time: "10:00",
    },
    {
      id: "competitor-monitor",
      icon: <TargetIcon className="size-5 text-primary" />,
      title: "竞品动态监控",
      description:
        "监控指定竞品的产品更新、新闻动态和用户反馈，及时掌握竞争格局",
      tags: ["每日", "14:00"],
      topic: "竞品监控",
      cadence: "每日",
      schedule_time: "14:00",
    },
    {
      id: "academic-papers",
      icon: <BookOpenIcon className="size-5 text-primary" />,
      title: "学术论文速递",
      description: "从 arXiv 等来源获取最新论文，提取核心方法和实验结论",
      tags: ["每周", "周一"],
      topic: "arxiv cs.AI cs.CL cs.LG latest papers",
      cadence: "每周",
      schedule_time: "09:00",
      schedule_day: "1",
    },
    {
      id: "industry-weekly",
      icon: <NewspaperIcon className="size-5 text-primary" />,
      title: "行业动态周报",
      description: "每周汇总行业重要新闻、政策变化和投融资动态，生成周报",
      tags: ["每周", "周五"],
      topic: "科技行业新闻、政策、投融资动态",
      cadence: "每周",
      schedule_time: "09:00",
      schedule_day: "5",
    },
    {
      id: "custom",
      icon: <Wand2Icon className="size-5 text-primary" />,
      title: "自定义任务",
      description: "从空白开始，自定义你想追踪的主题和数据来源",
      tags: ["自定义"],
      topic: "",
      cadence: "每日",
      schedule_time: "09:00",
    },
  ];

  const handleUseTemplate = (template: AutomationTemplate) => {
    if (onUseTemplate) {
      onUseTemplate(template);
    }
  };

  const handleCreateCustom = () => {
    if (onCreateCustom) {
      onCreateCustom();
    }
  };

  return compact ? (
    <div className="space-y-2">
      {templates.map((template, index) => {
        const isCustom = template.id === "custom";
        const coolTone = index % 2 === 0;
        return (
          <button
            key={template.id}
            type="button"
            onClick={
              isCustom ? handleCreateCustom : () => handleUseTemplate(template)
            }
            className={cn(
              "group flex w-full items-center gap-3 rounded-xl border p-3 text-left transition-[transform,border-color,background-color,box-shadow] hover:-translate-y-0.5",
              coolTone
                ? "border-border bg-card hover:border-primary/28 hover:bg-card hover:shadow-[var(--shadow-sm)]"
                : "border-border/90 bg-muted/18 hover:border-primary/24 hover:bg-muted/28 hover:shadow-[var(--shadow-sm)]",
            )}
          >
            <div
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-md border",
                coolTone
                  ? "border-border bg-muted/52"
                  : "border-primary/15 bg-primary/7",
              )}
            >
              {template.icon}
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-semibold text-foreground">
                {template.title}
              </h3>
              <p className="line-clamp-1 text-xs leading-relaxed text-muted-foreground">
                {template.description}
              </p>
            </div>
            <span className="sr-only">
              {isCustom
                ? t.intelligence.createCustomTask
                : t.intelligence.useTemplate}
            </span>
          </button>
        );
      })}
    </div>
  ) : (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 sm:gap-3 lg:grid-cols-3">
      {templates.map((template, index) => {
        const isCustom = template.id === "custom";
        const coolTone = index % 2 === 0;
        return (
          <button
            key={template.id}
            type="button"
            onClick={
              isCustom ? handleCreateCustom : () => handleUseTemplate(template)
            }
            className={cn(
              "group flex min-h-0 items-center gap-3 rounded-xl border p-3 text-left transition-[transform,border-color,background-color,box-shadow] active:scale-[0.99] hover:-translate-y-0.5 sm:min-h-40 sm:flex-col sm:items-start sm:gap-0 sm:p-5 sm:active:scale-100",
              coolTone
                ? "border-border bg-card hover:border-primary/28 hover:bg-card hover:shadow-[var(--shadow-sm)]"
                : "border-border/90 bg-muted/18 hover:border-primary/24 hover:bg-muted/28 hover:shadow-[var(--shadow-sm)]",
            )}
          >
            <div className="flex shrink-0 items-start sm:mb-5 sm:w-full">
              <div
                className={cn(
                  "flex size-10 items-center justify-center rounded-md border sm:size-11",
                  coolTone
                    ? "border-border bg-muted/52"
                    : "border-primary/15 bg-primary/7",
                )}
              >
                {template.icon}
              </div>
            </div>

            <div className="min-w-0 flex-1 space-y-1 sm:space-y-1.5">
              <h3 className="truncate text-sm font-semibold text-foreground sm:text-base">
                {template.title}
              </h3>
              <p className="line-clamp-1 text-xs leading-relaxed text-muted-foreground sm:line-clamp-2 sm:text-sm">
                {template.description}
              </p>
            </div>

            <span className="hidden items-center gap-1 text-xs font-medium text-primary/75 transition-colors group-hover:text-primary group-focus-visible:text-primary sm:mt-3 sm:flex">
              {isCustom
                ? t.intelligence.createCustomTask
                : t.intelligence.useTemplate}
              <ChevronRightIcon className="size-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>

            <ChevronRightIcon
              aria-hidden="true"
              className="size-4 shrink-0 text-muted-foreground/55 transition-transform group-hover:translate-x-0.5 group-hover:text-primary sm:hidden"
            />

            <span className="sr-only">
              {isCustom
                ? t.intelligence.createCustomTask
                : t.intelligence.useTemplate}
            </span>
          </button>
        );
      })}
    </div>
  );
}
