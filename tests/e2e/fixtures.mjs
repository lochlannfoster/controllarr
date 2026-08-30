// Shared fixtures: every test fails on any console error, page error, failed request or 4xx/5xx response
// (opt out per URL with errors.allow.push(/regex/)); login() signs the page's context in through the real
// form endpoint; control() drives the fake stack; harness.restart() gives the next page a cold panel.
import { test as base, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const STATE_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '.harness.json');
// Playwright's webServer is ready when /health answers, but harness.py writes STATE_FILE only after it has
// also waited for the first board — seconds later. Without this wait the first spec that needs the harness
// races that window and dies on ENOENT, and how often depends on how long a board pass happens to take.
export function state() {
  const deadline = Date.now() + 30_000;
  for (;;) {
    try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); }
    catch (e) {
      if (Date.now() > deadline) throw new Error(`harness state file never appeared at ${STATE_FILE}: ${e.message}`);
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);   // a synchronous 100 ms; state() has sync callers
    }
  }
}

export const test = base.extend({
  errors: [async ({ page }, use) => {
    const errors = { list: [], allow: [] };
    const allowed = url => errors.allow.some(re => re.test(url));
    page.on('console', m => { if (m.type() === 'error' && !allowed(m.location()?.url || '')) errors.list.push(`console.error: ${m.text()}`); });
    page.on('pageerror', e => errors.list.push(`pageerror: ${e.message}`));
    page.on('requestfailed', r => { const t = r.failure()?.errorText || ''; if (!/ERR_ABORTED/.test(t) && !allowed(r.url())) errors.list.push(`requestfailed: ${r.method()} ${r.url()} ${t}`); });
    page.on('response', r => { if (r.status() >= 400 && !allowed(r.url())) errors.list.push(`http ${r.status()} ${r.request().method()} ${r.url()}`); });
    await use(errors);
    expect(errors.list, 'console errors / page errors / failed or 4xx-5xx requests seen during the test').toEqual([]);
  }, { auto: true }],
  login: async ({ page }, use) => {
    await use(async (user = 'admin', password = state().password) => {
      const r = await page.request.post('/login', { form: { username: user, password, next: '' }, maxRedirects: 0 });
      expect(r.status(), `login as ${user}`).toBe(302);
    });
  },
  control: async ({ request }, use) => {
    const st = state();
    // control()            -> the fake's state: {scenario, down, calls}
    // control({down: [..]}) -> a command (down / up / scenario / clear_calls / reset)
    await use(async cmd => (cmd && Object.keys(cmd).length ? await request.post(st.control, { data: cmd }) : await request.get(st.control)).json());
  },
  harness: async ({ request }, use) => {
    const st = state();
    await use({
      state: st,
      restart: async () => { const r = await request.post(st.harness, { data: { restart: true } }); expect(r.ok(), 'panel restart').toBeTruthy(); },
    });
  },
});
export { expect };

// The dashboard is "loaded" once Needs attention, Live and the Library have rendered real content (skeletons gone) —
// every section a scan or an assertion may touch, so no test runs against a half-rendered page.
export async function loaded(page) {
  await expect(page.locator('#attention .skel')).toHaveCount(0);
  await expect(page.locator('#live .skel')).toHaveCount(0);
  await expect(page.locator('#library .item').first()).toBeVisible();
  await expect(page.locator('#system .sys-chip').first()).toBeVisible();   // the strip has rendered: nothing above the fold will move any more
}
