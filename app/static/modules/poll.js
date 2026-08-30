// One scheduler for every data source: jittered start, exponential backoff on failure (x2 up to 5x),
// pause while the tab is hidden or a hold is set (selection active), immediate refresh on focus/visible.
export function createScheduler() {
  const src = new Map();
  let holds = new Set();
  function schedule(s, ms) { clearTimeout(s.timer); s.timer = setTimeout(() => run(s.name), ms); s.due = Date.now() + ms; }
  async function run(name) {
    const s = src.get(name); if (!s) return;
    if (document.hidden || (holds.size && s.last)) { schedule(s, 1000); return; }   // a hold pauses refreshes, never a first load (a ticked row must not leave Reference a skeleton)
    if (s.inflight) return;
    s.inflight = true;
    try {
      const r = await fetch(s.url, { cache: 'no-cache', credentials: 'same-origin' });   // no-cache (not no-store): revalidate with ETag, reuse the body on 304
      if (r.status === 401 || (r.redirected && /\/login/.test(r.url))) { location.href = '/login'; return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      s.fails = 0; s.last = Date.now(); s.data = data; s.err = null;
      try { s.onData(data, s); } catch (e) { console.error(name, e); }
    } catch (e) {
      s.fails++; s.err = e.message || String(e);
      try { s.onError(s.err, s); } catch (e2) { console.error(name, e2); }
    } finally {
      s.inflight = false;
      const base = typeof s.base === 'function' ? s.base() : s.base;
      schedule(s, Math.min(base * Math.pow(2, s.fails), base * 5));
    }
  }
  return {
    add(name, url, base, onData, onError = () => {}) {
      const s = { name, url, base, onData, onError, fails: 0, data: null, err: null, inflight: false, timer: null, last: 0 };
      src.set(name, s);
      const b = typeof base === 'function' ? base() : base;
      schedule(s, Math.round(Math.random() * Math.min(1500, b * 0.2)));   // jittered start
      return s;
    },
    refresh(...names) { for (const n of (names.length ? names : [...src.keys()])) { const s = src.get(n); if (s) schedule(s, 50); } },
    hold(key, on) { if (on) holds.add(key); else holds.delete(key); },
    holding() { return holds.size > 0; },
    get(name) { return src.get(name); },
    all() { return [...src.values()]; },
  };
}
export function wirePageEvents(sched) {
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sched.refresh(); });
  window.addEventListener('focus', () => sched.refresh());
}
