// UI primitives: toasts (queued, live region), confirm dialog (native <dialog> = focus trap + Esc),
// theme / density persistence, cap-based visibility, status pill.
import { h, $, $$, clear } from './dom.js';

// ---- toasts
let toastHost;
export function toast(msg, kind = 'info', ms = 4000) {
  if (!toastHost) { toastHost = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(toastHost); }
  const t = h('div', { class: 'toast toast-' + kind, role: kind === 'error' ? 'alert' : null }, msg);
  toastHost.append(t);
  while (toastHost.children.length > 3) toastHost.firstChild.remove();
  let timer = setTimeout(() => t.remove(), ms);
  t.addEventListener('mouseenter', () => clearTimeout(timer));
  t.addEventListener('mouseleave', () => { timer = setTimeout(() => t.remove(), 1500); });
  return t;
}

// ---- confirm dialog (consequence text comes from the server)
export function confirmDialog({ title, text, verb = 'Confirm', danger = false }) {
  return new Promise(resolve => {
    const dlg = h('dialog', { class: 'dlg', 'aria-labelledby': 'dlg-title' },
      h('form', { method: 'dialog' },
        h('h2', { id: 'dlg-title' }, title),
        h('p', {}, text || ''),
        h('div', { class: 'dlg-actions' },
          h('button', { type: 'button', class: 'btn', value: 'cancel', onclick: () => dlg.close('cancel') }, 'Cancel'),
          h('button', { type: 'button', class: 'btn ' + (danger ? 'btn-danger' : 'btn-primary'), onclick: () => dlg.close('ok') }, verb))));
    dlg.addEventListener('close', () => { resolve(dlg.returnValue === 'ok'); dlg.remove(); });
    document.body.append(dlg); dlg.showModal();
    dlg.querySelector(danger ? '.btn:not(.btn-danger)' : '.btn-primary').focus();
  });
}

// ---- generic modal (release picker, password change, command palette)
export function modal(title, body, { wide = false, onClose } = {}) {
  const dlg = h('dialog', { class: 'dlg' + (wide ? ' dlg-wide' : ''), 'aria-label': title },
    h('div', { class: 'dlg-head' }, h('h2', {}, title), h('button', { type: 'button', class: 'btn btn-icon', 'aria-label': 'Close', onclick: () => dlg.close() }, '×')),
    h('div', { class: 'dlg-body' }, body));
  dlg.addEventListener('close', () => { onClose && onClose(); dlg.remove(); });
  document.body.append(dlg); dlg.showModal();
  return dlg;
}

// ---- theme + density
export function initPrefs() {
  const root = document.documentElement;
  const get = k => { try { return localStorage.getItem(k); } catch { return null; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
  const apply = () => { const t = get('mc-theme'); if (t && t !== 'auto') root.dataset.theme = t; else delete root.dataset.theme; root.dataset.density = get('mc-density') || 'comfortable'; };
  apply();
  return {
    theme() { return get('mc-theme') || 'auto'; },
    cycleTheme() { const order = ['auto', 'dark', 'light']; const n = order[(order.indexOf(this.theme()) + 1) % 3]; set('mc-theme', n); apply(); return n; },
    density() { return get('mc-density') || 'comfortable'; },
    toggleDensity() { set('mc-density', this.density() === 'compact' ? 'comfortable' : 'compact'); apply(); return this.density(); },
  };
}

// ---- capabilities
export function applyCaps(root, caps) {
  for (const el of $$('[data-cap]', root)) { const c = el.dataset.cap; el.hidden = !!c && !caps[c]; }
}

// ---- status pill: colour + glyph + word, always
const GLYPH = { ok: '●', warn: '▲', danger: '✕', flow: '▶', info: '●', muted: '○' };
export function pill(kind, text, extraClass = '') {
  return h('span', { class: `pill pill-${kind} ${extraClass}`.trim() }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, GLYPH[kind] || '●'), h('span', {}, text));
}
export function stageKind(stage) {
  return { Available: 'ok', Downloading: 'flow', Importing: 'flow', Partial: 'warn', Searching: 'warn', Waiting: 'muted', Unavailable: 'danger' }[stage] || 'muted';
}
export function ageChip(ageS, staleAfter) {
  if (ageS == null) return null;
  const stale = ageS > staleAfter;
  return h('span', { class: 'age' + (stale ? ' age-stale' : ''), title: stale ? 'This source stopped answering; showing the last good value' : 'Time since this source last answered' }, (stale ? 'as of ' : '') + fmtAge(ageS) + ' ago');
}
function fmtAge(s) { s = Math.round(s); return s < 60 ? s + ' s' : s < 3600 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h'; }

export function errorState(what, detail, retry) {
  return h('div', { class: 'state state-error', role: 'alert' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '✕'),
    h('div', {}, h('b', {}, what), detail ? h('div', { class: 'muted' }, detail) : null),
    retry ? h('button', { type: 'button', class: 'btn btn-sm', onclick: retry }, 'Retry') : null);
}
export function emptyState(text, action) {
  return h('div', { class: 'state state-empty' }, h('span', {}, text), action || null);
}
export function skeleton(rows = 3, cls = '') {
  return h('div', { class: 'skel ' + cls, 'aria-hidden': 'true' }, Array.from({ length: rows }, () => h('div', { class: 'skel-row' })));
}
