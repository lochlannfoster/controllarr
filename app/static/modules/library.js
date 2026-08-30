// Library (the board): stage sections with keyed rows, filter / sort / stage filter, multi-select bar,
// the inline season tree under a show, and the per-title drawer (a native <dialog>: focus trap, Esc, focus restored).
import { h, $, $$, clear, append, patchList, setText, fmt } from './dom.js';
import { pill, stageKind, applyCaps, modal, skeleton, emptyState, errorState, toast } from './ui.js';
import { createEpisodes, subSearch } from './episodes.js';
import * as incog from './incognito.js';

const TIP = {
  t_top: 'Move this torrent to the front of the queue (pinned so the optimiser leaves it)', t_bottom: 'Move this torrent to the back of the queue',
  t_pause: 'Stop this torrent (it stays listed)', t_resume: 'Start this torrent again', t_recheck: 'Re-verify the downloaded pieces on disk', t_reannounce: 'Ask the trackers for peers again',
  t_forcestart: 'Download/seed regardless of queue limits', t_delete: 'Remove the torrent from qBittorrent but KEEP the downloaded files', t_purge: 'Remove the torrent AND delete its files; a movie is purged whole, episodes stop being tracked',
  set_quality: 'Change the quality profile this title is allowed to grab', set_min_availability: 'When Radarr is allowed to start searching (announced / in cinemas / released)',
  set_series_type: 'How Sonarr parses episode numbering (standard / anime / daily)', set_root_folder: 'Which library folder this title lives in (existing files are not moved)',
  monitor: 'Toggle whether the arrs keep tracking and searching for this title', monitor_all: 'Monitor every episode of this show and search for all the gaps at once',
  retry: 'Search again and grab the best matching release automatically',
  refresh: "Re-scan this title's files and metadata", blocklist_retry: 'Blocklist the current download so it is never picked again, then search for another release',
  purge: 'Delete the files, the torrent AND the Jellyseerr request. Cannot be undone.', search: 'List the actual releases and grab a specific one',
  fetchsubs: 'Ask Bazarr to search all providers for missing subtitles now', subsearch: 'List subtitle candidates from every provider and pick one to download',
  openx: 'Open this title\'s controls (quality, monitoring, subtitles, torrents)', row_tv: 'Show the seasons and episodes; each one has its own controls', row_movie: 'Open this title\'s controls',
};

export function createLibrary(host, ctx) {
  const STAGES = ctx.stages;
  const state = { q: '', stage: '', sort: {}, collapsed: new Set(), data: null };
  try { state.collapsed = new Set(JSON.parse(localStorage.getItem('mc-collapsed') || '[]')); } catch {}
  const filter = $('#filter', host), stageSel = $('#fstage', host), sections = $('.sections', host), selbar = $('#selbar');
  for (const s of STAGES) stageSel.append(h('option', { value: s }, s));
  filter.addEventListener('input', () => { state.q = filter.value.trim().toLowerCase(); render(); });
  stageSel.addEventListener('change', () => { state.stage = stageSel.value; render(); syncHash(); });
  function syncHash() { const u = new URL(location.href); if (state.stage) u.hash = '#library?stage=' + encodeURIComponent(state.stage); history.replaceState(null, '', u); }
  const episodes = createEpisodes(ctx, { releasePicker });

  // ---- rows: a show expands inline into its seasons; a movie opens the drawer; › always opens the drawer
  function primary(n, it) {
    if (!Number.isInteger(it.id)) return;
    if (it.kind === 'tv') return toggleExpand(n, it);
    openDrawer(it.kind, it.id, n.querySelector('.info'));
  }
  function rowNode() {
    const n = h('article', { class: 'item' });
    // composedPath, not target.closest: a click inside the season tree re-renders it, so its target is detached by the time the event reaches the row
    n.addEventListener('click', e => { if (e.composedPath().some(el => el instanceof Element && el.matches('.sel-wrap, .openx, .expand, dialog'))) return; primary(n, n._item); });
    n.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('info')) { e.preventDefault(); primary(n, n._item); } });
    return n;
  }
  async function toggleExpand(n, it) {
    let exp = n.querySelector(':scope > .expand'); const info = n.querySelector('.info');
    if (exp) { exp.remove(); info.setAttribute('aria-expanded', 'false'); return; }
    exp = h('div', { class: 'expand' }); const tree = h('div', { class: 'tree' });
    exp.append(tree, h('div', { class: 'dwbtns expand-foot' }, h('button', { type: 'button', class: 'btn btn-sm', title: TIP.openx, onclick: () => openDrawer(it.kind, it.id, info) }, 'Title controls ›')));
    n.append(exp); info.setAttribute('aria-expanded', 'true');
    await episodes.seriesTree(it, tree, { openAll: true, onSelection: (k, total) => { const box = n.querySelector('.sel'); if (!box) return; box.indeterminate = k > 0 && k < total; if (k === total && total) box.checked = true; else if (k === 0 && box.checked && !box.dataset.bulk) box.checked = false; selCount(); } });
    if (n.querySelector('.sel')?.checked) episodes.selectAll(it, true);   // the row was ticked before it was expanded: every episode follows   // the full episode list, every tracked season open; each season still folds
  }
  function rowUpdate(n, it) {
    n._item = it;
    const sig = JSON.stringify(it) + incog.sig(); if (n.dataset.sig === sig) return; n.dataset.sig = sig; n.dataset.stage = it.stage; n.dataset.kind = it.kind; n.dataset.id = it.id; n.dataset.title = (it.title || '').toLowerCase(); n.dataset.size = it.size || 0;
    // incognito: what the row DRAWS. it.title stays the real one, so the filter box and the palette still find it
    const key = it.kind + ':' + it.id, title = incog.mask(it.title, key), poster = incog.poster(it.poster);
    const live = it.live;
    const detail = live ? `${live.prio ? 'queue #' + live.prio + ' · ' : ''}${live.pct} % · ${fmt.speed(live.dlspeed)} · ${live.seeds} seeds · ETA ${live.eta || '—'} · ratio ${Number(live.ratio).toFixed(2)}` : it.detail;
    // duration: a movie's runtime; a show's per-episode runtime and the hours on disk (episodes on disk × runtime)
    const dur = !it.runtime ? '' : it.kind === 'tv' ? `${it.runtime} min/ep${it.have ? ' · ' + fmt.duration(it.have * it.runtime) + ' on disk' : ''}` : fmt.duration(it.runtime);
    const meta = [detail, it.size ? fmt.bytes(it.size) : '', dur, it.reason, it.who ? 'req: ' + incog.who(it.who) : ''].filter(Boolean);
    let sub = null;
    if (it.sub === 'ok') sub = pill('ok', 'subs', 'pill-sm'); else if (it.sub === 'missing') sub = pill('danger', 'no subs', 'pill-sm'); else if (it.sub && it.sub.missing) sub = pill('warn', `${it.sub.missing} subs missing`, 'pill-sm');
    const real = Number.isInteger(it.id);
    const sel = real ? h('label', { class: 'sel-wrap', title: it.kind === 'tv' ? 'Tick every episode of this show (then untick the ones you want to leave out)' : 'Select for a bulk action' },
      h('input', { type: 'checkbox', class: 'sel', 'aria-label': 'Select ' + title, onchange: async e => { if (it.kind !== 'tv') return; e.target.indeterminate = false; if (e.target.checked && !n.querySelector(':scope > .expand')) await toggleExpand(n, it); episodes.selectAll(it, e.target.checked); } })) : h('span', { class: 'sel-wrap' });
    const wasChecked = n.querySelector('.sel')?.checked; const exp = n.querySelector(':scope > .expand');
    append(clear(n), sel,
      pill(stageKind(it.stage), it.stage, 'badge'),
      poster ? h('img', { class: 'pos', src: poster + '?size=250', alt: '', loading: 'lazy', width: 32, height: 48 }) : h('span', { class: 'pos' }),
      h('div', { class: 'info', role: real ? 'button' : null, tabindex: real ? '0' : null, title: real ? (it.kind === 'tv' ? TIP.row_tv : TIP.row_movie) : null, 'aria-expanded': it.kind === 'tv' && real ? String(!!exp) : null },
        h('div', { class: 't' }, h('span', { class: 'title' }, title), incog.yr(it.year) ? h('span', { class: 'yr' }, ` (${it.year})`) : null, sub ? ' ' : null, sub),
        h('div', { class: 'm mono' }, meta.join(' · ')),
        live && live.why ? h('div', { class: 'why' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '▲'), live.why) : null,
        live ? h('div', { class: 'pbar', role: 'progressbar', 'aria-label': 'Download progress', 'aria-valuenow': live.pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('div', { style: { width: live.pct + '%' } })) : null),
      real ? h('button', { type: 'button', class: 'openx', 'aria-label': `Controls for ${title}`, title: TIP.openx, onclick: e => { e.stopPropagation(); openDrawer(it.kind, it.id, e.currentTarget); } }, '›') : h('span', { class: 'openx', 'aria-hidden': 'true' }, ''),
      exp);
    if (wasChecked) n.querySelector('.sel').checked = true;
  }
  function sortItems(arr, sec, key) {
    const so = state.sort[key] || state.sort[sec] || '';
    const c = arr.slice();
    if (sec === 'Downloading' && !so) c.sort((a, b) => (a.live?.prio || 999) - (b.live?.prio || 999));
    else if (so === 'size') c.sort((a, b) => (b.size || 0) - (a.size || 0));
    else if (so === 'sizea') c.sort((a, b) => (a.size || 0) - (b.size || 0));
    else c.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
    return c;
  }
  function render() {
    const d = state.data; if (!d) return;
    for (const el of sections.querySelectorAll(':scope > .skel')) el.remove();   // the page's loading rows: keyed patching never removed them, so they sat between Available and Reference forever
    const items = d.items.filter(i => (!state.q || (i.title || '').toLowerCase().includes(state.q)) && (!state.stage || i.stage === state.stage));
    const troubled = d.items.some(i => i.stage !== 'Available');
    // one group per stage — except Available, which is two: the movies and the shows (each its own fold, sort and count)
    const groups = STAGES.flatMap(st => st === 'Available'
      ? [{ st, key: 'Available-movie', label: 'Available movies', items: items.filter(i => i.stage === st && i.kind === 'movie') }, { st, key: 'Available-tv', label: 'Available shows', items: items.filter(i => i.stage === st && i.kind === 'tv') }]
      : [{ st, key: st, label: st, items: items.filter(i => i.stage === st) }])
      .map(g => ({ ...g, items: sortItems(g.items, g.st, g.key) })).filter(g => g.items.length);
    patchList(sections, groups, g => g.key, g => {
      const sec = h('div', { class: 'stage', dataset: { sec: g.key, stage: g.st } },
        h('h3', { class: 'stage-head' },
          h('button', { type: 'button', class: 'stage-toggle', 'aria-expanded': 'true', onclick: e => { const c = state.collapsed; if (g.st === 'Available' && !c.has(g.key) && e.currentTarget.getAttribute('aria-expanded') === 'false') c.add('!' + g.key); else c.has(g.key) ? c.delete(g.key) : c.add(g.key); try { localStorage.setItem('mc-collapsed', JSON.stringify([...c])); } catch {} render(); } },
            h('span', { class: 'caret', 'aria-hidden': 'true' }, '▾'), h('span', {}, g.label), h('span', { class: 'cnt mono' }, '')),
          h('select', { class: 'ssort', name: 'sort', 'aria-label': 'Sort this section', onchange: e => { state.sort[g.key] = e.target.value; render(); } },
            h('option', { value: '' }, g.st === 'Downloading' ? 'queue order' : 'A–Z'), h('option', { value: 'size' }, 'size ↓'), h('option', { value: 'sizea' }, 'size ↑'), h('option', { value: 'title' }, 'title A–Z'))),
        h('div', { class: 'grid' }));
      return sec;
    }, (sec, g) => {
      const collapsed = state.collapsed.has(g.key) || (g.st === 'Available' && troubled && !state.collapsed.has('!' + g.key) && !state.q && !state.stage);
      setText(sec.querySelector('.cnt'), g.items.length);
      const tg = sec.querySelector('.stage-toggle'); tg.setAttribute('aria-expanded', String(!collapsed)); tg.querySelector('.caret').textContent = collapsed ? '▸' : '▾';
      const grid = sec.querySelector('.grid'); grid.hidden = collapsed;
      const so = state.sort[g.key] || state.sort[g.st] || ''; const sel = sec.querySelector('.ssort'); if (sel.value !== so) sel.value = so;
      if (!collapsed) patchList(grid, g.items, i => i.kind + ':' + i.id, rowNode, rowUpdate);
    });
    for (const el of sections.querySelectorAll(':scope > .state')) el.remove();
    if (!groups.length) sections.append(d.items.length ? emptyState('No titles match that filter.') : emptyState('Library is empty — request something in Jellyseerr.'));
    applyCaps(sections, ctx.caps);
    selCount();
  }
  // The two Available groups are collapsed by default only while something else needs attention; a group's first click
  // marks '!<key>' (expanded for good) instead of adding the key to the collapsed set — see the onclick above.

  // ---- selection bar
  function selected() { return $$('.sel:checked', sections).map(c => c.closest('.item')._item); }
  function selCount() { const n = $$('.sel:checked', sections).length; setText($('#seln'), n); selbar.hidden = !n; ctx.sched.hold('selection', n > 0); const hold = $('#holdnote'); if (hold) setText(hold, n ? `paused while ${n} selected` : ''); }
  sections.addEventListener('change', e => { if (e.target.classList.contains('sel')) selCount(); });
  function clearSel() { $$('.sel:checked', sections).forEach(c => { c.checked = false; }); selCount(); }
  selbar.addEventListener('click', async e => {
    const b = e.target.closest('button[data-a]'); if (!b) return;
    let action = b.dataset.a; const items = selected(); if (!items.length) return;
    if (action === 'clear') return clearSel();
    if (action === 'ase' || action === 'quality') { if (items.length !== 1) return toast('Pick exactly one title for that', 'warn'); return action === 'ase' ? releasePicker(items[0]) : qualityPicker(items[0]); }
    let extra = {};
    if (action === 'monitor_on' || action === 'monitor_off') { extra = { monitored: action === 'monitor_on' }; action = 'monitor_set'; }
    if (action === 'purge') {
      const ok = await ctx.confirm({ title: `Purge ${items.length} title${items.length > 1 ? 's' : ''}`, text: `Deletes their files, torrents and Jellyseerr requests: ${items.map(i => incog.mask(i.title, i.kind + ':' + i.id)).slice(0, 5).join(', ')}${items.length > 5 ? '…' : ''}. Can't be undone.`, verb: 'Purge', danger: true });
      if (!ok) return;
    }
    let bad = 0;
    for (const it of items) { const j = await ctx.post(Object.assign({ action }, { kind: it.kind, id: it.id, title: it.title, tmdbId: it.tmdbId, tvdbId: it.tvdbId }, extra)); if (!j.ok) bad++; }
    toast(bad ? `Done — ${bad} of ${items.length} failed` : `${b.textContent} — ${items.length} title${items.length > 1 ? 's' : ''}`, bad ? 'warn' : 'ok');
    clearSel(); ctx.refresh('board', 'attention', 'live');
  });

  // ---- release picker / quality picker
  async function releasePicker(item, season) {
    const body = h('div', {}, skeleton(4));
    const dlg = modal(`Releases: ${incog.mask(item.title, item.kind + ':' + item.id)}${season ? ' — season ' + season : ''}`, body, { wide: true });
    try {
      const rs = await (await fetch(`/api/releases?kind=${item.kind}&id=${item.id}${season ? '&season=' + season : ''}`)).json();
      clear(body);
      if (!rs.length) body.append(emptyState('No releases found right now.'));
      for (const r of rs) body.append(h('div', { class: 'rel' }, h('div', { class: 'g' }, pill(r.rejected ? 'danger' : 'ok', r.rejected ? 'rejected' : 'ok', 'pill-sm'), ' ', h('span', {}, incog.mask(r.title)),
        h('div', { class: 'muted mono' }, `${r.quality} · ${(r.size / 1e9).toFixed(2)} GB · ${r.seeders} seeders${r.rejections?.length ? ' · ' + r.rejections[0] : ''}`)),
        h('button', { type: 'button', class: 'btn btn-sm btn-primary', dataset: { cap: 'can_grab' }, title: 'Send this exact release to qBittorrent', onclick: async () => { const j = await ctx.post({ action: 'grab', guid: r.guid, indexerId: r.indexerId, kind: item.kind, id: item.id, title: item.title }); toast(j.message, j.ok ? 'ok' : 'error'); dlg.close(); ctx.refresh('board', 'live'); } }, 'Grab')));
      applyCaps(body, ctx.caps);
    } catch (e) { append(clear(body), errorState('Search failed', String(e))); }
  }
  async function qualityPicker(item) {
    const ps = await (await fetch('/api/qualityprofiles?kind=' + item.kind)).json();
    const sel = h('select', { 'aria-label': 'Quality profile' }, ps.map(p => h('option', { value: p.id }, p.name)));
    const dlg = modal('Quality profile: ' + incog.mask(item.title, item.kind + ':' + item.id), h('div', { class: 'dlg-row' }, sel, h('button', { type: 'button', class: 'btn btn-primary', onclick: async () => { const j = await ctx.post({ action: 'set_quality', profileId: sel.value, kind: item.kind, id: item.id, title: item.title }); toast(j.message, j.ok ? 'ok' : 'error'); dlg.close(); ctx.refresh('board'); } }, 'Set')));
  }

  // ---- drawer
  let DW = null, dwEl = null, dwOpener = null;
  async function openDrawer(kind, id, opener) {
    DW = { kind, id }; dwOpener = opener || document.activeElement;
    if (dwEl) dwEl.close();
    dwEl = h('dialog', { class: 'drawer', 'aria-label': 'Title controls' });
    dwEl.addEventListener('close', () => { dwEl.remove(); dwEl = null; DW = null; const u = new URL(location.href); u.searchParams.delete('item'); history.replaceState(null, '', u); const back = (dwOpener && dwOpener.isConnected) ? dwOpener : document.querySelector('#library h2'); if (back) { back.tabIndex = back.tabIndex >= 0 ? back.tabIndex : -1; back.focus(); } });
    dwEl.addEventListener('click', e => { if (e.target === dwEl) dwEl.close(); });
    dwEl.append(h('div', { class: 'dwhead' }, h('div', { class: 'dt' }, 'Loading…'), h('button', { type: 'button', class: 'btn btn-icon dwx', 'aria-label': 'Close', onclick: () => dwEl.close() }, '×')), skeleton(4));
    document.body.append(dwEl); dwEl.showModal();
    const u = new URL(location.href); u.searchParams.set('item', kind + ':' + id); history.replaceState(null, '', u);
    await refreshDrawer();
  }
  async function refreshDrawer() {
    if (!DW || !dwEl) return;
    try { const D = await (await fetch(`/api/item?kind=${DW.kind}&id=${DW.id}`)).json(); if (D && D.id) Object.assign(DW, { tmdbId: D.tmdbId, tvdbId: D.tvdbId, title: D.title }); renderDrawer(D); }
    catch (e) { append(clear(dwEl), h('div', { class: 'dwhead' }, h('div', { class: 'dt' }, 'Couldn\'t load this title'), h('button', { type: 'button', class: 'btn btn-icon dwx', 'aria-label': 'Close', onclick: () => dwEl.close() }, '×')), errorState('The panel could not reach Radarr/Sonarr', String(e), refreshDrawer)); }
  }
  async function drawerAct(action, extra, opts = {}) {
    const j = await ctx.runAction({ confirm: opts.confirm, body: Object.assign({ action }, DW || {}, extra || {}) });
    if (j !== false) { await refreshDrawer(); ctx.refresh('board', 'live', 'attention'); }
  }
  const tip = k => TIP[k] || null;
  function renderDrawer(D) {
    if (!dwEl) return;
    clear(dwEl);
    if (!D || !D.id) { dwEl.append(h('div', { class: 'dwhead' }, h('div', { class: 'dt' }, 'Not found'), h('button', { type: 'button', class: 'btn btn-icon dwx', 'aria-label': 'Close', onclick: () => dwEl.close() }, '×'))); return; }
    const head = h('div', { class: 'dwhead' },
      incog.poster(D.poster) ? h('img', { class: 'pos pos-lg', src: `/img/poster/${D.kind}/${D.id}?size=250`, alt: '' }) : h('span', { class: 'pos pos-lg' }),
      h('div', {}, h('div', { class: 'dt', id: 'dw-title' }, incog.mask(D.title, D.kind + ':' + D.id) + (incog.yr(D.year) ? ` (${D.year})` : '')), h('div', { style: { marginTop: '6px' } }, pill(stageKind(D.stage), D.stage || '—', 'badge'), D.sizeOnDisk ? h('span', { class: 'mono muted' }, ' ' + fmt.bytes(D.sizeOnDisk) + ' on disk') : null)),
      h('button', { type: 'button', class: 'btn btn-icon dwx', 'aria-label': 'Close', onclick: () => dwEl.close() }, '×'));
    dwEl.setAttribute('aria-labelledby', 'dw-title');
    const sec = (title, ...kids) => h('section', { class: 'dwsec' }, h('h4', {}, title), ...kids);
    const selRow = (label, act, key, opts, cur, extraAttrs = {}) => h('div', { class: 'dwrow' }, h('label', { for: 'dw-' + key }, label), h('select', Object.assign({ id: 'dw-' + key, name: key, title: tip(act), onchange: e => { const o = {}; o[key] = e.target.value; drawerAct(act, o); } }, extraAttrs), opts.map(o => h('option', { value: o.value, selected: o.value == cur }, o.label))));
    const quality = sec('Quality & size',
      selRow('Profile', 'set_quality', 'profileId', (D.profiles || []).map(p => ({ value: p.id, label: p.name })), D.qualityProfileId),
      D.kind === 'movie' ? selRow('Min availability', 'set_min_availability', 'value', ['announced', 'inCinemas', 'released', 'preDB'].map(x => ({ value: x, label: x })), D.minimumAvailability)
                         : selRow('Series type', 'set_series_type', 'value', ['standard', 'anime', 'daily'].map(x => ({ value: x, label: x })), D.seriesType),
      (D.rootfolders || []).length ? h('div', { dataset: { cap: 'can_change_root' } }, selRow('Root', 'set_root_folder', 'path', D.rootfolders.map(r => ({ value: r.path, label: `${r.path} (${r.freeGB} GB free)` })), D.rootFolderPath)) : null);
    const mon = sec('Monitoring',
      h('div', { class: 'dwbtns' }, h('button', { type: 'button', class: 'btn', title: tip('monitor'), 'aria-pressed': String(!!D.monitored), onclick: () => drawerAct('monitor') }, D.monitored ? 'Monitored ✓ — click to unmonitor' : 'Not monitored — click to monitor'),
        D.kind === 'tv' ? h('button', { type: 'button', class: 'btn', title: tip('monitor_all'), onclick: () => drawerAct('monitor_all') }, 'Monitor all + search') : null));
    let searchSel = null;
    if (D.kind === 'tv') {
      const tree = h('div', { class: 'tree', id: 'dwseasons' }); mon.append(tree);
      episodes.seriesTree({ kind: 'tv', id: D.id, title: D.title }, tree);
      const seasons = D.seasons || [];
      const missing = seasons.filter(s => s.monitored && s.season > 0 && s.have < s.total); const def = (missing[0] || seasons.filter(s => s.monitored && s.season > 0).slice(-1)[0] || {}).season;
      if (seasons.length) searchSel = h('select', { id: 'dwseason', title: 'Which season the interactive search looks for', 'aria-label': 'Season to search' }, seasons.filter(s => s.season > 0).map(s => h('option', { value: s.season, selected: s.season === def }, `S${s.season}${s.have < s.total ? ' (missing)' : ''}`)));
    }
    const subStatus = D.kind === 'movie' ? (D.sub === true ? pill('ok', 'subs') : D.sub === false ? pill('danger', 'subs missing') : h('span', { class: 'muted' }, 'no file yet'))
      : (D.sub_missing === 0 ? pill('ok', 'subs') : D.sub_missing > 0 ? pill('warn', `${D.sub_missing} subs missing`) : h('span', { class: 'muted' }, '—'));
    const submanual = h('div', { id: 'submanual' });
    const subs = sec('Subtitles', h('div', { class: 'dwrow' }, subStatus), h('div', { class: 'dwbtns' },
      h('button', { type: 'button', class: 'btn', title: tip('fetchsubs'), onclick: () => drawerAct('fetch_subs') }, 'Fetch subs'),
      D.kind === 'movie' ? h('button', { type: 'button', class: 'btn', title: tip('subsearch'), onclick: () => subSearch(ctx, 'movie', D.id, null, submanual) }, 'Manual search…') : h('span', { class: 'muted' }, 'manual search: per episode above'),
      ctx.role === 'admin' ? h('a', { class: 'btn', href: '/settings#subtitles', title: 'Open subtitle preferences (languages, providers, scoring)' }, 'Subs preferences') : null), submanual);
    const lib = sec('Library', h('div', { class: 'dwbtns' }, searchSel,
      h('button', { type: 'button', class: 'btn', title: tip('search'), onclick: () => releasePicker(DW, searchSel ? searchSel.value : null) }, 'Search…'),
      h('button', { type: 'button', class: 'btn', title: tip('retry'), onclick: () => drawerAct('retry') }, 'Auto-search'),
      h('button', { type: 'button', class: 'btn', title: tip('refresh'), onclick: () => drawerAct('refresh') }, 'Refresh'),
      h('button', { type: 'button', class: 'btn', title: tip('blocklist_retry'), dataset: { cap: 'can_remove' }, onclick: () => drawerAct('blocklist_retry', {}, { confirm: true }) }, 'Blocklist & retry'),
      h('button', { type: 'button', class: 'btn btn-danger-ghost', title: tip('purge'), dataset: { cap: 'can_purge' }, onclick: () => drawerAct('purge', {}, { confirm: true }) }, 'Purge')));
    const tors = D.torrents || [];
    const torSec = sec(`Torrents (${tors.length})`, ...(tors.length ? tors.map(t => h('div', { class: 'tor tor-inline' },
      h('div', { class: 'tn' }, incog.mask(t.name)), h('div', { class: 'tm mono' }, `${t.state} · ${t.progress} % · queue ${t.priority > 0 ? '#' + t.priority : '—'} · ↓${fmt.speed(t.dlspeed)} ↑${fmt.speed(t.upspeed)} · ${t.num_seeds} seeds · ratio ${t.ratio} · ETA ${t.eta}`),
      t.why ? h('div', { class: 'why' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '▲'), t.why) : null,
      h('div', { class: 'dwbtns' }, ...[['t_top', 'Top'], ['t_bottom', 'Bottom'], ['t_pause', 'Pause'], ['t_resume', 'Resume'], ['t_recheck', 'Recheck'], ['t_reannounce', 'Reannounce']].map(([a, l]) => h('button', { type: 'button', class: 'btn btn-sm', title: tip(a), onclick: () => tAct(a, t.hash) }, l)),
        h('button', { type: 'button', class: 'btn btn-sm', title: tip('t_forcestart'), dataset: { cap: 'can_control_client' }, onclick: () => tAct('t_forcestart', t.hash, { value: t.force_start ? 0 : 1 }) }, t.force_start ? 'Unforce' : 'Force start'),
        h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', title: tip('t_delete'), dataset: { cap: 'can_remove' }, onclick: () => tAct('t_delete', t.hash, { name: t.name }, true, { name: incog.mask(t.name) }) }, 'Remove'),
        h('button', { type: 'button', class: 'btn btn-sm btn-danger-ghost', title: tip('t_purge'), dataset: { cap: 'can_purge' }, onclick: () => tAct('t_purge', t.hash, { name: t.name }, true, { name: incog.mask(t.name) }) }, 'Purge')),
      h('div', { class: 'dwrow', dataset: { cap: 'can_control_client' } }, h('label', { title: 'Per-torrent download speed cap' }, '↓ cap'), h('input', { class: 'spin', type: 'number', min: 0, step: 0.5, value: t.dl_limit, inputmode: 'decimal', 'aria-label': 'Download cap, MB/s', onchange: e => tAct('t_dllimit', t.hash, { limit: e.target.value }) }),
        h('label', { title: 'Per-torrent upload speed cap' }, '↑ cap'), h('input', { class: 'spin', type: 'number', min: 0, step: 0.5, value: t.up_limit, inputmode: 'decimal', 'aria-label': 'Upload cap, MB/s', onchange: e => tAct('t_uplimit', t.hash, { limit: e.target.value }) }), h('span', { class: 'muted' }, 'MB/s, 0 = ∞')))) : [h('div', { class: 'muted' }, 'No active torrents for this title.')]));
    dwEl.append(head, quality, mon, subs, lib, torSec);
    applyCaps(dwEl, ctx.caps);
    if (!dwEl.contains(document.activeElement) || document.activeElement === dwEl) dwEl.querySelector('.dwx').focus();   // re-render must not drop focus to <body>
  }
  async function tAct(a, hash, extra = {}, confirm = false, shown = null) { const j = await ctx.runAction({ confirm, body: Object.assign({ action: a, hash }, extra), shown }); if (j !== false) { setTimeout(refreshDrawer, 500); ctx.refresh('live', 'board'); } }

  return {
    setData(d) { state.data = d; render(); },
    /** Incognito flipped: everything already on screen is drawn again, the open drawer and season trees too. */
    redraw() { render(); episodes.redrawAll(); if (DW) refreshDrawer(); },
    setStage(st) { state.stage = st === state.stage ? '' : st; stageSel.value = state.stage; render(); syncHash(); },
    setSort(st, so) { state.sort[st] = so; if (st === 'Available') { state.sort['Available-movie'] = so; state.sort['Available-tv'] = so; } render(); },
    stage() { return state.stage; },
    openDrawer, items() { return state.data ? state.data.items : []; },
    setError(err) { if (!state.data) { append(clear(sections), errorState('Library unavailable', err, () => ctx.refresh('board'))); } },
  };
}
