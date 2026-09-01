/**
 * Self-contained replay exporter — packages an agent run (a sequence of steps,
 * each with a title / detail / optional screenshot) into a single standalone
 * ``.html`` file with an embedded vanilla-JS player. No server, no login, no
 * network: double-click the file and it replays offline anywhere.
 *
 * The shape mirrors the in-app workbench replay, which steps through
 * ``WorkBlock``s (terminal / browser / file / search / …) rather than a screen
 * recording — so a step is primarily text (command + output), with an optional
 * inlined screenshot when one exists. That makes the export faithful for coding
 * runs, not just computer-use ones.
 *
 * This is the privacy-friendly answer to "shareable replay link": the artifact
 * is a file the user downloads (and can inspect) before sending, so redaction
 * happens at export time and nothing leaks beyond the steps handed in. Pure +
 * deterministic — given the same ReplayData, the same bytes — so it is fully
 * unit-testable without a browser. The caller (see ``replay-from-blocks``) maps
 * its ``WorkBlock``s into ``ReplayData`` and inlines any remote screenshots as
 * data-URLs first; this module never fetches.
 */

import {
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
} from "@/core/messages/utils";
import { escapeXml } from "./share-card";

export interface ReplayStep {
  /** One-line headline for the step (e.g. the tool/title). */
  title: string;
  /** Short secondary line (e.g. the target path / url). */
  subtitle?: string;
  /** Longer detail shown in a monospace block (command, output preview). */
  body?: string;
  /** Block kind — drives the glyph. One of terminal/browser/file/read/search/todo/agent/skill/swarm. */
  kind?: string;
  /** Status — drives the colour dot. done/error/warning/running/waiting_approval/pending. */
  status?: string;
  /** Optional inlined screenshot as a data-URL. Remote URLs must be inlined by the caller. */
  image?: string;
}

/** A durable, human-readable completion receipt. It deliberately contains
 * only user-facing facts; raw tool payloads stay in the individual steps. */
export interface ReplayReceiptItem {
  title: string;
  status: string;
  detail?: string;
}

export interface ReplayReceipt {
  summary?: string;
  items?: ReplayReceiptItem[];
  verification?: string[];
}

export interface ReplayData {
  title: string;
  steps: ReplayStep[];
  /** Brand line. Defaults to "EchoAI". */
  brand?: string;
  /** Footer note (e.g. a date). */
  footer?: string;
  /** Per-step dwell time in ms when playing. Default 1400. */
  frameMs?: number;
  /** Completed-case handoff, rendered above the replay rather than hidden in
   * the event list. This is optional so partial/live exports remain honest. */
  receipt?: ReplayReceipt;
}

/**
 * Embed a JS value safely inside a ``<script>`` tag: JSON-encode, then break up
 * any ``</`` and ``<!--`` so hostile content can't close the script element or
 * open a comment early.
 */
function embedJson(value: unknown): string {
  return JSON.stringify(value)
    .replace(/<\//g, "<\\/")
    .replace(/<!--/g, "<\\!--");
}

const INTERNAL_REPLAY_BLOCK_RE =
  /`?<(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)\b[^<>`]*>[\s\S]*?<\/(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)>`?/g;
const RAW_REPLAY_TOOL_NAME_RE =
  /\b(?:read_file|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace)\b/gi;
const REPLAY_SECRET_RE =
  /\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9]{8,}\b|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}|\b(?:Bearer|Authorization:?)\s+[A-Za-z0-9._-]{10,}|(["']?(?:api[_-]?key|secret|password|passwd|token)["']?\s*[:=]\s*)["']?[^\s"',}]{4,}/gi;

function cleanText(value: unknown): string {
  if (value == null) return "";
  const withoutInternalBlocks = String(value).replace(
    INTERNAL_REPLAY_BLOCK_RE,
    "",
  );
  return stripLeakedRendererMarkup(
    stripInternalToolProtocol(withoutInternalBlocks),
  )
    .replace(REPLAY_SECRET_RE, (_match, prefix?: string) =>
      prefix ? `${prefix}«redacted»` : "«redacted»",
    )
    .replace(RAW_REPLAY_TOOL_NAME_RE, "operation")
    .trim();
}

function cleanStep(step: ReplayStep): ReplayStep {
  return {
    title: cleanText(step.title),
    subtitle: cleanText(step.subtitle),
    body: cleanText(step.body),
    kind: cleanText(step.kind),
    status: cleanText(step.status),
    image: typeof step.image === "string" && step.image ? step.image : "",
  };
}

function cleanReceipt(
  receipt: ReplayReceipt | undefined,
): ReplayReceipt | undefined {
  if (!receipt) return undefined;
  const items = (receipt.items ?? [])
    .map((item) => ({
      title: cleanText(item.title),
      status: cleanText(item.status),
      detail: cleanText(item.detail),
    }))
    .filter((item) => item.title || item.detail);
  const verification = (receipt.verification ?? [])
    .map(cleanText)
    .filter(Boolean);
  const summary = cleanText(receipt.summary);
  return summary || items.length > 0 || verification.length > 0
    ? { summary, items, verification }
    : undefined;
}

export function buildReplayHtml(replay: ReplayData): string {
  const title = cleanText(replay.title || "EchoAI replay") || "EchoAI replay";
  const brand = cleanText(replay.brand || "EchoAI") || "EchoAI";
  const footer = cleanText(replay.footer || "");
  const frameMs = Number.isFinite(replay.frameMs)
    ? Math.max(200, replay.frameMs as number)
    : 1400;
  const steps = (replay.steps || [])
    .filter((s) => s && (s.title || s.body || s.image))
    .map(cleanStep);
  const doneCount = steps.filter((step) => step.status === "done").length;
  const attentionCount = steps.filter(
    (step) => step.status === "error" || step.status === "waiting_approval",
  ).length;
  const replayStatus = attentionCount > 0 ? "Needs attention" : "Replay ready";
  const receipt = cleanReceipt(replay.receipt);
  const receiptHtml = receipt
    ? `<section class="receipt" aria-label="Result receipt">
<div class="receipt-head"><div><div class="eyebrow">RESULT RECEIPT</div><h2>What was delivered</h2></div><span class="receipt-status">${attentionCount > 0 ? "Needs review" : "Ready to verify"}</span></div>
${receipt.summary ? `<p class="receipt-summary">${escapeXml(receipt.summary)}</p>` : ""}
${receipt.items && receipt.items.length > 0 ? `<div class="receipt-items">${receipt.items.map((item) => `<div class="receipt-item"><span class="receipt-dot ${escapeXml(item.status)}"></span><div><strong>${escapeXml(item.title)}</strong>${item.detail ? `<p>${escapeXml(item.detail)}</p>` : ""}</div><span class="receipt-item-status">${escapeXml(item.status || "done")}</span></div>`).join("")}</div>` : ""}
${receipt.verification && receipt.verification.length > 0 ? `<div class="verification"><div class="eyebrow">VERIFY IN YOUR BROWSER</div><ol>${receipt.verification.map((item) => `<li>${escapeXml(item)}</li>`).join("")}</ol></div>` : ""}
</section>`
    : "";

  const stepData = embedJson(steps);
  const empty = steps.length === 0;

  // Every dynamic value (titles, bodies, captions) is written into the DOM via
  // textContent / img.src by the player — never innerHTML — so step content can
  // never inject markup. Only the static page chrome is XML-escaped here.
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>${escapeXml(title)} · ${escapeXml(brand)}</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
:root { --ink:#202124; --muted:#6b7280; --line:#e5e7eb; --panel:#fff; --soft:#f7f7f5; --accent:#111827; }
body { margin:0; background:var(--soft); color:var(--ink); font:14px/1.5 system-ui,-apple-system,sans-serif; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 24px 18px 92px; }
.bar { height:3px; background:#111827; border-radius:2px; margin-bottom:18px; }
.brand { color:#6b7280; font-weight:600; font-size:12px; letter-spacing:.02em; }
h1 { font-size:24px; line-height:1.25; margin:5px 0 10px; color:var(--ink); word-break:break-word; }
.meta { display:flex; flex-wrap:wrap; align-items:center; gap:8px; color:var(--muted); font-size:12px; margin-bottom:18px; }
.meta .pill { border:1px solid var(--line); border-radius:999px; padding:3px 9px; background:#fff; }
.meta .status { color:#166534; border-color:#bbf7d0; background:#f0fdf4; }
.meta .attention { color:#92400e; border-color:#fde68a; background:#fffbeb; }
.receipt { margin:0 0 18px; border:1px solid var(--line); border-radius:14px; padding:18px 20px; background:var(--panel); box-shadow:0 1px 2px rgba(17,24,39,.03); }
.receipt-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.eyebrow { color:#6b7280; font-size:10px; font-weight:700; letter-spacing:.12em; }
.receipt h2 { margin:3px 0 0; font-size:16px; line-height:1.3; }
.receipt-status { flex:none; border-radius:999px; padding:3px 9px; background:#f0fdf4; color:#166534; font-size:11px; font-weight:600; }
.receipt-summary { margin:12px 0 0; color:#4b5563; }
.receipt-items { margin-top:14px; border-top:1px solid #f0f1f2; }
.receipt-item { display:flex; align-items:flex-start; gap:9px; padding:10px 0; border-bottom:1px solid #f0f1f2; }
.receipt-item > div { min-width:0; flex:1; }
.receipt-item strong { font-size:13px; }
.receipt-item p { margin:2px 0 0; color:var(--muted); font-size:12px; }
.receipt-dot { width:8px; height:8px; margin-top:5px; border-radius:50%; background:#9ca3af; }
.receipt-dot.done { background:#22c55e; }.receipt-dot.running { background:#6366f1; }.receipt-dot.error { background:#ef4444; }.receipt-dot.waiting_approval { background:#f59e0b; }
.receipt-item-status { flex:none; color:var(--muted); font-size:11px; text-transform:capitalize; }
.verification { margin-top:16px; padding:12px; border-radius:10px; background:#fafafa; }
.verification ol { margin:7px 0 0 18px; padding:0; color:#4b5563; font-size:12px; }.verification li + li { margin-top:4px; }
.layout { display:grid; grid-template-columns: 330px 1fr; gap:18px; align-items:start; }
@media (max-width:760px){ .layout { grid-template-columns:1fr; } }
.steps { border:1px solid var(--line); border-radius:14px; overflow:hidden; max-height:64vh; overflow-y:auto; background:var(--panel); box-shadow:0 1px 2px rgba(17,24,39,.03); }
.row { display:flex; align-items:center; gap:9px; width:100%; padding:11px 12px; border:0; background:transparent; color:inherit; cursor:pointer; text-align:left; font:inherit; border-bottom:1px solid #f0f1f2; }
.row:hover { background:#fafafa; }
.row.active { background:#f3f4f6; }
.row .n { color:#9ca3af; font-variant-numeric:tabular-nums; font-size:11px; min-width:24px; }
.row .glyph { width:18px; text-align:center; }
.row .t { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
.dot { width:7px; height:7px; border-radius:50%; flex:none; background:#9ca3af; }
.detail { border:1px solid var(--line); border-radius:14px; padding:20px; background:var(--panel); min-height:230px; box-shadow:0 1px 2px rgba(17,24,39,.03); }
.detail .dtitle { font-size:18px; font-weight:650; color:var(--ink); word-break:break-word; }
.detail .dsub { color:var(--muted); font-size:12px; margin-top:3px; word-break:break-word; }
.detail img { max-width:100%; max-height:48vh; display:block; margin:14px 0; border-radius:10px; border:1px solid var(--line); }
.detail pre { white-space:pre-wrap; word-break:break-word; background:#fafafa; border:1px solid var(--line); border-radius:9px; padding:12px; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; color:#374151; margin:14px 0 0; max-height:40vh; overflow:auto; }
.controls { display:flex; align-items:center; gap:8px; margin-top:14px; padding:10px 0 0; border-top:1px solid #f0f1f2; position:sticky; bottom:0; background:linear-gradient(transparent,var(--soft) 22%); }
button { background:#fff; color:var(--ink); border:1px solid var(--line); border-radius:8px; padding:6px 11px; cursor:pointer; font:inherit; }
button:hover { background:#f3f4f6; }
input[type=range] { flex:1; accent-color:#111827; }
.counter { color:var(--muted); font-variant-numeric:tabular-nums; min-width:52px; text-align:right; }
.hint { margin-top:8px; color:#9ca3af; font-size:11px; }
button[aria-pressed="true"] { background:#111827; border-color:#111827; color:#fff; }
.dock { position:fixed; left:50%; bottom:18px; transform:translateX(-50%); width:min(720px,calc(100% - 32px)); display:flex; align-items:center; gap:10px; padding:10px 12px; border:1px solid var(--line); border-radius:14px; background:rgba(255,255,255,.94); box-shadow:0 8px 30px rgba(17,24,39,.12); backdrop-filter:blur(12px); }
.dock .live { width:8px; height:8px; border-radius:50%; background:#22c55e; }
.dock .dock-label { font-weight:600; font-size:12px; }
.dock .dock-meta { color:var(--muted); font-size:12px; }
.dock .spacer { flex:1; }
.empty { color:#6b7280; padding:48px; text-align:center; }
footer { margin-top:20px; color:#9ca3af; font-size:12px; }
</style>
</head>
<body>
<div class="wrap">
<div class="bar"></div>
<div class="brand">${escapeXml(brand)} · replay</div>
<h1>${escapeXml(title)}</h1>
<div class="meta">
<span class="pill ${attentionCount > 0 ? "attention" : "status"}">${escapeXml(replayStatus)}</span>
<span class="pill">${steps.length} steps</span>
<span class="pill">${doneCount} completed</span>
${attentionCount > 0 ? `<span class="pill attention">${attentionCount} needs attention</span>` : ""}
</div>
${receiptHtml}
${
  empty
    ? `<div class="detail"><div class="empty">No steps in this replay.</div></div>`
    : `<div class="layout">
<div class="steps" id="steps"></div>
<div>
<div class="detail">
<div class="dtitle" id="dtitle"></div>
<div class="dsub" id="dsub"></div>
<img id="dimg" alt="" hidden/>
<pre id="dbody" hidden></pre>
</div>
<div class="controls">
<button id="prev" type="button">⟨ Prev</button>
<button id="play" type="button">▶ Play</button>
<button id="next" type="button">Next ⟩</button>
<button id="loop" type="button" aria-pressed="false" title="循环播放">⟲ Loop</button>
<button id="speed" type="button" title="播放速度">1×</button>
<input id="seek" type="range" min="0" value="0"/>
<span class="counter" id="counter"></span>
</div>
<div class="hint">← → 切换 · 空格 播放/暂停 · Home/End 首尾</div>
</div>
</div>`
}
${
  empty
    ? ""
    : `<div class="dock" role="status" aria-live="polite">
<span class="live"></span>
<span class="dock-label">Replay ${attentionCount > 0 ? "needs attention" : "ready"}</span>
<span class="dock-meta">${doneCount}/${steps.length} completed</span>
<span class="spacer"></span>
<button id="dock-play" type="button">▶ Play replay</button>
</div>`
}
<footer>${escapeXml(footer)}</footer>
</div>
${
  empty
    ? ""
    : `<script>
(function(){
  var STEPS = ${stepData};
  var MS = ${frameMs};
  var GLYPH = { terminal:'❯', browser:'\u{1f310}', file:'\u{1f4c4}', read:'\u{1f4d6}', search:'\u{1f50d}', todo:'☑', agent:'\u{1f916}', skill:'\u{1f9e9}', swarm:'\u{1f41d}' };
  var DOT = { done:'#22c55e', error:'#ef4444', running:'#6366f1', waiting_approval:'#f59e0b' };
  var i = 0, timer = null, loop = false, speedIdx = 1;
  var SPEEDS = [0.5, 1, 2];
  var stepsEl = document.getElementById('steps');
  var dtitle = document.getElementById('dtitle');
  var dsub = document.getElementById('dsub');
  var dimg = document.getElementById('dimg');
  var dbody = document.getElementById('dbody');
  var counter = document.getElementById('counter');
  var seek = document.getElementById('seek');
  var play = document.getElementById('play');
  var dockPlay = document.getElementById('dock-play');
  var loopBtn = document.getElementById('loop');
  var speedBtn = document.getElementById('speed');
  var rows = [];
  seek.max = String(STEPS.length - 1);
  STEPS.forEach(function(s, idx){
    var row = document.createElement('button');
    row.type = 'button'; row.className = 'row';
    var n = document.createElement('span'); n.className = 'n'; n.textContent = String(idx + 1);
    var g = document.createElement('span'); g.className = 'glyph'; g.textContent = GLYPH[s.kind] || '•';
    var t = document.createElement('span'); t.className = 't'; t.textContent = s.title || '(step)';
    var d = document.createElement('span'); d.className = 'dot'; d.style.background = DOT[s.status] || '#475569';
    row.appendChild(n); row.appendChild(g); row.appendChild(t); row.appendChild(d);
    row.onclick = function(){ stop(); i = idx; render(); };
    stepsEl.appendChild(row); rows.push(row);
  });
  function render(){
    var s = STEPS[i] || {};
    rows.forEach(function(r, idx){ r.classList.toggle('active', idx === i); });
    if (rows[i]) rows[i].scrollIntoView({ block: 'nearest' });
    dtitle.textContent = s.title || '';
    dsub.textContent = s.subtitle || '';
    if (s.image){ dimg.src = s.image; dimg.hidden = false; } else { dimg.removeAttribute('src'); dimg.hidden = true; }
    if (s.body){ dbody.textContent = s.body; dbody.hidden = false; } else { dbody.textContent = ''; dbody.hidden = true; }
    counter.textContent = (i + 1) + ' / ' + STEPS.length;
    seek.value = String(i);
  }
  function stop(){ if (timer){ clearInterval(timer); timer = null; } play.textContent = '▶ Play'; if (dockPlay) dockPlay.textContent = '▶ Play replay'; }
  function tick(){
    if (i >= STEPS.length - 1){ if (loop){ i = 0; render(); return; } stop(); return; }
    i++; render();
  }
  function start(){
    if (i >= STEPS.length - 1 && !loop) i = 0;
    play.textContent = '❚❚ Pause';
    if (dockPlay) dockPlay.textContent = '❚❚ Pause replay';
    timer = setInterval(tick, Math.round(MS / SPEEDS[speedIdx]));
  }
  function go(n){ stop(); i = Math.max(0, Math.min(STEPS.length - 1, n)); render(); }
  play.onclick = function(){ timer ? stop() : start(); };
  if (dockPlay) dockPlay.onclick = function(){ timer ? stop() : start(); };
  loopBtn.onclick = function(){ loop = !loop; loopBtn.setAttribute('aria-pressed', loop ? 'true' : 'false'); };
  speedBtn.onclick = function(){
    speedIdx = (speedIdx + 1) % SPEEDS.length;
    speedBtn.textContent = SPEEDS[speedIdx] + '×';
    if (timer){ clearInterval(timer); timer = setInterval(tick, Math.round(MS / SPEEDS[speedIdx])); }
  };
  document.getElementById('prev').onclick = function(){ go(i - 1); };
  document.getElementById('next').onclick = function(){ go(i + 1); };
  seek.oninput = function(){ go(Number(seek.value) || 0); };
  document.addEventListener('keydown', function(e){
    if (e.key === 'ArrowLeft'){ go(i - 1); }
    else if (e.key === 'ArrowRight'){ go(i + 1); }
    else if (e.key === 'Home'){ go(0); }
    else if (e.key === 'End'){ go(STEPS.length - 1); }
    else if (e.key === ' ' || e.key === 'Spacebar'){ e.preventDefault(); timer ? stop() : start(); }
  });
  render();
})();
</script>`
}
</body>
</html>`;
}
