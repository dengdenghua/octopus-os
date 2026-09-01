from __future__ import annotations

_LOGIN_JS = r"""<body>
<div id="root"></div>

<script>
// ═══════════════════════════════════════════════════════════
// Section logic.
// ═══════════════════════════════════════════════════════════

const LS = {
  get(k) { return localStorage.getItem(k); },
  set(k, v) { localStorage.setItem(k, v); },
  del(k) { localStorage.removeItem(k); },
};

function authHeaders() {
  const jwt = LS.get('echo.jwt');
  return jwt ? { 'Authorization': 'Bearer ' + jwt } : {};
}

function renderErrorMessage(container, message) {
  if (!container) return;
  container.replaceChildren();
  if (!message) return;
  const error = document.createElement('div');
  error.className = 'err';
  error.textContent = String(message);
  container.appendChild(error);
}

async function api(method, path, body = null) {
  const isAuthEndpoint = path.startsWith('/api/auth/');
  const r = await fetch(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  if (!r.ok) {
    // Section logic.
    if (r.status === 401 && !isAuthEndpoint && LS.get('echo.jwt')) {
      console.warn('JWT 401 · clearing session and returning to login');
      ['echo.jwt','echo.actor_id','echo.provider','echo.display','echo.model']
        .forEach(k => LS.del(k));
      setTimeout(() => render(), 100);
    }
    const err = new Error(`HTTP ${r.status}: ${text.slice(0, 200)}`);
    err.status = r.status;
    err.body = text;
    throw err;
  }
  try { return JSON.parse(text); }
  catch { return text; }
}

// ═══════════════════════════════════════════════════════════
// Router
// ═══════════════════════════════════════════════════════════

function render() {
  if (LS.get('echo.jwt')) {
    renderChat();
  } else {
    renderLogin();
  }
}

// ═══════════════════════════════════════════════════════════
// Login page
// ═══════════════════════════════════════════════════════════

async function renderLogin() {
  const root = document.getElementById('root');
  root.innerHTML = `
    <div class="login-root">
      <h1>🐙 Echo</h1>
      <p class="sub">登录使用 agent + 官方大模型</p>
      <div class="card" id="login-card">
        <div class="loading">加载登录方式…</div>
      </div>
      <p class="footer">
        JWT 存本地 · 退出即清 · 无密码模式仅限内网
      </p>
    </div>
  `;

  let providers = [];
  try {
    const r = await api('GET', '/api/auth/providers');
    providers = r.providers || [];
  } catch (e) {
    renderErrorMessage(
      document.getElementById('login-card'),
      '加载登录方式失败：' + (e.message || e),
    );
    return;
  }

  if (providers.length === 0) {
    document.getElementById('login-card').innerHTML = `
      <div class="err">
        ⚠️ 服务端没开启任何登录方式<br>
        <span style="font-size:11px;color:var(--mute);margin-top:8px;display:block">
          设 <code>config.oct.enabled=true</code> 或 <code>config.local_auth.enabled=true</code>
        </span>
      </div>
    `;
    return;
  }

  const hasOct = providers.find(p => p.id === 'oct');
  const hasLocal = providers.find(p => p.id === 'local');
  let activeTab = hasOct ? 'oct' : 'local';

  const mockMode = hasOct?.mock_mode;
  const localPwRequired = Boolean(hasLocal?.password_required);

  function paint() {
    const c = document.getElementById('login-card');
    c.innerHTML = `
      ${providers.length > 1 ? `
        <div class="tabs">
          ${hasOct ? `<button class="${activeTab==='oct'?'active':''}" data-tab="oct">✉️ 邮箱</button>` : ''}
          ${hasLocal ? `<button class="${activeTab==='local'?'active':''}" data-tab="local">💻 本地</button>` : ''}
        </div>
      ` : ''}
      <div id="tab-body"></div>
    `;
    c.querySelectorAll('[data-tab]').forEach(b => {
      b.onclick = () => { activeTab = b.dataset.tab; paint(); };
    });
    if (activeTab === 'oct') emailForm(mockMode);
    else localForm(localPwRequired);
  }
  paint();
}

function emailForm(mockMode) {
  const body = document.getElementById('tab-body');
  body.innerHTML = `
    ${mockMode ? `<div class="mock-hint">⚙️ Mock 模式 · 无真实短信 · 仅 dev/demo</div>` : ''}
    <div id="email-step-address">
      <label>邮箱</label>
      <input id="email" type="email" placeholder="name@example.com" autocomplete="email" autofocus>
      <button class="primary" id="send-btn">获取验证码</button>
    </div>
    <div id="email-step-code" style="display:none">
      <div class="info" style="font-size:12px;color:var(--mute);margin-bottom:6px">
        已发至 <span id="email-mask" style="color:var(--slate);font-family:monospace"></span> ·
        <button class="link" id="resend-btn">重发</button>
      </div>
      <div id="dev-code-hint" style="display:none"></div>
      <label>验证码</label>
      <input id="code" class="code" type="text" maxlength="8" inputmode="numeric" autocomplete="one-time-code" placeholder="••••••">
      <div class="row" style="margin-top:12px">
        <button class="secondary" id="back-btn" style="flex:1">返回</button>
        <button class="primary" id="verify-btn" style="flex:2;margin-top:0">登录</button>
      </div>
    </div>
    <div id="email-err"></div>
  `;

  const emailInput = document.getElementById('email');
  const codeInput = document.getElementById('code');
  const sendBtn = document.getElementById('send-btn');
  const verifyBtn = document.getElementById('verify-btn');
  const backBtn = document.getElementById('back-btn');
  const resendBtn = document.getElementById('resend-btn');
  const errBox = document.getElementById('email-err');
  let cooldown = 0, cooldownTimer = null;

  function showErr(msg) {
    renderErrorMessage(errBox, msg);
  }
  function tick() {
    if (cooldown > 0) {
      cooldown--;
      sendBtn.textContent = `${cooldown}s 后重试`;
      resendBtn.textContent = `${cooldown}s 后重发`;
      resendBtn.disabled = true;
      sendBtn.disabled = true;
    } else {
      sendBtn.textContent = '获取验证码';
      resendBtn.textContent = '重发';
      resendBtn.disabled = false;
      sendBtn.disabled = false;
      clearInterval(cooldownTimer);
    }
  }
  function cleanEmail() {
    return (emailInput.value || '').trim().toLowerCase();
  }
  function maskEmail(value) {
    const [name, domain] = value.split('@');
    if (!domain) return value;
    return `${name.slice(0, 2)}***@${domain}`;
  }

  async function doSend() {
    const email = cleanEmail();
    if (!email.includes('@')) return;
    showErr('');
    sendBtn.disabled = true; resendBtn.disabled = true;
    try {
      const r = await api('POST', '/api/auth/oct/email/send', { email });
      document.getElementById('email-step-address').style.display = 'none';
      document.getElementById('email-step-code').style.display = '';
      document.getElementById('email-mask').textContent = maskEmail(email);
      const hint = document.getElementById('dev-code-hint');
      if (mockMode && r?.dev_code) {
        hint.style.display = '';
        hint.className = 'dev-code';
        hint.textContent = '[mock] 验证码 · ' + r.dev_code;
      } else if (mockMode) {
        hint.style.display = '';
        hint.className = 'dev-code';
        hint.textContent = '[mock] 请使用网关返回的开发验证码';
      }
      codeInput.focus();
      cooldown = 60;
      cooldownTimer = setInterval(tick, 1000); tick();
    } catch (e) {
      showErr(explainErr(e));
      sendBtn.disabled = false;
    }
  }
  async function doVerify() {
    const email = cleanEmail();
    const code = (codeInput.value || '').trim();
    if (code.length < 4) return;
    showErr('');
    verifyBtn.disabled = true;
    try {
      const r = await api('POST', '/api/auth/oct/email/login', { email, code });
      if (!r?.access_token) throw new Error('no access_token');
      LS.set('echo.jwt', r.access_token);
      LS.set('echo.actor_id', r.actor_id);
      LS.set('echo.provider', 'oct');
      LS.set('echo.display', r.user?.email || email);
      render();
    } catch (e) {
      showErr(explainErr(e));
      verifyBtn.disabled = false;
    }
  }

  sendBtn.onclick = doSend;
  resendBtn.onclick = doSend;
  verifyBtn.onclick = doVerify;
  backBtn.onclick = () => {
    document.getElementById('email-step-address').style.display = '';
    document.getElementById('email-step-code').style.display = 'none';
    codeInput.value = '';
  };
  emailInput.onkeydown = (e) => { if (e.key === 'Enter') doSend(); };
  codeInput.onkeydown = (e) => { if (e.key === 'Enter') doVerify(); };
}

function localForm(passwordRequired) {
  const body = document.getElementById('tab-body');
  body.innerHTML = `
    <div class="mock-hint" style="color:var(--warn)">
      ${passwordRequired
        ? '🔒 需账号密码 · 询问管理员'
        : '⚠️ 无密码 · 输入用户名即可登录 · 仅限本机/内网'}
    </div>
    <label>用户名</label>
    <input id="username" placeholder="${passwordRequired ? 'admin' : 'alice'}" maxlength="64" autofocus>
    ${passwordRequired ? `
      <label>密码</label>
      <input id="password" type="password" maxlength="256" placeholder="••••••••">
    ` : `
      <label>显示名（可选）</label>
      <input id="display" placeholder="Alice" maxlength="128">
    `}
    <button class="primary" id="local-btn">登录</button>
    <div id="local-err"></div>
  `;
  const btn = document.getElementById('local-btn');
  const errBox = document.getElementById('local-err');
  async function doLogin() {
    const u = document.getElementById('username').value.trim();
    const p = passwordRequired
      ? document.getElementById('password').value
      : null;
    const d = passwordRequired
      ? null
      : document.getElementById('display').value.trim();
    if (!/^[A-Za-z0-9._@-]{1,64}$/.test(u)) {
      renderErrorMessage(errBox, '用户名只能含字母/数字/._@-');
      return;
    }
    if (passwordRequired && !p) {
      renderErrorMessage(errBox, '请输入密码');
      return;
    }
    btn.disabled = true;
    renderErrorMessage(errBox, '');
    try {
      const payload = { username: u };
      if (p) payload.password = p;
      if (d) payload.display_name = d;
      const r = await api('POST', '/api/auth/local/login', payload);
      if (!r?.access_token) throw new Error('no access_token');
      LS.set('echo.jwt', r.access_token);
      LS.set('echo.actor_id', r.actor_id);
      LS.set('echo.provider', 'local');
      LS.set('echo.display', r.user?.display_name || r.user?.username || u);
      render();
    } catch (e) {
      renderErrorMessage(errBox, explainErr(e));
      btn.disabled = false;
    }
  }
  btn.onclick = doLogin;
  document.getElementById('username').onkeydown = (e) => { if (e.key === 'Enter') doLogin(); };
  if (passwordRequired) {
    document.getElementById('password').onkeydown = (e) => { if (e.key === 'Enter') doLogin(); };
  } else {
    document.getElementById('display').onkeydown = (e) => { if (e.key === 'Enter') doLogin(); };
  }
}

function explainErr(e) {
  if (e.status === 503) return '服务未开启 · 请联系管理员';
  if (e.status === 502) return '上游不可达 · 请稍后重试';
  if (e.status === 401) return '验证码错误或已过期';
  if (e.status === 403) return '无权限 / 不在白名单';
  if (e.status === 400) return '请求参数错误：' + (e.body || '').slice(0, 100);
  return e.message || String(e);
}
"""
