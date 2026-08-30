// Tiny DOM helpers: element builder, keyed list patching, formatters. No innerHTML with data anywhere.
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;            // only for trusted static markup (icons)
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
/** Like Element.append, but drops null/false/undefined instead of printing them as text (native append stringifies null to "null"). */
export function append(el, ...children) {
  for (const c of children.flat(Infinity)) { if (c == null || c === false) continue; el.append(c.nodeType ? c : document.createTextNode(String(c))); }
  return el;
}
export function setText(el, s) { const t = String(s ?? ''); if (el.textContent !== t) el.textContent = t; }

/** Keyed reconcile: keeps existing nodes (focus, scroll, open state) and only touches what changed. */
export function patchList(container, items, keyOf, create, update) {
  const have = new Map();
  for (const n of container.children) if (n.dataset.key) have.set(n.dataset.key, n);
  let cursor = container.firstElementChild;
  for (const it of items) {
    const k = String(keyOf(it));
    let n = have.get(k);
    if (n) { update(n, it); have.delete(k); }
    else { n = create(it); n.dataset.key = k; update(n, it); }
    if (n !== cursor) container.insertBefore(n, cursor);
    else cursor = cursor.nextElementSibling;
  }
  for (const n of have.values()) n.remove();
}

export const fmt = {
  bytes(b) { b = Number(b) || 0; if (b >= 1e12) return (b / 1e12).toFixed(2) + ' TB'; if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB'; if (b >= 1e6) return (b / 1e6).toFixed(0) + ' MB'; return Math.round(b / 1e3) + ' kB'; },
  speed(b) { b = Number(b) || 0; return b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB/s' : (b / 1e3).toFixed(0) + ' kB/s'; },
  age(s) { if (s == null) return '—'; s = Math.max(0, Math.round(s)); if (s < 60) return s + ' s'; if (s < 3600) return Math.round(s / 60) + ' min'; if (s < 86400) return (s / 3600).toFixed(s < 36000 ? 1 : 0) + ' h'; return Math.round(s / 86400) + ' d'; },
  pct(p) { return (p == null ? '—' : Math.round(p) + ' %'); },
  duration(min) { min = Math.round(Number(min) || 0); if (!min) return ''; const h = Math.floor(min / 60), m = min % 60; return h ? `${h} h${m ? ' ' + m + ' min' : ''}` : `${m} min`; },
  ago(ts) { return ts ? fmt.age(Date.now() / 1000 - ts) + ' ago' : '—'; },
};
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
