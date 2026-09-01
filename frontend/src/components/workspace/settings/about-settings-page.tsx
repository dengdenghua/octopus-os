import { Suspense, lazy } from "react";

import { useI18n } from "@/core/i18n/hooks";

import { getAboutMarkdown } from "./about-content";
import { BundleInfo } from "./bundle-info";
import { CodexUpdateRadar } from "./codex-update-radar";

const LazyStreamdown = lazy(
  () => import("@/components/ai-elements/streamdown-host"),
);

export default function AboutSettingsPage() {
  const { locale } = useI18n();
  const aboutMarkdown = getAboutMarkdown(locale);

  return (
    <div>
      <Suspense
        fallback={
          <div className="whitespace-pre-wrap break-words">{aboutMarkdown}</div>
        }
      >
        <LazyStreamdown>{aboutMarkdown}</LazyStreamdown>
      </Suspense>
      <BundleInfo />
      <CodexUpdateRadar />
    </div>
  );
}
