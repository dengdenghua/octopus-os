import {
  LayoutTemplateIcon,
  GlobeIcon,
  ShoppingCartIcon,
  FileTextIcon,
  BarChart3Icon,
  UsersIcon,
  CalendarIcon,
  MessageSquareIcon,
  SearchIcon,
  DatabaseIcon,
  SparklesIcon,
  ArrowRightIcon,
} from "lucide-react";
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";

export interface QuickTemplate {
  id: string;
  name: string;
  description: string;
  icon: React.ReactNode;
  prompt: string;
  category: "website" | "app" | "tool" | "game" | "data";
}

interface QuickTemplatesProps {
  onSelect: (template: QuickTemplate) => void;
  className?: string;
}

const categoryColors: Record<string, string> = {
  website: "bg-info/10 text-info",
  app: "bg-chart-1/10 text-chart-1",
  tool: "bg-success/10 text-success",
  game: "bg-warning/10 text-warning",
  data: "bg-destructive/10 text-destructive",
};

export function QuickTemplates({ onSelect, className }: QuickTemplatesProps) {
  const { t } = useI18n();

  // Templates are built per-render so the active locale wins. The id,
  // icon, and category never change — only the three text fields do.
  const defaultTemplates = useMemo<QuickTemplate[]>(() => {
    const items = t.quickTemplates.items;
    return [
      {
        id: "company-website",
        ...items.companyWebsite,
        icon: <GlobeIcon className="size-4" />,
        category: "website",
      },
      {
        id: "personal-portfolio",
        ...items.personalPortfolio,
        icon: <FileTextIcon className="size-4" />,
        category: "website",
      },
      {
        id: "ecommerce-page",
        ...items.ecommercePage,
        icon: <ShoppingCartIcon className="size-4" />,
        category: "website",
      },
      {
        id: "dashboard",
        ...items.dashboard,
        icon: <BarChart3Icon className="size-4" />,
        category: "app",
      },
      {
        id: "crm-system",
        ...items.crmSystem,
        icon: <UsersIcon className="size-4" />,
        category: "app",
      },
      {
        id: "todo-app",
        ...items.todoApp,
        icon: <CalendarIcon className="size-4" />,
        category: "tool",
      },
      {
        id: "chat-interface",
        ...items.chatInterface,
        icon: <MessageSquareIcon className="size-4" />,
        category: "app",
      },
      {
        id: "search-page",
        ...items.searchPage,
        icon: <SearchIcon className="size-4" />,
        category: "website",
      },
      {
        id: "api-docs",
        ...items.apiDocs,
        icon: <DatabaseIcon className="size-4" />,
        category: "website",
      },
      {
        id: "puzzle-game",
        ...items.puzzleGame,
        icon: <SparklesIcon className="size-4" />,
        category: "game",
      },
    ];
  }, [t]);

  return (
    <div className={cn("flex flex-col h-full", className)}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-default">
        <LayoutTemplateIcon className="size-4 text-primary" />
        <span className="text-sm font-medium">{t.quickTemplates.title}</span>
      </div>

      {/* Templates grid */}
      <div className="flex-1 overflow-auto p-3">
        <div className="grid grid-cols-1 gap-2">
          {defaultTemplates.map((template) => (
            <button
              key={template.id}
              onClick={() => onSelect(template)}
              className={cn(
                "group flex items-start gap-3 p-3 rounded-lg border border-border-default",
                "hover:border-primary/30 hover:bg-primary/5 transition-colors duration-base",
                "text-left",
              )}
            >
              <div
                className={cn(
                  "flex size-8 items-center justify-center rounded-lg shrink-0",
                  categoryColors[template.category],
                )}
              >
                {template.icon}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground group-hover:text-primary transition-colors">
                    {template.name}
                  </span>
                  <ArrowRightIcon className="size-3.5 text-muted-foreground/50 group-hover:text-primary/70 group-hover:translate-x-0.5 transition-all" />
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                  {template.description}
                </p>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Footer hint */}
      <div className="px-3 py-2 border-t border-border-default">
        <p className="text-xs text-muted-foreground/60 text-center">
          {t.quickTemplates.hint}
        </p>
      </div>
    </div>
  );
}
