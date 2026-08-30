// A show below the title: the episode list (S01E01, S01E02, … under thin season headers) with a tick box per episode,
// a toolbar that appears above the list when anything is ticked — search, subs, track, torrent controls, delete,
// purge for exactly the ticked episodes — and the per-episode dialog behind ›. Used inline under a Library row and
// in the drawer's Monitoring section: one implementation, two hosts.
import { h, clear, append, fmt } from './dom.js';
import { pill, modal, skeleton, emptyState, errorState, applyCaps, toast } from './ui.js';
import * as incog from './incognito.js';

const TIP = {
  season_tick: 'Tick every episode of this season', season_monitor: 'Track this season (Sonarr searches for it) or stop tracking it',
  ep_tick: 'Tick this episode for the toolbar above', epopen: 'Open this episode\'s own controls',
  search: 'Search for the ticked episodes', subs: 'Find subtitles for the ticked episodes (one: pick from the candidates; several: Bazarr searches all providers)',
  track: 'Start tracking the ticked episodes', untrack: 'Stop tracking the ticked episodes (nothing is deleted)',
  t_top: 'Move their torrents to the front of the queue', t_bottom: 'Move their torrents to the back of the queue', t_pause: 'Stop their torrents (they stay listed)',
  t_resume: 'Start their torrents again', t_forcestart: 'Download their torrents regardless of the queue limits', t_delete: 'Remove their torrents from qBittorrent but KEEP the downloaded files',
  delete_files: 'Delete the ticked episodes\' files from disk (torrents are not touched)', purge: 'Delete the ticked episodes\' files and torrents and stop tracking them. Cannot be undone.',
  clear: 'Untick everything', showall: 'Also list seasons that are not being tracked (unmonitored / specials)',
  episode_monitor: 'Tick to track this episode; untick to ignore it', episode_search: 'Search for just this episode', ep_subs: 'List subtitle candidates for this episode and pick one',
  episode_delete_file: "Delete this episode's file from disk (the torrent is not touched)", episode_purge: 'Delete this episode\'s file and torrent and stop tracking it. Cannot be undone.',
  t_recheck: 'Re-verify the downloaded pieces on disk', t_purge: 'Remove the torrent AND delete its files; the episodes it carried stop being tracked',
};
const tip = k => TIP[k] || null;
const epCode = e => `S${String(e.season).padStart(2, '0')}E${String(e.ep || 0).padStart(2, '0')}`;
const showName = item => incog.mask(item.title, 'tv:' + item.id);            // the show, as this module prints it
const epName = (item, e) => incog.mask(e.title, `ep:${item.id}:${e.id}`);    // one pseudonym per episode, stable
const torState = t => !t ? null : /stalled|metaDL/.test(t.state) && t.progress < 100 ? pill('danger', 'stalled', 'pill-sm') : t.progress >= 100 ? pill('ok', 'downloaded', 'pill-sm') : pill('flow', `${t.progress ?? 0} %`, 'pill-sm');
// the subtitle word for an episode on disk: Bazarr's verdict, or nothing while Bazarr has not seen the file
const subState = e => !e.hasFile || e.sub == null ? null : e.sub ? pill('ok', 'subs', 'pill-sm') : pill('warn', 'no subs', 'pill-sm');

export function createEpisodes(ctx, { releasePicker }) {
  const trees = new Map();   // seriesId -> { data, open: Set(season), showAll, sel: Set(episodeId), hosts: Set(host), onSelection }

  async function load(sid) {
    const r = await fetch('/api/series-tree?seriesId=' + sid, { credentials: 'same-origin' });
    if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); }
    const d = await r.json(); if (!d || !d.id) throw new Error('Sonarr did not answer');
    return d;
  }
  const act = async (item, body, confirm = false) => { const j = await ctx.runAction({ confirm, body: Object.assign({ kind: 'tv', id: item.id, title: item.title }, body) }); if (j !== false) ctx.refresh('board', 'live', 'attention'); return j; };
  const tAct = (a, hashes, item, extra = {}, confirm = false) => ctx.runAction({ confirm, body: Object.assign({ action: a, hash: hashes, name: item.title }, extra), shown: { name: showName(item) } });
  function state(item) { let st = trees.get(item.id); if (!st) { st = { data: null, open: new Set(), showAll: false, sel: new Set(), hosts: new Set(), item, onSelection: null }; trees.set(item.id, st); } return st; }
  function notify(st) { const total = (st.data?.episodes || []).length; st.onSelection && st.onSelection(st.sel.size, total); }

  /** Render (or re-render) the episode list for `item` into `host`. openAll: every tracked season open at once. */
  async function seriesTree(item, host, { openAll = false, onSelection } = {}) {
    const st = state(item); st.hosts.add(host); if (onSelection) st.onSelection = onSelection;
    append(clear(host), skeleton(2));
    const reload = async () => {
      try { st.data = await load(item.id); if (openAll) { for (const s of st.data.seasons || []) if (s.monitored) st.open.add(s.season); }
        for (const id of [...st.sel]) if (!(st.data.episodes || []).some(e => e.id === id)) st.sel.delete(id);   // an episode that vanished is no longer ticked
        draw(); notify(st); }
      catch (e) { append(clear(host), errorState('Could not load the episodes', String(e.message || e), reload)); }
    };
    function draw() {
      const D = st.data; clear(host);
      const seasons = D.seasons || []; const vis = seasons.filter(s => s.monitored || st.showAll); const hidden = seasons.length - vis.length;
      if (!seasons.length) { host.append(emptyState('No seasons known yet.')); return; }
      host.append(toolbar(st, reload));
      for (const s of vis) {
        const eps = (D.episodes || []).filter(e => e.season === s.season); const open = st.open.has(s.season); const name = s.season === 0 ? 'Specials' : 'Season ' + s.season;
        const ticked = eps.filter(e => st.sel.has(e.id)).length; const dl = eps.filter(e => e.torrent).length;
        const list = h('div', { class: 'ep-list', hidden: !open });
        const caret = h('button', { type: 'button', class: 'btn btn-icon scaret', 'aria-expanded': String(open), title: `Show or hide the episodes of ${name}`, onclick: () => { st.open.has(s.season) ? st.open.delete(s.season) : st.open.add(s.season); draw(); } }, open ? '▾' : '▸');
        const box = h('input', { type: 'checkbox', class: 'ep-sel season-sel', 'aria-label': `Tick every episode of ${name}`, title: tip('season_tick'), checked: eps.length > 0 && ticked === eps.length, onchange: e => { for (const ep of eps) e.target.checked ? st.sel.add(ep.id) : st.sel.delete(ep.id); if (e.target.checked) st.open.add(s.season); draw(); notify(st); } });
        box.indeterminate = ticked > 0 && ticked < eps.length;
        host.append(h('div', { class: 'season' },
          h('div', { class: 'season-row' }, caret, box, h('button', { type: 'button', class: 'season-name link-quiet', title: `Show or hide the episodes of ${name}`, onclick: () => { st.open.has(s.season) ? st.open.delete(s.season) : st.open.add(s.season); draw(); } }, h('b', {}, name)),
            h('span', { class: 'mono muted' }, `${s.have}/${s.total} eps${s.size ? ' · ' + fmt.bytes(s.size) : ''}${dl ? ` · ${dl} downloading` : ''}${s.monitored ? '' : ' · not tracked'}`),
            h('label', { class: 'season-track', title: tip('season_monitor') }, h('input', { type: 'checkbox', checked: !!s.monitored, 'aria-label': `Track ${name}`, onchange: async e => { const j = await act(item, { action: 'set_season_monitor', season: s.season, monitored: e.target.checked }); if (j !== false) reload(); } }), ' tracked')),
          list));
        if (open) {
          if (!eps.length) list.append(h('div', { class: 'muted' }, 'No episodes.'));
          for (const e of eps) list.append(h('div', { class: 'ep' + (e.monitored ? '' : ' ep-off') + (st.sel.has(e.id) ? ' ep-on' : '') },
            h('input', { type: 'checkbox', class: 'ep-sel', checked: st.sel.has(e.id), 'aria-label': `Tick ${epCode(e)}`, title: tip('ep_tick'), onchange: ev => { ev.target.checked ? st.sel.add(e.id) : st.sel.delete(e.id); draw(); notify(st); } }),
            h('span', { class: 'mono ep-code' }, epCode(e)), h('span', { class: 'ep-title ell' }, e.title ? epName(item, e) : ''),
            e.size ? h('span', { class: 'mono muted ep-size', title: 'Size of the file on disk' }, fmt.bytes(e.size)) : null,
            e.torrent ? torState(e.torrent) : e.hasFile ? pill('ok', 'file', 'pill-sm') : pill('muted', e.monitored ? 'missing' : 'ignored', 'pill-sm'),
            subState(e),
            h('button', { type: 'button', class: 'ep-x', 'aria-label': `Controls for ${epCode(e)}`, title: tip('epopen'), onclick: () => episodeDialog(item, e, reload) }, '›')));
        }
      }
      if (hidden || st.showAll) host.append(h('div', { class: 'dwrow' }, h('button', { type: 'button', class: 'btn btn-sm', title: tip('showall'), onclick: () => { st.showAll = !st.showAll; draw(); } }, st.showAll ? 'Hide untracked seasons' : `Show all seasons (${hidden} hidden)`)));
      applyCaps(host, ctx.caps);
    }
    st.draw = draw; await reload();
    return reload;
  }

  /** The toolbar above the list: acts on the ticked episodes (and the torrents behind them). */
  function toolbar(st, reload) {
    const eps = (st.data.episodes || []).filter(e => st.sel.has(e.id)); const n = eps.length; const item = st.item;
    const bar = h('div', { class: 'ep-bar', role: 'toolbar', 'aria-label': 'Ticked episodes', hidden: !n });
    if (!n) return bar;
    const ids = eps.map(e => e.id); const hashes = [...new Set(eps.map(e => e.torrent?.hash).filter(Boolean))].join('|'); const files = eps.filter(e => e.episodeFileId).map(e => e.episodeFileId);
    const btn = (label, key, onclick, cls = 'btn btn-sm', cap = '', disabled = false) => h('button', { type: 'button', class: cls, title: tip(key), dataset: { cap }, disabled, onclick }, label);
    const after = async (j, keep = true) => { if (j === false) return; if (!keep) st.sel.clear(); await reload(); };
    bar.append(h('span', { class: 'ep-bar-n' }, h('b', { class: 'mono' }, n), n === 1 ? ' episode' : ' episodes'),
      btn('Search', 'search', async () => after(await act(item, { action: 'episode_search', episodeIds: ids }))),
      btn('Subs', 'subs', async () => { if (n === 1) { const box = h('div', { class: 'submanual' }); modal(`Subtitles for ${showName(item)} ${epCode(eps[0])}`, box, { wide: true }); subSearch(ctx, 'tv', item.id, eps[0].id, box); } else after(await act(item, { action: 'fetch_subs' })); }),
      btn('Track', 'track', async () => after(await act(item, { action: 'episode_monitor', episodeIds: ids, monitored: true }))),
      btn('Untrack', 'untrack', async () => after(await act(item, { action: 'episode_monitor', episodeIds: ids, monitored: false }))),
      h('span', { class: 'ep-bar-gap' }),
      btn('Top', 't_top', async () => after(await tAct('t_top', hashes, item)), 'btn btn-sm', '', !hashes),
      btn('Bottom', 't_bottom', async () => after(await tAct('t_bottom', hashes, item)), 'btn btn-sm', '', !hashes),
      btn('Pause', 't_pause', async () => after(await tAct('t_pause', hashes, item)), 'btn btn-sm', '', !hashes),
      btn('Resume', 't_resume', async () => after(await tAct('t_resume', hashes, item)), 'btn btn-sm', '', !hashes),
      btn('Force', 't_forcestart', async () => after(await tAct('t_forcestart', hashes, item, { value: 1 })), 'btn btn-sm', 'can_control_client', !hashes),
      btn('Remove', 't_delete', async () => after(await tAct('t_delete', hashes, item, {}, true), false), 'btn btn-sm btn-danger-ghost', 'can_remove', !hashes),
      btn('Delete files', 'delete_files', async () => after(await act(item, { action: 'episode_delete_files', episodeFileIds: files.join(',') }, true), false), 'btn btn-sm btn-danger-ghost', 'can_delete_files', !files.length),
      btn('Purge', 'purge', async () => after(await act(item, { action: 'episode_purge', episodeIds: ids.join(',') }, true), false), 'btn btn-sm btn-danger-ghost', 'can_purge'),
      btn('Clear', 'clear', () => { st.sel.clear(); st.draw(); notify(st); }));
    return bar;
  }

  /** The per-episode dialog behind ›: status, tracking, search, subtitles, file, purge, and the torrent behind it. */
  function episodeDialog(item, ep, onChange) {
    const body = h('div', { class: 'ep-dlg' }); let cur = ep;
    const dlg = modal(`${showName(item)} — ${epCode(ep)}${ep.title ? ' ' + epName(item, ep) : ''}`, body);
    const subhost = h('div', { class: 'submanual' });
    async function refresh() { try { const d = await load(item.id); cur = (d.episodes || []).find(e => e.id === ep.id) || cur; } catch {} draw(); onChange && onChange(); }
    const run = async (b, confirm) => { const j = await act(item, b, confirm); if (j !== false) refresh(); };
    const tb = (a, l, extra, confirm, cls = 'btn btn-sm', cap = '') => h('button', { type: 'button', class: cls, title: tip(a), dataset: { cap }, onclick: async () => { const j = await tAct(a, cur.torrent.hash, item, extra || {}, confirm); if (j !== false) { ctx.refresh('live', 'board'); refresh(); } } }, l);
    function draw() {
      const t = cur.torrent; clear(body);
      body.append(h('div', { class: 'dwrow' }, cur.hasFile ? pill('ok', 'file on disk') : pill('muted', 'no file'), cur.size ? h('span', { class: 'mono muted' }, fmt.bytes(cur.size)) : null, subState(cur), cur.monitored ? pill('flow', 'tracked') : pill('muted', 'not tracked'), t ? torState(t) : null, cur.airDate ? h('span', { class: 'mono muted' }, 'aired ' + cur.airDate) : null),
        h('section', { class: 'dwsec' }, h('h4', {}, 'Episode'), h('div', { class: 'dwbtns' },
          h('label', { class: 'btn', title: tip('episode_monitor') }, h('input', { type: 'checkbox', checked: !!cur.monitored, onchange: e => run({ action: 'episode_monitor', episodeId: cur.id, monitored: e.target.checked }) }), ' Track'),
          h('button', { type: 'button', class: 'btn', title: tip('episode_search'), onclick: () => run({ action: 'episode_search', episodeId: cur.id }) }, 'Search'),
          h('button', { type: 'button', class: 'btn', title: tip('ep_subs'), onclick: () => subSearch(ctx, 'tv', item.id, cur.id, subhost) }, 'Subtitles…'),
          cur.hasFile ? h('button', { type: 'button', class: 'btn btn-danger-ghost', title: tip('episode_delete_file'), dataset: { cap: 'can_delete_files' }, onclick: () => run({ action: 'episode_delete_file', episodeId: cur.id, episodeFileId: cur.episodeFileId }, true) }, 'Delete file') : null,
          h('button', { type: 'button', class: 'btn btn-danger-ghost', title: tip('episode_purge'), dataset: { cap: 'can_purge' }, onclick: async () => { const j = await act(item, { action: 'episode_purge', episodeIds: String(cur.id) }, true); if (j !== false) { dlg.close(); onChange && onChange(); } } }, 'Purge')), subhost),
        t ? h('section', { class: 'dwsec' }, h('h4', {}, 'Torrent'), h('div', { class: 'tm mono' }, `${t.state || '—'} · ${t.progress ?? '—'} % · ↓${fmt.speed(t.dlspeed)} · ETA ${t.eta || '—'}`),
          t.why ? h('div', { class: 'why' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '▲'), t.why) : null,
          h('div', { class: 'dwbtns tor-btns' }, tb('t_pause', 'Pause'), tb('t_resume', 'Resume'), tb('t_forcestart', 'Force', { value: 1 }, false, 'btn btn-sm', 'can_control_client'), tb('t_top', 'Top'), tb('t_bottom', 'Bottom'), tb('t_recheck', 'Recheck'),
            tb('t_delete', 'Remove', {}, true, 'btn btn-sm btn-danger-ghost', 'can_remove'), tb('t_purge', 'Purge', {}, true, 'btn btn-sm btn-danger-ghost', 'can_purge'))) : null);
      applyCaps(body, ctx.caps);
    }
    draw();
    return dlg;
  }

  return {
    seriesTree, episodeDialog,
    /** Incognito flipped: redraw every season tree that is open, wherever it is hosted. */
    redrawAll() { for (const st of trees.values()) st.draw && st.draw(); },
    /** Tick or untick every episode of a show (the series row's own checkbox). */
    selectAll(item, on) { const st = state(item); if (!st.data) return; for (const e of st.data.episodes || []) on ? st.sel.add(e.id) : st.sel.delete(e.id); if (on) for (const s of st.data.seasons || []) if (s.monitored) st.open.add(s.season); st.draw && st.draw(); notify(st); },
    selection(item) { const st = trees.get(item.id); return st ? st.sel.size : 0; },
  };
}

/** Bazarr's manual subtitle search into `host` (movies: kind 'movie', no episodeId). Shared with the drawer. */
export async function subSearch(ctx, kind, id, episodeId, host) {
  append(clear(host), skeleton(2));
  let j;
  try { j = await (await fetch(`/api/sub-search?kind=${kind}&id=${id}${episodeId ? '&episodeId=' + episodeId : ''}`, { credentials: 'same-origin' })).json(); }
  catch (e) { return append(clear(host), errorState('Subtitle search failed', String(e))); }
  clear(host);
  if (!j.available) return host.append(errorState('Bazarr manual search unavailable', 'Bazarr did not answer'));
  if (!j.results.length) return host.append(emptyState('No subtitles found right now.'));
  for (const r of j.results) host.append(h('div', { class: 'rel' }, h('div', { class: 'g' }, `${r.language || '?'} · ${r.provider || ''} · score ${r.score || 0}`, h('div', { class: 'muted mono' }, r.release ? incog.mask(String(r.release)) : '')),
    h('button', { type: 'button', class: 'btn btn-sm', title: 'Download this subtitle through Bazarr', onclick: async () => { const res = await ctx.post({ action: 'download_sub', kind, id, episodeId, language: r.language, subtitle: r.subtitle, provider: r.provider, hi: String(!!r.hi), forced: String(!!r.forced), original_format: String(!!r.original_format) }); toast(res.message || (res.ok ? 'Done' : 'Failed'), res.ok ? 'ok' : 'error'); clear(host); } }, 'Get')));
}
