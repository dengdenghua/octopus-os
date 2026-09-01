import { useCallback, useEffect, useState } from "react";
import { UserIcon, Loader2Icon } from "lucide-react";
import { toast } from "sonner";
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface Template {
  name: string;
  display_name: string;
  description: string;
  icon: string;
  category: string;
  tags: string[];
}

export function PersonalitySelector({
  agentName,
  className,
}: {
  agentName?: string;
  className?: string;
}) {
  const { t } = useI18n();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [applying, setApplying] = useState<string | null>(null);

  // UI-side localization for backend-supplied template metadata. Keeping the
  // mapping here means the backend can keep English-only `display_name` /
  // `description` / `category` strings (which also feed into LLM prompts via
  // soul_content), while the UI reads from i18n first and falls back to the
  // English source when a translation is missing. Keys mirror the backend's
  // BUILTIN_TEMPLATES.name values.
  const localizeName = (tpl: Template): string => {
    const overrides = t.personality?.templateNames ?? {};
    return overrides[tpl.name] ?? tpl.display_name;
  };
  const localizeDescription = (tpl: Template): string => {
    const overrides = t.personality?.templateDescriptions ?? {};
    return overrides[tpl.name] ?? tpl.description;
  };
  const localizeCategory = (tpl: Template): string => {
    const overrides = t.personality?.categories ?? {};
    return overrides[tpl.category] ?? tpl.category;
  };

  useEffect(() => {
    let cancelled = false;
    // We don't surface this failure as a toast · the selector is a
    // secondary UI element and renders nothing when the list is empty
    // (`templates.length === 0` → `return null` below). But we DO want
    // the error in the console so devtools isn't completely silent when
    // /api/personality/templates 500s or returns bad JSON.
    (async () => {
      try {
        const res = await fetch(
          `${getBackendBaseURL()}/api/personality/templates`,
          { headers: authHeaders() },
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status} ${res.statusText}`);
        }
        const data = (await res.json()) as { templates?: Template[] };
        if (!cancelled) setTemplates(data.templates ?? []);
      } catch (err) {
        if (!cancelled) {
          console.warn("[personality] failed to load templates:", err);
          setTemplates([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const applyTemplate = useCallback(
    async (name: string) => {
      setApplying(name);
      try {
        const url = `${getBackendBaseURL()}/api/personality/templates/${name}/apply${agentName ? `?agent_name=${agentName}` : ""}`;
        const res = await fetch(url, {
          method: "POST",
          headers: authHeaders(),
        });
        if (res.ok) {
          toast.success(
            t.personality?.applySuccess?.(name) ??
              `Template "${name}" applied successfully`,
          );
        } else {
          const err = await res.json().catch(() => ({}));
          toast.error(
            (err as { detail?: string }).detail ||
              t.personality?.applyFailed ||
              "Failed to apply template",
          );
        }
      } catch {
        toast.error(t.personality?.applyFailed || "Failed to apply template");
      } finally {
        setApplying(null);
      }
    },
    [agentName, t],
  );

  if (templates.length === 0) return null;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="text-muted-foreground text-xs font-medium">
        {t.personality?.templatesTitle ?? "Personality Templates"}
      </div>
      <div className="grid gap-1.5">
        {templates.map((tpl) => (
          <button
            key={tpl.name}
            type="button"
            onClick={() => void applyTemplate(tpl.name)}
            disabled={applying !== null}
            className={cn(
              "flex items-start gap-2 rounded-lg border p-2.5 text-left transition-colors",
              "hover:bg-muted/50 disabled:opacity-50",
            )}
          >
            <UserIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-medium">{localizeName(tpl)}</span>
                <span className="text-muted-foreground rounded bg-muted px-1 py-0.5 text-xs">
                  {localizeCategory(tpl)}
                </span>
              </div>
              <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
                {localizeDescription(tpl)}
              </p>
            </div>
            {applying === tpl.name ? (
              <Loader2Icon className="text-muted-foreground size-3.5 animate-spin" />
            ) : null}
          </button>
        ))}
      </div>
    </div>
  );
}
