import {
  FileTextIcon,
  LinkIcon,
  PaperclipIcon,
  PlusIcon,
  SearchIcon,
  Trash2Icon,
} from "lucide-react";
import type { RefObject } from "react";

import type { Translations } from "@/core/i18n/locales";
import type { ResearchSourceKind } from "@/core/research/api";
import { cn } from "@/lib/utils";
import {
  RESEARCH_SOURCE_OPTIONS,
  type ComposerResearchMaterial,
  parseComposerUrls,
} from "./helpers";

interface ResearchSourcePickerProps {
  researchUrlText: string;
  setResearchUrlText: (value: string) => void;
  researchTextTitle: string;
  setResearchTextTitle: (value: string) => void;
  researchTextBody: string;
  setResearchTextBody: (value: string) => void;
  researchNote: string;
  setResearchNote: (value: string) => void;
  researchMaterials: ComposerResearchMaterial[];
  researchSources: ResearchSourceKind[];
  maxSearches: number;
  setMaxSearches: (value: number) => void;
  uploadingMaterials: boolean;
  materialError: string | null;
  setResearchConfigOpen: (value: boolean) => void;
  isBusy: boolean;
  status?: string;
  t: Translations;
  fileInputRef: RefObject<HTMLInputElement | null>;
  addUrlMaterial: () => void;
  addTextMaterial: () => void;
  toggleMaterial: (id: string) => void;
  removeMaterial: (id: string) => void;
  toggleResearchSource: (kind: ResearchSourceKind) => void;
}

export function ResearchSourcePicker({
  researchUrlText,
  setResearchUrlText,
  researchTextTitle,
  setResearchTextTitle,
  researchTextBody,
  setResearchTextBody,
  researchNote,
  setResearchNote,
  researchMaterials,
  researchSources,
  maxSearches,
  setMaxSearches,
  uploadingMaterials,
  materialError,
  setResearchConfigOpen,
  isBusy,
  status,
  t,
  fileInputRef,
  addUrlMaterial,
  addTextMaterial,
  toggleMaterial,
  removeMaterial,
  toggleResearchSource,
}: ResearchSourcePickerProps) {
  const parsedResearchUrls = parseComposerUrls(researchUrlText);
  const disabled = isBusy || status === "streaming";

  return (
    <div className="absolute bottom-11 left-2 right-2 z-30 max-h-[min(70vh,560px)] overflow-y-auto rounded-lg border border-border-default bg-popover px-3 py-3 shadow-[var(--shadow-xs)]">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-foreground">
          <SearchIcon className="size-4 text-primary" />
          <span>{t.chatInputBox.deepResearchConfig}</span>
          {researchMaterials.length > 0 && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-normal text-muted-foreground">
              {researchMaterials.filter((item) => item.enabled).length}{" "}
              {t.chatInputBox.materials.toLowerCase()}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setResearchConfigOpen(false)}
          className="px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
        >
          {t.chatInputBox.collapse}
        </button>
      </div>
      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
        <label className="flex min-w-0 items-center gap-2 border border-border-default bg-background/40 px-2 py-1.5">
          <LinkIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <input
            value={researchUrlText}
            onChange={(event) => setResearchUrlText(event.target.value)}
            disabled={disabled}
            placeholder="https://example.com, https://..."
            className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/75 disabled:opacity-60"
          />
        </label>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <SearchIcon className="size-3.5" />
            <input
              type="number"
              min={1}
              max={1000}
              step={10}
              value={maxSearches}
              onChange={(event) => {
                const next = Number.parseInt(event.target.value, 10);
                if (Number.isFinite(next)) {
                  setMaxSearches(Math.min(1000, Math.max(1, next)));
                }
              }}
              disabled={disabled}
              className="h-7 w-16 rounded-lg border border-border-default bg-background/50 px-1.5 text-center text-xs text-foreground outline-none"
            />
          </label>
        </div>
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
        <input
          value={researchNote}
          onChange={(event) => setResearchNote(event.target.value)}
          disabled={disabled}
          placeholder={t.chatInputBox.materialNote}
          className="h-8 min-w-0 border border-border-default bg-background/40 px-2 text-xs outline-none placeholder:text-muted-foreground/75 disabled:opacity-60"
        />
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={addUrlMaterial}
            disabled={!parsedResearchUrls.length || disabled}
            className="flex h-8 items-center gap-1 border border-border-default px-2 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
          >
            <PlusIcon className="size-3.5" />
            {t.chatInputBox.url}
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
            className="flex h-8 items-center gap-1 border border-border-default px-2 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
          >
            {uploadingMaterials ? (
              <SearchIcon className="size-3.5 animate-pulse" />
            ) : (
              <PaperclipIcon className="size-3.5" />
            )}
            {t.chatInputBox.file}
          </button>
        </div>
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto]">
        <input
          value={researchTextTitle}
          onChange={(event) => setResearchTextTitle(event.target.value)}
          disabled={disabled}
          placeholder={t.chatInputBox.textTitle}
          className="h-8 min-w-0 border border-border-default bg-background/40 px-2 text-xs outline-none placeholder:text-muted-foreground/75 disabled:opacity-60"
        />
        <input
          value={researchTextBody}
          onChange={(event) => setResearchTextBody(event.target.value)}
          disabled={disabled}
          placeholder={t.chatInputBox.pasteTextMaterial}
          className="h-8 min-w-0 border border-border-default bg-background/40 px-2 text-xs outline-none placeholder:text-muted-foreground/75 disabled:opacity-60"
        />
        <button
          type="button"
          onClick={addTextMaterial}
          disabled={!researchTextBody.trim() || disabled}
          className="flex h-8 items-center gap-1 border border-border-default px-2 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
        >
          <FileTextIcon className="size-3.5" />
          {t.chatInputBox.text}
        </button>
      </div>
      {materialError && (
        <div className="mt-2 text-xs text-destructive">{materialError}</div>
      )}
      {researchMaterials.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {researchMaterials.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-2 rounded-lg border border-border-default bg-background/35 px-2 py-1.5"
            >
              <input
                type="checkbox"
                checked={item.enabled}
                onChange={() => toggleMaterial(item.id)}
                disabled={disabled}
                className="size-3.5"
                title={t.chatInputBox.toggleMaterial}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">
                  {item.material.title ||
                    item.material.url ||
                    item.material.path ||
                    "Material"}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {item.material.kind}
                  {item.material.notes ? ` · ${item.material.notes}` : ""}
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeMaterial(item.id)}
                disabled={disabled}
                className="flex size-6 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
                title={t.chatInputBox.removeMaterial}
              >
                <Trash2Icon className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {RESEARCH_SOURCE_OPTIONS.map((source) => {
          const active = researchSources.includes(source.kind);
          return (
            <button
              key={source.kind}
              type="button"
              onClick={() => toggleResearchSource(source.kind)}
              disabled={disabled}
              className={cn(
                "rounded-lg border px-2 py-1 text-xs font-medium transition-colors",
                active
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-border-default text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              {source.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
