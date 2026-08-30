// Incognito: the header switch that draws every title, poster, requester and file name as a made-up one.
// The point of these tests is the leak, not the label — the sweep below reads everything a screenshot would
// show (text plus the attributes a tooltip or a screen reader speaks) and fails on any real name still in it.
import { test, expect, loaded } from './fixtures.mjs';

// every name the fake stack owns: titles, the release names its torrents carry, and its people
const REAL = ['Arrival', 'Blade Runner 2049', 'Blade.Runner', 'Coherence', 'Dune Part Three', 'Severance',
  'The Expanse', 'The.Expanse', 'Doors & Corners', 'Good News About Hell'];
const PEOPLE = [/\bsam\b/i, /\balex\b/i];

/** Everything the page shows: rendered text, plus title / aria-label / alt / placeholder. */
async function visible(page) {
  return page.evaluate(() => {
    const parts = [document.body.innerText];
    for (const el of document.querySelectorAll('[title], [aria-label], [alt], [placeholder]')) {
      if (el.closest('[hidden]') || el.hidden) continue;
      for (const a of ['title', 'aria-label', 'alt', 'placeholder']) { const v = el.getAttribute(a); if (v) parts.push(v); }
    }
    return parts.join('\n');
  });
}
function expectClean(text, where) {
  for (const n of REAL) expect(text, `${where}: "${n}" is still on the page`).not.toContain(n);
  for (const re of PEOPLE) expect(text, `${where}: ${re} is still on the page`).not.toMatch(re);
}
const on = page => page.locator('#incognito').click();

test.describe('incognito', () => {
  test.beforeEach(async ({ page, login }) => { await login(); await page.goto('/'); await loaded(page); });

  test('the pseudonym is stable, made only of its own words, and one per thing', async ({ page }) => {
    const r = await page.evaluate(async () => {
      const m = await import('/static/modules/incognito.js');
      const keys = ['movie:2', 'tv:12', 'Blade.Runner.2049.2017.1080p.BluRay.x264-GROUP', ''];
      return {
        stable: keys.map(k => m.alias(k)), again: keys.map(k => m.alias(k)),
        person: m.person('sam'), personAgain: m.person('sam'),
        spread: new Set(Array.from({ length: 500 }, (_, i) => m.alias('movie:' + i))).size,
        distinct: m.alias('movie:2') !== m.alias('tv:12'),
        // off by default, and it masks nothing until it is on
        offTitle: m.mask('The Expanse', 'tv:12'), offYear: m.yr(2015), offOn: m.isOn(),
      };
    });
    expect(r.stable).toEqual(r.again);                                   // same key, same pseudonym, every time
    expect(r.person).toEqual(r.personAgain);
    expect(r.distinct).toBe(true);
    for (const a of r.stable) expect(a).toMatch(/^[A-Z][a-z]+ [A-Z][a-z]+ \d\d$/);   // only ever its own two words and a number
    expect(r.person).toMatch(/^[A-Z][a-z]+ [A-Z][a-z]+$/);
    expect(r.spread).toBeGreaterThan(450);                               // 500 titles collide a handful of times, no more
    expect(r.offOn).toBe(false);
    expect(r.offTitle).toBe('The Expanse'); expect(r.offYear).toBe(2015);
  });

  test('switched on, nothing a screenshot would show names anything real', async ({ page }) => {
    expect(await visible(page)).toContain('Blade Runner 2049');          // it is all there with the switch off
    await on(page);
    await expect(page.locator('#incognito')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#incognito')).toHaveText('Incognito on');
    expectClean(await visible(page), 'dashboard');
    // the row is still a row: its stage, size and reason are all there, and the poster is a blank placeholder
    const row = page.locator('.item[data-id="2"]');
    await expect(row.locator('.title')).toHaveText(/^[A-Z][a-z]+ [A-Z][a-z]+ \d\d$/);
    await expect(row.locator('.yr')).toHaveCount(0);                     // the year goes with the title
    await expect(row.locator('img.pos')).toHaveCount(0);
    await expect(row.locator('.badge')).toContainText('Downloading');
    // and the sections the server writes the sentences for
    await expect(page.locator('#attention .attn').first().locator('.attn-title')).toHaveText(/^[A-Z][a-z]+ [A-Z][a-z]+ \d\d — stalled$/);
    await expect(page.locator('#attention .attn').first().locator('.attn-detail')).toContainText('dead swarm');
  });

  test('opening a show, its episodes and the drawer stays clean', async ({ page }) => {
    await on(page);
    await page.locator('#library .stage[data-sec="Available-tv"] .stage-toggle').click();
    const show = page.locator('.item[data-kind="tv"]').first();
    await show.locator('.info').click();
    await expect(show.locator('.ep').first()).toBeVisible();
    expectClean(await visible(page), 'episode list');
    await show.locator('.openx').click();
    await expect(page.locator('dialog.drawer .dt')).toBeVisible();
    await expect(page.locator('dialog.drawer .tn').first()).toHaveText(/^[A-Z][a-z]+ [A-Z][a-z]+ \d\d$/);   // the torrent's file name
    expectClean(await visible(page), 'drawer');
  });

  test('it survives a poll and a reload, and the palette still finds what you own', async ({ page }) => {
    await on(page);
    await page.waitForResponse(r => r.url().includes('/api/live') && r.ok());
    await page.waitForResponse(r => r.url().includes('/api/attention') && r.ok());
    expectClean(await visible(page), 'after a poll');
    await page.reload();
    await loaded(page);
    await expect(page.locator('#incognito')).toHaveAttribute('aria-pressed', 'true');   // remembered per browser
    expectClean(await visible(page), 'after a reload');
    // typing the real title still finds the row — you know your library, the screen does not have to show it
    await page.locator('#filter').fill('expanse');
    await expect(page.locator('#library .item')).toHaveCount(1);
    await page.locator('#filter').fill('');
    await page.locator('#palette').click();
    await page.locator('.pal input[type="search"]').fill('Blade Runner');
    await expect(page.locator('.pal-item')).toHaveCount(1);
    await expect(page.locator('.pal-item')).toHaveText(/[A-Z][a-z]+ [A-Z][a-z]+ \d\d/);
    expectClean(await visible(page), 'command palette');
  });

  test('a purge confirmation still names the counts, and never the title', async ({ page, control }) => {
    await control({ clear_calls: true });
    await on(page);
    await page.locator('.item[data-id="2"] .sel').check();
    await page.locator('#selbar').getByRole('button', { name: 'Purge' }).click();
    const bulk = page.locator('dialog.dlg');
    await expect(bulk.locator('#dlg-title')).toHaveText('Purge 1 title');
    await expect(bulk.locator('p')).toContainText(/[A-Z][a-z]+ [A-Z][a-z]+ \d\d\. Can't be undone\./);
    expectClean(await bulk.innerText(), 'bulk purge dialog');
    await bulk.getByRole('button', { name: 'Cancel' }).click();
    // the server-written one: the episode counts are the reason it exists, so they have to survive
    const tor = page.locator('#live .tor-group').first();
    await tor.getByRole('button', { name: 'Purge all' }).click();
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText(/^Purge [A-Z][a-z]+ [A-Z][a-z]+ \d\d/);
    await expect(dlg.locator('p')).toContainText('episode');
    expectClean(await dlg.innerText(), 'torrent purge dialog');
    await dlg.getByRole('button', { name: 'Cancel' }).click();
    expect((await control()).calls.filter(c => /torrents\/delete/.test(c[2]))).toEqual([]);
    await control({ reset: true });
  });

  test('switching it back off puts the real names straight back, without waiting for a poll', async ({ page }) => {
    await on(page);
    expectClean(await visible(page), 'on');
    await page.locator('#incognito').click();
    await expect(page.locator('#incognito')).toHaveAttribute('aria-pressed', 'false');
    expect(await visible(page)).toContain('Blade Runner 2049');
    await expect(page.locator('.item[data-id="2"] img.pos')).toHaveCount(1);
  });
});
