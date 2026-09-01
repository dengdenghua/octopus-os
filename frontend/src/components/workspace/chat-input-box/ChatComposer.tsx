import {
  BookOpenIcon,
  ExternalLinkIcon,
  FileIcon,
  FlagIcon,
  FolderKanbanIcon,
  ImageIcon,
  LightbulbIcon,
  ListTodoIcon,
  Loader2Icon,
  MonitorIcon,
  PuzzleIcon,
  SearchIcon,
  SendHorizontalIcon,
  Settings2Icon,
  ZapIcon,
  MapIcon,
  PaperclipIcon,
  PlusIcon,
  SlidersHorizontalIcon,
  SquareIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useMentionAutocomplete } from "../mention-autocomplete";

import { swallow } from "@/core/utils/log";
import { currentActorId } from "@/core/auth/api";
import {
  consumeComposerImageEntries,
  rememberLastComposerTarget,
} from "@/core/composer-image-inbox";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { usePlugins } from "@/core/plugins/hooks";
import { useSkills } from "@/core/skills/hooks";
import {
  loadComposerDraft,
  saveComposerDraft,
} from "@/core/threads/composer-draft";
import { EvolutionIndicator } from "../evolution-indicator";
import { ModelPicker, type PickerModel } from "../model-picker";
import { CoderEngineControl } from "../coder-engine-control";
import { PreviewRefreshIndicator } from "../preview-refresh-indicator";
import { tryLocalSlash } from "../local-slash-dispatch";
import { useSlashTypeahead } from "../use-slash-typeahead";
import { ContextCompressor } from "../context-compressor";
import { PermissionIndicator } from "../permission-indicator";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { normalizePermissionMode } from "@/core/permissions";
import { captureComputerAppshot } from "@/core/computer/api";
import { uploadFiles, useAttachmentUploads } from "@/core/uploads";
import type { ResearchMaterial, ResearchSourceKind } from "@/core/research/api";
import {
  addComposerCapabilityRef,
  parseComposerDraft,
  removeComposerCapabilityRef,
  serializeComposerDraft,
  setComposerDraftMode,
  type ComposerCapabilityRef,
  type ComposerCommandMode,
} from "@/core/threads/composer-capability-refs";

import type { ChatInputBoxProps } from "../chat-input-box";
import {
  DEFAULT_RESEARCH_SOURCES,
  appendReferencedFiles,
  dataUrlToFile,
  fileBasename,
  imageFileKey,
  parseComposerUrls,
  pendingFileKey,
  uploadFileKey,
  type ComposerImageInjectionDetail,
  type ComposerResearchMaterial,
  type PendingContextFile,
  type WorkspaceFileInjectionDetail,
} from "./helpers";
import { MentionPicker } from "./MentionPicker";
import { FileAttachment } from "./FileAttachment";
import { ResearchSourcePicker } from "./ResearchSourcePicker";
import { AutomationTargetControl } from "./AutomationTargetControl";
import { FileTree } from "../file-tree";

/**
 * The main chat composer card: textarea + file attachments + deep-research
 * picker + footer (tools menu, model picker, send/stop). Owns all the
 * draft / image / file / research state. The status strip (workdir/mode
 * selectors) is rendered separately by the parent.
 */
export function ChatComposer({
  status,
  disabled,
  modelName,
  mode = "react",
  threadId,
  mentionMembers,
  responseModeControl,
  automationTarget,
  onAutomationTargetChange,
  isGroupConversation = false,
  groupTaskStrategy = "auto",
  onGroupTaskStrategyChange,
  projectCapabilityEnabled = false,
  onProjectCapabilityAction,
  onSwitchPanel,
  workDir,
  placeholder,
  autoFocus,
  defaultValue = "",
  contextTokens = 0,
  maxContextTokens = 128000,
  isCompressingContext = false,
  onCompressContext,
  allowAgentModes = false,
  showInspirationToggle = false,
  permissionMode,
  reasoningEffort,
  modelProfileControl = false,
  executionEngine = "echo",
  onPermissionModeChange,
  onReasoningEffortChange,
  onModelChange,
  onModelSwitchNotice,
  onModeChange,
  onDeepResearch,
  onSubmit,
  onStop,
  isUploading = false,
  className,
}: ChatInputBoxProps) {
  const { t } = useI18n();
  const { models } = useModels();
  const [draft, setDraft] = useState(
    () =>
      // A per-thread draft survives thread switches and reloads. defaultValue
      // (external injection, e.g. "retry this message") wins when present.
      defaultValue || (loadComposerDraft(threadId) ?? ""),
  );
  // Restore the stored draft when the composer moves to a different thread
  // (the component is reused across navigation).
  const prevDraftThreadRef = useRef(threadId);
  useEffect(() => {
    if (prevDraftThreadRef.current === threadId) return;
    prevDraftThreadRef.current = threadId;
    setDraft(loadComposerDraft(threadId) ?? "");
  }, [threadId]);
  // Persist the draft (debounced) so a reload never loses half-typed text.
  useEffect(() => {
    const timer = setTimeout(() => saveComposerDraft(threadId, draft), 300);
    return () => clearTimeout(timer);
  }, [threadId, draft]);
  const [researchUrlText, setResearchUrlText] = useState("");
  const [researchTextTitle, setResearchTextTitle] = useState("");
  const [researchTextBody, setResearchTextBody] = useState("");
  const [researchNote, setResearchNote] = useState("");
  const [researchMaterials, setResearchMaterials] = useState<
    ComposerResearchMaterial[]
  >([]);
  const [researchSources, setResearchSources] = useState<ResearchSourceKind[]>(
    DEFAULT_RESEARCH_SOURCES,
  );
  const [maxSearches, setMaxSearches] = useState(274);
  const [uploadingMaterials, setUploadingMaterials] = useState(false);
  const [materialError, setMaterialError] = useState<string | null>(null);
  const [researchConfigOpen, setResearchConfigOpen] = useState(false);
  const submitLockRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const [toolsMenuOpen, setToolsMenuOpen] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  // Images attached to the next message via paste / drop / file-picker.
  // Stored separately from research materials so they ride the
  // multimodal `images` channel into sendMessage rather than going
  // through the artifact-upload pipeline used by research files.
  const [pendingImages, setPendingImages] = useState<File[]>([]);
  const [pendingImagePreviews, setPendingImagePreviews] = useState<
    Record<string, string>
  >({});
  const [pendingImageSources, setPendingImageSources] = useState<
    Record<string, string>
  >({});
  const [pendingFiles, setPendingFiles] = useState<PendingContextFile[]>([]);
  const [capturingAppshot, setCapturingAppshot] = useState(false);
  const contextFileInputRef = useRef<HTMLInputElement | null>(null);
  const {
    plugins,
    isLoading: pluginsLoading,
    error: pluginsError,
  } = usePlugins({ enabled: toolsMenuOpen });
  const {
    skills,
    isLoading: skillsLoading,
    error: skillsError,
  } = useSkills({ enabled: toolsMenuOpen });
  // Attachments upload the moment they land in the composer, not at send.
  // The ref lets the removal handlers reach the API without taking it as a
  // dependency — they run inside `setState` updaters.
  const attachmentUploads = useAttachmentUploads(threadId);
  const attachmentUploadsRef = useRef(attachmentUploads);
  attachmentUploadsRef.current = attachmentUploads;

  const parsedComposerDraft = parseComposerDraft(draft);
  const activeComposerMode = parsedComposerDraft.mode;
  const activeLongTaskMode =
    activeComposerMode === "goal" || activeComposerMode === "project"
      ? activeComposerMode
      : undefined;
  const composerRefs = parsedComposerDraft.refs;
  const visibleDraft = parsedComposerDraft.body;
  const setVisibleDraft = useCallback((body: string) => {
    setDraft((current) => {
      const parsed = parseComposerDraft(current);
      return serializeComposerDraft({ ...parsed, body });
    });
  }, []);

  // Slash-command typeahead · shared hook (see use-slash-typeahead).
  // Returns the picker JSX + a keydown handler that we call FIRST in
  // our own onKeyDown so navigation keys (↑↓/Tab/Enter/Esc) are
  // consumed before the composer's default Enter-to-submit fires.
  const { picker: slashPicker, handleKeyDown: handleSlashKeyDown } =
    useSlashTypeahead({
      draft: visibleDraft,
      setDraft: setVisibleDraft,
      focusTextarea: () => textareaRef.current?.focus(),
    });

  const {
    isOpen: mentionOpen,
    items: mentionItems,
    selectedIndex: mentionSelectedIndex,
    isLoading: isLoadingMention,
    mentionQuery,
    handleKeyDown: handleMentionKeyDown,
    selectItem: selectMentionItem,
  } = useMentionAutocomplete({
    value: visibleDraft,
    onChange: setVisibleDraft,
    workDir,
    threadId,
    actor: currentActorId(),
    members: mentionMembers,
  });

  const pickerModels: PickerModel[] = useMemo(
    () =>
      models.map((m) => ({
        id: m.id,
        name: m.name || m.id,
        display_name: m.display_name || m.name || m.id,
        source_display_name: m.source_display_name,
        description: m.description,
        entry_id: m.entry_id,
        selection_id: m.selection_id,
        model: m.model,
        provider: m.provider,
        reasoning_efforts: m.reasoning_efforts,
        context_window: m.context_window,
        supports_thinking: m.supports_thinking,
        supports_vision: m.supports_vision,
        supports_tool_use: m.supports_tool_use,
        supports_reasoning_effort: m.supports_reasoning_effort,
        // The picker folds a ``::1m`` row into its base model, which it can
        // only detect from context_profile. Dropping the field here made the
        // long-context variant render as a second, identically-labelled row.
        context_profile: m.context_profile,
      })),
    [models],
  );
  const selectedModel =
    pickerModels.find(
      (m) =>
        m.selection_id === modelName ||
        m.entry_id === modelName ||
        m.name === modelName ||
        m.model === modelName,
    ) ??
    (modelName
      ? { name: modelName, display_name: modelName }
      : pickerModels[0]);
  const applyNativeModelChange = useCallback(
    (name: string) => {
      onModelChange?.(name);
      const model = pickerModels.find((candidate) =>
        [
          candidate.selection_id,
          candidate.entry_id,
          candidate.name,
          candidate.model,
          candidate.id,
        ].includes(name),
      );
      onModelSwitchNotice?.(model?.display_name || model?.name || name);
    },
    [onModelChange, onModelSwitchNotice, pickerModels],
  );
  const resolvedPermissionMode = normalizePermissionMode(permissionMode);
  const canUseDeepResearch =
    allowAgentModes && mode === "deep" && !!onDeepResearch;
  const isDeepResearchMode = canUseDeepResearch && researchConfigOpen;
  // Attachments have to land before the message can go: sending mid-transfer
  // is what produced a picture the model never received. A failed attachment
  // blocks too — silently dropping it is worse than making the user decide.
  const isBusy =
    disabled ||
    uploadingMaterials ||
    isUploading ||
    attachmentUploads.isUploading ||
    attachmentUploads.hasFailed;
  const sendLabel = t.chatInputBox.send;
  const stopLabel = t.chatInputBox.stop;
  const parsedResearchUrls = useMemo(
    () => parseComposerUrls(researchUrlText),
    [researchUrlText],
  );
  // Only surface the context meter once it's actually filling up — showing
  // "0%" on an empty thread is just noise. Appears at ≥50% (when compressing
  // starts to matter), or while a compression is running.
  const showContextCompressor =
    maxContextTokens > 0 &&
    (isCompressingContext || contextTokens / maxContextTokens >= 0.5);
  const sendableDraftText = visibleDraft.trim();
  const enabledPlugins = useMemo(
    () =>
      plugins
        .filter((plugin) => plugin.enabled)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [plugins],
  );
  const pluginNameById = useMemo(
    () => new Map(plugins.map((plugin) => [plugin.id, plugin.name])),
    [plugins],
  );
  const enabledSkills = useMemo(() => {
    const needle = skillSearch.trim().toLowerCase();
    return skills
      .filter((skill) => skill.enabled)
      .filter(
        (skill) =>
          !needle ||
          skill.name.toLowerCase().includes(needle) ||
          skill.description.toLowerCase().includes(needle),
      )
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [skillSearch, skills]);
  const hasBoundWorkDir = Boolean(workDir?.trim());
  const workspaceLabel = workDir?.trim() ? fileBasename(workDir.trim()) : "";
  const toolsMenuLabel = isGroupConversation
    ? t.chatInputBox.groupTaskAddContent
    : t.chatInputBox.composerInsertions;
  const toolsMenuTitle = automationTarget?.title?.trim()
    ? `${toolsMenuLabel} · ${automationTarget.title.trim()}`
    : toolsMenuLabel;

  useEffect(() => {
    if (!isGroupConversation) return;
    const contextChanged =
      (groupTaskStrategy === "build" && hasBoundWorkDir) ||
      (groupTaskStrategy === "develop" && !hasBoundWorkDir);
    if (contextChanged) onGroupTaskStrategyChange?.("auto");
  }, [
    groupTaskStrategy,
    hasBoundWorkDir,
    isGroupConversation,
    onGroupTaskStrategyChange,
  ]);

  useEffect(() => {
    if (!canUseDeepResearch) setResearchConfigOpen(false);
  }, [canUseDeepResearch]);

  useEffect(() => {
    const handler = (
      event: CustomEvent<{
        threadId?: string | null;
        topic?: string | null;
        text?: string | null;
      }>,
    ) => {
      const detail = event.detail;
      if (detail?.threadId && threadId && detail.threadId !== threadId) {
        return;
      }
      const nextDraft = (detail?.topic ?? detail?.text ?? "").trim();
      if (!nextDraft) return;
      setDraft(nextDraft);
      if (!allowAgentModes) {
        setTimeout(() => textareaRef.current?.focus(), 0);
        return;
      }
      setResearchConfigOpen(true);
      onModeChange?.("deep");
      setTimeout(() => textareaRef.current?.focus(), 0);
    };
    window.addEventListener(
      "echo:start-deep-research",
      handler as EventListener,
    );
    return () => {
      window.removeEventListener(
        "echo:start-deep-research",
        handler as EventListener,
      );
    };
  }, [allowAgentModes, onModeChange, threadId]);

  // A failed send hands the text back (the draft was cleared
  // optimistically on submit). Restore only when the box is still
  // empty — if the user already started retyping, theirs wins.
  useEffect(() => {
    const handler = (
      event: CustomEvent<{ threadId?: string | null; text?: string | null }>,
    ) => {
      const detail = event.detail;
      if (detail?.threadId && threadId && detail.threadId !== threadId) {
        return;
      }
      const lostText = detail?.text ?? "";
      if (!lostText) return;
      setDraft((current) => (current.trim() ? current : lostText));
      setTimeout(() => textareaRef.current?.focus(), 0);
    };
    window.addEventListener("echo:send-failed", handler as EventListener);
    return () => {
      window.removeEventListener(
        "echo:send-failed",
        handler as EventListener,
      );
    };
  }, [threadId]);

  const addPendingWorkspaceFile = useCallback(
    (detail: WorkspaceFileInjectionDetail) => {
      const rawPath = detail.path?.trim();
      if (!rawPath) return;
      const normalizedWorkDir =
        detail.workDir?.trim() || workDir?.trim() || null;
      const nextFile: PendingContextFile = {
        id: pendingFileKey(rawPath, normalizedWorkDir),
        name: fileBasename(rawPath),
        path: rawPath,
        workDir: normalizedWorkDir,
        sourceLabel: detail.sourceLabel?.trim() || null,
      };
      setPendingFiles((current) => {
        if (current.some((file) => file.id === nextFile.id)) return current;
        return [...current, nextFile];
      });
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    },
    [workDir],
  );

  useEffect(() => {
    const handler = (event: CustomEvent<WorkspaceFileInjectionDetail>) => {
      const detail = event.detail;
      if (detail?.threadId && threadId && detail.threadId !== threadId) {
        return;
      }
      addPendingWorkspaceFile(detail ?? {});
    };
    window.addEventListener("echo:open-file", handler as EventListener);
    return () => {
      window.removeEventListener("echo:open-file", handler as EventListener);
    };
  }, [addPendingWorkspaceFile, threadId]);

  const handleSubmit = useCallback(async () => {
    const text = draft.trim();
    const sendableText = parseComposerDraft(text).body.trim();
    const hasImages = pendingImages.length > 0;
    const hasFiles = pendingFiles.length > 0;
    if (
      (!sendableText && !hasImages && !hasFiles) ||
      isBusy ||
      (status === "streaming" && (hasImages || hasFiles))
    ) {
      return;
    }
    if (submitLockRef.current) return;
    submitLockRef.current = true;
    const releaseSubmitLock = () => {
      window.setTimeout(() => {
        submitLockRef.current = false;
      }, 250);
    };
    // Fast path: client-side slash commands (mode/model/permission/
    // compact/settings) resolve locally with no LLM round-trip.
    // Falls through for anything not handled here.
    if (
      tryLocalSlash(text, {
        onModeChange: onModeChange ? (mode) => onModeChange(mode) : undefined,
        // The shared server-side model profile owns its model namespace.
        onModelChange: modelProfileControl ? undefined : applyNativeModelChange,
        onPermissionModeChange,
        onCompact: onCompressContext
          ? () => {
              void onCompressContext();
            }
          : undefined,
        onSwitchPanel,
      })
    ) {
      setDraft("");
      releaseSubmitLock();
      return;
    }
    if (isDeepResearchMode) {
      const localFileMaterials = pendingFiles
        .filter((file) => !file.file)
        .map((file) => ({
          kind: "file" as const,
          title: file.name,
          path: file.path,
          notes: file.workDir ? `workspace: ${file.workDir}` : undefined,
        }));
      const pendingBrowserFiles = pendingFiles
        .map((file) => file.file)
        .filter((file): file is File => file instanceof File);
      let uploadedFileMaterials: Partial<ResearchMaterial>[] = [];
      if (pendingBrowserFiles.length > 0) {
        if (!threadId) {
          setMaterialError(t.chatInputBox.startThreadBeforeUpload);
          releaseSubmitLock();
          return;
        }
        setUploadingMaterials(true);
        setMaterialError(null);
        try {
          const result = await uploadFiles(threadId, pendingBrowserFiles);
          uploadedFileMaterials = result.files.map((file) => ({
            kind: "file" as const,
            title: file.filename,
            path: file.path,
            notes: `uploaded file · ${file.size} bytes`,
          }));
        } catch (err) {
          swallow(err);
          setMaterialError(t.chatInputBox.uploadFailed);
          releaseSubmitLock();
          return;
        } finally {
          setUploadingMaterials(false);
        }
      }
      let result: void | boolean;
      try {
        result = await onDeepResearch(
          appendReferencedFiles(text, pendingFiles),
          {
            urls: parsedResearchUrls,
            materials: [
              ...researchMaterials
                .filter((item) => item.enabled)
                .map((item) => item.material),
              ...localFileMaterials,
              ...uploadedFileMaterials,
            ],
            sourceKinds: researchSources,
            maxSearches,
          },
        );
      } finally {
        releaseSubmitLock();
      }
      if (result !== false) {
        setDraft(
          activeLongTaskMode
            ? serializeComposerDraft({
                mode: activeLongTaskMode,
                refs: [],
                body: "",
              })
            : "",
        );
        setPendingFiles([]);
      }
      return;
    }
    const browserUploadFiles = pendingFiles
      .map((file) => file.file)
      .filter((file): file is File => file instanceof File);
    const completedUploads = attachmentUploads.completed();
    try {
      onSubmit?.({
        text: appendReferencedFiles(text, pendingFiles),
        images: pendingImages.length > 0 ? pendingImages : undefined,
        files: browserUploadFiles.length > 0 ? browserUploadFiles : undefined,
        // Already on the server — the send path matches these by filename and
        // skips re-uploading the same bytes.
        uploaded: completedUploads.length > 0 ? completedUploads : undefined,
      });
    } finally {
      releaseSubmitLock();
    }
    setDraft(
      activeLongTaskMode
        ? serializeComposerDraft({
            mode: activeLongTaskMode,
            refs: [],
            body: "",
          })
        : "",
    );
    attachmentUploads.reset();
    if (pendingFiles.length > 0) {
      setPendingFiles([]);
      if (contextFileInputRef.current) contextFileInputRef.current.value = "";
    }
    if (pendingImages.length > 0) {
      setPendingImages([]);
      setPendingImagePreviews({});
      setPendingImageSources({});
    }
  }, [
    draft,
    isBusy,
    status,
    isDeepResearchMode,
    onDeepResearch,
    onSubmit,
    onSwitchPanel,
    parsedResearchUrls,
    researchMaterials,
    researchSources,
    maxSearches,
    onModeChange,
    applyNativeModelChange,
    modelProfileControl,
    onPermissionModeChange,
    onCompressContext,
    pendingImages,
    pendingFiles,
    attachmentUploads,
    t,
    threadId,
    activeLongTaskMode,
  ]);

  const addMaterial = useCallback((material: Partial<ResearchMaterial>) => {
    setResearchMaterials((current) => [
      ...current,
      {
        id: `mat_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        enabled: true,
        material,
      },
    ]);
  }, []);

  const addUrlMaterial = useCallback(() => {
    const urls = parsedResearchUrls;
    if (!urls.length) return;
    urls.forEach((url) =>
      addMaterial({
        kind: "url",
        title: url,
        url,
        notes: researchNote.trim() || undefined,
      }),
    );
    setResearchUrlText("");
    setResearchNote("");
    setMaterialError(null);
  }, [addMaterial, researchNote, parsedResearchUrls]);

  const addTextMaterial = useCallback(() => {
    const text = researchTextBody.trim();
    if (!text) return;
    addMaterial({
      kind: "text",
      title: researchTextTitle.trim() || text.slice(0, 48),
      text,
      notes: researchNote.trim() || undefined,
    });
    setResearchTextTitle("");
    setResearchTextBody("");
    setResearchNote("");
    setMaterialError(null);
  }, [addMaterial, researchNote, researchTextBody, researchTextTitle]);

  const handleUploadMaterials = useCallback(
    async (files: FileList | null) => {
      if (!files?.length) return;
      if (!threadId) {
        setMaterialError(t.chatInputBox.startThreadBeforeUpload);
        return;
      }
      setUploadingMaterials(true);
      setMaterialError(null);
      try {
        const result = await uploadFiles(threadId, Array.from(files));
        result.files.forEach((file) =>
          addMaterial({
            kind: "file",
            title: file.filename,
            path: file.path,
            notes: researchNote.trim() || `uploaded file · ${file.size} bytes`,
          }),
        );
        setResearchNote("");
      } catch (err) {
        swallow(err);
        setMaterialError(t.chatInputBox.uploadFailed);
      } finally {
        setUploadingMaterials(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [addMaterial, researchNote, t, threadId],
  );

  const toggleMaterial = useCallback((id: string) => {
    setResearchMaterials((current) =>
      current.map((item) =>
        item.id === id ? { ...item, enabled: !item.enabled } : item,
      ),
    );
  }, []);

  const removeMaterial = useCallback((id: string) => {
    setResearchMaterials((current) => current.filter((item) => item.id !== id));
  }, []);

  const insertCommandModeMarker = useCallback((mode: ComposerCommandMode) => {
    setDraft((current) => setComposerDraftMode(current, mode));
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  }, []);

  const clearLongTaskMode = useCallback(() => {
    setDraft((current) => setComposerDraftMode(current, undefined));
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  }, []);

  const insertCapabilityRef = useCallback((ref: ComposerCapabilityRef) => {
    setDraft((current) => addComposerCapabilityRef(current, ref));
    setToolsMenuOpen(false);
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  }, []);

  const insertBrowserSurface = useCallback(() => {
    insertCapabilityRef({ type: "surface", id: "browser" });
  }, [insertCapabilityRef]);

  const insertChromeSurface = useCallback(() => {
    insertCapabilityRef({ type: "surface", id: "chrome" });
  }, [insertCapabilityRef]);

  const removeCapabilityRef = useCallback((ref: ComposerCapabilityRef) => {
    setDraft((current) => removeComposerCapabilityRef(current, ref));
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  }, []);

  const openHubCatalog = useCallback(
    (tab: "plugins" | "skills", view?: "installed" | "all") => {
      const params = new URLSearchParams({ surface: "chat", tab });
      if (view) params.set("view", view);
      window.location.hash = `#/workspace/agents?${params.toString()}`;
      setToolsMenuOpen(false);
    },
    [],
  );

  const toggleResearchSource = useCallback((kind: ResearchSourceKind) => {
    setResearchSources((current) => {
      if (current.includes(kind)) {
        const next = current.filter((item) => item !== kind);
        return next.length > 0 ? next : current;
      }
      return [...current, kind];
    });
  }, []);

  // ── Image attachments (paste / drop / picker) ─────────────────
  // The composer accepts images via three paths and feeds them all
  // through the same `pendingImages` slot which rides into onSubmit.
  // Previews are stored as object URLs so we can revoke them on
  // cleanup; previews are keyed by `name|size` so the UI keeps
  // referential stability across renders.
  const addPendingImages = useCallback(
    (
      files: File[] | FileList | null | undefined,
      options?: { sourceLabel?: string | null },
    ) => {
      if (!files) return;
      const arr = Array.from(files).filter((file) =>
        file.type.toLowerCase().startsWith("image/"),
      );
      if (arr.length === 0) return;
      const sourceLabel = options?.sourceLabel?.trim() || "图片";
      // Being in the composer *is* being uploaded: start the transfer now so
      // the chip can show real progress and send can wait on it.
      attachmentUploads.start(
        arr.map((file) => ({ key: uploadFileKey(file), file })),
      );
      setPendingImages((current) => {
        const known = new Set(current.map((file) => imageFileKey(file)));
        const next = [...current];
        for (const file of arr) {
          const key = imageFileKey(file);
          if (!known.has(key)) {
            next.push(file);
            known.add(key);
          }
        }
        return next;
      });
      setPendingImagePreviews((current) => {
        const next = { ...current };
        for (const file of arr) {
          const key = imageFileKey(file);
          if (!next[key]) next[key] = URL.createObjectURL(file);
        }
        return next;
      });
      setPendingImageSources((current) => {
        const next = { ...current };
        for (const file of arr) {
          const key = imageFileKey(file);
          if (!next[key]) next[key] = sourceLabel;
        }
        return next;
      });
    },
    [attachmentUploads],
  );
  const removePendingImage = useCallback((index: number) => {
    setPendingImages((current) => {
      const removed = current[index];
      if (!removed) return current;
      const key = imageFileKey(removed);
      attachmentUploadsRef.current?.remove(uploadFileKey(removed));
      setPendingImagePreviews((prev) => {
        const url = prev[key];
        if (url) URL.revokeObjectURL(url);
        const { [key]: _omit, ...rest } = prev;
        return rest;
      });
      setPendingImageSources((prev) => {
        const { [key]: _omit, ...rest } = prev;
        return rest;
      });
      return current.filter((_, i) => i !== index);
    });
  }, []);

  const addPendingUploadFiles = useCallback(
    (files: File[] | FileList | null | undefined) => {
      if (!files) return;
      const arr = Array.from(files);
      if (arr.length === 0) return;
      attachmentUploads.start(
        arr.map((file) => ({ key: uploadFileKey(file), file })),
      );
      setPendingFiles((current) => {
        const known = new Set(current.map((file) => file.id));
        const next = [...current];
        for (const file of arr) {
          const id = uploadFileKey(file);
          if (known.has(id)) continue;
          next.push({
            id,
            name: file.name || "upload.bin",
            path: file.name || "upload.bin",
            sourceLabel: "Upload",
            file,
          });
          known.add(id);
        }
        return next;
      });
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    },
    [attachmentUploads],
  );

  const addCurrentWindowAppshot = useCallback(async () => {
    if (capturingAppshot) return;
    setCapturingAppshot(true);
    try {
      const appshot = await captureComputerAppshot({
        controlSessionId: `thread:${threadId || "new"}`,
      });
      const title =
        appshot.target.app_name || appshot.target.title || "Current window";
      const safeTitle = title.replace(/[^\p{L}\p{N}._-]+/gu, "-").slice(0, 72);
      const screenshot = await dataUrlToFile(
        appshot.screenshot.data_url || "",
        `Appshot-${safeTitle || "window"}.png`,
      );
      if (!screenshot) throw new Error("Screenshot data is unavailable");
      const semantic = new File(
        [
          JSON.stringify(
            {
              schema: appshot.schema,
              snapshot_id: appshot.snapshot_id,
              created_at: appshot.created_at,
              target: appshot.target,
              accessibility: appshot.accessibility,
            },
            null,
            2,
          ),
        ],
        `Appshot-${safeTitle || "window"}.json`,
        { type: "application/json" },
      );
      addPendingImages([screenshot], {
        sourceLabel: t.chatInputBox.appshotSource,
      });
      addPendingUploadFiles([semantic]);
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    } catch (error) {
      swallow(error);
      toast.error(
        error instanceof Error && error.message
          ? `${t.chatInputBox.appshotFailed}：${error.message}`
          : t.chatInputBox.appshotFailed,
      );
    } finally {
      setCapturingAppshot(false);
    }
  }, [
    addPendingImages,
    addPendingUploadFiles,
    capturingAppshot,
    t.chatInputBox.appshotFailed,
    t.chatInputBox.appshotSource,
    threadId,
  ]);

  const removePendingFile = useCallback((id: string) => {
    // Context files picked from the workspace have no upload entry; the hook
    // ignores unknown keys, so this is safe for both kinds of chip.
    attachmentUploadsRef.current?.remove(id);
    setPendingFiles((current) => current.filter((file) => file.id !== id));
  }, []);

  useEffect(() => {
    // Free any leftover object URLs when the component unmounts.
    return () => {
      setPendingImagePreviews((current) => {
        for (const url of Object.values(current)) URL.revokeObjectURL(url);
        return {};
      });
      setPendingImageSources({});
    };
  }, []);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash || "";
    if (!hash.startsWith("#/workspace/realtime/")) return;
    rememberLastComposerTarget(hash);
  }, [threadId]);
  useEffect(() => {
    const queued = consumeComposerImageEntries(threadId);
    if (queued.length === 0) return;
    void Promise.all(
      queued.map(async (entry) => ({
        file: await dataUrlToFile(entry.dataUrl, entry.filename),
        sourceLabel: entry.sourceLabel?.trim() || "浏览器截图",
      })),
    ).then((entries) => {
      const files = entries
        .map((entry) => entry.file)
        .filter((file): file is File => file instanceof File);
      if (files.length === 0) return;
      addPendingImages(files, {
        sourceLabel:
          entries.find((entry) => entry.file instanceof File)?.sourceLabel ||
          "浏览器截图",
      });
    });
  }, [addPendingImages, threadId]);
  // Same recovery path for failed image-only sends: if the turn never
  // started, hand the images back to the composer so the user doesn't
  // have to paste or pick the screenshot again. We also expose the
  // same lane for future host/browser-injected screenshots.
  useEffect(() => {
    const handler = (event: CustomEvent<ComposerImageInjectionDetail>) => {
      const detail = event.detail;
      if (detail?.threadId && threadId && detail.threadId !== threadId) {
        return;
      }
      const images = Array.isArray(detail?.images)
        ? detail.images.filter((file) => file instanceof File)
        : [];
      const contextText =
        event.type === "echo:inject-composer-images"
          ? detail?.text?.trim() || ""
          : "";
      if (images.length > 0) {
        addPendingImages(images, {
          sourceLabel: detail?.sourceLabel?.trim() || "浏览器截图",
        });
      }
      if (contextText) {
        setDraft((current) =>
          current.trim() ? `${current.trim()}\n\n${contextText}` : contextText,
        );
      }
      if (images.length === 0 && !contextText) return;
      setTimeout(() => textareaRef.current?.focus(), 0);
    };
    window.addEventListener("echo:send-failed", handler as EventListener);
    window.addEventListener(
      "echo:inject-composer-images",
      handler as EventListener,
    );
    return () => {
      window.removeEventListener(
        "echo:send-failed",
        handler as EventListener,
      );
      window.removeEventListener(
        "echo:inject-composer-images",
        handler as EventListener,
      );
    };
  }, [addPendingImages, threadId]);
  const handlePasteImages = useCallback(
    (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const items = event.clipboardData?.items;
      if (!items) return;
      const pasted: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item && item.kind === "file") {
          const file = item.getAsFile();
          if (file) {
            pasted.push(file);
          }
        }
      }
      if (pasted.length === 0) return;
      const imageFiles = pasted.filter((file) =>
        file.type.toLowerCase().startsWith("image/"),
      );
      const otherFiles = pasted.filter(
        (file) => !file.type.toLowerCase().startsWith("image/"),
      );
      event.preventDefault();
      if (imageFiles.length > 0) addPendingImages(imageFiles);
      if (otherFiles.length > 0) addPendingUploadFiles(otherFiles);
    },
    [addPendingImages, addPendingUploadFiles],
  );
  const handleDropFiles = useCallback(
    (event: React.DragEvent<HTMLTextAreaElement>) => {
      const files = event.dataTransfer?.files;
      if (!files || files.length === 0) return;
      const dropped = Array.from(files);
      const imageFiles = dropped.filter((file) =>
        file.type.toLowerCase().startsWith("image/"),
      );
      const otherFiles = dropped.filter(
        (file) => !file.type.toLowerCase().startsWith("image/"),
      );
      if (imageFiles.length === 0 && otherFiles.length === 0) return;
      event.preventDefault();
      if (imageFiles.length > 0) addPendingImages(imageFiles);
      if (otherFiles.length > 0) addPendingUploadFiles(otherFiles);
    },
    [addPendingImages, addPendingUploadFiles],
  );
  const handleSelectFiles = useCallback(
    (files: FileList | null | undefined) => {
      if (!files || files.length === 0) return;
      const selected = Array.from(files);
      const imageFiles = selected.filter((file) =>
        file.type.toLowerCase().startsWith("image/"),
      );
      const otherFiles = selected.filter(
        (file) => !file.type.toLowerCase().startsWith("image/"),
      );
      if (imageFiles.length > 0) addPendingImages(imageFiles);
      if (otherFiles.length > 0) addPendingUploadFiles(otherFiles);
    },
    [addPendingImages, addPendingUploadFiles],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Backspace" && visibleDraft.length === 0) {
        const lastRef = composerRefs[composerRefs.length - 1];
        if (lastRef) {
          e.preventDefault();
          setDraft((current) => removeComposerCapabilityRef(current, lastRef));
          return;
        }
        if (!activeComposerMode) return;
        e.preventDefault();
        setDraft((current) => setComposerDraftMode(current, undefined));
        return;
      }
      if (handleSlashKeyDown(e)) return;
      if (mentionOpen) {
        handleMentionKeyDown(e);
        if (e.defaultPrevented) return;
      }
      if (!mentionOpen) {
        handleMentionKeyDown(e);
      }
      if (
        e.key === "Enter" &&
        !e.shiftKey &&
        !e.nativeEvent.isComposing &&
        !e.defaultPrevented
      ) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [
      activeComposerMode,
      composerRefs,
      handleSubmit,
      handleSlashKeyDown,
      mentionOpen,
      handleMentionKeyDown,
      visibleDraft.length,
    ],
  );

  return (
    <div
      data-testid="chat-composer"
      className={cn(
        "group relative",
        "rounded-xl border border-border-subtle bg-background/90 shadow-none backdrop-blur-sm",
        "transition-[background-color,border-color,box-shadow] duration-base ease-out",
        "hover:border-border-default",
        "focus-within:border-primary/30 focus-within:shadow-[0_0_0_3px_rgba(138,127,255,0.08)]",
        className,
      )}
    >
      <div className="relative">
        {slashPicker}
        <MentionPicker
          isOpen={mentionOpen}
          items={mentionItems}
          selectedIndex={mentionSelectedIndex}
          isLoading={isLoadingMention}
          mentionQuery={mentionQuery}
          onSelect={selectMentionItem}
        />
      </div>
      <FileAttachment
        pendingFiles={pendingFiles}
        pendingImages={pendingImages}
        pendingImagePreviews={pendingImagePreviews}
        pendingImageSources={pendingImageSources}
        onRemoveFile={removePendingFile}
        onRemoveImage={removePendingImage}
        isUploading={isUploading}
        uploads={attachmentUploads.uploads}
        onRetryUpload={attachmentUploads.retry}
        t={t}
      />
      {composerRefs.length > 0 ? (
        <div
          data-testid="composer-capability-rail"
          className="flex min-h-8 items-center gap-1.5 overflow-x-auto px-3 pt-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {composerRefs.map((ref) => {
            const key = `${ref.type}:${ref.id}`;
            const isPlugin = ref.type === "plugin";
            const isSkill = ref.type === "skill";
            const label = isPlugin
              ? (pluginNameById.get(ref.id) ?? ref.id)
              : isSkill
                ? ref.id
                : ref.id === "chrome"
                  ? "Chrome"
                  : "Browser";
            return (
              <span
                key={key}
                data-testid={`composer-capability-${ref.type}-${ref.id}`}
                className={cn(
                  "inline-flex h-6 max-w-48 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-semibold",
                  isPlugin &&
                    "bg-violet-500/10 text-violet-700 dark:text-violet-300",
                  isSkill && "bg-blue-500/10 text-blue-700 dark:text-blue-300",
                  ref.type === "surface" &&
                    "bg-cyan-500/10 text-cyan-700 dark:text-cyan-300",
                )}
              >
                {isPlugin ? (
                  <PuzzleIcon className="size-3.5" />
                ) : isSkill ? (
                  <BookOpenIcon className="size-3.5" />
                ) : (
                  <MonitorIcon className="size-3.5" />
                )}
                <span className="truncate">{label}</span>
                <button
                  type="button"
                  className="-mr-0.5 grid size-4 shrink-0 place-items-center rounded-sm opacity-60 transition-opacity hover:bg-current/10 hover:opacity-100"
                  aria-label={t.chatInputBox.removeCapability(label)}
                  onClick={() => removeCapabilityRef(ref)}
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            );
          })}
        </div>
      ) : null}
      <div className="relative">
        {activeComposerMode ? (
          <span
            data-testid="composer-command-prefix"
            className={cn(
              "pointer-events-none absolute left-3 top-2.5 z-10 inline-flex items-center gap-1 text-sm font-bold leading-snug",
              activeComposerMode === "goal" &&
                "text-violet-600 dark:text-violet-400",
              activeComposerMode === "plan" && "text-sky-600 dark:text-sky-400",
              activeComposerMode === "spec" &&
                "text-amber-600 dark:text-amber-400",
              activeComposerMode === "project" &&
                "text-rose-600 dark:text-rose-400",
            )}
          >
            {activeComposerMode === "project" ? (
              <FlagIcon className="size-4" />
            ) : activeComposerMode === "goal" ? (
              <span className="text-[15px] leading-none" aria-hidden="true">
                🎯
              </span>
            ) : activeComposerMode === "plan" ? (
              <MapIcon className="size-4" />
            ) : (
              <ListTodoIcon className="size-4" />
            )}
            {activeComposerMode === "project"
              ? "Milestone"
              : activeComposerMode === "goal"
                ? "Goal"
                : activeComposerMode === "plan"
                  ? "Plan"
                  : "Spec"}
          </span>
        ) : null}
        <textarea
          key={`${activeComposerMode ?? "plain"}:${composerRefs
            .map((ref) => `${ref.type}:${ref.id}`)
            .join(",")}`}
          data-testid="chat-composer-input"
          ref={textareaRef}
          autoFocus={autoFocus}
          disabled={isBusy}
          placeholder={placeholder ?? t.inputBox.placeholder}
          aria-label={placeholder ?? t.inputBox.placeholder}
          value={visibleDraft}
          onChange={(e) => setVisibleDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={handlePasteImages}
          onDrop={handleDropFiles}
          onDragOver={(e) => {
            if (e.dataTransfer?.types?.includes("Files")) e.preventDefault();
          }}
          rows={1}
          className={cn(
            "min-h-11 max-h-40 w-full resize-none overflow-y-auto bg-transparent pb-1.5 pt-2.5 text-sm leading-snug outline-none [field-sizing:content] placeholder:text-muted-foreground/70 disabled:opacity-60",
            activeComposerMode === "project"
              ? "pl-[7.5rem] pr-3"
              : activeComposerMode
                ? "pl-[5.25rem] pr-3"
                : "px-3",
          )}
        />
      </div>
      {isDeepResearchMode && researchConfigOpen && (
        <ResearchSourcePicker
          researchUrlText={researchUrlText}
          setResearchUrlText={setResearchUrlText}
          researchTextTitle={researchTextTitle}
          setResearchTextTitle={setResearchTextTitle}
          researchTextBody={researchTextBody}
          setResearchTextBody={setResearchTextBody}
          researchNote={researchNote}
          setResearchNote={setResearchNote}
          researchMaterials={researchMaterials}
          researchSources={researchSources}
          maxSearches={maxSearches}
          setMaxSearches={setMaxSearches}
          uploadingMaterials={uploadingMaterials}
          materialError={materialError}
          setResearchConfigOpen={setResearchConfigOpen}
          isBusy={isBusy}
          status={status}
          t={t}
          fileInputRef={fileInputRef}
          addUrlMaterial={addUrlMaterial}
          addTextMaterial={addTextMaterial}
          toggleMaterial={toggleMaterial}
          removeMaterial={removeMaterial}
          toggleResearchSource={toggleResearchSource}
        />
      )}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => void handleUploadMaterials(event.target.files)}
      />
      <input
        ref={contextFileInputRef}
        data-testid="chat-device-file-input"
        type="file"
        multiple
        className="hidden"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          handleSelectFiles(event.target.files);
          if (contextFileInputRef.current) {
            contextFileInputRef.current.value = "";
          }
        }}
      />
      <input
        ref={imageInputRef}
        data-testid="chat-image-input"
        type="file"
        multiple
        accept="image/*"
        className="hidden"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          addPendingImages(event.target.files);
          if (imageInputRef.current) imageInputRef.current.value = "";
        }}
      />
      <div className="composer-footer flex min-h-9 flex-wrap items-center justify-between gap-1 px-2 pb-1.5 pt-0.5 sm:gap-2">
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-0.5">
          <DropdownMenu
            open={toolsMenuOpen}
            onOpenChange={(open) => {
              setToolsMenuOpen(open);
              if (!open) setSkillSearch("");
            }}
          >
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                data-testid="chat-tools-trigger"
                disabled={isBusy || status === "streaming"}
                className="relative flex size-[42px] items-center justify-center rounded-lg text-muted-foreground/70 outline-none transition-all duration-base hover:bg-muted/60 hover:text-foreground focus-visible:bg-muted/60 focus-visible:text-foreground focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45 sm:size-8 active:scale-95"
                title={toolsMenuTitle}
                aria-label={toolsMenuLabel}
              >
                <PlusIcon className="size-4" />
                {automationTarget ? (
                  <span
                    data-testid="automation-target-active-indicator"
                    className="absolute top-1 right-1 size-1.5 rounded-full bg-primary ring-2 ring-background sm:top-0.5 sm:right-0.5"
                  />
                ) : null}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              data-testid="chat-tools-menu"
              align="start"
              side="top"
              sideOffset={8}
              aria-label={toolsMenuLabel}
              className="w-60 rounded-lg border-border-default p-1.5 shadow-[var(--shadow-xs)]"
            >
              <DropdownMenuItem
                data-testid="chat-upload-images"
                onSelect={() => imageInputRef.current?.click()}
                className="gap-2 rounded-lg text-sm"
              >
                <ImageIcon className="size-4" />
                {t.chatInputBox.uploadImages}
              </DropdownMenuItem>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger
                  data-testid="chat-project-files-submenu"
                  className="gap-2 rounded-lg text-sm"
                >
                  <FileIcon className="size-4" />
                  <span className="min-w-0 flex-1 truncate">
                    {workspaceLabel
                      ? t.chatInputBox.workspaceFiles(workspaceLabel)
                      : t.chatInputBox.projectFiles}
                  </span>
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="w-80 overflow-hidden p-1.5">
                  {workDir?.trim() ? (
                    <>
                      <DropdownMenuLabel className="px-2 py-1 text-xs text-muted-foreground">
                        {workspaceLabel}
                      </DropdownMenuLabel>
                      <FileTree
                        workDir={workDir.trim()}
                        threadId={threadId}
                        className="max-h-72 overflow-y-auto rounded-lg border border-border-subtle bg-muted/10"
                        onFileClick={(path) => {
                          addPendingWorkspaceFile({
                            path,
                            workDir: workDir.trim(),
                            threadId,
                            sourceLabel: workspaceLabel,
                          });
                          setToolsMenuOpen(false);
                        }}
                      />
                      <DropdownMenuSeparator />
                    </>
                  ) : (
                    <DropdownMenuLabel className="px-2 py-2 text-xs font-normal text-muted-foreground">
                      {t.chatInputBox.noWorkspaceFiles}
                    </DropdownMenuLabel>
                  )}
                  <DropdownMenuItem
                    data-testid="chat-upload-device-files"
                    onSelect={() => contextFileInputRef.current?.click()}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <PaperclipIcon className="size-4" />
                    {t.chatInputBox.uploadDeviceFiles}
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              {onAutomationTargetChange ? (
                <AutomationTargetControl
                  placement="submenu"
                  value={automationTarget}
                  onChange={onAutomationTargetChange}
                  onAddCurrentWindow={() => void addCurrentWindowAppshot()}
                  capturingCurrentWindow={capturingAppshot}
                  disabled={isBusy || status === "streaming"}
                />
              ) : (
                <DropdownMenuItem
                  data-testid="chat-add-appshot"
                  disabled={capturingAppshot}
                  onSelect={() => void addCurrentWindowAppshot()}
                  className="gap-2 rounded-lg text-sm"
                >
                  {capturingAppshot ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <MonitorIcon className="size-4" />
                  )}
                  {capturingAppshot
                    ? t.chatInputBox.capturingAppshot
                    : t.chatInputBox.addAppshot}
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuSub>
                <DropdownMenuSubTrigger
                  data-testid="chat-commands-submenu"
                  className="gap-2 rounded-lg text-sm"
                >
                  <ZapIcon className="size-4" />
                  {t.chatInputBox.commands}
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="w-56 p-1.5">
                  <DropdownMenuItem
                    data-testid="chat-insert-codex-spec"
                    onSelect={() => insertCommandModeMarker("spec")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <ListTodoIcon className="size-4" />
                    Spec
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="chat-insert-codex-plan"
                    onSelect={() => insertCommandModeMarker("plan")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <MapIcon className="size-4" />
                    Plan
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="chat-insert-codex-goal"
                    onSelect={() => insertCommandModeMarker("goal")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <span
                      className="text-[15px] leading-none grayscale opacity-70"
                      aria-hidden="true"
                    >
                      🎯
                    </span>
                    Goal
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="chat-insert-project-mode"
                    onSelect={() => insertCommandModeMarker("project")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <FlagIcon className="size-4" />
                    Milestone
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="chat-insert-browser-surface"
                    onSelect={insertBrowserSurface}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <MonitorIcon className="size-4" />
                    Browser
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="chat-insert-chrome-surface"
                    onSelect={insertChromeSurface}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <MonitorIcon className="size-4" />
                    Chrome
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger
                  data-testid="chat-plugins-submenu"
                  className="gap-2 rounded-lg text-sm"
                >
                  <PuzzleIcon className="size-4" />
                  {t.chatInputBox.plugins}
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="max-h-[min(70vh,32rem)] w-80 overflow-y-auto p-1.5">
                  <DropdownMenuLabel className="px-2 py-1 text-xs text-muted-foreground">
                    {t.chatInputBox.availablePlugins}
                  </DropdownMenuLabel>
                  {pluginsLoading ? (
                    <DropdownMenuItem disabled>
                      <Loader2Icon className="size-4 animate-spin" />
                      {t.common.loading}
                    </DropdownMenuItem>
                  ) : pluginsError ? (
                    <DropdownMenuItem disabled>
                      {t.chatInputBox.capabilityLoadFailed}
                    </DropdownMenuItem>
                  ) : enabledPlugins.length === 0 ? (
                    <DropdownMenuItem disabled>
                      {t.chatInputBox.noAvailablePlugins}
                    </DropdownMenuItem>
                  ) : (
                    enabledPlugins.map((plugin) => (
                      <DropdownMenuItem
                        key={plugin.id}
                        data-testid={`chat-plugin-${plugin.id}`}
                        onSelect={() =>
                          insertCapabilityRef({
                            type: "plugin",
                            id: plugin.id,
                          })
                        }
                        className="items-start gap-2 rounded-lg py-2 text-sm"
                      >
                        <PuzzleIcon className="mt-0.5 size-4 text-violet-600" />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">
                            {plugin.name}
                          </span>
                          {plugin.description ? (
                            <span className="block truncate text-xs text-muted-foreground">
                              {plugin.description}
                            </span>
                          ) : null}
                        </span>
                      </DropdownMenuItem>
                    ))
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    data-testid="chat-manage-plugins"
                    onSelect={() => openHubCatalog("plugins", "installed")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <Settings2Icon className="size-4" />
                    {t.chatInputBox.managePlugins}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    data-testid="chat-explore-plugins"
                    onSelect={() => openHubCatalog("plugins", "all")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <ExternalLinkIcon className="size-4" />
                    {t.chatInputBox.explorePlugins}
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger
                  data-testid="chat-skills-submenu"
                  className="gap-2 rounded-lg text-sm"
                >
                  <BookOpenIcon className="size-4" />
                  {t.chatInputBox.skills}
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="max-h-[min(72vh,36rem)] w-96 overflow-y-auto p-1.5">
                  <div className="sticky top-0 z-10 bg-popover px-1 pb-1">
                    <div className="flex h-9 items-center gap-2 rounded-lg border border-border-default bg-background px-2">
                      <SearchIcon className="size-4 text-muted-foreground" />
                      <input
                        data-testid="chat-skill-search"
                        value={skillSearch}
                        onChange={(event) => setSkillSearch(event.target.value)}
                        onKeyDown={(event) => event.stopPropagation()}
                        placeholder={t.chatInputBox.searchSkills}
                        aria-label={t.chatInputBox.searchSkills}
                        className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                      />
                    </div>
                  </div>
                  {skillsLoading ? (
                    <DropdownMenuItem disabled>
                      <Loader2Icon className="size-4 animate-spin" />
                      {t.common.loading}
                    </DropdownMenuItem>
                  ) : skillsError ? (
                    <DropdownMenuItem disabled>
                      {t.chatInputBox.capabilityLoadFailed}
                    </DropdownMenuItem>
                  ) : enabledSkills.length === 0 ? (
                    <DropdownMenuItem disabled>
                      {t.chatInputBox.noAvailableSkills}
                    </DropdownMenuItem>
                  ) : (
                    enabledSkills.map((skill) => (
                      <DropdownMenuItem
                        key={skill.name}
                        data-testid={`chat-skill-${skill.name}`}
                        onSelect={() =>
                          insertCapabilityRef({
                            type: "skill",
                            id: skill.name,
                          })
                        }
                        className="items-start gap-2 rounded-lg py-2 text-sm"
                      >
                        <BookOpenIcon className="mt-0.5 size-4 text-blue-600" />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">
                            {skill.name}
                          </span>
                          {skill.description ? (
                            <span className="block truncate text-xs text-muted-foreground">
                              {skill.description}
                            </span>
                          ) : null}
                        </span>
                      </DropdownMenuItem>
                    ))
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    data-testid="chat-manage-skills"
                    onSelect={() => openHubCatalog("skills")}
                    className="gap-2 rounded-lg text-sm"
                  >
                    <Settings2Icon className="size-4" />
                    {t.chatInputBox.manageSkills}
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              {(isGroupConversation || canUseDeepResearch) && (
                <DropdownMenuSeparator />
              )}
              {isGroupConversation && onProjectCapabilityAction ? (
                <DropdownMenuItem
                  data-testid="group-project-capability-action"
                  onSelect={onProjectCapabilityAction}
                  className="gap-2 rounded-lg text-sm"
                >
                  <FolderKanbanIcon className="size-4" />
                  {projectCapabilityEnabled
                    ? t.projectCapability.openWorkbench
                    : t.projectCapability.startPlan}
                </DropdownMenuItem>
              ) : null}
              {isGroupConversation && groupTaskStrategy !== "auto" ? (
                <DropdownMenuItem
                  data-testid="group-task-clear-action"
                  onSelect={() => onGroupTaskStrategyChange?.("auto")}
                  className="gap-2 rounded-lg text-sm"
                >
                  <ListTodoIcon className="size-4" />
                  {t.chatInputBox.groupTaskClear}
                </DropdownMenuItem>
              ) : null}
              {canUseDeepResearch && (
                <DropdownMenuItem
                  onSelect={() => setResearchConfigOpen((open) => !open)}
                  className="gap-2 rounded-lg text-sm"
                >
                  <SlidersHorizontalIcon className="size-4" />
                  {t.chatInputBox.deepResearchConfig}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          <div className="composer-footer__secondary contents">
            <PreviewRefreshIndicator />
          </div>
          {(!isGroupConversation || resolvedPermissionMode !== "default") && (
            <div className="composer-footer__secondary contents">
              <PermissionIndicator
                mode={resolvedPermissionMode}
                onModeChange={(nextMode) => onPermissionModeChange?.(nextMode)}
                compact
              />
            </div>
          )}
          {activeLongTaskMode ? (
            <button
              type="button"
              data-testid="composer-long-task-indicator"
              onClick={clearLongTaskMode}
              className="flex size-[42px] shrink-0 items-center justify-center rounded-lg text-muted-foreground outline-none transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/35 sm:size-8"
              title={
                activeLongTaskMode === "goal"
                  ? "Goal 模式 · 点击退出"
                  : "里程碑模式 · 点击退出"
              }
              aria-label={
                activeLongTaskMode === "goal"
                  ? "Goal 模式 · 点击退出"
                  : "里程碑模式 · 点击退出"
              }
            >
              {activeLongTaskMode === "goal" ? (
                <span
                  className="text-[15px] leading-none grayscale opacity-70"
                  aria-hidden="true"
                >
                  🎯
                </span>
              ) : (
                <FlagIcon className="size-4" />
              )}
            </button>
          ) : null}
          {/* 上下文压缩指示器 */}
          {showContextCompressor && (
            <div className="composer-footer__secondary contents">
              <ContextCompressor
                currentTokens={contextTokens}
                maxTokens={maxContextTokens}
                isCompressing={isCompressingContext}
                onCompress={onCompressContext}
                disabled={isBusy || status === "streaming"}
              />
            </div>
          )}
        </div>
        <div className="ml-auto flex min-w-0 shrink-0 items-center justify-end gap-1">
          {responseModeControl ? (
            <div className="composer-footer__response contents">
              {responseModeControl}
            </div>
          ) : showInspirationToggle ? (
            <button
              type="button"
              data-testid="chat-mode-toggle"
              disabled={disabled || status === "streaming"}
              onClick={() =>
                onModeChange?.(mode === "chat" ? "react" : "chat", draft)
              }
              className={cn(
                "flex size-[42px] items-center justify-center rounded-lg text-xs font-medium transition-all duration-base sm:size-8",
                mode === "chat"
                  ? "bg-primary/10 text-primary hover:bg-primary/15"
                  : "border border-transparent text-muted-foreground hover:border-border-default hover:bg-muted/60 hover:text-foreground",
                "disabled:cursor-not-allowed disabled:opacity-45",
              )}
              title={t.inputBox.chatModeDescription}
              aria-label={t.inputBox.chatModeDescription}
              aria-pressed={mode === "chat"}
            >
              <span className="relative flex size-4 items-center justify-center">
                <LightbulbIcon className="size-4" />
                <ZapIcon
                  className={cn(
                    "absolute left-1/2 top-[46%] size-2.5 -translate-x-1/2 -translate-y-1/2",
                    mode === "chat" ? "fill-current" : "",
                  )}
                  strokeWidth={2.4}
                />
              </span>
            </button>
          ) : null}
          <div className="composer-footer__secondary contents">
            <EvolutionIndicator compact quiet />
          </div>
          {modelProfileControl ? (
            <div className="composer-footer__model contents">
              <CoderEngineControl
                systemModels={pickerModels}
                disabled={disabled || status === "streaming"}
                executionEngine={executionEngine}
                value={modelName}
                onChange={onModelChange}
                onEffectiveModelChange={onModelSwitchNotice}
                reasoningEffort={reasoningEffort}
                onReasoningEffortChange={onReasoningEffortChange}
              />
            </div>
          ) : (
            <div className="composer-footer__model contents">
              <ModelPicker
                models={pickerModels}
                // Pass the raw modelName so the picker sees the "auto"
                // sentinel — selectedModel falls back to pickerModels[0]
                // when name doesn't match, which would mask the auto state.
                value={modelName ?? selectedModel?.name}
                onChange={applyNativeModelChange}
                reasoningEffort={reasoningEffort}
                reasoningEffortDisabled={disabled || status === "streaming"}
                onReasoningEffortChange={onReasoningEffortChange}
              />
            </div>
          )}
          {status === "streaming" && sendableDraftText ? (
            <>
              <button
                type="button"
                onClick={handleSubmit}
                data-testid="chat-steer-button"
                className="flex size-[42px] items-center justify-center rounded-lg bg-foreground text-background transition-all duration-base hover:bg-foreground/90 active:scale-95 sm:size-8"
                title={sendLabel}
                aria-label={sendLabel}
              >
                <SendHorizontalIcon className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={onStop}
                className="flex size-[42px] items-center justify-center rounded-lg border border-border bg-muted/60 text-muted-foreground transition-all duration-base hover:border-destructive/25 hover:bg-destructive/10 hover:text-destructive active:scale-95 sm:size-8"
                title={stopLabel}
                aria-label={stopLabel}
              >
                <SquareIcon className="size-3" fill="currentColor" />
              </button>
            </>
          ) : status === "streaming" ? (
            <button
              type="button"
              onClick={onStop}
              className="flex size-[42px] items-center justify-center rounded-lg border border-border bg-muted/60 text-muted-foreground transition-all duration-base hover:border-destructive/25 hover:bg-destructive/10 hover:text-destructive active:scale-95 sm:size-8"
              title={stopLabel}
              aria-label={stopLabel}
            >
              <SquareIcon className="size-3" fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              data-testid="chat-send-button"
              disabled={
                (!sendableDraftText &&
                  pendingImages.length === 0 &&
                  pendingFiles.length === 0) ||
                isBusy
              }
              className={cn(
                "flex size-[42px] items-center justify-center rounded-lg transition-all duration-base sm:size-8",
                isDeepResearchMode
                  ? "bg-primary text-primary-foreground hover:bg-primary/90 active:scale-95"
                  : "bg-foreground text-background hover:bg-foreground/90 active:scale-95",
                "disabled:bg-transparent disabled:text-muted-foreground/50 disabled:cursor-not-allowed disabled:hover:bg-muted/60 disabled:hover:text-muted-foreground",
              )}
              // A disabled send button should say why it is disabled.
              title={
                attachmentUploads.isUploading
                  ? t.uploads.waitingForUpload
                  : attachmentUploads.hasFailed
                    ? t.uploads.uploadFailed
                    : sendLabel
              }
              aria-label={sendLabel}
            >
              {isBusy ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <SendHorizontalIcon className="size-3.5" />
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
