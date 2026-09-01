import { useMemo } from "react";
import React from "react";
import { useI18n } from "@/core/i18n/hooks";
import packageInfo from "../../../../package.json";

interface InfoRow {
  label: string;
  value: string;
}

export function BundleInfo() {
  const { t } = useI18n();
  const rows: InfoRow[] = useMemo(() => {
    const env = import.meta.env.MODE ?? "unknown";
    const viteVersion = import.meta.env.VITE_VERSION ?? __VITE_VERSION__;
    const reactVersion = React.version;

    return [
      { label: t.bundleInfo.appVersion, value: packageInfo.version },
      { label: t.bundleInfo.license, value: packageInfo.license },
      { label: t.bundleInfo.environment, value: env },
      { label: t.bundleInfo.vite, value: viteVersion },
      { label: t.bundleInfo.react, value: reactVersion },
    ];
  }, [t]);

  return (
    <div className="mt-6 rounded-lg border p-4">
      <h3 className="text-sm font-semibold mb-3">{t.bundleInfo.title}</h3>
      <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-x-4 gap-y-2 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="break-all text-right font-mono text-xs">
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/* Vite injects __VITE_VERSION__ at build time — declare for TS */
declare const __VITE_VERSION__: string;
