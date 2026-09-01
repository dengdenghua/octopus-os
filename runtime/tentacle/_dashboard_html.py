"""Dashboard HTML 模板（自包含，无外部依赖）."""

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Echo Tentacle Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --purple: #bc8cff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 20px; }
  h1 { font-size: 24px; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
  h1 span.icon { font-size: 28px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 16px; color: var(--text2); margin-bottom: 12px;
             text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-row { display: flex; gap: 16px; margin-bottom: 12px; }
  .stat { flex: 1; text-align: center; padding: 12px; background: var(--bg);
          border-radius: 6px; }
  .stat .num { font-size: 28px; font-weight: 700; }
  .stat .label { font-size: 12px; color: var(--text2); margin-top: 4px; }
  .device-item { display: flex; align-items: center; gap: 10px; padding: 10px;
                 border-bottom: 1px solid var(--border); }
  .device-item:last-child { border-bottom: none; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .status-dot.online { background: var(--green); }
  .status-dot.offline { background: var(--red); }
  .status-dot.busy { background: var(--yellow); }
  .device-info { flex: 1; }
  .device-id { font-weight: 600; font-size: 14px; }
  .device-meta { font-size: 12px; color: var(--text2); }
  .task-input { display: flex; gap: 8px; margin-bottom: 12px; }
  .task-input input { flex: 1; padding: 10px 14px; background: var(--bg);
                      border: 1px solid var(--border); border-radius: 6px;
                      color: var(--text); font-size: 14px; outline: none; }
  .task-input input:focus { border-color: var(--accent); }
  .task-input select { padding: 10px; background: var(--bg);
                       border: 1px solid var(--border); border-radius: 6px;
                       color: var(--text); font-size: 14px; }
  .btn { padding: 10px 20px; background: var(--accent); color: #fff;
         border: none; border-radius: 6px; cursor: pointer; font-size: 14px;
         font-weight: 600; }
  .btn:hover { opacity: 0.9; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .log { max-height: 400px; overflow-y: auto; font-family: 'SF Mono', monospace;
         font-size: 13px; line-height: 1.6; }
  .log-entry { padding: 8px 0; border-bottom: 1px solid var(--border); }
  .log-entry .time { color: var(--text2); margin-right: 8px; }
  .log-entry.success .result { color: var(--green); }
  .log-entry.fail .result { color: var(--red); }
  .log-entry .tool { color: var(--purple); }
  .log-entry .detail { color: var(--text2); font-size: 12px; margin-top: 4px; }
  .empty { color: var(--text2); text-align: center; padding: 30px; font-size: 14px; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--text2);
             border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<h1><span class="icon">🐙</span> Echo Tentacle Dashboard</h1>

<div class="grid">
  <!-- 左列 -->
  <div>
    <div class="card" style="margin-bottom:20px">
      <h2>📊 统计</h2>
      <div class="stat-row" id="stats-row">
        <div class="stat"><div class="num" id="stat-total">-</div><div class="label">设备总数</div></div>
        <div class="stat"><div class="num" id="stat-online" style="color:var(--green)">-</div><div class="label">在线</div></div>
        <div class="stat"><div class="num" id="stat-busy" style="color:var(--yellow)">-</div><div class="label">忙碌</div></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:20px">
      <h2>📱 设备</h2>
      <div id="device-list"><div class="empty">加载中...</div></div>
    </div>

    <div class="card">
      <h2>💬 提交任务</h2>
      <div class="task-input">
        <select id="device-select"><option value="">自动选择</option></select>
        <input id="task-input" placeholder="打开微信" onkeydown="if(event.key==='Enter')submitTask()">
        <button class="btn" id="submit-btn" onclick="submitTask()">执行</button>
      </div>
      <div id="task-result"></div>
    </div>

    <div class="card" style="margin-top:20px">
      <h2>📡 群发（群控）</h2>
      <label style="font-size:13px;color:var(--text2);display:block;margin-bottom:6px">
        <input type="checkbox" id="bcast-all" checked onchange="toggleBcastAll()"> 所有在线设备
      </label>
      <div id="bcast-devices" style="max-height:120px;overflow:auto;margin-bottom:8px"></div>
      <div class="task-input">
        <input id="bcast-input" placeholder="给所有手机：打开微信签到" onkeydown="if(event.key==='Enter')broadcastTask()">
        <button class="btn" id="bcast-btn" onclick="broadcastTask()">群发</button>
      </div>
      <div id="bcast-result"></div>
    </div>
  </div>

  <!-- 右列 -->
  <div>
    <div class="card">
      <h2>📋 执行日志</h2>
      <div class="log" id="log-list"><div class="empty">暂无任务</div></div>
    </div>
  </div>
</div>

<script>
const API = '/api/tentacle';

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function timeAgo(ts) {
  const s = Math.floor(Date.now()/1000 - ts);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  return Math.floor(s/3600) + 'h ago';
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN');
}

async function refresh() {
  try {
    const [stats, devices] = await Promise.all([api('/stats'), api('/devices')]);

    document.getElementById('stat-total').textContent = stats.total || 0;
    document.getElementById('stat-online').textContent = stats.online || 0;
    document.getElementById('stat-busy').textContent = stats.busy || 0;

    const dl = document.getElementById('device-list');
    const sel = document.getElementById('device-select');
    if (devices.length === 0) {
      dl.innerHTML = '<div class="empty">暂无设备连接</div>';
    } else {
      dl.innerHTML = devices.map(d => `
        <div class="device-item">
          <div class="status-dot ${d.status}"></div>
          <div class="device-info">
            <div class="device-id">${d.tentacle_id}</div>
            <div class="device-meta">${d.platform} · ${d.total_capabilities} 技能 · ${d.meta?.brand || ''} ${d.meta?.model || ''}</div>
          </div>
        </div>
      `).join('');
    }

    // 更新设备选择
    const curVal = sel.value;
    sel.innerHTML = '<option value="">自动选择</option>' +
      devices.filter(d => d.is_online).map(d =>
        `<option value="${d.tentacle_id}">${d.tentacle_id}</option>`
      ).join('');
    sel.value = curVal;

    // 群发设备多选
    const bd = document.getElementById('bcast-devices');
    const allOn = document.getElementById('bcast-all').checked;
    const online = devices.filter(d => d.is_online);
    bd.innerHTML = online.length === 0
      ? '<div class="empty" style="padding:10px">无在线设备</div>'
      : online.map(d =>
          `<label style="display:block;font-size:13px;padding:2px 0">
             <input type="checkbox" class="bcast-dev" value="${d.tentacle_id}" ${allOn ? 'disabled' : ''}> ${d.tentacle_id}
           </label>`
        ).join('');
  } catch(e) {
    console.error('refresh error:', e);
  }
}

function toggleBcastAll() {
  const dis = document.getElementById('bcast-all').checked;
  document.querySelectorAll('.bcast-dev').forEach(c => { c.disabled = dis; if (dis) c.checked = false; });
}

async function broadcastTask() {
  const input = document.getElementById('bcast-input');
  const btn = document.getElementById('bcast-btn');
  const resultDiv = document.getElementById('bcast-result');
  const task = input.value.trim();
  if (!task) return;
  let ids = null;
  if (!document.getElementById('bcast-all').checked) {
    ids = Array.from(document.querySelectorAll('.bcast-dev:checked')).map(c => c.value);
    if (ids.length === 0) {
      resultDiv.innerHTML = '<div style="color:var(--red);margin-top:8px">请选择至少一台设备，或勾选"所有在线设备"</div>';
      return;
    }
  }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 群发中';
  resultDiv.innerHTML = '';
  try {
    const r = await api('/broadcast', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task, tentacle_ids: ids}),
    });
    const color = r.failed === 0 ? 'var(--green)' : 'var(--yellow)';
    resultDiv.innerHTML = `<div style="color:${color};margin-top:8px">📡 ${r.total} 台 · ✓${r.succeeded} ✗${r.failed} · ${r.duration_ms}ms</div>`;
    input.value = '';
  } catch(e) {
    resultDiv.innerHTML = `<div style="color:var(--red);margin-top:8px">❌ ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '群发';
    loadHistory();
    refresh();
  }
}

async function loadHistory() {
  try {
    const tasks = await api('/tasks');
    const ll = document.getElementById('log-list');
    if (tasks.length === 0) {
      ll.innerHTML = '<div class="empty">暂无任务</div>';
      return;
    }
    ll.innerHTML = tasks.map(t => {
      const cls = t.success ? 'success' : 'fail';
      const icon = t.success ? '✅' : '❌';
      if (t.broadcast) {
        const detail = (t.results || []).map(r =>
          `<span class="tool">${r.tentacle_id}</span> ${r.ok ? '✓' : '✗ ' + (r.error || '')}`
        ).join('&nbsp;&nbsp;');
        return `<div class="log-entry ${cls}">
          <span class="time">${fmtTime(t.timestamp)}</span>
          📡 "${t.task}" (群发 ${t.total}台: ✓${t.succeeded} ✗${t.failed}, ${t.duration_ms}ms)
          ${detail ? `<div class="detail">${detail}</div>` : ''}
        </div>`;
      }
      let detail = '';
      if (t.results) {
        detail = t.results.map(r =>
          `<span class="tool">${r.tool}</span> → ${r.success ? '✓' : '✗ ' + (r.error || '')}`
        ).join(' → ');
      }
      return `<div class="log-entry ${cls}">
        <span class="time">${fmtTime(t.timestamp)}</span>
        ${icon} "${t.task}" (${t.steps}步, ${t.duration_ms}ms)
        ${detail ? `<div class="detail">${detail}</div>` : ''}
      </div>`;
    }).join('');
  } catch(e) {
    console.error('history error:', e);
  }
}

async function submitTask() {
  const input = document.getElementById('task-input');
  const sel = document.getElementById('device-select');
  const btn = document.getElementById('submit-btn');
  const resultDiv = document.getElementById('task-result');
  const task = input.value.trim();
  if (!task) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 执行中';
  resultDiv.innerHTML = '';

  try {
    const r = await api('/task', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task, tentacle_id: sel.value || undefined}),
    });
    resultDiv.innerHTML = `<div style="color:var(--green);margin-top:8px">
      ✅ 完成 · ${r.steps} 步 · ${r.duration_ms}ms</div>`;
    input.value = '';
  } catch(e) {
    resultDiv.innerHTML = `<div style="color:var(--red);margin-top:8px">❌ ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '执行';
    loadHistory();
    refresh();
  }
}

// 初始化
refresh();
loadHistory();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""
