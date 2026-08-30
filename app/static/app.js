// Controllarr — dashboard entry. Vanilla ES modules, no build, no dependencies.
import { h, $, $$, clear, append, setText, fmt } from './modules/dom.js';
import { createScheduler, wirePageEvents } from './modules/poll.js';
import { toast, confirmDialog, initPrefs, applyCaps, modal, pill } from './modules/ui.js';
import { createDash } from './modules/dash.js';
import { renderAttention, renderLive, renderReference } from './modules/sections.js';
import { createLibrary } from './modules/library.js';
import { createSystem } from './modules/system.js';
import { initTips } from './modules/tips.js';
import * as incog from './modules/incognito.js';

const CFG = window.MC || {};
const prefs = initPrefs();
const sched = createScheduler(); wirePageEvents(sched);

// ---- actions: one path for every write (consequence → confirm → POST → toast → refresh)
async function post(body) {
  try {
    const r = await fetch('/api/action', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' });
    if (r.status === 401) { location.href = '/login'; return { ok: false, message: 'Signed out' }; }
    return await r.json();
  } catch (e) { return { ok: false, message: 'The panel did not answer — ' + (e.message || e) }; }
}
// What the confirmation asks the server about. The POST below still carries the real title and the real id —
// only this copy is pseudonymised, and `incognito=1` tells the server to leave its own names out of the prose
// it writes. `a.shown` is what the row that fired the action actually displays, when that is keyed differently.
function consequenceOf(a) {
  const b = { ...a.body };
  if (!incog.isOn()) return b;
  const key = b.kind && b.id != null ? `${b.kind}:${b.id}` : null;
  if (b.title != null) b.title = incog.mask(b.title, key || b.title);
  if (b.name != null) b.name = incog.mask(b.name, key || b.name);
  return { ...b, ...(a.shown || {}), incognito: 1 };
}
async function runAction(a, item) {
  if (a.open) { library.openDrawer(a.open.kind, a.open.id); return false; }
  if (a.jump) { navigate(a.jump); return false; }
  const body = a.body; if (!body) return false;
  if (a.confirm || body.action === 'purge') {
    let c = { title: body.action, text: '' };
    try { const q = new URLSearchParams(Object.entries(consequenceOf(a)).filter(([k, v]) => v != null && typeof v !== 'object').map(([k, v]) => [k, String(v)])); c = await (await fetch('/api/consequence?' + q)).json(); } catch {}
    const ok = await confirmDialog({ title: c.title || body.action, text: c.text, verb: (c.title || 'Confirm').split(' ')[0], danger: /purge|delete|remove|decline|blocklist/i.test(body.action) });
    if (!ok) return false;
  }
  const j = await post(body);
  toast(j.message || (j.ok ? 'Done' : 'Failed'), j.ok ? 'ok' : 'error');
  sched.refresh('attention', 'live', 'board');
  return j;
}
const ctx = {
  caps: CFG.caps || {}, role: CFG.role, stages: CFG.stages || [], vpn: !!CFG.vpn, sched,
  runAction, post, confirm: confirmDialog, refresh: (...n) => sched.refresh(...n),
  openItem: (kind, id) => library.openDrawer(kind, id),
  spark: { dl: [], up: [], push(dl, up) { this.dl.push(dl); this.up.push(up); if (this.dl.length > 24) { this.dl.shift(); this.up.shift(); } drawSpark(); } },
};

// ---- header controls
const themeBtn = $('#theme'), densBtn = $('#density'), incogBtn = $('#incognito');
function labelPrefs() {
  setText(themeBtn, { auto: 'Theme: auto', dark: 'Theme: dark', light: 'Theme: light' }[prefs.theme()]); setText(densBtn, prefs.density() === 'compact' ? 'Compact' : 'Comfortable'); densBtn.setAttribute('aria-pressed', String(prefs.density() === 'compact'));
  setText(incogBtn, incog.isOn() ? 'Incognito on' : 'Incognito'); incogBtn.setAttribute('aria-pressed', String(incog.isOn()));
}
themeBtn.addEventListener('click', () => { prefs.cycleTheme(); labelPrefs(); });
densBtn.addEventListener('click', () => { prefs.toggleDensity(); labelPrefs(); });
incogBtn.addEventListener('click', () => { incog.toggle(); labelPrefs(); });
// Flipping it redraws everything already on screen from the data the page is holding — waiting for the next
// poll would leave real names up for seconds. Pickers and per-episode dialogs close: they are transient, and
// re-rendering one under the pointer is worse than reopening it.
incog.onChange(() => {
  for (const d of $$('dialog')) if (!d.classList.contains('drawer')) d.close();
  if (lastAttention) renderAttention($('#attention'), lastAttention, ctx);
  if (lastLive) renderLive($('#live'), lastLive, ctx);
  library.redraw();
  const sys = sched.get('system'); if (sys && sys.data) system.render(sys.data);
});
labelPrefs();
initTips($('#help'));
applyCaps(document, ctx.caps);
if (ctx.role !== 'admin') for (const el of $$('[data-admin]')) el.hidden = true;
// the two apps a household actually opens, one tap from the top of the page
const ql = $('#quicklinks');
if (ql && CFG.links) append(ql,
  h('a', { class: 'btn btn-sm', href: CFG.links.jellyfin, target: '_blank', rel: 'noopener', title: 'Open Jellyfin — watch what is in the library' }, 'Jellyfin ↗'),
  h('a', { class: 'btn btn-sm', href: CFG.links.jellyseerr, target: '_blank', rel: 'noopener', title: 'Open Jellyseerr — request a movie or show' }, 'Jellyseerr ↗'));

// ---- sections
const system = createSystem($('#system'), ctx);
const library = createLibrary($('#library'), ctx);
const dash = createDash($('#dash'), { onStation: st => { library.setStage(st); navigate('#library'); }, hostname: CFG.hostname, ip: CFG.host });
let lastBoard = null, lastLive = null, lastAttention = null, boardAt = 0;   // boardAt: when /api/board last answered (a 304 reuses a cached body, so its own timestamp can be older)
function updateLine() {
  const stalled = (lastLive?.torrents || []).filter(t => /stalled|metaDL/.test(t.state) && t.progress < 100).length;
  const down = (lastAttention?.items || []).filter(i => i.kind === 'container').length;
  dash.update({ summary: lastBoard?.summary, stalled, vpnState: lastLive?.vpn, containersDown: down, flowing: (lastLive?.transfer?.dl || 0) > 0,
    ageS: boardAt ? (Date.now() - boardAt) / 1000 : null, activeFilter: library.stage() });
}
function sectionAge(name, sources, staleAfter) {
  const el = $(`section[data-section="${name}"] [data-age]`); if (!el) return;
  const ages = Object.values(sources || {}).filter(m => m.age_s != null).map(m => m.age_s);
  clear(el); if (!ages.length) return;
  const worst = Math.max(...ages); const bad = Object.values(sources || {}).some(m => !m.ok);
  el.append(h('span', { class: 'age' + (bad || worst > staleAfter ? ' age-stale' : ''), title: bad ? 'At least one source failed; showing the last good values' : 'Time since the oldest source in this section answered' }, (bad || worst > staleAfter ? 'as of ' : '') + fmt.age(worst) + ' ago'));
}
sched.add('attention', '/api/attention', 10000, d => { lastAttention = d; renderAttention($('#attention'), d, ctx); sectionAge('attention', d.sources, 30); updateLine(); }, err => { if (!lastAttention) renderAttention($('#attention'), { items: [], sources: { panel: { ok: false, err } } }, ctx); });
sched.add('live', '/api/live', () => ((lastLive?.torrents?.length || lastLive?.sessions?.length) ? 5000 : 30000), d => { lastLive = d; renderLive($('#live'), d, ctx); updateLine(); }, err => { if (!lastLive) renderLive($('#live'), { torrents: [], sessions: [], transfer: null, sources: { qbit: { ok: false, err }, jellyfin: { ok: false, err } } }, ctx); });
sched.add('board', '/api/board', 15000, d => { lastBoard = d; boardAt = Date.now(); library.setData(d); updateLine(); }, err => library.setError(err));
sched.add('reference', '/api/reference', 300000, d => renderReference($('#reference'), d, ctx), err => renderReference($('#reference'), { apps: [], sources: { panel: { ok: false, err } } }, ctx));
sched.add('system', '/api/system', 10000, d => system.render(d), err => system.setError(err));
renderAttention($('#attention'), null, ctx); renderLive($('#live'), null, ctx); renderReference($('#reference'), null, ctx);
setInterval(updateLine, 1000);
// When the panel itself stops answering, the server-side age_s freezes at the last good reply, so each
// section's chip is driven from the client's own last success until the source recovers (its onData
// then rewrites the chip).
const SECTION_SOURCE = { attention: 'attention', live: 'live' };
function tickStale() {
  for (const [sec, name] of Object.entries(SECTION_SOURCE)) {
    const s = sched.get(name); const el = $(`section[data-section="${sec}"] [data-age]`) || $(`#${sec} [data-age]`);
    if (!s || !el || !s.fails || !s.last) continue;
    append(clear(el), h('span', { class: 'age age-stale', title: `The panel is not answering (${s.err}); showing values from the last good reply` },
      `unreachable · as of ${fmt.age((Date.now() - s.last) / 1000)} ago`));
  }
}
setInterval(tickStale, 1000);

// ---- sparkline (2 minutes at 5 s)
function drawSpark() {
  const svg = $('#spark'); if (!svg) return; const W = 120, H = 28;
  const max = Math.max(1, ...ctx.spark.dl.filter(v => v != null), ...ctx.spark.up.filter(v => v != null));
  const path = arr => arr.map((v, i) => `${i ? 'L' : 'M'}${(i / 23 * W).toFixed(1)},${(H - (v == null ? 0 : v / max * (H - 2)) - 1).toFixed(1)}`).join(' ');
  svg.querySelector('.sp-dl').setAttribute('d', path(ctx.spark.dl)); svg.querySelector('.sp-up').setAttribute('d', path(ctx.spark.up));
  svg.setAttribute('aria-label', `Download ${fmt.speed(ctx.spark.dl.at(-1) || 0)}, upload ${fmt.speed(ctx.spark.up.at(-1) || 0)} over the last two minutes`);
}
// global client controls
for (const b of $$('[data-global]')) b.addEventListener('click', () => runAction({ confirm: b.dataset.global === 'qall_pause', body: { action: b.dataset.global } }));
// Tune: the presets (Settings ▸ Presets) one tap away — admin only, confirmed with the preset's own description.
// They are throughput only: quality is the guide's, and is applied from a diff you have read, never from a menu.
const tune = $('#tune');
if (tune && ctx.role === 'admin') (async () => {
  try {
    const ps = await (await fetch('/api/config/presets', { credentials: 'same-origin' })).json();
    tune.append(h('optgroup', { label: 'Right now' }, ps.map(p => h('option', { value: p.name }, p.name))));
    tune.addEventListener('change', async () => {
      const name = tune.value; tune.value = ''; if (!name) return;
      let c = {}; try { c = await (await fetch('/api/consequence?action=config_preset&name=' + encodeURIComponent(name))).json(); } catch {}
      if (!await confirmDialog({ title: c.title || 'Apply preset', text: c.text, verb: 'Apply' })) return;
      const r = await fetch('/api/config/preset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }), credentials: 'same-origin' });
      const j = await r.json(); toast(j.message || (j.ok ? 'Applied' : 'Failed'), j.ok ? 'ok' : 'error'); sched.refresh('live', 'system');
    });
  } catch { tune.hidden = true; }
})();
$('#import-btn')?.addEventListener('click', () => runAction({ confirm: true, body: { action: 'import_library' } }));

// ---- navigation: sections + hash + ?item deep link
function navigate(hash) {
  const [id, q] = hash.replace('#', '').split('?');
  const el = document.getElementById(id); if (!el) return;
  if (q) { const p = new URLSearchParams(q); if (p.get('stage')) library.setStage(p.get('stage')); if (p.get('sort')) library.setSort('Available', p.get('sort')); }
  el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  history.replaceState(null, '', '#' + id + (q ? '?' + q : ''));
  setTimeout(markCurrent, 700);
}
for (const a of $$('.secnav a[href^="#"]')) a.addEventListener('click', e => { e.preventDefault(); navigate(a.getAttribute('href')); });
let markTimer = null;
function markCurrent() {
  markTimer = null;
  const line = Math.min(innerHeight * 0.35, 260); const secs = $$('main > section'); let cur = null;
  for (const s of secs) if (s.getBoundingClientRect().top <= line) cur = s;
  if (window.scrollY + innerHeight >= document.documentElement.scrollHeight - 2) cur = secs[secs.length - 1];   // page bottom: the last section is current even when short
  for (const a of $$('.secnav a')) a.setAttribute('aria-current', cur && a.getAttribute('href') === '#' + cur.id ? 'true' : 'false');
}
addEventListener('scroll', () => { if (!markTimer) markTimer = requestAnimationFrame(markCurrent); }, { passive: true });
addEventListener('resize', markCurrent); setTimeout(markCurrent, 100);
if (location.hash) setTimeout(() => navigate(location.hash), 50);
const m = new URLSearchParams(location.search).get('item');
if (m) { const [k, id] = m.split(':'); if (k && id) library.openDrawer(k, parseInt(id, 10)); }

// ---- command palette (Ctrl/⌘ K): sections, apps, titles, actions
function openPalette() {
  const input = h('input', { type: 'search', name: 'palette', placeholder: 'Jump to a section, app, title, or run an action…', 'aria-label': 'Command palette', autocomplete: 'off' });
  const list = h('div', { class: 'pal-list', role: 'listbox' });
  const dlg = modal('Go to', h('div', { class: 'pal' }, input, list));
  const ref = sched.get('reference')?.data;
  const cmds = [
    ...[['attention', 'Needs attention'], ['live', 'Live'], ['library', 'Library'], ['reference', 'Reference']].map(([id, l]) => ({ label: l, kind: 'section', run: () => navigate('#' + id) })),
    ...(ref?.apps || []).map(a => ({ label: 'Open ' + a.name, kind: 'app', run: () => window.open(a.url, '_blank', 'noopener') })),
    { label: 'Pause all torrents', kind: 'action', cap: 'can_control_client', run: () => runAction({ confirm: true, body: { action: 'qall_pause' } }) },
    { label: 'Resume all torrents', kind: 'action', cap: 'can_control_client', run: () => runAction({ body: { action: 'qall_resume' } }) },
    { label: 'Toggle alt-speed', kind: 'action', cap: 'can_control_client', run: () => runAction({ body: { action: 'alt_toggle' } }) },
    { label: 'RSS sync now', kind: 'action', cap: 'can_control_client', run: () => runAction({ body: { action: 'rss_sync' } }) },
    { label: 'Test all indexers', kind: 'action', cap: 'can_control_client', run: () => runAction({ body: { action: 'indexers_test_all' } }) },
    { label: 'Scan Jellyfin library', kind: 'action', cap: 'can_control_client', run: () => runAction({ body: { action: 'jf_scan' } }) },
    ...(ctx.role === 'admin' ? [{ label: 'Settings', kind: 'page', run: () => { location.href = '/settings'; } }] : []),
    ...library.items().filter(i => Number.isInteger(i.id)).map(i => ({ label: `${incog.mask(i.title, i.kind + ':' + i.id)}${incog.yr(i.year) ? ' (' + i.year + ')' : ''}`, search: `${i.title} ${i.year || ''}`, kind: i.stage, run: () => library.openDrawer(i.kind, i.id) })),
  ].filter(c => !c.cap || ctx.caps[c.cap]);
  let sel = 0;
  function draw() {
    // searched against the real title even in incognito: you know your own library, the screen does not have to show it
    const q = input.value.trim().toLowerCase(); const hits = (q ? cmds.filter(c => (c.search || c.label).toLowerCase().includes(q)) : cmds).slice(0, 12);
    clear(list); sel = Math.min(sel, Math.max(0, hits.length - 1));
    hits.forEach((c, i) => list.append(h('button', { type: 'button', role: 'option', class: 'pal-item' + (i === sel ? ' is-sel' : ''), 'aria-selected': String(i === sel), onclick: () => { dlg.close(); c.run(); } }, h('span', {}, c.label), h('span', { class: 'muted mono' }, c.kind))));
    list._hits = hits;
  }
  input.addEventListener('input', () => { sel = 0; draw(); });
  input.addEventListener('keydown', e => { const n = list._hits?.length || 0; if (e.key === 'ArrowDown') { e.preventDefault(); sel = (sel + 1) % n; draw(); } else if (e.key === 'ArrowUp') { e.preventDefault(); sel = (sel - 1 + n) % n; draw(); } else if (e.key === 'Enter') { e.preventDefault(); const c = list._hits?.[sel]; if (c) { dlg.close(); c.run(); } } });
  draw(); input.focus();
}
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); } });
$('#palette')?.addEventListener('click', openPalette);
$('#refresh-now')?.addEventListener('click', async () => { try { await fetch('/api/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch {} sched.refresh(); toast('Refreshed', 'ok'); });
