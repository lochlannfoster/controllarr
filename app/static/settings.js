// Settings — every knob across the stack, grouped by what you control (not by container).
// Nothing is written on blur: edits collect in the ApplyBar as a diff, Apply sends the group.
import { h, $, $$, clear, append, setText, fmt } from './modules/dom.js';
import { toast, confirmDialog, modal, initPrefs, pill, skeleton, errorState, emptyState } from './modules/ui.js';
import { initTips } from './modules/tips.js';

const CFG = window.MC || {};
const prefs = initPrefs();
const themeBtn = $('#theme'), densBtn = $('#density');
function labelPrefs() { setText(themeBtn, { auto: 'Theme: auto', dark: 'Theme: dark', light: 'Theme: light' }[prefs.theme()]); setText(densBtn, prefs.density() === 'compact' ? 'Compact' : 'Comfortable'); }
themeBtn.addEventListener('click', () => { prefs.cycleTheme(); labelPrefs(); }); densBtn.addEventListener('click', () => { prefs.toggleDensity(); labelPrefs(); }); labelPrefs();
initTips($('#help'));

async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' });
  if (r.status === 401 || (r.redirected && /\/login/.test(r.url))) { location.href = '/login'; throw new Error('signed out'); }
  return r.json();
}
const CAPS = { can_purge: 'Purge (delete files + torrent + request)', can_delete_files: 'Delete torrent / episode files', can_import: 'Import existing files / add titles',
  can_remove: 'Remove a torrent or queue item / blocklist & retry', can_change_root: 'Change root folder', can_grab: 'Grab a specific release', can_control_client: 'Pause/resume all, speed limits, force-start, RSS sync, test indexers', can_manage_requests: 'Approve / decline Jellyseerr requests' };

// ---- field definitions (names are the API keys the server already reads/applies)
const num = (name, label, opt = {}) => ({ name, label, type: 'number', ...opt });
const bool = (name, label, help) => ({ name, label, type: 'bool', help });
const arrFields = [
  { section: 'Size' }, num('size_cap', 'Preferred size', { unit: 'MB/min', help: 'Releases near this bitrate score best' }), num('size_max', 'Maximum size', { unit: 'MB/min', help: 'Bigger releases are rejected' }),
  { section: 'Releases' }, num('min_seeders', 'Release threshold', { help: 'This app\'s own minimum, applied to every indexer it has' }), { name: 'audio_language', label: 'Audio language', type: 'select', options: ['Any', 'English', 'Japanese', 'Original'], help: 'Original penalises dubbed releases' },
  bool('allow_unknown', 'Allow unknown-quality releases'), bool('prefer_h264', 'Prefer h264 over x265'), bool('propers', 'Prefer & upgrade to propers / repacks'),
  { section: 'Files' }, bool('rename', 'Rename files to the standard scheme'), bool('copy_hardlinks', 'Hardlink on import (not copy)', 'Needs torrents and media on one filesystem — they are'),
  { name: 'recycle_bin', label: 'Recycle bin path', type: 'text', help: 'Blank = delete outright' }, num('recycle_days', 'Recycle bin cleanup', { unit: 'days' }), num('min_free_mb', 'Minimum free space on import', { unit: 'MB' }),
];
// an optional part of the stack that this install runs without (no qBittorrent, no Prowlarr, no ntfy) has no group
const PRESENT = g => !g.app || !(CFG.services || []).length || CFG.services.includes(g.app);
const GROUPS = [
  { id: 'presets', label: 'Presets', custom: renderPresets },
  { id: 'downloads', label: 'Downloads', tab: 'qbit', app: 'qbittorrent', fields: [
    { section: 'Speed' }, num('dl_limit', 'Download limit', { unit: 'MB/s', help: '0 = unlimited', step: 0.5 }), num('up_limit', 'Upload limit', { unit: 'MB/s', help: '0 = unlimited', step: 0.5 }),
    { section: 'Alternative speed' }, bool('scheduler_enabled', 'Use the alternative limits on a schedule'), num('sched_from', 'From', { unit: 'h', min: 0, max: 23 }), num('sched_to', 'To', { unit: 'h', min: 0, max: 23 }), num('alt_dl_limit', 'Alt download limit', { unit: 'MB/s', step: 0.5 }), num('alt_up_limit', 'Alt upload limit', { unit: 'MB/s', step: 0.5 }),
    { section: 'Queue' }, num('max_active_downloads', 'Active downloads', { help: `capped at ${CFG.maxActive} by the installer — a single-disk box saturates beyond that`, max: CFG.maxActive, min: 1 }), num('max_active_uploads', 'Active uploads'),
    { section: 'Seeding' }, bool('seed_after_complete', 'Seed after a download completes'), num('max_ratio', 'Stop seeding at ratio', { help: '0 = never', step: 0.1 }), bool('remove_completed', 'Remove the torrent once imported'),
    { section: 'Network', render: (d, ctx) => h('div', { class: 'frow' }, h('span', { class: 'flabel' }, 'Listen port'), h('span', { class: 'mono' }, ctx.vpnPort ? ctx.vpnPort : '—'), h('span', { class: 'fhelp' }, ctx.vpnPort ? 'set by the VPN port-forward watchdog every 5 min' : 'no forwarded port (VPN off or no PF)')) },
    { section: 'Right now', render: () => h('div', { class: 'dwbtns' }, actBtn('Pause all', 'qall_pause', true), actBtn('Resume all', 'qall_resume'), actBtn('Toggle alt-speed', 'alt_toggle'), actBtn('RSS sync', 'rss_sync')) },
  ] },
  { id: 'quality', label: 'Quality & size', sub: [{ id: 'radarr', label: 'Movies (Radarr)', tab: 'radarr', app: 'radarr', fields: arrFields }, { id: 'sonarr', label: 'TV (Sonarr)', tab: 'sonarr', app: 'sonarr', fields: arrFields }] },
  { id: 'indexers', label: 'Indexers', tab: 'prowlarr', app: 'prowlarr', custom: renderIndexers },
  { id: 'subtitles', label: 'Subtitles', tab: 'bazarr', app: 'bazarr', fields: [
    { section: 'Languages' }, { name: 'subtitle_langs', label: 'Languages', type: 'text', help: 'comma-separated codes, e.g. en,fr' }, bool('hearing_impaired', 'Prefer hearing-impaired'), bool('forced', 'Prefer forced'),
    { section: 'Scoring' }, num('minimum_score_movie', 'Minimum score — movies', { unit: '%' }), num('minimum_score', 'Minimum score — episodes', { unit: '%' }), bool('adaptive_searching', 'Adaptive searching'),
    { section: 'Upgrades' }, bool('upgrade_subs', 'Upgrade existing subtitles'), num('days_to_upgrade_subs', 'Upgrade window', { unit: 'days' }),
    { section: 'Embedded' }, bool('use_embedded_subs', 'Use embedded subtitles'), bool('embedded_subs_show_desired', 'Show desired embedded'), bool('ignore_pgs_subs', 'Ignore PGS'), bool('ignore_vobsub_subs', 'Ignore VobSub'),
    { section: 'Providers', render: renderProviders },
  ] },
  { id: 'requests', label: 'Requests', tab: 'jellyseerr', app: 'jellyseerr', custom: renderJellyseerr },
  { id: 'media', label: 'Media server', tab: 'jellyfin', app: 'jellyfin', custom: renderJellyfin },
  { id: 'notifications', label: 'Notifications', tab: 'notify', app: 'ntfy', fields: [
    { section: 'ntfy' }, { name: 'ntfy_url', label: 'Server URL', type: 'text', help: 'blank = the stack\'s own ntfy' }, { name: 'topic_media', label: 'Media topic', type: 'text' }, { name: 'topic_admin', label: 'Admin topic', type: 'text' },
    { section: 'Quiet hours', help: 'Media notifications are silent between these hours; admin alerts always ring' }, num('quiet_start', 'From', { unit: 'h', min: 0, max: 23 }), num('quiet_end', 'To', { unit: 'h', min: 0, max: 23 }),
    { section: 'Test', render: () => h('div', {}, h('button', { type: 'button', class: 'btn', onclick: async () => { const j = await api('/api/ntfy-test', {}); toast(j.message, j.ok ? 'ok' : 'error'); } }, 'Send a test notification'), h('p', { class: 'fhelp' }, 'Saved here and used for the test above; anything else on this box that reads settings.local picks them up on its next run.')) },
  ] },
  { id: 'users', label: 'Users & roles', custom: renderUsers },
  { id: 'backup', label: 'Backup & config', custom: renderBackup },
].filter(PRESENT).map(g => g.sub ? { ...g, sub: g.sub.filter(PRESENT) } : g);
function actBtn(label, action, confirm = false) { return h('button', { type: 'button', class: 'btn', onclick: () => runAction(action, confirm) }, label); }
async function runAction(action, confirm) {
  if (confirm) { let c = {}; try { c = await api('/api/consequence?action=' + action); } catch {} if (!await confirmDialog({ title: c.title || action, text: c.text, verb: (c.title || 'Confirm').split(' ')[0] })) return; }
  const j = await api('/api/action', { action }); toast(j.message || (j.ok ? 'Done' : 'Failed'), j.ok ? 'ok' : 'error');
}

// ---- state
let current = null, original = null, values = null, services = {}, vpnPort = null, showSeq = 0;   // showSeq: only the latest show() may render
const pane = $('#pane'), bar = $('#applybar'), diffEl = $('#applydiff');

function flat(groups) { return groups.flatMap(g => g.sub ? g.sub.map(s => ({ ...s, parent: g })) : [g]); }
const ALL = flat(GROUPS);
function buildNav() {
  const ul = $('#grouplist'), sel = $('#groupsel');
  for (const g of GROUPS) {
    if (g.sub) { ul.append(h('li', { class: 'gn-parent' }, g.label)); for (const s of g.sub) { ul.append(h('li', {}, h('a', { href: '#' + s.id, class: 'gn-sub' }, s.label))); sel.append(h('option', { value: s.id }, g.label + ' — ' + s.label)); } }
    else { ul.append(h('li', {}, h('a', { href: '#' + g.id }, g.label))); sel.append(h('option', { value: g.id }, g.label)); }
  }
  sel.addEventListener('change', () => { location.hash = '#' + sel.value; });
}
function reach(app) { const s = services[app]; if (!s) return pill('muted', 'status unknown'); return s.state === 'running' && s.health !== 'unhealthy' ? pill('ok', 'reachable') : pill('danger', s.state === 'missing' ? 'missing' : s.health || s.state); }

async function show(id) {
  if (current && hasDiff()) { if (!await confirmDialog({ title: 'Discard unsaved changes?', text: `You changed ${Object.keys(diff()).length} setting(s) in ${current.label} and did not apply them.`, verb: 'Discard' })) { location.hash = '#' + current.id; return; } }
  const g = ALL.find(x => x.id === id) || ALL[0]; current = g; original = null; values = null; hideBar(); const my = ++showSeq;
  for (const a of $$('#grouplist a')) a.setAttribute('aria-current', a.getAttribute('href') === '#' + g.id ? 'page' : 'false');
  $('#groupsel').value = g.id;
  append(clear(pane), h('div', { class: 'pane-head' }, h('h2', {}, (g.parent ? g.parent.label + ' — ' : '') + g.label), g.app ? h('span', { class: 'reach' }, h('span', { class: 'muted' }, g.app), reach(g.app)) : null), skeleton(5));
  try {
    const data = g.tab ? await api('/api/set/' + g.tab) : {};
    if (my !== showSeq) return;   // the user moved on (or Apply re-opened) while this group was loading: a stale render would show one group's fields with another's values
    if (data && data.error) throw new Error(data.error);
    original = JSON.parse(JSON.stringify(data)); values = JSON.parse(JSON.stringify(data));
    const body = h('div', { class: 'pane-body' });
    if (g.custom) body.append(await g.custom(data, g)); else body.append(renderFields(g, data));
    if (my !== showSeq) return;
    for (const s of $$('.skel', pane)) s.remove();
    pane.append(body);
  } catch (e) {
    if (my !== showSeq) return;
    for (const s of $$('.skel', pane)) s.remove();
    pane.append(errorState(`${g.app || 'The panel'} did not answer — nothing can be changed here until it does`, String(e.message || e), () => show(id)));
  }
}
function renderFields(g, data) {
  const wrap = h('div', { class: 'fields' });
  let sec = null;
  for (const f of g.fields) {
    if (f.section) { sec = h('fieldset', { class: 'fset' }, h('legend', {}, f.section), f.help ? h('p', { class: 'fhelp' }, f.help) : null); wrap.append(sec); if (f.render) sec.append(f.render(data, { vpnPort })); continue; }
    const id = 'f-' + f.name; let input;
    const onchange = e => { if (!values) return; values[f.name] = f.type === 'bool' ? e.target.checked : (f.type === 'number' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value); showDiff(); };
    if (f.type === 'bool') input = h('input', { type: 'checkbox', id, checked: !!data[f.name], onchange });
    else if (f.type === 'select') input = h('select', { id, onchange }, f.options.map(o => h('option', { value: o, selected: o === data[f.name] }, o)));
    else input = h('input', { type: f.type === 'number' ? 'number' : 'text', id, value: data[f.name] ?? '', step: f.step, min: f.min, max: f.max, inputmode: f.type === 'number' ? 'decimal' : null, onchange, oninput: onchange });
    const row = h('div', { class: 'frow' + (f.type === 'bool' ? ' frow-bool' : '') },
      f.type === 'bool' ? h('label', { for: id, class: 'flabel' }, input, ' ', f.label) : h('label', { for: id, class: 'flabel' }, f.label),
      f.type === 'bool' ? null : h('span', { class: 'fctl' }, input, f.unit ? h('span', { class: 'unit mono' }, f.unit) : null),
      f.help ? h('span', { class: 'fhelp' }, f.help) : null);
    (sec || wrap).append(row);
  }
  return wrap;
}
function diff() { const d = {}; if (!original || !values) return d; for (const k of new Set([...Object.keys(original), ...Object.keys(values)])) { if (JSON.stringify(original[k]) !== JSON.stringify(values[k])) d[k] = [original[k], values[k]]; } return d; }
function hasDiff() { return Object.keys(diff()).length > 0; }
function label(k) { for (const g of ALL) for (const f of g.fields || []) if (f.name === k) return f.label; return k; }
function fmtV(v) { return v === true ? 'on' : v === false ? 'off' : Array.isArray(v) ? v.join(',') : (v === '' || v == null ? '—' : String(v)); }
function showDiff() {
  const d = diff(); if (!Object.keys(d).length) return hideBar();
  append(clear(diffEl), h('b', {}, `${Object.keys(d).length} change${Object.keys(d).length > 1 ? 's' : ''}: `), ...Object.entries(d).map(([k, [a, b]]) => h('span', { class: 'chg' }, `${label(k)} ${fmtV(a)} → ${fmtV(b)}`)));
  bar.hidden = false;
}
function hideBar() { bar.hidden = true; }
$('#discard').addEventListener('click', () => show(current.id));
$('#apply').addEventListener('click', async () => {
  if (!current || !current.tab) return;
  const btn = $('#apply'); btn.disabled = true; setText(btn, 'Applying…');
  try {
    const j = await api('/api/set/' + current.tab, values);
    toast(j.message || (j.ok ? 'Applied' : 'Failed'), j.ok ? 'ok' : 'error');
    if (j.ok) { original = JSON.parse(JSON.stringify(values)); show(current.id); }   // applied = nothing left to discard, so show() must not ask
  } catch (e) { toast('The panel did not answer — ' + e.message, 'error'); }
  btn.disabled = false; setText(btn, 'Apply');
});

// ---- custom groups
function renderProviders(data) {
  const box = h('div', { class: 'provgrid' });
  const en = new Set(data.enabled_providers || []);
  for (const p of data.providers || []) box.append(h('label', { class: 'flabel chk' }, h('input', { type: 'checkbox', checked: en.has(p), onchange: e => { if (!values) return; const s = new Set(values.enabled_providers || []); e.target.checked ? s.add(p) : s.delete(p); values.enabled_providers = [...s]; showDiff(); } }), ' ', p));
  return box;
}
async function renderIndexers(data, g) {
  const list = h('div', { class: 'idxlist' });
  const draw = d => { clear(list); if (!(d.indexers || []).length) list.append(emptyState('No indexers in Prowlarr yet.'));
    for (const i of d.indexers || []) list.append(h('div', { class: 'frow' }, h('span', { class: 'flabel' }, i.name), pill(i.enable ? 'ok' : 'muted', i.enable ? 'enabled' : 'disabled'),
      h('span', { class: 'dwbtns' }, h('button', { type: 'button', class: 'btn btn-sm', onclick: async () => { const j = await api('/api/prowlarr', { act: 'toggle', id: i.id }); toast(j.message, j.ok ? 'ok' : 'error'); draw(await api('/api/set/prowlarr')); } }, i.enable ? 'Disable' : 'Enable'),
        h('button', { type: 'button', class: 'btn btn-sm', onclick: async () => { toast('Testing ' + i.name + '…'); const j = await api('/api/prowlarr', { act: 'test', id: i.id }); toast(i.name + ': ' + j.message, j.ok ? 'ok' : 'error'); } }, 'Test')))); };
  draw(data);
  let fs = data.flaresolverr;
  return h('div', {}, list, h('div', { class: 'dwbtns', style: { marginTop: '12px' } },
    h('button', { type: 'button', class: 'btn', onclick: () => runAction('indexers_test_all') }, 'Test all'),
    h('button', { type: 'button', class: 'btn', title: 'Push Prowlarr\'s indexer list to Radarr and Sonarr now', onclick: async () => { const j = await api('/api/prowlarr', { act: 'sync' }); toast(j.message, j.ok ? 'ok' : 'error'); } }, 'Sync to Radarr / Sonarr'),
    h('a', { class: 'btn', href: CFG.links?.prowlarr || '#', target: '_blank', rel: 'noopener' }, 'Open Prowlarr')),
    h('p', { class: 'fhelp' }, 'FlareSolverr: ', fs == null ? 'unknown' : fs ? 'answering' : 'not answering — Cloudflare-protected indexers will fail'), h('p', { class: 'fhelp' }, 'Minimum seeders lives under Quality & size, per app.'));
}
function renderJellyseerr(data) {
  const box = h('div', { class: 'fields' });
  for (const kind of ['movie', 'series']) {
    const d = data[kind] || {};
    const fs = h('fieldset', { class: 'fset' }, h('legend', {}, kind === 'movie' ? 'New movie requests' : 'New series requests'));
    fs.append(h('div', { class: 'frow' }, h('label', { class: 'flabel', for: 'js-' + kind + '-p' }, 'Quality profile'), h('span', { class: 'fctl' }, h('select', { id: 'js-' + kind + '-p', onchange: e => { if (!values) return; values[kind] = { ...(values[kind] || {}), serverId: d.serverId, profile: Number(e.target.value) }; showDiff(); } }, (d.profiles || []).map(p => h('option', { value: p.id, selected: p.id === d.profile }, p.name))))));
    fs.append(h('div', { class: 'frow' }, h('label', { class: 'flabel', for: 'js-' + kind + '-r' }, 'Root folder'), h('span', { class: 'fctl' }, h('select', { id: 'js-' + kind + '-r', onchange: e => { if (!values) return; values[kind] = { ...(values[kind] || {}), serverId: d.serverId, root: e.target.value }; showDiff(); } }, (d.roots || []).map(r => h('option', { value: r, selected: r === d.root }, r))))));
    box.append(fs);
  }
  return box;
}
function renderJellyfin(data) {
  return h('div', { class: 'fields' }, h('fieldset', { class: 'fset' }, h('legend', {}, 'API key'),
    h('div', { class: 'frow' }, h('span', { class: 'flabel' }, 'Key in app.env'), data.key_present ? pill('ok', 'present') : pill('danger', 'missing'), h('span', { class: 'fhelp' }, data.key_present ? 'The dashboard reads now-playing sessions with it.' : 'Re-run ./install.sh — it looks up the arr-stack key in Jellyfin and writes JELLYFIN_APIKEY to app.env.')),
    h('div', { class: 'frow' }, h('span', { class: 'flabel' }, 'Server'), data.version ? h('span', { class: 'mono' }, 'Jellyfin ' + data.version) : pill('danger', 'unreachable'))),
    h('fieldset', { class: 'fset' }, h('legend', {}, 'Library'), h('div', { class: 'dwbtns' }, h('button', { type: 'button', class: 'btn', disabled: !data.key_present, onclick: () => runAction('jf_scan') }, 'Scan library now')), h('p', { class: 'fhelp' }, 'Imports already trigger a targeted scan; use this after moving files by hand.')));
}
async function renderUsers() {
  const box = h('div', { class: 'fields' });
  const list = h('div', {});
  async function draw() {
    const us = await api('/api/users'); clear(list);
    for (const u of us) list.append(h('div', { class: 'frow' }, h('span', { class: 'flabel' }, h('b', {}, u.username)), pill(u.role === 'admin' ? 'warn' : 'muted', u.role),
      h('span', { class: 'dwbtns' }, h('button', { type: 'button', class: 'btn btn-sm', onclick: () => passwordDialog(u.username, draw) }, 'Change password'),
        h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', onclick: async () => { if (!await confirmDialog({ title: `Remove user ${u.username}`, text: 'Deletes this login. The last admin cannot be removed.', verb: 'Remove', danger: true })) return; const j = await api('/api/users/delete', { username: u.username }); toast(j.message, j.ok ? 'ok' : 'error'); draw(); } }, 'Remove'))));
  }
  await draw();
  const nu = h('input', { placeholder: 'username', autocomplete: 'off', 'aria-label': 'New username' }), np = h('input', { type: 'password', placeholder: 'password', autocomplete: 'new-password', 'aria-label': 'New password' }), nr = h('select', { 'aria-label': 'Role' }, h('option', { value: 'user' }, 'user'), h('option', { value: 'admin' }, 'admin'));
  box.append(h('fieldset', { class: 'fset' }, h('legend', {}, 'Accounts'), list,
    h('div', { class: 'dwrow', style: { marginTop: '10px' } }, nu, np, nr, h('button', { type: 'button', class: 'btn btn-primary', onclick: async () => { const j = await api('/api/users', { username: nu.value, password: np.value, role: nr.value }); toast(j.message, j.ok ? 'ok' : 'error'); if (j.ok) { nu.value = np.value = ''; draw(); } } }, 'Add user'))));
  const roles = await api('/api/roles'); const caps = roles.user || {}; const grid = h('div', { class: 'provgrid' });
  for (const [c, l] of Object.entries(CAPS)) grid.append(h('label', { class: 'flabel chk' }, h('input', { type: 'checkbox', dataset: { cap: c }, checked: !!caps[c] }), ' ', l));
  box.append(h('fieldset', { class: 'fset' }, h('legend', {}, 'What standard users may do'), h('p', { class: 'fhelp' }, 'Admins can do everything. Standard users can search, retry, monitor, change quality, fetch subtitles and manage their own torrents; tick to grant more.'), grid,
    h('div', { class: 'dwbtns', style: { marginTop: '10px' } }, h('button', { type: 'button', class: 'btn btn-primary', onclick: async () => { const b = { role: 'user' }; for (const i of grid.querySelectorAll('input')) b[i.dataset.cap] = i.checked; const j = await api('/api/roles', b); toast(j.message, j.ok ? 'ok' : 'error'); } }, 'Save permissions'))));
  return box;
}
function passwordDialog(username, done) {
  const p1 = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'New password', required: true }), p2 = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'Repeat password', required: true });
  const err = h('p', { class: 'login-err', role: 'alert' });
  const dlg = modal('New password for ' + username, h('form', { class: 'fields', onsubmit: async e => { e.preventDefault(); if (p1.value !== p2.value) { setText(err, 'The two passwords differ.'); return; } if (p1.value.length < 8) { setText(err, 'Use at least 8 characters.'); return; } const j = await api('/api/users', { username, password: p1.value }); toast(j.message, j.ok ? 'ok' : 'error'); if (j.ok) { dlg.close(); done && done(); } else setText(err, j.message); } },
    h('label', { class: 'flabel' }, 'New password', p1), h('label', { class: 'flabel' }, 'Repeat it', p2), err, h('div', { class: 'dlg-actions' }, h('button', { type: 'button', class: 'btn', onclick: () => dlg.close() }, 'Cancel'), h('button', { type: 'submit', class: 'btn btn-primary' }, 'Change password'))));
  p1.focus();
}
async function applyPreset(name) {
  let c = {}; try { c = await api('/api/consequence?action=config_preset&name=' + encodeURIComponent(name)); } catch {}
  if (!await confirmDialog({ title: c.title || 'Apply preset', text: c.text, verb: 'Apply' })) return;
  const j = await api('/api/config/preset', { name }); toast(j.message, j.ok ? 'ok' : 'error');
}
async function renderPresets() {
  const box = h('div', { class: 'fields' });
  const presets = await api('/api/config/presets');
  const groups = [['throughput', 'Right now — what the box does', 'Speed limits, seeding and the queue, applied to qBittorrent at once. Also in the Live section\'s Tune menu on the dashboard.'],
                  ['quality', 'What it looks for', 'Size and quality rules for Radarr and Sonarr. New grabs follow them; nothing already on disk changes.']];
  for (const [g, title, help] of groups) {
    const grid = h('div', { class: 'preset-grid' });
    for (const p of presets.filter(p => p.group === g)) grid.append(h('div', { class: 'preset' }, h('b', {}, p.name), h('p', { class: 'fhelp' }, p.desc), h('button', { type: 'button', class: 'btn btn-sm btn-primary', title: 'Apply this preset now (a confirmation names what changes)', onclick: () => applyPreset(p.name) }, 'Apply')));
    box.append(h('fieldset', { class: 'fset' }, h('legend', {}, title), h('p', { class: 'fhelp' }, help), grid));
  }
  box.append(h('p', { class: 'fhelp' }, 'A preset overlays a few values on the current settings; every knob it touches can be fine-tuned in the groups on the left afterwards.'));
  return box;
}
async function renderBackup(data) {
  const box = h('div', { class: 'fields' });
  const b = await api('/api/set/backup');
  box.append(h('fieldset', { class: 'fset' }, h('legend', {}, 'Nightly backup'),
    h('div', { class: 'frow' }, h('span', { class: 'flabel' }, 'Last backup'), b.last_backup_h == null ? pill(b.visible ? 'danger' : 'muted', b.visible ? 'none found' : 'folder not visible to the panel') : pill(b.last_backup_h > 36 ? 'warn' : 'ok', fmt.age(b.last_backup_h * 3600) + ' ago'), h('span', { class: 'fhelp' }, `backup-config.sh writes encrypted tarballs to ${b.dir || '(not set)'} at 03:30; the log is active/backup-config.log on the server`))));
  const file = h('input', { type: 'file', accept: 'application/json', hidden: true, onchange: async e => { const f = e.target.files[0]; if (!f) return; try { const cfg = JSON.parse(await f.text()); let c = {}; try { c = await api('/api/consequence?action=config_import'); } catch {} if (!await confirmDialog({ title: c.title || 'Load config', text: c.text, verb: 'Load' })) return; const j = await api('/api/config/import', cfg); toast(j.message, j.ok ? 'ok' : 'error'); } catch { toast('That file is not a config snapshot', 'error'); } e.target.value = ''; } });
  box.append(h('fieldset', { class: 'fset' }, h('legend', {}, 'Settings snapshots'), h('p', { class: 'fhelp' }, 'A snapshot holds every group above (no secrets). One-click tuning lives under Presets.'),
    h('div', { class: 'dwbtns' },
      h('button', { type: 'button', class: 'btn', onclick: async () => { const r = await fetch('/api/config/export'); const txt = await r.text(); const a = h('a', { href: URL.createObjectURL(new Blob([txt], { type: 'application/json' })), download: 'media-config.json' }); document.body.append(a); a.click(); a.remove(); toast('Snapshot downloaded', 'ok'); } }, 'Save snapshot (.json)'),
      h('button', { type: 'button', class: 'btn', onclick: () => file.click() }, 'Load snapshot…'), file)));
  box.append(h('fieldset', { class: 'fset danger-zone' }, h('legend', {}, 'Danger zone'), h('div', { class: 'dwbtns' }, h('button', { type: 'button', class: 'btn btn-danger-ghost', onclick: async () => { let c = {}; try { c = await api('/api/consequence?action=config_defaults'); } catch {} if (!await confirmDialog({ title: c.title || 'Restore defaults', text: c.text, verb: 'Restore', danger: true })) return; const j = await api('/api/config/defaults', {}); toast(j.message, j.ok ? 'ok' : 'error'); } }, 'Restore installer defaults'))));
  return box;
}

// ---- boot
buildNav();
(async () => { try { const r = await api('/api/reference'); for (const a of r.apps || []) services[a.container] = { state: a.state }; const l = await api('/api/live'); vpnPort = l.vpn && l.vpn.port; } catch {} show((location.hash || '#presets').slice(1)); })();
window.addEventListener('hashchange', () => show(location.hash.slice(1)));
window.addEventListener('beforeunload', e => { if (hasDiff()) { e.preventDefault(); e.returnValue = ''; } });
