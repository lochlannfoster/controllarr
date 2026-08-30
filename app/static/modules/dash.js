// The Dash — one compact bar: the seven pipeline stations (a coloured dot, the short name, the count) on the left,
// the box's name and address in the centre, the VPN state and the data age on the right. A station's dot is its
// status; click one to filter the library. Order = board_gen.STAGES.
import { h, setText } from './dom.js';

const ORDER = ['Unavailable', 'Searching', 'Downloading', 'Importing', 'Partial', 'Waiting', 'Available'];
const SHORT = { Unavailable: 'UNAVAIL', Searching: 'SEARCH', Downloading: 'DOWNLOAD', Importing: 'IMPORT', Partial: 'PARTIAL', Waiting: 'WAITING', Available: 'AVAILABLE' };

export function createDash(host, { onStation, hostname, ip }) {
  const stations = new Map();
  const healthGlyph = h('span', { class: 'glyph', 'aria-hidden': 'true' }, '●'), healthWord = h('span', {}, 'checking');
  const health = h('span', { class: 'pill pill-muted dash-health', title: 'The stack as a whole: every expected container running and, when routed, the VPN up' }, healthGlyph, healthWord);
  const track = h('div', { class: 'dash-track', role: 'group', 'aria-label': 'Pipeline' }, health);
  for (const st of ORDER) {
    const btn = h('button', { type: 'button', class: 'station', dataset: { stage: st }, 'aria-pressed': 'false', title: `Show only ${st} titles`, onclick: () => onStation(st) },
      h('span', { class: 'dot', 'aria-hidden': 'true' }), h('span', { class: 'st-name' }, SHORT[st]), h('span', { class: 'st-count mono' }, '–'), h('span', { class: 'st-note' }, ''), h('span', { class: 'sr-only' }, ` — ${st} titles`));
    track.append(btn); stations.set(st, btn);
  }
  const centre = h('div', { class: 'dash-host mono', title: 'The server this panel runs on' }, hostname ? h('b', {}, hostname) : null, hostname && ip ? ' · ' : null, ip || null);
  const vpn = h('div', { class: 'dash-vpn' }), age = h('span', { class: 'dash-age' }, '');
  host.append(track, centre, h('div', { class: 'dash-side' }, vpn, age));

  function update({ summary, stalled = 0, vpnState, containersDown = 0, flowing = false, ageS, activeFilter }) {
    for (const [st, btn] of stations) {
      const n = summary && st in summary ? summary[st] : null;
      setText(btn.querySelector('.st-count'), n == null ? '–' : n);
      let note = '', kind = n ? 'muted' : '';
      if (st === 'Unavailable' && n) { kind = 'danger'; note = 'needs you'; }
      if (st === 'Downloading' && stalled) { kind = 'danger'; note = stalled + ' stalled'; } else if (st === 'Downloading' && n) kind = flowing ? 'flow' : 'warn';
      if (st === 'Importing' && n) kind = 'flow';
      if (st === 'Partial' && n) { kind = 'warn'; note = 'incomplete'; }
      if (st === 'Searching' && n) { kind = 'flow'; note = 'grabbing'; }
      if (st === 'Available' && n) kind = 'ok';
      btn.dataset.kind = kind; setText(btn.querySelector('.st-note'), note);
      btn.setAttribute('aria-pressed', activeFilter === st ? 'true' : 'false');
    }
    const state = (vpnState && vpnState.enabled && !vpnState.up) || containersDown ? 'danger' : (vpnState?.orphaned?.length ? 'warn' : 'ok');
    track.dataset.state = state;
    health.className = 'pill pill-' + state + ' dash-health'; healthGlyph.textContent = state === 'ok' ? '●' : state === 'warn' ? '▲' : '✕';
    setText(healthWord, state === 'ok' ? 'stack up' : state === 'warn' ? 'VPN orphaned' : containersDown ? `${containersDown} container${containersDown > 1 ? 's' : ''} down` : 'VPN down');
    vpn.replaceChildren();
    if (vpnState && vpnState.enabled) {
      const up = !!vpnState.up, orphan = vpnState.orphaned?.length;
      vpn.append(h('span', { class: 'pill pill-' + (up ? (orphan ? 'warn' : 'ok') : 'danger') }, h('span', { class: 'glyph', 'aria-hidden': 'true' }, up ? (orphan ? '▲' : '●') : '✕'),
        h('span', {}, up ? (orphan ? 'VPN · orphaned' : 'VPN protected') : 'VPN down')));
      if (up) vpn.append(h('span', { class: 'mono muted' }, `${vpnState.ip || '—'}`), h('span', { class: 'mono muted', title: 'Forwarded port (ProtonVPN); qBittorrent listens here' }, vpnState.port ? ':' + vpnState.port : 'no port'));
    } else if (vpnState && vpnState.enabled === false) {
      vpn.append(h('span', { class: 'muted' }, 'no VPN'));
    }
    setText(age, ageS == null ? '' : `${Math.round(ageS)} s`);
  }
  return { update };
}
