from __future__ import annotations

_MODELS_JS = r"""
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ═══════════════════════════════════════════════════════════
// Section logic.
// ═══════════════════════════════════════════════════════════

function openModelsModal() {
  const existing = document.querySelector('.modal-backdrop');
  if (existing) existing.remove();

  function paint() {
    const custom = loadCustomModels();
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML = `
      <div class="modal">
        <div class="modal-head">
          <h3>⚙️ 模型管理</h3>
          <button class="icon-btn" id="modal-close">✕</button>
        </div>
        <div class="modal-body">
          <div style="font-size:12px;color:var(--mute);margin-bottom:10px">
            官方模型走内置代理 · 自定义模型可选走代理或自己的 base_url+api_key 直连
          </div>

          <div class="model-list">
            ${BUILTIN_MODELS.map(m => `
              <div class="model-item builtin">
                <span class="tag b">内置</span>
                <span class="mlabel">${escapeHtml(m.label)}</span>
                <span class="mid">${escapeHtml(m.id)}</span>
              </div>
            `).join('')}
            ${custom.map((m, i) => `
              <div class="model-item">
                <span class="tag c">自定义</span>
                <span class="mlabel">${escapeHtml(m.label || m.id)}</span>
                <span class="mid">${escapeHtml(m.id)}</span>
                ${m.base_url ? `<span class="tag ext" title="${escapeHtml(m.base_url)}">外部</span>` : ''}
                <button data-del="${i}" title="删除">🗑</button>
              </div>
            `).join('')}
          </div>

          <h4 style="margin:20px 0 8px 0;color:white;font-size:13px">➕ 添加模型</h4>
          <div class="add-form">
            <div class="row">
              <div>
                <label>模型 ID *</label>
                <input id="add-id" placeholder="gpt-4o-mini / claude-haiku-4-5 / ...">
              </div>
              <div>
                <label>显示名（可选）</label>
                <input id="add-label" placeholder="GPT-4o Mini">
              </div>
            </div>
            <details style="margin-top:8px">
              <summary style="cursor:pointer;color:var(--cephalo-light);font-size:12px">
                外部端点（用自己的 API key）
              </summary>
              <div style="margin-top:8px">
                <label>Base URL（OpenAI-compat · 含 /v1）</label>
                <input id="add-base" placeholder="https://api.openai.com/v1 · 留空走账户网关">
                <div class="form-hint">例：OpenAI · https://api.openai.com/v1 · DeepSeek · https://api.deepseek.com/v1</div>
                <label>API Key</label>
                <input id="add-key" type="password" placeholder="sk-...">
                <div class="form-hint">
                  ⚠️ 存 localStorage · 仅本浏览器 · 不上传服务器<br>
                  使用直连时按你自己的 provider 计费
                </div>
              </div>
            </details>
            <button class="primary" id="add-btn" style="margin-top:12px;width:100%;padding:9px;border:none;border-radius:6px;background:var(--cephalo);color:white;cursor:pointer">添加</button>
          </div>
        </div>
        <div class="modal-foot">
          <button class="secondary" id="modal-done" style="padding:7px 16px;border:1px solid var(--ink-600);background:var(--ink-700);color:var(--slate);border-radius:6px;cursor:pointer">完成</button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);

    // Section logic.
    const close = () => {
      backdrop.remove();
      // Section logic.
      const sel = document.getElementById('model-sel');
      if (sel) sel.innerHTML = renderModelOptions(currentModel);
    };
    backdrop.onclick = (e) => { if (e.target === backdrop) close(); };
    backdrop.querySelector('#modal-close').onclick = close;
    backdrop.querySelector('#modal-done').onclick = close;

    // Section logic.
    backdrop.querySelectorAll('[data-del]').forEach(btn => {
      btn.onclick = () => {
        const i = parseInt(btn.dataset.del);
        const arr = loadCustomModels();
        arr.splice(i, 1);
        saveCustomModels(arr);
        // Section logic.
        if (!modelById(currentModel)) {
          currentModel = BUILTIN_MODELS[0].id;
          LS.set('echo.model', currentModel);
        }
        backdrop.remove();
        paint();
      };
    });

    // Section logic.
    backdrop.querySelector('#add-btn').onclick = () => {
      const id = backdrop.querySelector('#add-id').value.trim();
      const label = backdrop.querySelector('#add-label').value.trim();
      const baseUrl = backdrop.querySelector('#add-base').value.trim();
      const apiKey = backdrop.querySelector('#add-key').value.trim();
      if (!id) {
        alert('模型 ID 必填');
        return;
      }
      if (BUILTIN_MODELS.some(m => m.id === id)) {
        alert('ID 和内置模型冲突 · 换一个');
        return;
      }
      if (baseUrl && !/^https?:\/\//.test(baseUrl)) {
        alert('base_url 需以 http(s):// 开头');
        return;
      }
      const arr = loadCustomModels();
      // Section logic.
      const exist = arr.findIndex(m => m.id === id);
      const entry = { id, label: label || id, provider: baseUrl ? 'external' : 'official' };
      if (baseUrl) entry.base_url = baseUrl;
      if (apiKey) entry.api_key = apiKey;
      if (exist >= 0) arr[exist] = entry; else arr.push(entry);
      saveCustomModels(arr);
      backdrop.remove();
      paint();
    };
  }
  paint();
}

// ═══════════════════════════════════════════════════════════
// Section logic.
// ═══════════════════════════════════════════════════════════

async function sendToExternalEndpoint(model, messages) {
  const payload = {
    model: model.id,
    messages,
    stream: false,
  };
  const r = await fetch(model.base_url.replace(/\/$/, '') + '/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + model.api_key,
    },
    body: JSON.stringify(payload),
  });
  const text = await r.text();
  if (!r.ok) {
    throw new Error(`${model.base_url} HTTP ${r.status}: ${text.slice(0, 200)}`);
  }
  try { return JSON.parse(text); }
  catch { throw new Error(`${model.base_url} non-JSON: ${text.slice(0, 200)}`); }
}

// ═══════════════════════════════════════════════════════════
// Section logic.
// ═══════════════════════════════════════════════════════════

function renderNoBackend(display) {
  document.body.classList.add('chatting');
  const root = document.getElementById('root');
  root.innerHTML = `
    <div class="chat-root">
      <div class="chat-header">
        <div class="who">
          <span style="font-size:22px">🐙</span>
          <div style="min-width:0">
            <h2>Echo Chat</h2>
            <div class="info">
              <span class="badge plain">💻 本地</span>
              <span style="color:var(--slate)">${escapeHtml(display || '')}</span>
            </div>
          </div>
        </div>
        <div class="header-actions">
          <button class="icon-btn" id="logout-btn" title="退出登录">⏻</button>
        </div>
      </div>
      <div style="flex:1;display:flex;align-items:center;justify-content:center;padding:40px 20px">
        <div style="max-width:420px;text-align:center">
          <div style="font-size:56px;margin-bottom:16px">🔌</div>
          <h2 style="color:white;margin:0 0 8px 0">当前账号没有 LLM 后端</h2>
          <p style="color:var(--mute);font-size:14px;line-height:1.6;margin:0 0 24px 0">
            你用的是 <b style="color:var(--slate)">本地账号</b> · 没绑任何大模型账号 · 无法聊天。
            <br><br>
            换"✉️ 邮箱"登录即可 · 会自动接入账户网关支持的模型。
          </p>
          <button class="primary" style="padding:12px 28px;font-size:14px;border:none;border-radius:8px;background:var(--cephalo);color:white;cursor:pointer;font-weight:600" id="switch-btn">
            🔄 切换到邮箱登录
          </button>
          <p style="color:var(--mute);font-size:11px;margin-top:16px">
            本地账号仍可访问 <code class="hint-kbd">/api/agents</code> · <code class="hint-kbd">/api/skills</code> 等元数据端点
          </p>
        </div>
      </div>
    </div>
  `;
  const switchOut = () => {
    ['echo.jwt','echo.actor_id','echo.provider','echo.display','echo.model']
      .forEach(k => LS.del(k));
    document.body.classList.remove('chatting');
    render();
  };
  document.getElementById('switch-btn').onclick = switchOut;
  document.getElementById('logout-btn').onclick = switchOut;
}

render();
</script>
</body>
</html>
"""
