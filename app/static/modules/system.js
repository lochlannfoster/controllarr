// The resource monitor under the Dash: one compact line — every figure with a coloured word that says what it means
// (fine / busy / maxed …) and a headline pill for the whole box — that expands to every container with its state,
// CPU, memory, what it is doing right now and its last log line. The open/closed state is remembered per browser.
import { h, clear, append, patchList, fmt } from './dom.js';
import { pill, errorState } from './ui.js';

const WORDS = {   // [ok, warn, danger] — the word the figure earns at each level
  cpu: ['fine', 'busy', 'maxed out'], io: ['fine', 'waiting on disk', 'disk saturated'], load: ['fine', 'high', 'overloaded'],
  ram: ['fine', 'tight', 'full'], swap: ['parked', 'swapping', 'thrashing'], disk: ['fine', 'filling up', 'nearly full'], temp: ['cool', 'warm', 'hot'],
};
// swap: what is IN swap does not matter (the kernel parks idle pages there and leaves them); pages moving in and out do.
// Below 20 pages/s (~80 kB/s) nothing is happening; past 500 pages/s (~2 MB/s) the box is short of RAM and crawling.
const SWAP_IO = [20, 500];
export function createSystem(host, ctx) {
  let open = false; try { open = localStorage.getItem('mc-sys') === 'open'; } catch {}
  const caret = h('span', { class: 'caret', 'aria-hidden': 'true' }, open ? '▾' : '▸');
  const toggle = h('button', { type: 'button', class: 'sys-toggle', 'aria-expanded': String(open), 'aria-controls': 'sys-detail', title: 'Show every container with its CPU, memory, current task and last log line' }, caret, h('span', {}, 'System'));
  const headline = h('span', { class: 'pill pill-muted sys-headline' }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, '○'), h('span', {}, 'reading…'));
  const line = h('div', { class: 'sys-line', tabindex: '0', role: 'group', 'aria-label': 'Host figures' });   // it scrolls sideways on a phone, so it must be reachable by keyboard
  const detail = h('div', { id: 'sys-detail', class: 'sys-detail', hidden: !open });
  const tbody = h('tbody');
  detail.append(h('table', { class: 'sys-table' }, h('thead', {}, h('tr', {}, ['Container', 'State', 'CPU', 'Memory', 'Doing now', 'Last log line'].map(x => h('th', { scope: 'col' }, x)))), tbody));
  host.append(h('div', { class: 'sys-head' }, toggle, headline, line), detail);
  toggle.addEventListener('click', () => { open = !open; detail.hidden = !open; toggle.setAttribute('aria-expanded', String(open)); caret.textContent = open ? '▾' : '▸'; try { localStorage.setItem('mc-sys', open ? 'open' : 'closed'); } catch {} if (open) ctx.refresh('system'); });
  const level = (p, warn, danger) => p == null ? null : p >= danger ? 2 : p >= warn ? 1 : 0;
  const KIND = ['ok', 'warn', 'danger'];
  const chip = (key, label, val, lvl, title) => h('span', { class: 'sys-chip mono' + (lvl ? ' sys-' + KIND[lvl] : ''), title }, h('span', { class: 'muted' }, label), ' ', h('b', {}, val), lvl == null ? null : h('span', { class: 'sys-word sys-word-' + KIND[lvl] }, WORDS[key][lvl]));
  const pct = v => v == null ? '—' : v + ' %';
  function render(d) {
    const H = d.host || {}; const worst = [];
    // note: what the headline says beside the word, so "disk filling up" also says how much is left
    const add = (key, label, val, lvl, title, note = '') => { if (lvl >= 1) worst.push([lvl, `${label} ${WORDS[key][lvl]}${note ? ' (' + note + ')' : ''}`]); return chip(key, label, val, lvl, title); };
    append(clear(line),
      add('cpu', 'CPU', pct(H.cpu_pct), level(H.cpu_pct, 70, 90), `Host CPU busy since the previous poll (${H.cpus || '?'} cores)`),
      add('io', 'IO wait', pct(H.iowait_pct), level(H.iowait_pct, 20, 50), 'Time the CPU spent waiting for the disk — high means the disk is saturated and everything crawls'),
      add('load', 'load', H.load ? H.load.join(' · ') : '—', level(H.load && H.cpus ? H.load[0] / H.cpus * 100 : null, 100, 200), 'Load average over 1 · 5 · 15 minutes, against the core count'),
      add('ram', 'RAM', H.mem_total ? `${fmt.bytes(H.mem_used)} / ${fmt.bytes(H.mem_total)}` : '—', level(H.mem_total ? 100 * H.mem_used / H.mem_total : null, 85, 95), 'Memory in use (cache excluded) of the total'),
      H.swap_total ? add('swap', 'swap', fmt.bytes(H.swap_used) + (H.swap_io ? ` · ${H.swap_io} pg/s` : ''), H.swap_io == null ? (H.swap_used ? 0 : null) : level(H.swap_io, SWAP_IO[0], SWAP_IO[1]),
        'What sits in swap is harmless — the kernel parks idle pages there even with RAM to spare. The word turns amber only while pages are actually moving in and out (swapping), red when that is constant (thrashing = short of RAM)') : null,
      H.disk && H.disk.disk_pct != null ? add('disk', 'disk', `${H.disk.disk_pct} % · ${H.disk.disk_free} GB free`, level(H.disk.disk_pct, 80, 90), 'The media volume: filling up from 80 %; a single film is 2–8 GB, so a few GB free is one or two more downloads', `${H.disk.disk_free} GB free`) : null,
      H.temp_c != null ? add('temp', 'temp', H.temp_c + ' °C', level(H.temp_c, 75, 90), 'Hottest thermal zone the panel can read') : null,
      H.uptime_s != null ? chip('cpu', 'up', fmt.age(H.uptime_s), null, 'Host uptime') : null);
    const rows = d.containers || []; const down = rows.filter(r => r.state !== 'running' || r.health === 'unhealthy');
    if (down.length) worst.push([2, `${down.length} container${down.length > 1 ? 's' : ''} not running`]);
    const top = worst.sort((a, b) => b[0] - a[0]);
    headline.className = 'pill pill-' + (top.length ? KIND[top[0][0]] : 'ok') + ' sys-headline';
    headline.firstChild.textContent = top.length ? (top[0][0] === 2 ? '✕' : '▲') : '●';
    headline.lastChild.textContent = top.length ? top.map(x => x[1]).slice(0, 3).join(' · ') : 'all fine';
    patchList(tbody, rows, r => r.name, () => h('tr', {}), (tr, r) => {
      const sig = [r.state, r.health, r.cpu_pct, r.mem_mb, r.task, r.log].join('|'); if (tr.dataset.sig === sig) return; tr.dataset.sig = sig;
      const st = r.state === 'running' ? (r.health === 'unhealthy' ? pill('warn', 'unhealthy') : pill('ok', 'running')) : pill('danger', r.state);
      append(clear(tr), h('th', { scope: 'row', class: 'sys-name' }, r.name), h('td', {}, st),
        h('td', { class: 'mono' }, r.cpu_pct == null ? '—' : r.cpu_pct.toFixed(1) + ' %'), h('td', { class: 'mono' }, r.mem_mb == null ? '—' : r.mem_mb + ' MB'),
        h('td', { class: 'sys-task' }, r.task == null ? h('span', { class: 'muted' }, '—') : r.task),
        h('td', { class: 'sys-log mono', title: r.log || null }, r.log || ''));
    });
    for (const el of detail.querySelectorAll('.state')) el.remove();
    const S = d.sources || {};
    if (S.docker && !S.docker.ok) detail.append(errorState('Docker did not answer', S.docker.err || ''));
  }
  return { render, setError(err) { append(clear(line), h('span', { class: 'age age-stale' }, 'not answering — ' + err)); } };
}
