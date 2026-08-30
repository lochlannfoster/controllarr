// Section renderers: Needs attention, Live, Reference.
// Every one handles loading / empty / partial / error / stale.
import { h, $, clear, append, patchList, setText, fmt } from './dom.js';
import { pill, ageChip, errorState, emptyState, skeleton, applyCaps } from './ui.js';
import * as incog from './incognito.js';

const SEV = { danger: 'danger', warn: 'warn', info: 'flow' };
const OPEN_GROUPS = new Set();   // Live groups the user expanded this page load (collapsed by default: ten episode rows is a screenful on a phone)
const WORD = { stalled: 'STALLED', vpn: 'VPN', orphaned: 'ORPHANED', import: 'IMPORT', indexer: 'INDEXER', client: 'DOWNLOADS', disk: 'DISK', container: 'SERVICE', request: 'REQUEST', unavailable: 'UNAVAILABLE', backup: 'BACKUP' };

function sourceRows(sources, labels) {
  // one row per failed source: "Prowlarr didn't answer — indexer status unknown"
  const out = [];
  for (const [k, m] of Object.entries(sources || {})) {
    if (m.ok) continue;
    out.push(h('div', { class: 'attn attn-src', role: 'alert' }, h('span', { class: 'glyph muted', 'aria-hidden': 'true' }, '○'),
      h('div', {}, h('b', {}, labels[k] || k), ' didn\'t answer', m.age_s != null ? ` — showing data from ${fmt.age(m.age_s)} ago` : ' — its status is unknown', m.err ? h('div', { class: 'muted mono'}, m.err) : null)));
  }
  return out;
}
// Incognito: an item's `subjects` are the real names the server composed into its sentences (a release, a
// title, a requester). Only those are replaced, so the reason, the counts and the wording all survive.
function shown(i) {
  const subs = i.subjects;
  if (!incog.isOn() || !subs || !subs.length) return i;
  const rep = t => {
    let out = t == null ? t : String(t);
    for (const s of subs) if (out && s.text) out = out.split(s.text).join(s.who ? incog.person(s.key || s.text) : incog.alias(s.key || s.text));
    return out;
  };
  return { ...i, title: rep(i.title), detail: rep(i.detail), facts: (i.facts || []).map(rep) };
}
const LABELS = { vpn: 'gluetun', services: 'Docker', qbit: 'qBittorrent', queue: 'Radarr/Sonarr queue', arr_health: 'Radarr/Sonarr health', prowlarr_health: 'Prowlarr', flaresolverr: 'FlareSolverr', requests: 'Jellyseerr', recovery: 'the retry ledger', board: 'the library scan', jellyfin: 'Jellyfin', calendar: 'the calendar', wanted: 'wanted lists', versions: 'version checks' };

export function renderAttention(host, data, ctx) {
  const list = $('.attn-list', host) || host.appendChild(h('div', { class: 'attn-list' }));
  const count = $('[data-count]', host.closest('section'));
  if (!data) { append(clear(list), skeleton(3, 'skel-attn')); return; }
  for (const el of list.querySelectorAll(':scope > .skel')) el.remove();
  const boardReady = data.sources?.board?.ok !== false;
  const items = data.items || [];
  if (count) setText(count, items.length ? items.length : '');
  const nodes = [];
  patchList(list, items, i => i.id, () => h('article', { class: 'attn' }), (n, raw) => {
    // structure (severity, kind, actions) rarely changes; the numbers in title/detail/facts change every
    // poll (peer counts, ages) and are updated in place so buttons keep focus and clicks land
    const i = shown(raw);
    const sig = [i.id, i.sev, i.kind, (i.actions || []).map(a => a.label).join(','), incog.sig()].join('|');
    if (n.dataset.sig === sig) {
      setText(n.querySelector('.attn-title'), i.title);
      const det = n.querySelector('.attn-detail'); if (det) setText(det, i.detail || '');
      const facts = n.querySelector('.attn-facts'); if (facts) { const sp = facts.children; (i.facts || []).forEach((f, k) => { if (sp[k]) setText(sp[k], f); }); }
      return;
    }
    n.dataset.sig = sig;
    n.className = 'attn attn-' + (SEV[i.sev] || 'muted');
    append(clear(n), 
      h('span', { class: 'attn-word pill pill-' + (SEV[i.sev] || 'muted') }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, i.sev === 'danger' ? '✕' : i.sev === 'warn' ? '▲' : '●'), h('span', {}, WORD[i.kind] || i.kind)),
      h('div', { class: 'attn-body' },
        h('div', { class: 'attn-title' }, i.title),
        i.detail ? h('div', { class: 'attn-detail' }, i.detail) : null,
        i.facts?.length ? h('div', { class: 'attn-facts mono' }, i.facts.map(f => h('span', {}, f))) : null),
      h('div', { class: 'attn-actions' },
        ...(i.actions || []).map((a, idx) => h('button', { type: 'button', class: 'btn btn-sm' + (idx === 0 && a.body ? ' btn-primary' : ''), dataset: { cap: a.cap || '' },
          onclick: () => ctx.runAction(a, raw) }, a.label))));
    applyCaps(n, ctx.caps);
  });
  // partial: failed sources + not-ready board
  for (const el of list.querySelectorAll('.attn-src')) el.remove();
  const extra = sourceRows(data.sources, LABELS);
  if (!boardReady) extra.unshift(h('div', { class: 'attn attn-src' }, skeleton(1), h('div', { class: 'muted' }, 'First library scan still running — disk, stuck titles and torrent matching appear when it finishes.')));
  list.append(...extra);
  const emptyEl = $('.state-empty', list);
  if (!items.length && !extra.length) list.append(emptyState(`Nothing needs you. Checked ${fmt.age(Date.now() / 1000 - (data.generated || 0))} ago.`));
  else if (emptyEl && (items.length || extra.length)) emptyEl.remove();
}

// The pseudonym key for a torrent: the title it belongs to when the arrs know it (so the Live row, the group
// header and the Library row all read the same), else the name it is grouped under.
const torKey = t => (t.matched && t.iid ? `${t.kind}:${t.iid}` : (t.group || t.name || ''));

// ---- Live: one torrent row. `inGroup` rows sit under a group header, so they show the episode label only.
function torRow(n, t, ctx, inGroup) {
  const stalled = /stalled|metaDL/.test(t.state) && t.progress < 100;
  n.className = 'tor' + (stalled ? ' tor-stalled' : '');
  const sig = [t.state, t.matched, t.group || t.name, t.label, t.category, t.poster, inGroup, incog.sig()].join('|');
  if (n.dataset.sig === sig) {   // structure unchanged: update the moving numbers only
    n.querySelector('.pbar > div').style.width = t.progress + '%'; n.querySelector('.pbar').setAttribute('aria-valuenow', t.progress);
    setText(n.querySelector('.tor-bar .mono'), fmt.pct(t.progress)); setText(n.querySelector('.qn'), t.priority > 0 ? '#' + t.priority : '—');
    const f = n.querySelectorAll('.tor-facts span'); setText(f[0], '↓ ' + fmt.speed(t.dlspeed)); setText(f[1], '↑ ' + fmt.speed(t.upspeed)); setText(f[2], 'ETA ' + (t.eta || '—')); setText(f[3], `${t.num_seeds} seeds / ${t.num_leechs} peers`); setText(f[4], 'ratio ' + t.ratio);
    let w = n.querySelector('.tor-why'); if (t.why && !w) n.querySelector('.tor-actions').before(h('div', { class: 'tor-why' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '▲'), t.why)); else if (w && !t.why) w.remove(); else if (w) setText(w.lastChild, t.why);
    return;
  }
  n.dataset.sig = sig;
  const key = torKey(t), label = incog.epLabel(t.label);   // keyed on the label itself: one pseudonym per episode
  const gname = incog.mask(t.group || t.name, key);
  const name = inGroup ? (label || gname) : gname;
  const poster = incog.poster(t.poster);
  append(clear(n),
    h('div', { class: 'tor-head' },
      h('span', { class: 'qn mono', title: 'Queue position' }, t.priority > 0 ? '#' + t.priority : '—'),
      !inGroup && poster ? h('img', { class: 'pos pos-sm', src: poster, alt: '', loading: 'lazy' }) : null,
      h('div', { class: 'tor-title' }, h('b', {}, name), !inGroup && label ? h('span', { class: 'tor-ep mono' }, label) : null,
        inGroup ? null : t.matched ? h('button', { type: 'button', class: 'link', title: 'Open this title in the library', onclick: () => ctx.openItem(t.kind, t.iid) }, 'open') : h('span', { class: 'muted' }, 'not in library')),
      pill(stalled ? 'danger' : /DL|downloading/.test(t.state) ? 'flow' : /UP|uploading/.test(t.state) ? 'ok' : 'muted', t.state)),
    h('div', { class: 'tor-bar' }, h('div', { class: 'pbar', role: 'progressbar', 'aria-label': 'Download progress', 'aria-valuenow': t.progress, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('div', { style: { width: t.progress + '%' } })), h('span', { class: 'mono' }, fmt.pct(t.progress))),
    h('div', { class: 'tor-facts mono' }, h('span', {}, '↓ ' + fmt.speed(t.dlspeed)), h('span', {}, '↑ ' + fmt.speed(t.upspeed)), h('span', {}, 'ETA ' + (t.eta || '—')), h('span', {}, `${t.num_seeds} seeds / ${t.num_leechs} peers`), h('span', {}, 'ratio ' + t.ratio), h('span', {}, fmt.bytes(t.size)), t.category ? h('span', {}, t.category) : null),
    t.why ? h('div', { class: 'tor-why' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '▲'), t.why) : null,
    h('div', { class: 'tor-actions' },
      ...[['t_pause', 'Pause', 'Stop this torrent (it stays listed)'], ['t_resume', 'Resume', 'Start this torrent again'], ['t_recheck', 'Recheck', 'Re-verify the downloaded pieces on disk'],
         ['t_reannounce', 'Reannounce', 'Ask the trackers for peers again'], ['t_top', 'Top', 'Move to the front of the queue (pinned)'], ['t_bottom', 'Bottom', 'Move to the back of the queue']]
        .map(([a, l, tip]) => h('button', { type: 'button', class: 'btn btn-sm', title: tip, onclick: () => ctx.runAction({ body: { action: a, hash: t.hash } }) }, l)),
      h('button', { type: 'button', class: 'btn btn-sm', title: 'Download/seed regardless of queue limits', dataset: { cap: 'can_control_client' }, onclick: () => ctx.runAction({ body: { action: 't_forcestart', hash: t.hash, value: 1 } }) }, 'Force'),
      h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', title: 'Remove the torrent from qBittorrent but keep the downloaded files', dataset: { cap: 'can_remove' }, onclick: () => ctx.runAction({ confirm: true, body: { action: 't_delete', hash: t.hash, name: t.group || t.name }, shown: { name: gname } }) }, 'Remove'),
      h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', title: 'Delete the torrent AND its files: a movie is purged from the whole stack (Radarr, Jellyseerr too); episodes stop being tracked', dataset: { cap: 'can_purge' }, onclick: () => ctx.runAction({ confirm: true, body: { action: 't_purge', hash: t.hash, name: (t.group || t.name) + (t.label ? ' ' + t.label : '') }, shown: { name: gname + (label ? ' ' + label : '') } }) }, 'Purge')));
  applyCaps(n, ctx.caps);
}
export function renderLive(host, data, ctx) {
  const meter = $('.meter', host), tlist = $('.torrents', host), now = $('.nowplaying', host);
  if (!data) { append(clear(tlist), skeleton(1, 'skel-torrent')); append(clear(now), skeleton(1)); return; }
  const tr = data.transfer, q = data.sources?.qbit;
  // meter
  setText($('.dl b', meter), tr ? fmt.speed(tr.dl) : '—'); setText($('.up b', meter), tr ? fmt.speed(tr.up) : '—');
  ctx.spark.push(tr ? tr.dl : null, tr ? tr.up : null);
  const alt = $('[data-alt]', host); if (alt) { alt.setAttribute('aria-pressed', tr && tr.alt ? 'true' : 'false'); setText(alt, tr && tr.alt ? 'Alt-speed on' : 'Alt-speed'); }
  const conn = $('.conn', meter); if (conn) append(clear(conn), tr ? pill(tr.connection === 'connected' ? 'ok' : tr.connection === 'firewalled' ? 'warn' : 'danger', tr.connection || 'unknown') : pill('muted', 'no client'), tr ? h('span', { class: 'mono muted' }, `${tr.dht} DHT`) : null);
  // torrents: one row per torrent, labelled by episode (S10E03 · title) instead of the bare series name; torrents of the
  // same title fold into a group with controls that act on all of them at once (a season pack's episodes, say)
  const tors = data.torrents || [];
  for (const el of tlist.querySelectorAll(':scope > .skel')) el.remove();
  const groups = [], byKey = new Map();
  for (const t of tors) {
    const gk = t.matched && t.iid ? `${t.kind}:${t.iid}` : `n:${t.group || t.name}`;
    let g = byKey.get(gk); if (!g) { g = { key: gk, title: t.group || t.name, kind: t.kind, iid: t.iid, poster: t.poster, matched: t.matched, tors: [] }; byKey.set(gk, g); groups.push(g); }
    g.tors.push(t);
  }
  const items = groups.map(g => g.tors.length > 1 ? { type: 'group', key: 'g:' + g.key, g } : { type: 'tor', key: g.tors[0].hash, t: g.tors[0] });
  patchList(tlist, items, i => i.key, i => i.type === 'group' ? h('section', { class: 'tor-group' }) : h('article', { class: 'tor' }), (n, i) => {
    if (i.type === 'tor') return torRow(n, i.t, ctx, false);
    let head = n.querySelector(':scope > .tg-head'); if (!head) { head = h('div', { class: 'tg-head' }); n.append(head, h('div', { class: 'tg-rows', hidden: true })); }
    const g = i.g, hashes = g.tors.map(t => t.hash).join('|'); const seasons = [...new Set(g.tors.map(t => t.season).filter(x => x != null))];
    const open = OPEN_GROUPS.has(i.key); const rows = n.querySelector(':scope > .tg-rows'); rows.hidden = !open;
    // the aggregate line changes every poll; the structure (title, count, actions) rarely does
    const dl = g.tors.filter(t => /DL|downloading/.test(t.state) && t.progress < 100).length, done = g.tors.filter(t => t.progress >= 100).length;
    const size = g.tors.reduce((a, t) => a + (t.size || 0), 0), got = g.tors.reduce((a, t) => a + (t.size || 0) * (t.progress || 0) / 100, 0);
    const pct = size ? Math.round(100 * got / size) : 0, speed = g.tors.reduce((a, t) => a + (t.dlspeed || 0), 0);
    const agg = `${done}/${g.tors.length} done${dl ? ` · ${dl} downloading` : ''} · ↓ ${fmt.speed(speed)} · ${fmt.bytes(size)}`;
    const sig = [g.title, g.poster, hashes, seasons.join(','), g.matched, incog.sig()].join('|');
    if (head.dataset.sig === sig) { setText(head.querySelector('.tg-agg'), agg); const bar = head.querySelector('.pbar > div'); bar.style.width = pct + '%'; head.querySelector('.pbar').setAttribute('aria-valuenow', pct); setText(head.querySelector('.tg-bar .mono'), fmt.pct(pct)); return; }
    head.dataset.sig = sig;
    const gtitle = incog.mask(g.title, torKey(g.tors[0])), gposter = incog.poster(g.poster);
    const suffix = seasons.length === 1 ? ` S${String(seasons[0]).padStart(2, '0')}` : '';
    const name = g.title + suffix, shownName = gtitle + suffix;   // the action carries the real name, the dialog what the row shows
    const caret = h('button', { type: 'button', class: 'btn btn-icon scaret tg-caret', 'aria-expanded': String(open), title: `Show or hide the ${g.tors.length} torrents of ${gtitle}`, onclick: () => { const now = !OPEN_GROUPS.has(i.key); now ? OPEN_GROUPS.add(i.key) : OPEN_GROUPS.delete(i.key); rows.hidden = !now; caret.setAttribute('aria-expanded', String(now)); caret.textContent = now ? '▾' : '▸'; } }, open ? '▾' : '▸');
    append(clear(head), caret,
      gposter ? h('img', { class: 'pos pos-sm', src: gposter, alt: '', loading: 'lazy' }) : null,
      h('div', { class: 'tor-title' }, h('b', {}, gtitle), h('span', { class: 'mono muted' }, `${g.tors.length} torrents${seasons.length ? ' · ' + seasons.map(x => 'S' + String(x).padStart(2, '0')).join(', ') : ''}`),
        g.matched && g.iid ? h('button', { type: 'button', class: 'link', title: 'Open this title in the library', onclick: () => ctx.openItem(g.kind, g.iid) }, 'open') : h('span', { class: 'muted' }, 'not in library')),
      h('div', { class: 'tor-bar tg-bar' }, h('div', { class: 'pbar', role: 'progressbar', 'aria-label': 'Group progress', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('div', { style: { width: pct + '%' } })), h('span', { class: 'mono' }, fmt.pct(pct))),
      h('div', { class: 'tg-agg mono muted' }, agg),
        h('div', { class: 'tor-actions tg-actions' },
          ...[['t_pause', 'Pause all', 'Stop every torrent in this group'], ['t_resume', 'Resume all', 'Start every torrent in this group'], ['t_top', 'Top', 'Move the whole group to the front of the queue'], ['t_bottom', 'Bottom', 'Move the whole group to the back of the queue']]
            .map(([a, l, tip]) => h('button', { type: 'button', class: 'btn btn-sm', title: tip, onclick: () => ctx.runAction({ body: { action: a, hash: hashes } }) }, l)),
          h('button', { type: 'button', class: 'btn btn-sm', title: 'Download every torrent in this group regardless of the queue limits', dataset: { cap: 'can_control_client' }, onclick: () => ctx.runAction({ body: { action: 't_forcestart', hash: hashes, value: 1 } }) }, 'Force all'),
          h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', title: 'Remove every torrent in this group from qBittorrent but keep the downloaded files', dataset: { cap: 'can_remove' }, onclick: () => ctx.runAction({ confirm: true, body: { action: 't_delete', hash: hashes, name: name }, shown: { name: shownName } }) }, 'Remove all'),
          h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', title: 'Delete every torrent in this group AND its files; the episodes stop being tracked (a movie is purged whole)', dataset: { cap: 'can_purge' }, onclick: () => ctx.runAction({ confirm: true, body: { action: 't_purge', hash: hashes, name: name }, shown: { name: shownName } }) }, 'Purge all')));
    applyCaps(head, ctx.caps);
    patchList(rows, g.tors, t => t.hash, () => h('article', { class: 'tor' }), (rn, t) => torRow(rn, t, ctx, true));
  });
  const errs = [];
  if (q && !q.ok) errs.push(errorState('qBittorrent unreachable', (ctx.vpn ? 'Usually the VPN tunnel — check the VPN item above. ' : '') + (q.err || ''), () => ctx.refresh('live')));
  for (const el of tlist.querySelectorAll('.state')) el.remove();
  if (errs.length) tlist.append(...errs);
  else if (!tors.length) tlist.append(emptyState('No active downloads.', h('a', { class: 'link', href: '#library' }, 'Open the library')));
  const stale = $('[data-age]', host.closest('section')); if (stale) append(clear(stale), q ? ageChip(q.age_s, 30) : null);
  // now playing
  const sess = data.sessions, jf = data.sources?.jellyfin;
  clear(now);
  if (jf && !jf.ok) now.append(errorState(/no Jellyfin API key/.test(jf.err || '') ? 'Jellyfin key missing' : 'Jellyfin unreachable', /no Jellyfin API key/.test(jf.err || '') ? 'Re-run ./install.sh — it stores the arr-stack key in app.env' : jf.err));
  else if (!sess || !sess.length) now.append(emptyState('Nobody is watching.'));
  else for (const s of sess) {
    const method = s.method === 'DirectPlay' ? 'Direct Play' : s.method === 'DirectStream' ? 'Direct Stream' : 'Transcode';
    now.append(h('article', { class: 'np' },
      h('div', { class: 'np-title' }, h('b', {}, incog.mask(s.title)), s.paused ? h('span', { class: 'muted' }, ' · paused') : null),
      h('div', { class: 'np-facts mono' }, h('span', {}, incog.who(s.user)), h('span', {}, s.client), h('span', {}, incog.who(s.device)), s.pct != null ? h('span', {}, s.pct + ' %') : null),
      h('div', {}, pill(method === 'Transcode' ? 'warn' : 'ok', method), method === 'Transcode' && s.reasons?.length ? h('span', { class: 'muted' }, ' ' + s.reasons.join(', ') + (s.video ? ` → ${s.video}` : '')) : null)));
  }
}

// ---- Reference
export function renderReference(host, data, ctx) {
  const apps = $('.apps', host);
  if (!data) { append(clear(apps), skeleton(2)); return; }
  clear(apps);
  const S = data.sources || {}; const bad = Object.entries(S).find(([k, m]) => !m.ok);
  if (!(data.apps || []).length && bad) { apps.append(errorState('Reference unavailable', `${LABELS[bad[0]] || bad[0]} — ${bad[1].err || 'no answer'}`, () => ctx.refresh('reference'))); return; }
  for (const a of data.apps || []) {
    apps.append(h('div', { class: 'app' },
      h('a', { class: 'app-name', href: a.url, target: '_blank', rel: 'noopener', title: `Open ${a.name} in a new tab` }, a.name),
      h('span', { class: 'mono muted' }, a.version || ''),
      a.state && a.state !== 'running' ? pill('danger', a.state) : null,
      a.name === 'Jellyseerr' && data.jellyseerr_update ? pill('warn', 'update available') : null));
  }
}
