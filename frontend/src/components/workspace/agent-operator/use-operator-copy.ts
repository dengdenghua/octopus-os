import { useCallback } from "react";
import { useI18n } from "@/core/i18n/hooks";

export function useOperatorCopy() {
  const { t } = useI18n();
  return useCallback(
    (source: string) => t.agentOperator[source] ?? source,
    [t.agentOperator],
  );
}
