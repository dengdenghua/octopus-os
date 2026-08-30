import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { FolderOpenIcon, XIcon } from "lucide-react";
import { useI18n } from "@/core/i18n/hooks";
import { useLocalSettings } from "@/core/settings";
import { pickLocalDirectory } from "@/core/workspace/pick-local-directory";
import { basename } from "@/lib/path-utils";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { toast } from "sonner";

import { SettingsSection } from "./settings-section";

const COPY = {
  zh: {
    title: "个人空间",
    description: "管理个人空间的默认位置和专属工作规则。",
    workspace: "个人空间文件夹",
    workspaceDescription:
      "选择个人空间任务默认使用的文件夹；临时选择项目时以项目文件夹为准。",
    roleFolderDescription:
      "它只作为根目录；各角色会自动使用以角色名称命名的独立子文件夹，界面统一显示为“个人空间”。",
    roleFolderDescriptionCompact: "选择根目录；角色子文件夹自动按角色命名。",
    chooseWorkspace: "选择文件夹",
    clearWorkspace: "清除个人空间文件夹",
    noWorkspace: "未设置（使用隔离工作区）",
    chooseWorkspaceFailed: "选择文件夹失败，请重试",
    instructions: "自定义工作规则",
    instructionsDescription:
      "这些规则只加入个人空间任务，不影响绑定的项目目录。",
    placeholder:
      "例如：研究时优先中文来源；构建成果统一放进 outputs/；回答保持简洁……",
    count: (count: number) => `${count}/2000`,
  },
  en: {
    title: "Personal space",
    description:
      "Manage the personal space location and its custom work rules.",
    workspace: "Personal space folder",
    workspaceDescription:
      "Choose the default folder for personal tasks. A selected project folder takes priority.",
    roleFolderDescription:
      "This is the root only. Each role gets a separate child folder named after that role; the UI still shows Personal space.",
    roleFolderDescriptionCompact:
      "Choose the root; role folders are named automatically.",
    chooseWorkspace: "Choose folder",
    clearWorkspace: "Clear personal space folder",
    noWorkspace: "Not set (use isolated workspace)",
    chooseWorkspaceFailed: "Could not choose a folder",
    instructions: "Custom work rules",
    instructionsDescription:
      "These rules apply only to personal space, never to bound projects.",
    placeholder:
      "For example: prefer primary sources; put build outputs in outputs/; keep replies concise…",
    count: (count: number) => `${count}/2000`,
  },
  ja: {
    title: "個人スペース",
    description: "個人スペースの場所とカスタム作業ルールを管理します。",
    workspace: "個人スペースフォルダー",
    workspaceDescription:
      "個人タスクの既定フォルダーを選択します。プロジェクトのフォルダーが優先されます。",
    roleFolderDescription:
      "ここはルートのみです。各ロールは名前付きの専用サブフォルダーを使い、画面では個人スペースと表示されます。",
    roleFolderDescriptionCompact:
      "ルートを選択すると、ロール別フォルダーが自動で作成されます。",
    chooseWorkspace: "フォルダーを選択",
    clearWorkspace: "個人スペースフォルダーをクリア",
    noWorkspace: "未設定（分離ワークスペースを使用）",
    chooseWorkspaceFailed: "フォルダーを選択できませんでした",
    instructions: "カスタム作業ルール",
    instructionsDescription:
      "個人スペースだけに適用され、接続済みプロジェクトには影響しません。",
    placeholder:
      "例：一次情報を優先する、成果物は outputs/ に置く、回答は簡潔にする…",
    count: (count: number) => `${count}/2000`,
  },
  ko: {
    title: "개인 공간",
    description: "개인 공간 위치와 사용자 지정 작업 규칙을 관리합니다.",
    workspace: "개인 공간 폴더",
    workspaceDescription:
      "개인 작업의 기본 폴더를 선택합니다. 프로젝트 폴더가 우선됩니다.",
    roleFolderDescription:
      "이 폴더는 루트이며 역할마다 역할 이름의 전용 하위 폴더를 사용합니다. 화면에는 개인 공간으로 표시됩니다.",
    roleFolderDescriptionCompact:
      "루트를 선택하면 역할별 폴더 이름이 자동 지정됩니다.",
    chooseWorkspace: "폴더 선택",
    clearWorkspace: "개인 공간 폴더 지우기",
    noWorkspace: "설정 안 함(격리된 작업 공간 사용)",
    chooseWorkspaceFailed: "폴더를 선택할 수 없습니다",
    instructions: "사용자 지정 작업 규칙",
    instructionsDescription:
      "개인 공간에만 적용되며 연결된 프로젝트에는 영향을 주지 않습니다.",
    placeholder:
      "예: 1차 출처 우선, 결과물은 outputs/에 저장, 답변은 간결하게…",
    count: (count: number) => `${count}/2000`,
  },
} as const;

export default function PersonalSpaceSettingsPage() {
  const { locale } = useI18n();
  const language = locale.slice(0, 2).toLowerCase();
  const copy =
    language === "zh"
      ? COPY.zh
      : language === "ja"
        ? COPY.ja
        : language === "ko"
          ? COPY.ko
          : COPY.en;
  return (
    <SettingsSection title={copy.title} description={copy.description}>
      <div className="space-y-6">
        <PersonalSpaceFolderSettings headingLevel="compact" />
        <PersonalWorkRulesSettings headingLevel="compact" />
      </div>
    </SettingsSection>
  );
}

export function PersonalSpaceFolderSettings({
  headingLevel = "section",
}: {
  headingLevel?: "compact" | "section";
} = {}) {
  const { locale, t } = useI18n();
  const language = locale.slice(0, 2).toLowerCase();
  const copy =
    language === "zh"
      ? COPY.zh
      : language === "ja"
        ? COPY.ja
        : language === "ko"
          ? COPY.ko
          : COPY.en;
  const [settings, setSettings] = useLocalSettings();
  const [pickingWorkspace, setPickingWorkspace] = useState(false);
  const personal = settings.personal_space;

  const chooseWorkspace = async () => {
    setPickingWorkspace(true);
    try {
      const selected = await pickLocalDirectory(personal.default_folder);
      if (selected) setSettings("personal_space", { default_folder: selected });
    } catch {
      toast.error(copy.chooseWorkspaceFailed);
    } finally {
      setPickingWorkspace(false);
    }
  };

  return (
    <section className="space-y-3" aria-labelledby="personal-workspace">
      <div>
        <h3
          id="personal-workspace"
          className={cn(
            "text-foreground",
            headingLevel === "section"
              ? "text-base font-semibold"
              : "text-sm font-medium",
          )}
        >
          {copy.workspace}
        </h3>
        <p className="mt-1 hidden text-xs leading-5 text-muted-foreground sm:block">
          {copy.workspaceDescription}
        </p>
      </div>
      <div className="flex flex-col gap-3 rounded-lg border border-border-default bg-card/50 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
        <div className="flex min-w-0 items-start gap-2">
          <FolderOpenIcon className="size-4 shrink-0 text-primary" />
          <div className="min-w-0">
            <p
              className="truncate text-sm font-medium"
              title={personal.default_folder || copy.noWorkspace}
            >
              {personal.default_folder
                ? basename(personal.default_folder)
                : copy.noWorkspace}
            </p>
            <p className="mt-1 max-w-xl text-xs leading-4 text-muted-foreground sm:leading-5">
              <span className="sm:hidden">
                {copy.roleFolderDescriptionCompact}
              </span>
              <span className="hidden sm:inline">
                {copy.roleFolderDescription}
              </span>
            </p>
          </div>
        </div>
        <div className="flex shrink-0 gap-2 self-start sm:self-auto">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void chooseWorkspace()}
            disabled={pickingWorkspace}
          >
            <FolderOpenIcon className="mr-1.5 size-3.5" />
            {pickingWorkspace ? t.common.loading : copy.chooseWorkspace}
          </Button>
          {personal.default_folder && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() =>
                setSettings("personal_space", { default_folder: "" })
              }
              aria-label={copy.clearWorkspace}
              title={copy.clearWorkspace}
            >
              <XIcon className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

export function PersonalWorkRulesSettings({
  headingLevel = "section",
}: {
  headingLevel?: "compact" | "section";
} = {}) {
  const { locale } = useI18n();
  const language = locale.slice(0, 2).toLowerCase();
  const copy =
    language === "zh"
      ? COPY.zh
      : language === "ja"
        ? COPY.ja
        : language === "ko"
          ? COPY.ko
          : COPY.en;
  const [settings, setSettings] = useLocalSettings();
  const personal = settings.personal_space;

  return (
    <section className="space-y-3" aria-labelledby="personal-instructions">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h3
            id="personal-instructions"
            className={cn(
              "text-foreground",
              headingLevel === "section"
                ? "text-base font-semibold"
                : "text-sm font-medium",
            )}
          >
            {copy.instructions}
          </h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {copy.instructionsDescription}
          </p>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground">
          {copy.count(personal.custom_instructions.length)}
        </span>
      </div>
      <Textarea
        value={personal.custom_instructions}
        maxLength={2000}
        rows={5}
        placeholder={copy.placeholder}
        aria-label={copy.instructions}
        onChange={(event) =>
          setSettings("personal_space", {
            custom_instructions: event.target.value,
          })
        }
      />
    </section>
  );
}
