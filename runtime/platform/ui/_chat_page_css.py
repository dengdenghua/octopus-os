from __future__ import annotations

_HEAD_CSS = r"""<!doctype html>
<html lang="zh-CN" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🐙 Echo · Chat</title>
<style>
  :root {
    --ink-950: #070a10;
    --ink-900: #0a0e14;
    --ink-800: #111721;
    --ink-700: #1a2433;
    --ink-600: #1f2733;
    --cephalo: #7a4dff;
    --cephalo-light: #b5a4ff;
    --sucker: #38bdf8;
    --ok: #a6e3a1;
    --warn: #f9e2af;
    --bad: #f38ba8;
    --mute: #6e7278;
    --slate: #d3d7de;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    background: var(--ink-900);
    color: var(--slate);
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: var(--ink-950); }
  ::-webkit-scrollbar-thumb { background: var(--ink-600); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--cephalo); }

  /* ─── Login ─── */
  .login-root {
    max-width: 380px;
    width: 100%;
    padding: 24px;
  }
  .login-root h1 {
    text-align: center;
    color: white;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .login-root .sub {
    text-align: center;
    color: var(--mute);
    margin: 0 0 24px 0;
    font-size: 13px;
  }
  .card {
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 12px;
    padding: 20px;
  }
  .tabs {
    display: flex;
    gap: 4px;
    background: var(--ink-900);
    border: 1px solid var(--ink-700);
    border-radius: 6px;
    padding: 4px;
    margin-bottom: 16px;
  }
  .tabs button {
    flex: 1;
    padding: 8px 12px;
    border: none;
    background: transparent;
    color: var(--mute);
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
  }
  .tabs button.active {
    background: rgba(122, 77, 255, 0.2);
    color: white;
  }
  label {
    display: block;
    font-size: 12px;
    color: var(--slate);
    margin-bottom: 4px;
    margin-top: 12px;
  }
  input, select {
    width: 100%;
    padding: 10px 12px;
    background: var(--ink-900);
    color: var(--slate);
    border: 1px solid var(--ink-600);
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
  }
  input:focus, select:focus {
    outline: none;
    border-color: var(--cephalo);
  }
  input.code {
    font-family: "SF Mono", Consolas, monospace;
    font-size: 20px;
    letter-spacing: 6px;
    text-align: center;
  }
  button.primary {
    width: 100%;
    padding: 12px;
    background: var(--cephalo);
    color: white;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 16px;
  }
  button.primary:hover:not(:disabled) {
    background: #5f2df0;
  }
  button.primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  button.secondary {
    padding: 8px 12px;
    background: var(--ink-700);
    color: var(--slate);
    border: 1px solid var(--ink-600);
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
  }
  button.secondary:hover { background: var(--ink-600); }
  .row { display: flex; gap: 8px; align-items: center; }
  .mock-hint {
    background: rgba(249, 226, 175, 0.1);
    border: 1px solid rgba(249, 226, 175, 0.3);
    color: var(--warn);
    padding: 8px 10px;
    border-radius: 6px;
    font-size: 11px;
    margin-bottom: 12px;
  }
  .dev-code {
    background: rgba(122, 77, 255, 0.15);
    border: 1px solid rgba(122, 77, 255, 0.4);
    color: var(--cephalo-light);
    padding: 8px 10px;
    border-radius: 6px;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 12px;
    margin-bottom: 12px;
  }
  .err {
    color: var(--bad);
    background: rgba(243, 139, 168, 0.1);
    border: 1px solid rgba(243, 139, 168, 0.3);
    padding: 8px 10px;
    border-radius: 6px;
    font-size: 12px;
    margin-top: 10px;
  }
  .link {
    color: var(--cephalo-light);
    cursor: pointer;
    text-decoration: underline;
    background: none;
    border: none;
    font-size: 12px;
    padding: 0;
  }
  .link:disabled { color: var(--mute); text-decoration: none; cursor: default; }
  .footer {
    text-align: center;
    color: var(--mute);
    font-size: 11px;
    margin-top: 16px;
  }

  /* ═══════════════════════════════════════════════════════
     Chat · 参考 ChatGPT/Claude 的比例与节奏
  ═══════════════════════════════════════════════════════ */
  body.chatting {
    align-items: stretch;
    justify-content: center;
  }
  .chat-root {
    width: 100%;
    max-width: 820px;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* Section styles. */
  .chat-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    border-bottom: 1px solid var(--ink-700);
    background: var(--ink-900);
  }
  .chat-header .who {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    flex: 1;
  }
  .chat-header h2 {
    margin: 0;
    color: white;
    font-size: 14px;
    font-weight: 600;
  }
  .chat-header .info {
    font-size: 11px;
    color: var(--mute);
    margin-top: 2px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .header-actions { display: flex; gap: 8px; flex-shrink: 0; }
  .icon-btn {
    width: 32px;
    height: 32px;
    border-radius: 6px;
    border: 1px solid var(--ink-600);
    background: transparent;
    color: var(--slate);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    transition: all 0.15s;
  }
  .icon-btn:hover { background: var(--ink-700); border-color: var(--cephalo); }

  .badge {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 10px;
    font-size: 10px;
    border: 1px solid;
    font-weight: 500;
  }
  .badge.ok { background: rgba(166,227,161,0.12); color: var(--ok); border-color: rgba(166,227,161,0.3); }
  .badge.accent { background: rgba(122,77,255,0.2); color: var(--cephalo-light); border-color: rgba(122,77,255,0.5); }
  .badge.plain { background: var(--ink-800); color: var(--mute); border-color: var(--ink-600); }

  /* Section styles. */
  .chat-toolbar {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 10px 20px;
    border-bottom: 1px solid var(--ink-700);
    font-size: 12px;
  }
  .chat-toolbar label {
    margin: 0;
    color: var(--mute);
    font-size: 11px;
  }
  .chat-toolbar select {
    padding: 5px 10px;
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 6px;
    color: var(--slate);
    font-size: 12px;
    cursor: pointer;
    width: auto;
  }
  /* Section styles. */
  .switch {
    position: relative;
    display: inline-block;
    width: 34px;
    height: 18px;
    flex-shrink: 0;
  }
  .switch input { display: none; }
  .switch-slider {
    position: absolute;
    cursor: pointer;
    inset: 0;
    background: var(--ink-600);
    border-radius: 10px;
    transition: 0.18s;
  }
  .switch-slider::before {
    content: "";
    position: absolute;
    width: 14px; height: 14px;
    left: 2px; top: 2px;
    background: var(--slate);
    border-radius: 50%;
    transition: 0.18s;
  }
  .switch input:checked + .switch-slider {
    background: var(--cephalo);
  }
  .switch input:checked + .switch-slider::before {
    transform: translateX(16px);
    background: white;
  }
  .switch-wrap {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--mute);
    cursor: pointer;
    user-select: none;
  }
  .switch-wrap.active { color: var(--cephalo-light); }

  /* Section styles. */
  .md-picker { position: relative; display: inline-block; }
  .md-trigger {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 6px;
    color: var(--slate);
    font-size: 12px;
    cursor: pointer;
    min-width: 160px;
  }
  .md-trigger:hover { border-color: var(--cephalo); }
  .md-trigger .arrow { margin-left: auto; font-size: 10px; color: var(--mute); }
  .md-popup {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 50;
    min-width: 260px;
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    overflow: hidden;
    animation: fadein 0.12s ease-out;
  }
  .md-tabs {
    display: flex;
    padding: 4px;
    gap: 2px;
    background: var(--ink-900);
    border-bottom: 1px solid var(--ink-700);
  }
  .md-tabs button {
    flex: 1;
    padding: 6px 10px;
    border: none;
    background: transparent;
    color: var(--mute);
    border-radius: 5px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .md-tabs button.active {
    background: var(--cephalo);
    color: white;
  }
  .md-tabs button:not(.active):hover {
    color: var(--slate);
    background: var(--ink-800);
  }
  .md-list {
    max-height: 280px;
    overflow-y: auto;
    padding: 4px;
  }
  .md-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 10px;
    border-radius: 5px;
    cursor: pointer;
    font-size: 13px;
    color: var(--slate);
  }
  .md-item:hover { background: var(--ink-700); }
  .md-item.active {
    background: rgba(122,77,255,0.18);
    color: white;
  }
  .md-item .m-id {
    margin-left: auto;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 10px;
    color: var(--mute);
  }
  .md-item .m-badge {
    font-size: 9px;
    padding: 1px 5px;
    border-radius: 3px;
    background: rgba(56,189,248,0.15);
    color: var(--sucker);
    border: 1px solid rgba(56,189,248,0.4);
  }
  .md-empty {
    padding: 20px 12px;
    text-align: center;
    color: var(--mute);
    font-size: 12px;
  }
  .md-foot {
    border-top: 1px solid var(--ink-700);
    padding: 6px;
    display: flex;
    justify-content: flex-end;
  }
  .md-foot button {
    padding: 5px 10px;
    background: transparent;
    border: 1px solid var(--ink-600);
    color: var(--slate);
    border-radius: 5px;
    cursor: pointer;
    font-size: 11px;
  }
  .md-foot button:hover { background: var(--ink-700); border-color: var(--cephalo); }

  .mode-pill {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    background: var(--ink-900);
    border: 1px solid var(--ink-600);
    border-radius: 8px;
  }
  .mode-pill button {
    padding: 5px 12px;
    border: none;
    background: transparent;
    color: var(--mute);
    border-radius: 6px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .mode-pill button.active {
    background: var(--cephalo);
    color: white;
  }
  .mode-pill button:not(.active):hover {
    color: var(--slate);
    background: var(--ink-800);
  }
  .chat-toolbar .credits {
    margin-left: auto;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 12px;
    color: var(--ok);
    display: flex;
    align-items: center;
    gap: 4px;
  }

  /* Section styles. */
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px 20px 16px;
  }
  .messages-inner {
    max-width: 704px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .msg-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    animation: fadein 0.18s ease-out;
  }
  @keyframes fadein {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .msg-avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    margin-top: 2px;
    user-select: none;
  }
  .msg-row.user .msg-avatar {
    background: linear-gradient(135deg, #7a4dff 0%, #38bdf8 100%);
    color: white;
    font-weight: 600;
    font-size: 13px;
  }
  .msg-row.assistant .msg-avatar {
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
  }

  .msg-body {
    flex: 1;
    min-width: 0;
    padding-top: 4px;
  }
  .msg-meta {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 4px;
    font-size: 12px;
  }
  .msg-meta .role {
    font-weight: 600;
    color: white;
  }
  .msg-meta .model {
    font-size: 11px;
    color: var(--mute);
    font-family: "SF Mono", Consolas, monospace;
  }
  .msg-content {
    color: var(--slate);
    line-height: 1.65;
    font-size: 14.5px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg-content code {
    background: var(--ink-800);
    border: 1px solid var(--ink-700);
    padding: 1px 6px;
    border-radius: 3px;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 13px;
  }
  .msg-content pre {
    background: var(--ink-950);
    border: 1px solid var(--ink-700);
    padding: 10px 14px;
    border-radius: 6px;
    overflow-x: auto;
    margin: 8px 0;
  }
  .msg-content pre code {
    background: transparent;
    border: 0;
    padding: 0;
    font-size: 12.5px;
  }
  .msg-usage {
    margin-top: 6px;
    font-size: 10.5px;
    color: var(--mute);
    font-family: "SF Mono", Consolas, monospace;
  }
  .msg-row.error .msg-content { color: var(--bad); }

  /* Section styles. */
  .composer-wrap {
    padding: 16px 20px 24px;
    background: var(--ink-900);
    border-top: 1px solid var(--ink-700);
  }
  .composer {
    max-width: 704px;
    margin: 0 auto;
    position: relative;
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 14px;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .composer:focus-within {
    border-color: var(--cephalo);
    box-shadow: 0 0 0 3px rgba(122, 77, 255, 0.15);
  }
  .composer textarea {
    width: 100%;
    padding: 14px 56px 14px 18px;
    background: transparent;
    border: none;
    outline: none;
    color: var(--slate);
    font-size: 14.5px;
    line-height: 1.5;
    font-family: inherit;
    resize: none;
    min-height: 52px;
    max-height: 240px;
    display: block;
  }
  .composer textarea::placeholder { color: var(--mute); }
  .send-btn {
    position: absolute;
    right: 8px;
    bottom: 8px;
    width: 36px;
    height: 36px;
    border-radius: 10px;
    border: none;
    background: var(--cephalo);
    color: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .send-btn:hover:not(:disabled) {
    background: #5f2df0;
    transform: scale(1.05);
  }
  .send-btn:disabled {
    background: var(--ink-700);
    color: var(--mute);
    cursor: not-allowed;
    transform: none;
  }
  .send-btn svg { width: 18px; height: 18px; }

  .composer-hint {
    max-width: 704px;
    margin: 6px auto 0;
    font-size: 10.5px;
    color: var(--mute);
    text-align: center;
  }

  /* Section styles. */
  .empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--slate);
    padding: 40px 20px;
  }
  .empty-icon { font-size: 48px; margin-bottom: 16px; }
  .empty-title { font-size: 18px; color: white; font-weight: 600; margin-bottom: 6px; }
  .empty-sub { font-size: 13px; color: var(--mute); max-width: 400px; line-height: 1.6; }

  .hint-kbd {
    display: inline-block;
    padding: 1px 6px;
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 3px;
    font-family: "SF Mono", Consolas, monospace;
    font-size: 10px;
    color: var(--slate);
  }

  .thinking {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 4px 0;
    color: var(--mute);
    font-size: 13px;
  }
  .thinking-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--cephalo-light);
    animation: bounce 1.4s infinite;
  }
  .thinking-dot:nth-child(2) { animation-delay: 0.2s; }
  .thinking-dot:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
    30% { transform: translateY(-6px); opacity: 1; }
  }

  /* Section styles. */
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(7, 10, 16, 0.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
    animation: fadein 0.15s ease-out;
  }
  .modal {
    background: var(--ink-800);
    border: 1px solid var(--ink-600);
    border-radius: 12px;
    width: min(560px, 92vw);
    max-height: 88vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .modal-head {
    padding: 14px 18px;
    border-bottom: 1px solid var(--ink-700);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .modal-head h3 { margin: 0; color: white; font-size: 15px; }
  .modal-body { padding: 16px 18px; overflow-y: auto; }
  .modal-foot {
    padding: 12px 18px;
    border-top: 1px solid var(--ink-700);
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .model-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
  .model-item {
    padding: 8px 12px;
    background: var(--ink-900);
    border: 1px solid var(--ink-700);
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
  }
  .model-item .mid { flex: 1; font-family: "SF Mono", Consolas, monospace; font-size: 12px; }
  .model-item .mlabel { color: var(--slate); font-weight: 500; }
  .model-item.builtin { opacity: 0.65; }
  .model-item .tag {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 3px;
    border: 1px solid;
  }
  .model-item .tag.b { background: var(--ink-700); color: var(--mute); border-color: var(--ink-600); }
  .model-item .tag.c { background: rgba(122,77,255,0.2); color: var(--cephalo-light); border-color: rgba(122,77,255,0.5); }
  .model-item .tag.ext { background: rgba(56,189,248,0.15); color: var(--sucker); border-color: rgba(56,189,248,0.4); }
  .model-item button {
    border: none;
    background: transparent;
    color: var(--bad);
    cursor: pointer;
    padding: 2px 6px;
    font-size: 13px;
    opacity: 0.6;
  }
  .model-item button:hover { opacity: 1; }
  .add-form { background: var(--ink-900); border: 1px dashed var(--ink-600); border-radius: 6px; padding: 12px; margin-top: 8px; }
  .add-form label { margin-top: 0; font-size: 11px; }
  .add-form input { padding: 7px 10px; font-size: 13px; margin-bottom: 4px; }
  .add-form .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .form-hint { font-size: 10px; color: var(--mute); margin-top: 2px; line-height: 1.4; }
</style>
</head>
"""
