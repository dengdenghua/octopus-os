from __future__ import annotations

_CHAT_JS = r"""
// ═══════════════════════════════════════════════════════════
// Chat page
// ═══════════════════════════════════════════════════════════

// Section logic.
const BUILTIN_MODELS = [
  { id: 'echo-agent', label: '🎯 自动路由',   provider: 'official' },
  { id: 'kimi-k2.5',     label: 'Kimi K2.5',     provider: 'official' },
  { id: 'glm-4.7',       label: 'GLM-4.7',       provider: 'official' },
  { id: 'deepseek-v3.2', label: 'DeepSeek-V3.2', provider: 'official' },
  { id: 'minimax-m2.5',  label: 'MiniMax M2.5',  provider: 'official' },
  { id: 'qwen3-max',     label: 'Qwen3-Max',     provider: 'official' },
];

// Section logic.
function loadCustomModels() {
  try { return JSON.parse(LS.get('echo.customModels') || '[]'); }
  catch { return []; }
}
function saveCustomModels(arr) {
  LS.set('echo.customModels', JSON.stringify(arr));
}
function allModels() {
  return [...BUILTIN_MODELS, ...loadCustomModels()];
}
function modelById(id) {
  return allModels().find(m => m.id === id);
}
// Section logic.
function renderBuiltinOptions(selectedId) {
  return BUILTIN_MODELS.map(m =>
    `<option value="${escapeHtml(m.id)}" ${m.id===selectedId?'selected':''}>${escapeHtml(m.label)}</option>`
  ).join('');
}
// Section logic.
function renderCustomOptions(selectedId) {
  const custom = loadCustomModels();
  if (custom.length === 0) {
    return `<option disabled selected>（无 · 点 ⚙️ 添加）</option>`;
  }
  return custom.map(m =>
    `<option value="${escapeHtml(m.id)}" ${m.id===selectedId?'selected':''}>${escapeHtml(m.label || m.id)}${m.base_url ? ' · 🌐' : ''}</option>`
  ).join('');
}
// Section logic.
function effectiveModelId() {
  if (modelSource === 'custom') {
    const customModelId = LS.get('echo.customModel');
    const custom = loadCustomModels();
    if (customModelId && custom.find(m => m.id === customModelId)) return customModelId;
    if (custom.length > 0) return custom[0].id;
    return null;   // 没有自定义 · 兜底到内置
  }
  return LS.get('echo.builtinModel') || currentModel;
}

// Section logic.
let currentModel = LS.get('echo.model') || 'echo-agent';
// Section logic.
// desktop_operator was briefly treated as a legacy/deprecated agent id;
// since #22 (CUA productization) it is a first-class persona again, so
// the stored-agent cleanup below no longer removes it.
let _storedAgent = LS.get('echo.agent') || 'general';
let currentAgent = _storedAgent;
let modelSource = LS.get('echo.modelSource') || 'builtin';
let availableAgents = [];
let sending = false;
let credits = null;

// Section logic.
function loadAllConvs() {
  try { return JSON.parse(LS.get('echo.convs') || '{}'); }
  catch { return {}; }
}
function saveAllConvs(all) {
  LS.set('echo.convs', JSON.stringify(all));
}
function convIdFor(agentId) { return 'a:' + agentId; }
function getConv(agentId) {
  const all = loadAllConvs();
  const id = convIdFor(agentId);
  return all[id] || { messages: [], agent: agentId, createdAt: 0, lastAt: 0 };
}
function saveConv(agentId, conv) {
  const all = loadAllConvs();
  all[convIdFor(agentId)] = conv;
  saveAllConvs(all);
}
function clearConv(agentId) {
  const all = loadAllConvs();
  delete all[convIdFor(agentId)];
  saveAllConvs(all);
}

// Section logic.
function currentMessages() {
  return getConv(currentAgent).messages;
}
function setCurrentMessages(msgs) {
  const conv = getConv(currentAgent);
  conv.messages = msgs;
  conv.lastAt = Date.now();
  if (!conv.createdAt) conv.createdAt = Date.now();
  saveConv(currentAgent, conv);
}

// Section logic.
let chatMode = LS.get('echo.chatMode') || 'agent';

async function renderChat() {
  const provider = LS.get('echo.provider');
  const display = LS.get('echo.display') || LS.get('echo.actor_id');
  const isOct = provider === 'oct';

  function getChatEndpoint() {
    if (chatMode === 'direct' && isOct) {
      return '/api/oct/openai/v1/chat/completions';
    }
    return '/v1/chat/completions';
  }

  document.body.classList.add('chatting');

  // Section logic.
  if (!isOct) {
    renderNoBackend(display);
    return;
  }
  // Section logic.
  try {
    const linkResp = await fetch('/api/account/oct', { headers: authHeaders() });
    if (linkResp.status === 404) {
      renderNoBackend(display);
      return;
    }
    if (!linkResp.ok && linkResp.status === 401) {
      // Section logic.
      ['echo.jwt','echo.actor_id','echo.provider','echo.display']
        .forEach(k => LS.del(k));
      render();
      return;
    }
  } catch (e) { /* 网络错 · 继续让用户试 · 后续请求会再报 */ }
  const root = document.getElementById('root');
  const initials = (display || '?').slice(0, 2).toUpperCase();

  root.innerHTML = `
    <div class="chat-root">
      <div class="chat-header">
        <div class="who">
          <span style="font-size:22px">🐙</span>
          <div style="min-width:0">
            <h2>Echo Chat</h2>
            <div class="info">
              <span class="badge ${isOct?'accent':'plain'}">${isOct?'✉️ 邮箱':'💻 本地'}</span>
              <span style="color:var(--slate)">${escapeHtml(display || '')}</span>
              <span id="credits-badge"></span>
            </div>
          </div>
        </div>
        <div class="header-actions">
          ${isOct ? `<button class="icon-btn" id="new-conv-btn" title="新建对话（清当前 agent 的聊天记录）">🗘</button>` : ''}
          ${isOct ? `<button class="icon-btn" id="refresh-btn" title="刷新余额">🔄</button>` : ''}
          <button class="icon-btn" id="logout-btn" title="退出登录">⏻</button>
        </div>
      </div>

      ${isOct ? `
      <div class="chat-toolbar">
        <div class="mode-pill" id="mode-pill">
          <button data-mode="agent" class="${chatMode==='agent'?'active':''}" title="经 planner + skills · 可调工具">🤖 Agent</button>
          <button data-mode="direct" class="${chatMode==='direct'?'active':''}" title="直接打 LLM · 不过 planner">⚡ 直聊</button>
        </div>
        <span id="agent-group" style="display:${chatMode==='agent'?'inline-flex':'none'};gap:6px;align-items:center">
          <label>Agent</label>
          <select id="agent-sel"><option>loading…</option></select>
        </span>
        <label style="margin-left:12px">模型</label>
        <div class="md-picker" id="md-picker">
          <button class="md-trigger" id="md-trigger">
            <span id="md-trigger-label">…</span>
            <span class="arrow">▾</span>
          </button>
        </div>
        <span class="credits" id="credits-inline"></span>
      </div>
      ` : ''}

      <div class="messages" id="messages">
        <div class="messages-inner" id="messages-inner"></div>
      </div>

      <div class="composer-wrap">
        <div class="composer">
          <textarea
            id="input"
            rows="1"
            ${isOct ? '' : 'disabled'}
            placeholder="${escapeHtml(isOct ? '给 ' + (modelById(currentModel)?.label || currentModel) + ' 发消息…' : '本地模式不接 LLM · 请切邮箱登录')}"
          ></textarea>
          <button class="send-btn" id="send-btn" ${isOct ? '' : 'disabled'} title="发送 (Enter)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l7-7 7 7"/><path d="M12 19V5"/></svg>
          </button>
        </div>
        <div class="composer-hint">
          ${isOct
            ? '<span class="hint-kbd">Enter</span> 发送 · <span class="hint-kbd">Shift+Enter</span> 换行 · 用量计入 Oct 账户'
            : '本地账号无 LLM 对话能力 · 点右上角 ⏻ 退出后选"邮箱登录"'}
        </div>
      </div>
    </div>
  `;

  // Section logic.
  paintMessages();

  document.getElementById('logout-btn').onclick = () => {
    ['echo.jwt','echo.actor_id','echo.provider','echo.display','echo.model']
      .forEach(k => LS.del(k));
    document.body.classList.remove('chatting');
    messages = [];
    render();
  };
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) refreshBtn.onclick = loadCredits;
  const newConvBtn = document.getElementById('new-conv-btn');
  if (newConvBtn) newConvBtn.onclick = () => {
    if (currentMessages().length === 0) return;
    if (confirm('清空当前 agent (' + currentAgent + ') 的对话记录？')) {
      clearConv(currentAgent);
      paintMessages();
    }
  };
  // Section logic.
  function triggerLabel() {
    const m = modelById(currentModel);
    if (!m) return '选模型…';
    return m.label;
  }
  function renderTriggerLabel() {
    document.getElementById('md-trigger-label').textContent = triggerLabel();
  }
  function closePopup() {
    const p = document.querySelector('.md-popup');
    if (p) p.remove();
  }
  function openPopup() {
    closePopup();
    const picker = document.getElementById('md-picker');
    const popup = document.createElement('div');
    popup.className = 'md-popup';
    popup.innerHTML = `
      <div class="md-tabs">
        <button data-tab="builtin" class="${modelSource==='builtin'?'active':''}">官方</button>
        <button data-tab="custom"  class="${modelSource==='custom'?'active':''}">自定义</button>
      </div>
      <div class="md-list" id="md-list"></div>
      <div class="md-foot">
        <button id="md-manage">⚙️ 管理自定义模型</button>
      </div>
    `;
    picker.appendChild(popup);

    const paintList = () => {
      const list = popup.querySelector('#md-list');
      if (modelSource === 'builtin') {
        list.innerHTML = BUILTIN_MODELS.map(m => `
          <div class="md-item ${m.id===currentModel?'active':''}" data-id="${escapeHtml(m.id)}">
            <span>${escapeHtml(m.label)}</span>
            <span class="m-id">${escapeHtml(m.id)}</span>
          </div>
        `).join('');
      } else {
        const custom = loadCustomModels();
        if (custom.length === 0) {
          list.innerHTML = `<div class="md-empty">
            还没添加自定义模型<br>
            <span style="font-size:11px">点下方"⚙️ 管理"添加</span>
          </div>`;
        } else {
          list.innerHTML = custom.map(m => `
            <div class="md-item ${m.id===currentModel?'active':''}" data-id="${escapeHtml(m.id)}">
              <span>${escapeHtml(m.label || m.id)}</span>
              ${m.base_url ? '<span class="m-badge" title="'+escapeHtml(m.base_url)+'">外部</span>' : ''}
              <span class="m-id">${escapeHtml(m.id)}</span>
            </div>
          `).join('');
        }
      }
      list.querySelectorAll('.md-item').forEach(it => {
        it.onclick = () => {
          currentModel = it.dataset.id;
          LS.set('echo.model', currentModel);
          renderTriggerLabel();
          updateComposerPlaceholder();
          closePopup();
        };
      });
    };
    paintList();

    popup.querySelectorAll('.md-tabs button').forEach(btn => {
      btn.onclick = () => {
        modelSource = btn.dataset.tab;
        LS.set('echo.modelSource', modelSource);
        popup.querySelectorAll('.md-tabs button').forEach(b =>
          b.classList.toggle('active', b.dataset.tab === modelSource));
        paintList();
      };
    });
    popup.querySelector('#md-manage').onclick = () => {
      closePopup();
      openModelsModal();
    };

    // Section logic.
    setTimeout(() => {
      const off = (e) => {
        if (!popup.contains(e.target) &&
            !document.getElementById('md-trigger').contains(e.target)) {
          closePopup();
          document.removeEventListener('click', off);
        }
      };
      document.addEventListener('click', off);
    }, 0);
  }

  document.getElementById('md-trigger').onclick = (e) => {
    e.stopPropagation();
    if (document.querySelector('.md-popup')) closePopup();
    else openPopup();
  };

  function updateComposerPlaceholder() {
    const ta = document.getElementById('input');
    if (!ta) return;
    const m = modelById(currentModel);
    if (!m) { ta.placeholder = '先选一个模型'; return; }
    if (m.id === 'echo-agent') ta.placeholder = '🎯 自动路由 · 系统挑最合适的模型';
    else if (m.base_url) ta.placeholder = `🌐 直连 ${m.label} @ ${new URL(m.base_url).host}`;
    else ta.placeholder = '给 ' + m.label + ' 发消息…';
  }
  renderTriggerLabel();
  updateComposerPlaceholder();
  // Section logic.
  const modePill = document.getElementById('mode-pill');
  if (modePill) {
    modePill.querySelectorAll('button').forEach(btn => {
      btn.onclick = () => {
        chatMode = btn.dataset.mode;
        LS.set('echo.chatMode', chatMode);
        modePill.querySelectorAll('button').forEach(b =>
          b.classList.toggle('active', b.dataset.mode === chatMode));
        // Section logic.
        const group = document.getElementById('agent-group');
        if (group) group.style.display = chatMode === 'agent' ? 'inline-flex' : 'none';
        if (messages.length === 0) paintMessages();
      };
    });
  }

  // Section logic.
  if (isOct) loadAgents();

  async function loadAgents() {
    const sel = document.getElementById('agent-sel');
    if (!sel) return;
    try {
      const r = await fetch('/api/agents', { headers: authHeaders() });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      // Section logic.
      availableAgents = Array.isArray(data) ? data : (data.agents || []);
      if (availableAgents.length === 0) {
        sel.innerHTML = '<option>(未注册 agent)</option>';
        sel.disabled = true;
        return;
      }
      // Section logic.
      const idOf = (a) => a.agent_id || a.name;
      sel.disabled = false;
      sel.innerHTML = availableAgents.map(a => {
        const id = idOf(a);
        return `<option value="${escapeHtml(id)}" ${id === currentAgent ? 'selected' : ''} title="${escapeHtml((a.description||'').slice(0,120))}">${escapeHtml(a.icon || '🤖')} ${escapeHtml(a.display_name || id)}</option>`;
      }).join('');
      // Section logic.
      if (!availableAgents.some(a => idOf(a) === currentAgent)) {
        currentAgent = idOf(availableAgents[0]);
        LS.set('echo.agent', currentAgent);
        sel.value = currentAgent;
      }
      sel.onchange = (e) => {
        currentAgent = e.target.value;
        LS.set('echo.agent', currentAgent);
        const a = availableAgents.find(x => idOf(x) === currentAgent);
        const ta = document.getElementById('input');
        if (ta && a) ta.placeholder = `和 ${a.icon||'🤖'} ${a.display_name||currentAgent} 聊…`;
        // Section logic.
        paintMessages();
      };
    } catch (e) {
      sel.innerHTML = '<option>(加载失败)</option>';
      sel.disabled = true;
    }
  }

  const ta = document.getElementById('input');
  const sendBtn = document.getElementById('send-btn');
  ta.oninput = () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(140, ta.scrollHeight) + 'px';
  };
  ta.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };
  sendBtn.onclick = send;

  if (isOct) loadCredits();

  async function loadCredits() {
    try {
      const r = await api('POST', '/api/account/oct/refresh');
      credits = r?.credits?.credits ?? r?.credits?.surplusCredits;
      if (typeof credits === 'number') {
        const text = `💎 ${credits.toLocaleString()}`;
        const el = document.getElementById('credits-inline');
        const el2 = document.getElementById('credits-badge');
        if (el) el.textContent = text;
        if (el2) {
          el2.className = 'badge ok';
          el2.textContent = text;
        }
      }
    } catch (e) { /* ignore */ }
  }

  async function send() {
    if (sending) return;
    const content = ta.value.trim();
    if (!content) return;
    sending = true;
    ta.value = '';
    ta.style.height = 'auto';
    sendBtn.disabled = true;

    // Section logic.
    const msgs = currentMessages();
    msgs.push({ role: 'user', content });
    setCurrentMessages(msgs);
    paintMessages();

    const agentMeta = availableAgents.find(a => (a.agent_id || a.name) === currentAgent);
    const agentIcon = agentMeta?.icon || '🤖';
    const agentLabel = agentMeta?.display_name || currentAgent;

    // Section logic.
    const inner = document.getElementById('messages-inner');
    const thinking = document.createElement('div');
    thinking.className = 'msg-row assistant';
    thinking.innerHTML = `
      <div class="msg-avatar">${escapeHtml(agentIcon)}</div>
      <div class="msg-body">
        <div class="msg-meta"><span class="role">${escapeHtml(agentLabel)}</span>
          <span class="model">${escapeHtml(currentModel)}</span></div>
        <div class="thinking">
          <span class="thinking-dot"></span>
          <span class="thinking-dot"></span>
          <span class="thinking-dot"></span>
          <span style="margin-left:4px">思考中</span>
        </div>
      </div>
    `;
    inner.appendChild(thinking);
    scrollToBottom();

    try {
      const sel = modelById(currentModel);
      if (!sel) throw new Error('请先选一个模型');

      // Section logic.
      const apiMessages = msgs.map(m => ({ role: m.role, content: m.content }));

      let r;
      if (sel.base_url && sel.api_key) {
        r = await sendToExternalEndpoint(sel, apiMessages);
      } else {
        const payload = { model: sel.id, messages: apiMessages, stream: false };
        if (chatMode === 'agent' && isOct) payload.agent = currentAgent;
        r = await api('POST', getChatEndpoint(), payload);
      }
      const reply = r?.choices?.[0]?.message;
      const usage = r?.usage;
      if (reply) {
        msgs.push({
          role: 'assistant', content: reply.content || '',
          _usage: usage,
          _model: currentModel,
          _agent: currentAgent,
          _agentIcon: agentIcon,
          _agentLabel: agentLabel,
        });
        setCurrentMessages(msgs);
      }
      thinking.remove();
      paintMessages();
      if (isOct) loadCredits();
    } catch (e) {
      msgs.push({
        role: 'assistant',
        content: '调用失败：' + (e.message || e),
        _error: true,
        _model: currentModel,
        _agent: currentAgent,
        _agentIcon: agentIcon,
        _agentLabel: agentLabel,
      });
      setCurrentMessages(msgs);
      thinking.remove();
      paintMessages();
    } finally {
      sending = false;
      sendBtn.disabled = false;
      ta.focus();
    }
  }

  function scrollToBottom() {
    const box = document.getElementById('messages');
    box.scrollTop = box.scrollHeight;
  }

  function paintMessages() {
    const inner = document.getElementById('messages-inner');
    const messages = currentMessages();
    if (messages.length === 0) {
      const modeTxt = chatMode === 'agent'
      ? '🤖 Agent 模式 · 经过 planner → skills → LLM · 可自动调工具'
      : '⚡ 直聊模式 · 直接发给 LLM · 无 agent 参与';
    const m = modelById(currentModel);
    let routeTxt;
    if (!m) routeTxt = '⚠️ 未选模型';
    else if (m.id === 'echo-agent') routeTxt = '🎯 自动路由 · 系统挑最合适的模型';
    else if (m.base_url) routeTxt = `🌐 外部直连 · ${m.label} @ ${new URL(m.base_url).host}`;
    else routeTxt = '📌 固定 · ' + m.label;
    inner.innerHTML = `
        <div class="empty">
          <div class="empty-icon">🐙</div>
          <div class="empty-title">和官方大模型聊聊</div>
          <div class="empty-sub">
            ${modeTxt}
            <br><br>
            ${escapeHtml(routeTxt)}
            <br><br>
            <span class="hint-kbd">Enter</span> 发送 · <span class="hint-kbd">Shift+Enter</span> 换行 · 积分实时扣
          </div>
        </div>
      `;
      return;
    }
    inner.innerHTML = messages.map(m => {
      if (m.role === 'user') {
        return `<div class="msg-row user">
          <div class="msg-avatar">${escapeHtml(initials)}</div>
          <div class="msg-body">
            <div class="msg-meta"><span class="role">你</span></div>
            <div class="msg-content">${escapeHtml(m.content)}</div>
          </div>
        </div>`;
      }
      // Section logic.
      const icon = m._error ? '⚠️' : (m._agentIcon || '🤖');
      const label = m._error ? '错误' : (m._agentLabel || 'Assistant');
      const modelTag = m._model || currentModel;
      const u = m._usage ? `<div class="msg-usage">
        ↑ ${escapeHtml(m._usage.prompt_tokens||0)} · ↓ ${escapeHtml(m._usage.completion_tokens||0)}${
          m._usage.total_tokens ? ' · total ' + escapeHtml(m._usage.total_tokens) : ''
        }</div>` : '';
      return `<div class="msg-row assistant ${m._error?'error':''}">
        <div class="msg-avatar">${escapeHtml(icon)}</div>
        <div class="msg-body">
          <div class="msg-meta">
            <span class="role">${escapeHtml(label)}</span>
            <span class="model">${escapeHtml(modelTag)}</span>
          </div>
          <div class="msg-content">${escapeHtml(m.content)}</div>
          ${u}
        </div>
      </div>`;
    }).join('');
    scrollToBottom();
  }
}
"""
