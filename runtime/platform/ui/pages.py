"""Inline fallback/admin HTML pages for the UI app."""

from __future__ import annotations

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>echo-agent dashboard</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 20px; background: #0a0e14; color: #d3d7de; }
  h1 { color: #fff; margin: 0 0 8px 0; font-weight: 600; }
  .tagline { color: #6e7278; margin: 0 0 24px 0; font-size: 13px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
  .card { background: #111721; border: 1px solid #1f2733; border-radius: 8px; padding: 16px; }
  .card h2 { margin: 0 0 12px 0; font-size: 14px; color: #89b4fa; text-transform: uppercase; letter-spacing: .05em; }
  .metric { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #1a1f2a; font-size: 13px; }
  .metric:last-child { border-bottom: none; }
  .metric span:last-child { color: #a6e3a1; font-family: "SF Mono", Consolas, monospace; }
  .bad { color: #f38ba8 !important; }
  pre { background: #0a0e14; border: 1px solid #1f2733; border-radius: 4px; padding: 10px; overflow-x: auto; font-size: 12px; color: #cdd6f4; max-height: 300px; }
  button { background: #1a2433; color: #89b4fa; border: 1px solid #1f2733; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  button:hover { background: #232d3d; }
  input, select { background: #0a0e14; color: #d3d7de; border: 1px solid #1f2733; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
  .row { display: flex; gap: 8px; margin-bottom: 8px; }
  .row > * { flex: 1; }
  footer { margin-top: 32px; font-size: 11px; color: #4a5058; text-align: center; }
</style>
</head>
<body>
<h1>🐙 echo-agent</h1>
<p class="tagline" id="tagline">loading…</p>

<div class="grid">

  <div class="card">
    <h2>Status</h2>
    <div id="status">loading...</div>
  </div>

  <div class="card">
    <h2>Skills (<span id="skillcount">–</span>)</h2>
    <div id="skills">loading...</div>
  </div>

  <div class="card" style="grid-column: 1 / -1;">
    <h2>Tasks (<span id="taskcount">0</span> · <span id="taskrunning">0</span> running)</h2>
    <div id="tasks" style="font-size:12px; font-family:'SF Mono',Consolas,monospace;">loading...</div>
  </div>

  <div class="card">
    <h2>Journal</h2>
    <div id="journal">loading...</div>
  </div>

  <div class="card">
    <h2>Reflection</h2>
    <div id="reflect">loading...</div>
  </div>

  <div class="card" style="grid-column: 1 / -1;">
    <h2>Run</h2>
    <div class="row">
      <input id="goal" placeholder="goal · e.g. 'list files'" value="list files">
      <select id="planner"><option>static</option><option>llm</option></select>
      <button onclick="runGoal()">Run</button>
    </div>
    <pre id="runout">(press Run)</pre>
  </div>

  <div class="card" style="grid-column: 1 / -1;">
    <h2>Knowledge Graph</h2>
    <div class="row">
      <input id="kgsubj" placeholder="subject (optional)">
      <input id="kgpred" placeholder="predicate (optional)">
      <button onclick="queryKG()">Query</button>
    </div>
    <pre id="kgout">(press Query)</pre>
  </div>

</div>
<footer>echo-agent · <span id="version">?</span> · <a href="/docs" style="color:#6e7278;">API docs</a></footer>

<script>
function authHeaders() {
  const token = window.localStorage.getItem('echo_auth_token');
  return token ? {'Authorization': `Bearer ${token}`} : {};
}
function hasAuthToken() {
  return !!window.localStorage.getItem('echo_auth_token');
}
function clearAuthToken() {
  window.localStorage.removeItem('echo_auth_token');
  window.localStorage.removeItem('echo_user');
  window.localStorage.removeItem('echo_auth_ts');
  window.sessionStorage.removeItem('echo_auth_token');
  window.sessionStorage.removeItem('echo_user');
}
function showAuthRequired(id) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = '<div style="color:#6e7278;font-size:12px;">login required</div>';
}
async function fetchJSON(url, opts={}) {
  const r = await fetch(url, {
    ...opts,
    headers: {
      ...authHeaders(),
      ...(opts.headers || {}),
    },
  });
  if (r.status === 401) {
    clearAuthToken();
  }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function metric(label, value, bad=false) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><span class="${bad?'bad':''}">${escapeHtml(value)}</span></div>`;
}

async function loadStatus() {
  const s = await fetchJSON('/api/status');
  document.getElementById('version').textContent = s.version;
  document.getElementById('tagline').textContent = s.tagline;
  const el = document.getElementById('status');
  el.innerHTML = [
    metric('version', s.version),
    metric('skills', s.skill_count),
    metric('OTel', s.capabilities.opentelemetry ? '✓' : '✗', !s.capabilities.opentelemetry),
    metric('MCP SDK', s.capabilities.mcp ? '✓' : '✗', !s.capabilities.mcp),
    metric('httpx', s.capabilities.httpx ? '✓' : '✗', !s.capabilities.httpx),
    metric('Anthropic', s.capabilities.anthropic ? '✓' : '✗', !s.capabilities.anthropic),
  ].join('');
}
async function loadSkills() {
  const data = await fetchJSON('/api/skills');
  document.getElementById('skillcount').textContent = data.skills.length;
  document.getElementById('skills').innerHTML = data.skills.map(s =>
    metric(s.name, s.cost_profile)
  ).join('');
}
async function loadJournal() {
  if (!hasAuthToken()) { showAuthRequired('journal'); return; }
  let data;
  try {
    data = await fetchJSON('/api/journal');
  } catch (e) {
    showAuthRequired('journal');
    return;
  }
  const el = document.getElementById('journal');
  if (data.total === 0) {
    el.innerHTML = '<div style="color:#6e7278;font-size:12px;">(no events · pass --journal path to start)</div>';
    return;
  }
  el.innerHTML = [
    metric('total events', data.total),
    metric('steps', data.counts.step || 0),
    metric('trajectories', data.counts.trajectory || 0),
    metric('immune checks', data.counts.immune || 0),
    metric('budget commits', data.counts.budget_commit || 0),
  ].join('');
}
async function loadReflect() {
  if (!hasAuthToken()) { showAuthRequired('reflect'); return; }
  let r;
  try {
    r = await fetchJSON('/api/reflect');
  } catch (e) {
    showAuthRequired('reflect');
    return;
  }
  if (r.error) {
    document.getElementById('reflect').innerHTML = `<div style="color:#6e7278;font-size:12px;">${escapeHtml(r.error)}</div>`;
    return;
  }
  document.getElementById('reflect').innerHTML = [
    metric('[1] skill_forge',    `${r.skill_forge.promoted} promoted`),
    metric('[2] rules',           `${r.rule_extractor.rules}`),
    metric('[3] kg triples',      `${r.kg.accepted}`),
    metric('[4] memories',        `${r.memory.memories}`),
    metric('[5] wf proposals',    `${r.workflow.proposals}`),
    metric('[6] recipes',         `${r.recipe.recipes} found`),
  ].join('');
}
async function runGoal() {
  const goal = document.getElementById('goal').value;
  const planner = document.getElementById('planner').value;
  const out = document.getElementById('runout');
  out.textContent = 'running...';
  try {
    const r = await fetchJSON('/api/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({goal, planner}),
    });
    out.textContent = JSON.stringify(r, null, 2);
    loadJournal();
    loadReflect();
  } catch (e) { out.textContent = 'ERROR: ' + e; }
}
async function queryKG() {
  const subject = document.getElementById('kgsubj').value || null;
  const predicate = document.getElementById('kgpred').value || null;
  const params = new URLSearchParams();
  if (subject) params.set('subject', subject);
  if (predicate) params.set('predicate', predicate);
  try {
    const r = await fetchJSON('/api/kg?' + params);
    document.getElementById('kgout').textContent =
      `${r.count} triples\\n\\n` +
      r.triples.map(t => `(${t.subject}, ${t.predicate}, ${t.object})`).join('\\n');
  } catch(e) {
    document.getElementById('kgout').textContent = 'ERROR: ' + e;
  }
}

async function loadProgress() {
  try {
    if (!hasAuthToken()) { showAuthRequired('tasks'); return; }
    const r = await fetchJSON('/api/progress');
    document.getElementById('taskcount').textContent = r.count;
    document.getElementById('taskrunning').textContent = r.running;
    const el = document.getElementById('tasks');
    if (!r.tasks.length) { el.textContent = '(no tasks yet)'; return; }
    el.innerHTML = r.tasks.map(t => {
      const progress = Math.max(0, Math.min(100, Number(t.progress_pct) || 0));
      const filled = Math.round(progress / 10);
      const bar = '█'.repeat(filled) + '░'.repeat(10 - filled);
      const statusColor = t.status === 'running' ? '#f9e2af'
                        : t.status === 'completed' ? '#a6e3a1'
                        : t.status === 'failed' ? '#f38ba8' : '#6e7278';
      const cur = t.current_node_id ? ` · at ${escapeHtml(t.current_node_id)}` : '';
      return `<div style="padding:4px 0;border-bottom:1px solid #1a1f2a">
        <span style="color:${statusColor}">[${escapeHtml(t.status)}]</span>
        <span>${escapeHtml(String(t.task_id ?? '').slice(0, 8))}</span>
        <span style="color:#6e7278">${escapeHtml(t.strategy)}</span>
        <span>${bar} ${escapeHtml(t.nodes_completed)}/${escapeHtml(t.total_nodes)}</span>${cur}
      </div>`;
    }).join('');
  } catch(e) {
    document.getElementById('tasks').textContent = 'ERROR: ' + e;
  }
}

loadStatus(); loadSkills(); loadJournal(); loadReflect(); loadProgress();
setInterval(() => { loadJournal(); loadReflect(); loadProgress(); }, 5000);
</script>
</body>
</html>"""


# Self-contained reflex monitoring panel · zero JS deps, polls API
# every 2 s, renders a sortable table of rules + per-variant breakdown.
# Served from /admin/reflex when reflex_router is wired (see app
# factory below). Kept inline as a string so a sysadmin can deploy
# the panel by visiting one URL · no extra build step.
_REFLEX_PANEL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Reflex monitor · echo-agent</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 20px; background: #0a0e14; color: #d3d7de; font-size: 13px; }
  h1 { color: #fff; margin: 0; font-weight: 600; font-size: 18px; }
  .sub { color: #6e7278; margin: 4px 0 20px 0; font-size: 12px; }
  .row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat { background: #111721; border: 1px solid #1f2733; border-radius: 6px; padding: 12px 16px; min-width: 120px; }
  .stat .label { color: #6e7278; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
  .stat .val { color: #a6e3a1; font-size: 24px; font-family: "SF Mono", Consolas, monospace; }
  .stat.warn .val { color: #f9e2af; }
  table { width: 100%; border-collapse: collapse; background: #111721; border-radius: 6px; overflow: hidden; }
  th { background: #1a1f2a; color: #89b4fa; text-align: left; padding: 10px 12px; font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
  td { padding: 10px 12px; border-top: 1px solid #1a1f2a; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
  tr:hover td { background: #161c25; }
  .id { color: #cdd6f4; }
  .pat { color: #94e2d5; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .kind { color: #f5c2e7; }
  .num { color: #fab387; text-align: right; }
  .rate { color: #a6e3a1; }
  .rate.zero { color: #585b70; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; background: #313244; color: #cdd6f4; font-size: 10px; margin-right: 4px; }
  .badge.action { background: #1e3a5f; color: #89dceb; }
  .badge.variant { background: #5b3a1e; color: #fab387; }
  .badge.stale { background: #5b1e1e; color: #f38ba8; }
  .badge.unex { background: #3a3a3a; color: #9399b2; }
  .variants { margin-top: 6px; padding-left: 12px; font-size: 11px; color: #6c7086; }
  .variants .v { display: flex; gap: 8px; padding: 2px 0; }
  .variants .vid { color: #fab387; min-width: 30px; }
  .variants .preview { color: #9399b2; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .variants .vhits { color: #a6e3a1; min-width: 40px; text-align: right; }
  button { background: #1e3a5f; color: #89dceb; border: 1px solid #1e3a5f; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; margin-right: 8px; }
  button:hover { background: #2a4a73; }
  .ok { color: #a6e3a1; }
  .err { color: #f38ba8; }
</style>
</head>
<body>
<h1>SpinalCord reflex monitor</h1>
<div class="sub">Live · polls every 2 s · <span id="ts">never</span></div>

<div class="row">
  <div class="stat"><div class="label">Try count</div><div class="val" id="try">0</div></div>
  <div class="stat"><div class="label">Hit count</div><div class="val" id="hit">0</div></div>
  <div class="stat"><div class="label">Hit rate</div><div class="val" id="rate">0%</div></div>
  <div class="stat"><div class="label">Rules loaded</div><div class="val" id="nrules">0</div></div>
  <div class="stat warn"><div class="label">Stale (24h)</div><div class="val" id="stale">0</div></div>
  <div class="stat"><div class="label">Last hour hits</div><div class="val" id="lasthour">0</div></div>
</div>

<div class="row">
  <div style="background:#111721;border:1px solid #1f2733;border-radius:6px;padding:12px 16px;flex:1;min-width:400px;">
    <div style="color:#6e7278;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">
      Reflex hits · last 60 min · 1 min buckets
    </div>
    <canvas id="spark" width="900" height="60" style="width:100%;height:60px;"></canvas>
  </div>
</div>

<div class="row">
  <button onclick="reload(false)">Reload rules from yaml</button>
  <button onclick="reload(true)">Reload + reset stats</button>
  <span id="reload-msg"></span>
</div>

<table>
  <thead><tr>
    <th>Rule</th><th>Kind</th><th>Pattern / Type</th><th>Prio</th>
    <th class="num">Tries</th><th class="num">Hits</th><th class="num">Rate</th>
    <th class="num">Last hit</th>
  </tr></thead>
  <tbody id="rows"></tbody>
</table>

<script>
function drawSpark(buckets) {
  const c = document.getElementById('spark');
  if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.clientWidth, h = c.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!buckets || !buckets.length) return;
  const max = Math.max(1, ...buckets.map(b => b.count));
  const bw = w / buckets.length;
  ctx.fillStyle = '#a6e3a1';
  for (let i = 0; i < buckets.length; i++) {
    const bh = (buckets[i].count / max) * (h - 4);
    ctx.fillRect(i * bw + 1, h - bh, Math.max(1, bw - 2), bh);
  }
  ctx.fillStyle = '#1f2733';
  ctx.fillRect(0, h - 1, w, 1);
}

async function refresh() {
  try {
    const [stats, rulesResp, ts] = await Promise.all([
      fetch('/api/reflex/stats').then(r => r.json()),
      fetch('/api/reflex/rules').then(r => r.json()),
      fetch('/api/reflex/timeseries?window_minutes=60&bucket_seconds=60').then(r => r.json()),
    ]);
    document.getElementById('try').textContent = stats.try_count;
    document.getElementById('hit').textContent = stats.hit_count;
    document.getElementById('rate').textContent = (stats.hit_rate * 100).toFixed(1) + '%';
    document.getElementById('nrules').textContent = rulesResp.rules.length;
    const staleEl = document.getElementById('stale');
    if (staleEl) staleEl.textContent = (stats.coverage && stats.coverage.stale ? stats.coverage.stale.length : 0);
    const lhEl = document.getElementById('lasthour');
    if (lhEl) lhEl.textContent = ts.total_events || 0;
    document.getElementById('ts').textContent = new Date().toLocaleTimeString();
    drawSpark(ts.buckets);

    const staleSet = new Set((stats.coverage && stats.coverage.stale) || []);
    const unexSet = new Set((stats.coverage && stats.coverage.unexercised) || []);

    const rows = rulesResp.rules.sort((a, b) => b.priority - a.priority).map(r => {
      const s = stats.by_rule[r.rule_id] || { tries: 0, hits: 0, hit_rate: 0 };
      const pat = r.pattern || r.intent_type || (r.kind === 'cache' ? `ttl=${r.ttl_seconds}s` : '');
      const badges = [];
      if (r.actions) r.actions.forEach(a => badges.push(`<span class="badge action">${escapeHtml(a)}</span>`));
      if (r.variants) badges.push(`<span class="badge variant">A/B ×${r.variants.length}</span>`);
      if (staleSet.has(r.rule_id)) badges.push(`<span class="badge stale">stale</span>`);
      if (unexSet.has(r.rule_id)) badges.push(`<span class="badge unex">unexercised</span>`);
      let variantsHtml = '';
      if (r.variants) {
        variantsHtml = '<div class="variants">' + r.variants.map(v =>
          `<div class="v"><span class="vid">${escapeHtml(v.variant_id)}</span><span class="preview">${escapeHtml(v.preview)}</span><span class="vhits">${escapeHtml(v.hits)}× (w=${escapeHtml(v.weight)})</span></div>`
        ).join('') + '</div>';
      }
      const rateClass = s.hits > 0 ? 'rate' : 'rate zero';
      let lastHit = '—';
      if (r.last_hit_at) {
        const hours = (Date.now() / 1000 - r.last_hit_at) / 3600;
        if (hours < 1) lastHit = `${Math.round(hours * 60)}m ago`;
        else if (hours < 24) lastHit = `${hours.toFixed(1)}h ago`;
        else lastHit = `${(hours / 24).toFixed(1)}d ago`;
      }
      return `<tr>
        <td class="id">${escapeHtml(r.rule_id)} ${badges.join(' ')}${variantsHtml}</td>
        <td class="kind">${escapeHtml(r.kind)}</td>
        <td class="pat" title="${escapeHtml(pat)}">${escapeHtml(pat)}</td>
        <td class="num">${escapeHtml(r.priority)}</td>
        <td class="num">${escapeHtml(s.tries)}</td>
        <td class="num">${escapeHtml(s.hits)}</td>
        <td class="num ${rateClass}">${(s.hit_rate * 100).toFixed(0)}%</td>
        <td class="num" style="color:#6c7086">${lastHit}</td>
      </tr>`;
    }).join('');
    document.getElementById('rows').innerHTML = rows || '<tr><td colspan="8">no rules</td></tr>';
  } catch (e) {
    document.getElementById('ts').textContent = 'ERROR: ' + e.message;
  }
}
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function setReloadMessage(message, kind) {
  const element = document.getElementById('reload-msg');
  element.className = kind || '';
  element.textContent = String(message ?? '');
}
async function reload(reset) {
  const url = '/api/reflex/reload' + (reset ? '?reset_stats=true' : '');
  try {
    const r = await fetch(url, { method: 'POST' });
    const j = await r.json();
    if (j.ok) {
      setReloadMessage(`✓ loaded ${j.rules_loaded} rules${j.stats_reset ? ' · stats reset' : ''}`, 'ok');
    } else {
      setReloadMessage(`✗ ${j.error}`, 'err');
    }
    refresh();
  } catch (e) {
    setReloadMessage(`✗ ${e.message}`, 'err');
  }
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


# YAML rule editor · /admin/reflex/edit · zero-build, talks to
# /api/reflex/rules-yaml (GET to load, POST to save+reload). The
# textarea uses ``white-space: pre`` for monospace yaml editing
# and the "Test" button POSTs current buffer to /api/reflex/test
# so the operator can validate before saving.
_REFLEX_EDITOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Reflex rules editor · echo-agent</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; background: #0a0e14; color: #d3d7de; font-size: 13px; }
  h1 { color: #fff; margin: 0; font-weight: 600; font-size: 18px; }
  .sub { color: #6e7278; margin: 4px 0 16px 0; font-size: 12px; }
  .row { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
  button { background: #1e3a5f; color: #89dceb; border: 1px solid #2a4a73; padding: 7px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  button:hover { background: #2a4a73; }
  button.primary { background: #1e5f3a; color: #a6e3a1; border-color: #2a7350; }
  button.primary:hover { background: #2a7350; }
  button.danger { background: #5b1e1e; color: #f38ba8; border-color: #732a2a; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .status { padding: 4px 10px; border-radius: 3px; font-family: "SF Mono", Consolas, monospace; font-size: 11px; }
  .status.ok { background: #1e3f1e; color: #a6e3a1; }
  .status.err { background: #3f1e1e; color: #f38ba8; }
  .status.warn { background: #3f3a1e; color: #f9e2af; }
  textarea { width: 100%; height: calc(100vh - 220px); background: #111721; color: #cdd6f4; border: 1px solid #1f2733; border-radius: 6px; padding: 12px; font-family: "SF Mono", Consolas, "Liberation Mono", monospace; font-size: 12px; line-height: 1.5; tab-size: 2; box-sizing: border-box; resize: vertical; }
  textarea:focus { outline: none; border-color: #89b4fa; }
  .meta { color: #6c7086; font-size: 11px; font-family: monospace; }
  pre.test-out { background: #111721; border: 1px solid #1f2733; border-radius: 6px; padding: 10px 14px; max-height: 180px; overflow: auto; white-space: pre-wrap; font-size: 11px; color: #cdd6f4; margin: 8px 0; }
  pre.test-out .pass { color: #a6e3a1; }
  pre.test-out .fail { color: #f38ba8; }
</style>
</head>
<body>
<h1>Reflex rules editor</h1>
<div class="sub"><span id="path">…</span> · <span id="meta" class="meta"></span> · <a href="/admin/reflex" style="color:#89dceb">← back to monitor</a></div>

<div class="row">
  <button onclick="loadFile()">Reload from disk</button>
  <button onclick="runTests()">Run tests</button>
  <button class="primary" onclick="save(true)">Save + hot-reload</button>
  <button onclick="save(false)">Save (no reload)</button>
  <span id="status" class="status">idle</span>
</div>

<div id="testbox" style="display:none"><pre class="test-out" id="testout"></pre></div>

<textarea id="ed" spellcheck="false" placeholder="Loading…"></textarea>

<script>
let mtime = 0;
async function loadFile() {
  setStatus('loading…');
  const r = await fetch('/api/reflex/rules-yaml').then(r => r.json());
  if (!r.ok) { setStatus('load failed: ' + r.error, 'err'); return; }
  document.getElementById('ed').value = r.content;
  document.getElementById('path').textContent = r.path;
  document.getElementById('meta').textContent = r.size + ' bytes · mtime ' + new Date(r.mtime * 1000).toLocaleString();
  mtime = r.mtime;
  setStatus('loaded', 'ok');
}
async function save(reload) {
  setStatus('saving…');
  const body = {
    content: document.getElementById('ed').value,
    expected_mtime: mtime,
    reload: reload,
  };
  const r = await fetch('/api/reflex/rules-yaml', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  }).then(r => r.json());
  if (!r.ok) { setStatus('save failed: ' + r.error, 'err'); return; }
  mtime = r.new_mtime;
  let msg = 'saved · ' + r.rules_in_file + ' rules in file';
  if (r.reloaded === true) msg += ' · reloaded ' + r.rules_loaded + ' active';
  if (r.reloaded === false) msg += ' · reload failed: ' + r.reload_error;
  setStatus(msg, r.reloaded === false ? 'warn' : 'ok');
  document.getElementById('meta').textContent = body.content.length + ' bytes · mtime ' + new Date(mtime * 1000).toLocaleString();
}
async function runTests() {
  setStatus('running tests…');
  const r = await fetch('/api/reflex/test').then(r => r.json());
  document.getElementById('testbox').style.display = 'block';
  if (r.error) {
    document.getElementById('testout').innerHTML = '<span class="fail">ERROR: ' + escapeHtml(r.error) + '</span>';
    setStatus('test error', 'err');
    return;
  }
  const head = `${r.passed}/${r.total} passed, ${r.failed} failed`;
  let body = '';
  if (r.failures && r.failures.length) {
    body = r.failures.map(f =>
      `<span class="fail">✗ rule=${escapeHtml(f.source_rule_id)} input=${escapeHtml(JSON.stringify(f.input))} · ${escapeHtml(f.reason)}</span>`
    ).join('\\n');
  } else {
    body = '<span class="pass">✓ all green</span>';
  }
  document.getElementById('testout').innerHTML = escapeHtml(head) + '\\n' + body;
  setStatus(head, r.failed === 0 ? 'ok' : 'err');
}
function setStatus(msg, kind) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status' + (kind ? ' ' + kind : '');
}
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
// Ctrl/Cmd+S = save+reload (vim-friendly).
window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    save(true);
  }
});
loadFile();
</script>
</body>
</html>"""
