// Loading / empty / partial / stale / error states, each produced deterministically through the fake
// stack's control endpoint and a cold panel (harness restart), never by waiting for a cache to expire.
import { test, expect, loaded } from './fixtures.mjs';

test.describe('states', () => {
  // A cold panel BEFORE each test (fresh page, nothing polling yet); never restart while a page is still open —
  // the error-capture fixture would rightly report the refused polls.
  test.beforeEach(async ({ control, harness }) => { await control({ reset: true }); await harness.restart(); });
  test.afterAll(async ({ request }) => {
    const { state } = await import('./fixtures.mjs'); const st = state();
    await request.post(st.control, { data: { reset: true } }); await request.post(st.harness, { data: { restart: true } });
  });

  test('loading: skeletons hold the place until the section answers', async ({ page, login }) => {
    await login();
    await page.route('**/api/attention', async route => { await new Promise(r => setTimeout(r, 1500)); await route.continue(); });
    await page.goto('/');
    await expect(page.locator('#attention .skel-attn')).toBeVisible();
    await expect(page.locator('#live .skel-torrent')).toHaveCount(0, { timeout: 8000 });
    await loaded(page);
  });

  test('partial: a dead source is named in its section and the rest keeps working', async ({ page, login, control, harness }) => {
    await control({ down: ['jellyfin', 'prowlarr'] });
    await harness.restart();
    await login(); await page.goto('/'); await loaded(page);
    await expect(page.locator('.nowplaying .state-error')).toContainText('Jellyfin unreachable');
    await expect(page.locator('#attention .attn-src')).toContainText("Prowlarr didn't answer — its status is unknown");
    await expect(page.locator('#attention [data-age] .age-stale')).toContainText('as of');
    await expect(page.locator('#live .tor')).toHaveCount(4);
    await expect(page.locator('#attention .attn:not(.attn-src)')).toHaveCount(5);
  });

  test('stale: when the panel stops answering, the last good values stay and the chip says so', async ({ page, login, errors }) => {
    await login(); await page.goto('/'); await loaded(page);
    errors.allow.push(/\/api\/live/);
    await page.route('**/api/live', route => route.abort());
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));   // asks every source to refresh now
    await expect(page.locator('#live [data-age]')).toContainText('unreachable · as of', { timeout: 12000 });
    await expect(page.locator('#live .tor')).toHaveCount(4);              // last good rows are still there
  });

  test('empty: the good news is said out loud', async ({ page, login, control, harness }) => {
    test.skip(harness.state.disk_pct >= 80, 'the temp volume is above the disk-warning threshold, so an item is expected');
    await control({ scenario: 'empty' });
    await harness.restart();
    await login(); await page.goto('/'); await loaded(page);
    await expect(page.locator('#attention .state-empty')).toContainText('Nothing needs you');
    await expect(page.locator('#attention [data-count]')).toHaveText('');
    await expect(page.locator('#live .torrents .state-empty')).toHaveText(/No active downloads/);
    await expect(page.locator('.nowplaying .state-empty')).toHaveText('Nobody is watching.');
    await expect(page.locator('.station[data-stage="Unavailable"] .st-count')).toHaveText('0');
    await expect(page.locator('.station[data-stage="Unavailable"] .st-note')).toHaveText('');
    await expect(page.locator('#library .stage')).toHaveCount(2);   // everything Available: the movies group and the shows group, nothing else
  });

  test('error: a stopped container is a danger item, the Dash turns red, Reference marks it', async ({ page, login, control, harness }) => {
    await control({ scenario: 'container_down' });
    await harness.restart();
    await login(); await page.goto('/'); await loaded(page);
    const item = page.locator('#attention .attn-danger', { hasText: 'radarr has stopped' });
    await expect(item.locator('.attn-word')).toHaveText(/SERVICE/);
    await expect(item.locator('.attn-detail')).toContainText('docker logs radarr');   // where the rest of the story is, now that there is no log viewer in the stack
    await expect(page.locator('#dash .dash-track')).toHaveAttribute('data-state', 'danger');
    await expect(page.locator('#reference .app', { hasText: 'Radarr' }).locator('.pill')).toHaveText(/exited/);
  });

  test('signed out underneath the page: the next poll returns to sign-in', async ({ page, login }) => {
    await login(); await page.goto('/'); await loaded(page);
    await page.context().clearCookies();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(page).toHaveURL(/\/login/, { timeout: 12000 });
  });
});
