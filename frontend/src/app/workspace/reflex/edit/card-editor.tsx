/* Implementation note. */

import { swallow } from "@/core/utils/log";
import { LockIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { reflexFetch } from "../api";

type TriggerMode = "exact" | "contains" | "regex";
type PriorityBand = "low" | "medium" | "high";
type ActionMode = "none" | "webhook" | "mqtt";

type WebhookCfg = {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
  timeout_ms: number;
};
type MqttCfg = {
  broker: string;
  port: number;
  topic: string;
  payload: string;
  qos: number;
  retain: boolean;
};
type ActionCard = {
  mode: ActionMode;
  webhook?: WebhookCfg;
  mqtt?: MqttCfg;
};

type ReplySource = "text" | "workflow";

type CardModel = {
  id: string;
  trigger_mode: TriggerMode;
  trigger_text: string;
  reply: string;
  reply_on_failure: string;
  reply_source: ReplySource;
  delegate_to_workflow: string;
  priority: PriorityBand;
  priority_raw: number;
  action: ActionCard;
  advanced: boolean;
};

type WorkflowItem = {
  id: string;
  name: string;
  description: string;
};

type CardsResp = {
  ok: boolean;
  cards?: CardModel[];
  mtime?: number;
  error?: string;
};

type CardSaveResp = {
  ok: boolean;
  rules_in_file?: number;
  reloaded?: boolean;
  rules_loaded?: number;
  reload_error?: string;
  new_mtime?: number;
  error?: string;
};

interface Props {
  /* Implementation note. */
  onSwitchToYaml: () => void;
  /* Implementation note. */
  onSavedExternally: () => void;
}

// Implementation note.
type HubPreset = {
  id: string;
  label: string;
  apply: (mode: ActionMode) => Partial<WebhookCfg> | Partial<MqttCfg>;
  forMode: ActionMode;
};

const HUB_PRESETS: HubPreset[] = [
  {
    id: "home_assistant_light_off",
    label: "Home Assistant · 关灯",
    forMode: "webhook",
    apply: () => ({
      url: "http://homeassistant.local:8123/api/services/light/turn_off",
      method: "POST",
      headers: { Authorization: "Bearer YOUR_HA_LONG_LIVED_TOKEN" },
      body: { entity_id: "light.living_room" },
      timeout_ms: 1500,
    }),
  },
  {
    id: "home_assistant_light_on",
    label: "Home Assistant · 开灯",
    forMode: "webhook",
    apply: () => ({
      url: "http://homeassistant.local:8123/api/services/light/turn_on",
      method: "POST",
      headers: { Authorization: "Bearer YOUR_HA_LONG_LIVED_TOKEN" },
      body: { entity_id: "light.living_room", brightness: 200 },
      timeout_ms: 1500,
    }),
  },
  {
    id: "node_red_webhook",
    label: "Node-RED · 自定义流",
    forMode: "webhook",
    apply: () => ({
      url: "http://nodered.local:1880/echo/light",
      method: "POST",
      headers: {},
      body: { action: "off", room: "living_room" },
      timeout_ms: 1000,
    }),
  },
  {
    id: "tasmota_mqtt",
    label: "Tasmota · MQTT 关",
    forMode: "mqtt",
    apply: () => ({
      broker: "192.168.1.10",
      port: 1883,
      topic: "cmnd/tasmota_light_living/POWER",
      payload: "OFF",
      qos: 0,
      retain: false,
    }),
  },
  {
    id: "zigbee2mqtt",
    label: "Zigbee2MQTT · 关灯",
    forMode: "mqtt",
    apply: () => ({
      broker: "192.168.1.10",
      port: 1883,
      topic: "zigbee2mqtt/living_room_light/set",
      payload: '{"state":"OFF"}',
      qos: 0,
      retain: false,
    }),
  },
];

const DEFAULT_WEBHOOK: WebhookCfg = {
  url: "",
  method: "POST",
  headers: {},
  body: null,
  timeout_ms: 1000,
};

const DEFAULT_MQTT: MqttCfg = {
  broker: "",
  port: 1883,
  topic: "",
  payload: "",
  qos: 0,
  retain: false,
};

export function ReflexCardEditor({ onSwitchToYaml, onSavedExternally }: Props) {
  const { t } = useI18n();
  const [cards, setCards] = useState<CardModel[] | null>(null);
  const [origIds, setOrigIds] = useState<Set<string>>(new Set());
  const [mtime, setMtime] = useState<number>(0);
  const [workflows, _setWorkflows] = useState<WorkflowItem[]>([]);
  const [status, setStatus] = useState<{
    kind: "idle" | "ok" | "err";
    msg: string;
  }>({
    kind: "idle",
    msg: "",
  });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r: CardsResp = await reflexFetch<CardsResp>(
        "/api/reflex/rules-cards",
      );
      if (!r.ok || !r.cards) {
        setStatus({ kind: "err", msg: r.error ?? "load failed" });
        return;
      }
      setCards(r.cards);
      setOrigIds(new Set(r.cards.map((c) => c.id)));
      setMtime(r.mtime ?? 0);
      setStatus({ kind: "idle", msg: "" });
    } catch (e) {
      swallow(e);
      setStatus({
        kind: "err",
        msg: e instanceof Error ? e.message : "fetch error",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!cards) return false;
    return true;
  }, [cards]);

  const updateCard = (idx: number, patch: Partial<CardModel>) => {
    setCards((cs) => {
      if (!cs) return cs;
      const next = [...cs];
      next[idx] = { ...next[idx], ...patch } as CardModel;
      return next;
    });
  };

  const { confirm, confirmDialog } = useConfirmDialog();

  const deleteCard = async (idx: number) => {
    if (!cards) return;
    const ok = await confirm({
      title: t.common.delete,
      description: t.reflexEditor.cardConfirmDelete,
    });
    if (!ok) return;
    setCards(cards.filter((_, i) => i !== idx));
  };

  const addCard = () => {
    setCards((cs) => [
      ...(cs ?? []),
      {
        id: `new_rule_${Date.now()}`,
        trigger_mode: "exact",
        trigger_text: "",
        reply: "",
        reply_on_failure: "",
        reply_source: "text",
        delegate_to_workflow: "",
        priority: "medium",
        priority_raw: 20,
        action: { mode: "none" },
        advanced: false,
      },
    ]);
  };

  const save = async () => {
    if (!cards) return;
    setBusy(true);
    setStatus({ kind: "idle", msg: "saving…" });
    const upserts = cards
      .filter((c) => !c.advanced)
      .map((c) => ({
        id: c.id,
        trigger_mode: c.trigger_mode,
        trigger_text: c.trigger_text,
        reply: c.reply,
        reply_on_failure: c.reply_on_failure,
        reply_source: c.reply_source,
        delegate_to_workflow: c.delegate_to_workflow,
        priority: c.priority,
        action: c.action,
      }));
    const currentIds = new Set(cards.map((c) => c.id));
    const deletes = Array.from(origIds).filter((id) => !currentIds.has(id));
    try {
      const r: CardSaveResp = await reflexFetch<CardSaveResp>(
        "/api/reflex/rules-cards",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_mtime: mtime,
            reload: true,
            upserts,
            deletes,
          }),
        },
      );
      if (!r.ok) {
        setStatus({ kind: "err", msg: r.error ?? "save failed" });
        return;
      }
      setStatus({ kind: "ok", msg: t.reflexEditor.cardSavedHint });
      setMtime(r.new_mtime ?? mtime);
      onSavedExternally();
      await load();
    } catch (e) {
      swallow(e);
      setStatus({
        kind: "err",
        msg: e instanceof Error ? e.message : "save error",
      });
    } finally {
      setBusy(false);
    }
  };

  if (cards === null) {
    return (
      <div className="px-4 py-8 text-sm text-muted-foreground">
        {t.reflexEditor.statusLoading}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={busy || !dirty}>
          {t.reflexEditor.saveAndReload}
        </Button>
        <Button size="sm" variant="outline" onClick={addCard}>
          <PlusIcon className="mr-1 size-4" />
          {t.reflexEditor.cardAddNew}
        </Button>
        <Button size="sm" variant="ghost" onClick={onSwitchToYaml}>
          {t.reflexEditor.modeYaml}
        </Button>
        {status.msg && (
          <span
            className={cn(
              "ml-auto rounded-md px-2.5 py-1 font-mono text-xs",
              status.kind === "ok" && "bg-success/15 text-success",
              status.kind === "err" && "bg-destructive/15 text-destructive",
              status.kind === "idle" && "bg-muted/40 text-muted-foreground",
            )}
          >
            {status.msg}
          </span>
        )}
      </div>

      {cards.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-default px-4 py-12 text-center text-sm text-muted-foreground">
          {t.reflexEditor.cardEmpty}
        </div>
      ) : (
        <div className="grid gap-3">
          {cards.map((c, i) => (
            <RuleCard
              key={`${c.id}-${i}`}
              card={c}
              workflows={workflows}
              onChange={(p) => updateCard(i, p)}
              onDelete={() => void deleteCard(i)}
            />
          ))}
        </div>
      )}
      {confirmDialog}
    </div>
  );
}

function RuleCard({
  card,
  workflows,
  onChange,
  onDelete,
}: {
  card: CardModel;
  workflows: WorkflowItem[];
  onChange: (patch: Partial<CardModel>) => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const readOnly = card.advanced;
  // Defensive default · older snapshots / HMR-retained state may not
  // have `action`; treat missing as "no action".
  const safeAction: ActionCard = card.action ?? { mode: "none" };

  const setActionMode = (mode: ActionMode) => {
    if (mode === "none") {
      onChange({ action: { mode: "none" } });
    } else if (mode === "webhook") {
      onChange({
        action: {
          mode: "webhook",
          webhook: safeAction.webhook ?? { ...DEFAULT_WEBHOOK },
        },
      });
    } else {
      onChange({
        action: {
          mode: "mqtt",
          mqtt: safeAction.mqtt ?? { ...DEFAULT_MQTT },
        },
      });
    }
  };

  const applyPreset = (preset: HubPreset) => {
    if (preset.forMode === "webhook") {
      onChange({
        action: {
          mode: "webhook",
          webhook: {
            ...(safeAction.webhook ?? DEFAULT_WEBHOOK),
            ...(preset.apply("webhook") as Partial<WebhookCfg>),
          },
        },
      });
    } else {
      onChange({
        action: {
          mode: "mqtt",
          mqtt: {
            ...(safeAction.mqtt ?? DEFAULT_MQTT),
            ...(preset.apply("mqtt") as Partial<MqttCfg>),
          },
        },
      });
    }
  };

  return (
    <Card
      className={cn(
        "rounded-lg border-white/40 shadow-none dark:border-white/10",
        readOnly && "opacity-70",
      )}
    >
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2">
          <input
            value={card.id}
            disabled={readOnly}
            onChange={(e) => onChange({ id: e.target.value })}
            placeholder={t.reflexEditor.cardField_id}
            aria-label={t.reflexEditor.cardField_id}
            className="flex-1 rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-sm outline-none focus:border-primary disabled:cursor-not-allowed"
          />
          {readOnly && (
            <span className="flex items-center gap-1 rounded-md bg-warning/15 px-2 py-0.5 text-xs text-warning">
              <LockIcon className="size-3" />
              {t.reflexEditor.cardAdvancedBadge}
            </span>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onDelete}
            disabled={readOnly}
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2Icon className="size-4" />
          </Button>
        </div>

        <div className="grid gap-1">
          <label className="text-xs text-muted-foreground">
            {t.reflexEditor.cardField_trigger}
          </label>
          <div className="flex gap-2">
            <select
              value={card.trigger_mode}
              disabled={readOnly}
              onChange={(e) =>
                onChange({ trigger_mode: e.target.value as TriggerMode })
              }
              className="rounded-md border border-border-default bg-background px-2 py-1.5 text-sm outline-none focus:border-primary disabled:cursor-not-allowed"
            >
              <option value="exact">{t.reflexEditor.triggerMode_exact}</option>
              <option value="contains">
                {t.reflexEditor.triggerMode_contains}
              </option>
              <option value="regex">{t.reflexEditor.triggerMode_regex}</option>
            </select>
            <input
              value={card.trigger_text}
              disabled={readOnly}
              onChange={(e) => onChange({ trigger_text: e.target.value })}
              aria-label={t.reflexEditor.cardField_trigger}
              placeholder={
                card.trigger_mode === "regex"
                  ? "^开(灯|空调)$"
                  : card.trigger_mode === "contains"
                    ? "天气"
                    : "你好"
              }
              className="flex-1 rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-sm outline-none focus:border-primary disabled:cursor-not-allowed"
            />
          </div>
        </div>

        <div className="grid gap-1">
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground">
              {t.reflexEditor.cardField_replySource}
            </label>
            <div className="inline-flex rounded-md border border-border-default p-0.5">
              {(["text", "workflow"] as ReplySource[]).map((s) => (
                <button
                  key={s}
                  disabled={readOnly}
                  onClick={() => onChange({ reply_source: s })}
                  className={cn(
                    "rounded px-2 py-0.5 text-xs transition-colors disabled:cursor-not-allowed",
                    card.reply_source === s
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted/40",
                  )}
                >
                  {t.reflexEditor[`replySource_${s}` as const]}
                </button>
              ))}
            </div>
            {card.reply_source === "workflow" && (
              <span className="rounded-md bg-warning/15 px-2 py-0.5 text-xs text-warning">
                {t.reflexEditor.replySource_slowHint}
              </span>
            )}
          </div>
          {card.reply_source === "text" ? (
            <textarea
              value={card.reply}
              disabled={readOnly}
              onChange={(e) => onChange({ reply: e.target.value })}
              rows={2}
              placeholder={t.reflexEditor.cardField_reply}
              className="rounded-md border border-border-default bg-background px-3 py-1.5 text-sm outline-none focus:border-primary disabled:cursor-not-allowed"
            />
          ) : (
            <div className="grid gap-1">
              <select
                value={card.delegate_to_workflow}
                disabled={readOnly}
                onChange={(e) =>
                  onChange({ delegate_to_workflow: e.target.value })
                }
                className="rounded-md border border-border-default bg-background px-3 py-1.5 text-sm outline-none focus:border-primary disabled:cursor-not-allowed"
              >
                <option value="">
                  {t.reflexEditor.cardField_workflowPick}
                </option>
                {workflows.map((w) => (
                  <option key={w.id} value={w.id}>
                    {(w.name || w.id) +
                      (w.description ? ` · ${w.description.slice(0, 40)}` : "")}
                  </option>
                ))}
              </select>
              <textarea
                value={card.reply}
                disabled={readOnly}
                onChange={(e) => onChange({ reply: e.target.value })}
                rows={1}
                placeholder={t.reflexEditor.cardField_workflowFallback}
                className="rounded-md border border-border-default bg-background px-3 py-1.5 text-xs outline-none focus:border-primary disabled:cursor-not-allowed"
              />
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {t.reflexEditor.cardField_priority}
          </span>
          {(["low", "medium", "high"] as PriorityBand[]).map((p) => (
            <button
              key={p}
              disabled={readOnly}
              onClick={() => onChange({ priority: p })}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed",
                card.priority === p
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted/30 text-muted-foreground hover:bg-muted/50",
              )}
            >
              {t.reflexEditor[`priority_${p}` as const]}
            </button>
          ))}
        </div>

        <ActionEditor
          action={safeAction}
          readOnly={readOnly}
          onModeChange={setActionMode}
          onWebhookChange={(p) =>
            onChange({
              action: {
                ...safeAction,
                mode: "webhook",
                webhook: { ...(safeAction.webhook ?? DEFAULT_WEBHOOK), ...p },
              },
            })
          }
          onMqttChange={(p) =>
            onChange({
              action: {
                ...safeAction,
                mode: "mqtt",
                mqtt: { ...(safeAction.mqtt ?? DEFAULT_MQTT), ...p },
              },
            })
          }
          onPreset={applyPreset}
        />

        {safeAction.mode !== "none" && (
          <div className="grid gap-1">
            <label className="text-xs text-muted-foreground">
              {t.reflexEditor.cardField_replyOnFailure}
            </label>
            <input
              value={card.reply_on_failure}
              disabled={readOnly}
              onChange={(e) => onChange({ reply_on_failure: e.target.value })}
              placeholder={t.reflexEditor.cardField_replyOnFailurePlaceholder}
              aria-label={t.reflexEditor.cardField_replyOnFailure}
              className="rounded-md border border-border-default bg-background px-3 py-1.5 text-sm outline-none focus:border-primary disabled:cursor-not-allowed"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ActionEditor({
  action,
  readOnly,
  onModeChange,
  onWebhookChange,
  onMqttChange,
  onPreset,
}: {
  action: ActionCard;
  readOnly: boolean;
  onModeChange: (m: ActionMode) => void;
  onWebhookChange: (p: Partial<WebhookCfg>) => void;
  onMqttChange: (p: Partial<MqttCfg>) => void;
  onPreset: (p: HubPreset) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="grid gap-2 rounded-lg border border-border-subtle bg-muted/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">
          {t.reflexEditor.cardField_action}
        </span>
        {(["none", "webhook", "mqtt"] as ActionMode[]).map((m) => (
          <button
            key={m}
            disabled={readOnly}
            onClick={() => onModeChange(m)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors disabled:cursor-not-allowed",
              action.mode === m
                ? "bg-primary text-primary-foreground"
                : "bg-muted/30 text-muted-foreground hover:bg-muted/50",
            )}
          >
            {t.reflexEditor[`actionMode_${m}` as const]}
          </button>
        ))}
        {action.mode !== "none" && (
          <select
            disabled={readOnly}
            defaultValue=""
            onChange={(e) => {
              const p = HUB_PRESETS.find((x) => x.id === e.target.value);
              if (p) onPreset(p);
              e.target.value = "";
            }}
            className="ml-auto rounded-md border border-border-default bg-background px-2 py-1 text-xs outline-none disabled:cursor-not-allowed"
          >
            <option value="">{t.reflexEditor.cardField_hubPreset}</option>
            {HUB_PRESETS.filter((p) => p.forMode === action.mode).map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        )}
      </div>

      {action.mode === "webhook" && action.webhook && (
        <WebhookFields
          cfg={action.webhook}
          readOnly={readOnly}
          onChange={onWebhookChange}
        />
      )}
      {action.mode === "mqtt" && action.mqtt && (
        <MqttFields
          cfg={action.mqtt}
          readOnly={readOnly}
          onChange={onMqttChange}
        />
      )}
    </div>
  );
}

function WebhookFields({
  cfg,
  readOnly,
  onChange,
}: {
  cfg: WebhookCfg;
  readOnly: boolean;
  onChange: (p: Partial<WebhookCfg>) => void;
}) {
  const { t } = useI18n();
  const headerEntries = Object.entries(cfg.headers || {});
  const bodyText = useMemo(() => {
    if (cfg.body === null || cfg.body === undefined) return "";
    if (typeof cfg.body === "string") return cfg.body;
    try {
      return JSON.stringify(cfg.body, null, 2);
    } catch (e) {
      swallow(e);
      return "";
    }
  }, [cfg.body]);

  return (
    <div className="grid gap-2">
      <div className="flex gap-2">
        <select
          value={cfg.method}
          disabled={readOnly}
          onChange={(e) => onChange({ method: e.target.value })}
          className="rounded-md border border-border-default bg-background px-2 py-1.5 font-mono text-xs outline-none disabled:cursor-not-allowed"
        >
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
        </select>
        <input
          value={cfg.url}
          disabled={readOnly}
          onChange={(e) => onChange({ url: e.target.value })}
          placeholder="http://homeassistant.local:8123/api/services/light/turn_off"
          aria-label="URL"
          className="flex-1 rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-xs outline-none focus:border-primary disabled:cursor-not-allowed"
        />
      </div>
      <div className="grid gap-1">
        <label className="text-xs text-muted-foreground">
          {t.reflexEditor.cardField_headers}
        </label>
        <div className="grid gap-1">
          {headerEntries.map(([k, v], i) => (
            <div key={i} className="flex gap-1">
              <input
                value={k}
                disabled={readOnly}
                onChange={(e) => {
                  const next = { ...cfg.headers };
                  delete next[k];
                  next[e.target.value] = v;
                  onChange({ headers: next });
                }}
                placeholder="Authorization"
                aria-label="Header name"
                className="w-44 rounded-md border border-border-default bg-background px-2 py-1 font-mono text-xs outline-none disabled:cursor-not-allowed"
              />
              <input
                value={v}
                disabled={readOnly}
                onChange={(e) =>
                  onChange({ headers: { ...cfg.headers, [k]: e.target.value } })
                }
                placeholder="Bearer ..."
                aria-label="Header value"
                className="flex-1 rounded-md border border-border-default bg-background px-2 py-1 font-mono text-xs outline-none disabled:cursor-not-allowed"
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={readOnly}
                onClick={() => {
                  const next = { ...cfg.headers };
                  delete next[k];
                  onChange({ headers: next });
                }}
                aria-label="Delete header"
                className="rounded-md px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive disabled:cursor-not-allowed"
              >
                ×
              </Button>
            </div>
          ))}
          <Button
            size="sm"
            variant="outline"
            disabled={readOnly}
            onClick={() => onChange({ headers: { ...cfg.headers, "": "" } })}
            className="self-start border-dashed text-xs text-muted-foreground hover:bg-muted/30 disabled:cursor-not-allowed"
          >
            + {t.reflexEditor.cardField_addHeader}
          </Button>
        </div>
      </div>
      <div className="grid gap-1">
        <label className="text-xs text-muted-foreground">
          {t.reflexEditor.cardField_body}
        </label>
        <textarea
          value={bodyText}
          disabled={readOnly}
          rows={3}
          onChange={(e) => {
            const text = e.target.value;
            if (!text.trim()) {
              onChange({ body: null });
              return;
            }
            try {
              onChange({ body: JSON.parse(text) });
            } catch (e) {
              swallow(e);
              onChange({ body: text });
            }
          }}
          placeholder='{"entity_id":"light.living_room"}'
          className="rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-xs outline-none focus:border-primary disabled:cursor-not-allowed"
        />
      </div>
    </div>
  );
}

function MqttFields({
  cfg,
  readOnly,
  onChange,
}: {
  cfg: MqttCfg;
  readOnly: boolean;
  onChange: (p: Partial<MqttCfg>) => void;
}) {
  return (
    <div className="grid gap-2">
      <div className="flex gap-2">
        <input
          value={cfg.broker}
          disabled={readOnly}
          onChange={(e) => onChange({ broker: e.target.value })}
          placeholder="192.168.1.10"
          aria-label="MQTT broker"
          className="flex-1 rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-xs outline-none focus:border-primary disabled:cursor-not-allowed"
        />
        <input
          type="number"
          value={cfg.port}
          disabled={readOnly}
          onChange={(e) => onChange({ port: Number(e.target.value) || 1883 })}
          aria-label="Port"
          className="w-24 rounded-md border border-border-default bg-background px-2 py-1.5 font-mono text-xs outline-none disabled:cursor-not-allowed"
        />
      </div>
      <input
        value={cfg.topic}
        disabled={readOnly}
        onChange={(e) => onChange({ topic: e.target.value })}
        placeholder="zigbee2mqtt/living_room_light/set"
        aria-label="Topic"
        className="rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-xs outline-none focus:border-primary disabled:cursor-not-allowed"
      />
      <input
        value={cfg.payload}
        disabled={readOnly}
        onChange={(e) => onChange({ payload: e.target.value })}
        placeholder='{"state":"OFF"} 或 OFF'
        aria-label="Payload"
        className="rounded-md border border-border-default bg-background px-3 py-1.5 font-mono text-xs outline-none focus:border-primary disabled:cursor-not-allowed"
      />
      <div className="flex gap-3 text-xs text-muted-foreground">
        <label className="flex items-center gap-1">
          QoS
          <select
            value={cfg.qos}
            disabled={readOnly}
            onChange={(e) => onChange({ qos: Number(e.target.value) })}
            className="rounded-md border border-border-default bg-background px-2 py-0.5 font-mono text-xs disabled:cursor-not-allowed"
          >
            <option value={0}>0</option>
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={cfg.retain}
            disabled={readOnly}
            onChange={(e) => onChange({ retain: e.target.checked })}
            aria-label="Retain"
          />
          retain
        </label>
      </div>
    </div>
  );
}
