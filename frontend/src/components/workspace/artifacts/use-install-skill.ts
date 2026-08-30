import { useCallback, useState } from "react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import { installSkill } from "@/core/skills/api";

/**
 * Shared install-skill flow for artifact panels. Returns the installing
 * filepath (null when idle) and a trigger that handles toast + loading state.
 */
export function useInstallSkill(threadId: string) {
  const { t } = useI18n();
  const [installingFile, setInstallingFile] = useState<string | null>(null);

  const install = useCallback(
    async (filepath: string) => {
      if (installingFile) return;
      setInstallingFile(filepath);
      try {
        const result = await installSkill({
          thread_id: threadId,
          path: filepath,
        });
        if (result.success) {
          toast.success(result.message);
        } else {
          toast.error(result.message ?? t.toolCalls.toastSkillInstallFailed);
        }
      } catch (error) {
        console.error("Failed to install skill:", error);
        toast.error(t.toolCalls.toastSkillInstallFailed);
      } finally {
        setInstallingFile(null);
      }
    },
    [installingFile, threadId, t],
  );

  return { installingFile, install };
}
