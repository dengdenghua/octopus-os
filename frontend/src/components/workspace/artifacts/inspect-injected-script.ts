const BRIDGE_TOKEN_MARKER = "__ECHO_PRIVATE_BRIDGE_TOKEN__";

const INSPECT_INJECTED_SCRIPT_TEMPLATE = `
(function() {
  if (window.__echoInspectInstalled) return;
  window.__echoInspectInstalled = true;
  const BRIDGE_TOKEN = ${BRIDGE_TOKEN_MARKER};
  const bridgeScript = document.currentScript;
  if (bridgeScript && bridgeScript.parentNode) bridgeScript.parentNode.removeChild(bridgeScript);

  function postToParent(message) {
    try {
      window.parent.postMessage(
        BRIDGE_TOKEN ? Object.assign({}, message, { echoBridgeToken: BRIDGE_TOKEN }) : message,
        '*'
      );
    } catch (_) {}
  }

  let active = false;
  let editing = false;
  let editDirty = false;
  let editOriginalBody = '';
  let lastHover = null;
  const OUTLINE_ID = '__echo_inspect_outline__';

  function makeOutline() {
    const el = document.createElement('div');
    el.id = OUTLINE_ID;
    el.style.cssText = [
      'position:fixed',
      'pointer-events:none',
      'border:2px solid #8b5cf6',
      'background:rgba(139,92,246,0.12)',
      'z-index:2147483647',
      'display:none',
      'transition:all 50ms ease-out',
      'border-radius:2px',
      'box-sizing:border-box'
    ].join(';');
    return el;
  }

  let outline = null;
  function ensureOutline() {
    if (outline && outline.isConnected) return outline;
    if (!document.body) return null;
    outline = makeOutline();
    document.body.appendChild(outline);
    return outline;
  }

  function buildSelector(el) {
    if (!el || !el.tagName) return '';
    if (el === document.body) return 'body';
    const escapeCss = function(value) {
      if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
      const slash = String.fromCharCode(92);
      return String(value).split(slash).join(slash + slash).split('"').join(slash + '"');
    };
    const pageNodeId = el.getAttribute && el.getAttribute('data-page-node-id');
    if (pageNodeId) return '[data-page-node-id="' + escapeCss(pageNodeId) + '"]';
    const testId = el.getAttribute && el.getAttribute('data-testid');
    if (testId) return '[data-testid="' + escapeCss(testId) + '"]';
    if (el.id && /^[A-Za-z][\\w-]*$/.test(el.id)) return '#' + el.id;
    const path = [];
    let cur = el;
    let depth = 0;
    while (cur && cur.nodeType === 1 && cur !== document.body && depth < 5) {
      let part = cur.tagName.toLowerCase();
      const cls = cur.classList ? Array.from(cur.classList).filter(function(c){
        return /^[A-Za-z_-][\\w-]*$/.test(c);
      }).slice(0, 2) : [];
      if (cls.length) {
        part += '.' + cls.join('.');
      } else if (cur.parentElement) {
        const sibs = Array.from(cur.parentElement.children).filter(function(c){
          return c.tagName === cur.tagName;
        });
        if (sibs.length > 1) {
          part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
        }
      }
      path.unshift(part);
      cur = cur.parentElement;
      depth++;
    }
    return path.join(' > ');
  }

  function trim(s, n) {
    if (typeof s !== 'string') return '';
    return s.length > n ? s.slice(0, n) + '\\u2026' : s;
  }

  function showOutline(el) {
    const node = ensureOutline();
    if (!node || !el) return;
    const r = el.getBoundingClientRect();
    node.style.display = 'block';
    node.style.left = r.left + 'px';
    node.style.top = r.top + 'px';
    node.style.width = r.width + 'px';
    node.style.height = r.height + 'px';
  }

  function hideOutline() {
    if (outline) outline.style.display = 'none';
  }

  function onMove(e) {
    if (!active) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el || el.id === OUTLINE_ID) return;
    if (el === lastHover) return;
    lastHover = el;
    showOutline(el);
  }

  function onClick(e) {
    if (!active) return;
    e.preventDefault();
    e.stopPropagation();
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el || el.id === OUTLINE_ID) return;
    const r = el.getBoundingClientRect();
    const payload = {
      selector: buildSelector(el),
      tagName: el.tagName ? el.tagName.toLowerCase() : '',
      outerHTML: trim(el.outerHTML || '', 600),
      textContent: trim((el.textContent || '').trim().replace(/\\s+/g, ' '), 200),
      rect: {
        x: Math.round(r.left),
        y: Math.round(r.top),
        w: Math.round(r.width),
        h: Math.round(r.height)
      }
    };
    postToParent({ type: 'echo:inspect:select', payload: payload });
    setActive(false);
  }

  function onKey(e) {
    if (active && e.key === 'Escape') {
      e.preventDefault();
      setActive(false);
      return;
    }
    if (editing && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      emitEditedContent();
    }
  }

  function setActive(v) {
    active = !!v;
    if (active) {
      ensureOutline();
      document.documentElement.style.cursor = 'crosshair';
      document.addEventListener('mousemove', onMove, true);
      document.addEventListener('click', onClick, true);
      document.addEventListener('keydown', onKey, true);
    } else {
      document.documentElement.style.cursor = '';
      document.removeEventListener('mousemove', onMove, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKey, true);
      hideOutline();
      lastHover = null;
    }
    postToParent({ type: 'echo:inspect:state', active: active });
  }

  function removeOutline() {
    const node = document.getElementById(OUTLINE_ID);
    if (node) node.remove();
    outline = null;
  }

  function announceEditState() {
    postToParent({ type: 'echo:edit:state', active: editing, dirty: editDirty });
  }

  function onEditInput() {
    if (!editing || editDirty) return;
    editDirty = true;
    announceEditState();
  }

  function onEditClick(e) {
    if (!editing) return;
    const target = e.target && e.target.nodeType === 1 ? e.target : null;
    const interactive = target && target.closest
      ? target.closest('a,button,input,select,textarea,label,[role="button"],[contenteditable="false"]')
      : null;
    // Stop page-owned click handlers while preserving the browser's native
    // caret/selection behaviour on ordinary editable text.
    e.stopImmediatePropagation();
    if (interactive) e.preventDefault();
  }

  function onEditSubmit(e) {
    if (!editing) return;
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  function enableEditing() {
    if (!document.body || editing) return;
    setActive(false);
    removeOutline();
    editOriginalBody = document.body.innerHTML;
    document.body.setAttribute('contenteditable', 'true');
    document.body.spellcheck = true;
    editing = true;
    editDirty = false;
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('input', onEditInput, true);
    document.addEventListener('click', onEditClick, true);
    document.addEventListener('submit', onEditSubmit, true);
    document.body.focus();
    announceEditState();
  }

  function finishEditing(restore) {
    if (!document.body) return;
    document.body.removeAttribute('contenteditable');
    document.body.removeAttribute('spellcheck');
    if (restore) document.body.innerHTML = editOriginalBody;
    editing = false;
    editDirty = false;
    editOriginalBody = '';
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('input', onEditInput, true);
    document.removeEventListener('click', onEditClick, true);
    document.removeEventListener('submit', onEditSubmit, true);
    announceEditState();
  }

  function emitEditedContent() {
    if (!editing || !document.body) return;
    const contentEditable = document.body.getAttribute('contenteditable');
    document.body.removeAttribute('contenteditable');
    removeOutline();
    const bodyHtml = document.body.innerHTML;
    if (contentEditable !== null) {
      document.body.setAttribute('contenteditable', contentEditable);
    }
    postToParent({ type: 'echo:edit:content', bodyHtml: bodyHtml });
  }

  window.addEventListener('message', function(e) {
    const data = e && e.data;
    if (!data || typeof data !== 'object') return;
    if (BRIDGE_TOKEN && data.echoBridgeToken !== BRIDGE_TOKEN) return;
    if (BRIDGE_TOKEN) e.stopImmediatePropagation();
    if (data.type === 'echo:inspect:enable') setActive(true);
    else if (data.type === 'echo:inspect:disable') setActive(false);
    else if (data.type === 'echo:edit:enable') enableEditing();
    else if (data.type === 'echo:edit:request-save') emitEditedContent();
    else if (data.type === 'echo:edit:cancel') finishEditing(true);
    else if (data.type === 'echo:edit:commit') finishEditing(false);
  });

  function announce() {
    postToParent({ type: 'echo:inspect:ready' });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', announce);
  } else {
    announce();
  }
})();
`;

export function inspectInjectedScript(bridgeToken = ""): string {
  return INSPECT_INJECTED_SCRIPT_TEMPLATE.replace(
    BRIDGE_TOKEN_MARKER,
    JSON.stringify(bridgeToken),
  );
}

/** Tokenless export retained for isolated bridge unit tests. */
export const INSPECT_INJECTED_SCRIPT = inspectInjectedScript();
