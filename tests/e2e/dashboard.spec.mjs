import { test, expect, loaded } from './fixtures.mjs';

test.describe('dashboard', () => {
  test.beforeEach(async ({ page, login }) => { await login(); await page.goto('/'); await loaded(page); });

  test('the Dash: seven stations, counts, and a word where something needs you', async ({ page }) => {
    const stations = page.locator('#dash .station');
    await expect(stations).toHaveCount(7);
    await expect(stations.locator('.st-name')).toHaveText(['UNAVAIL', 'SEARCH', 'DOWNLOAD', 'IMPORT', 'PARTIAL', 'WAITING', 'AVAILABLE']);
    await expect(page.locator('.station[data-stage="Available"] .st-count')).toHaveText('2');
    await expect(page.locator('.station[data-stage="Unavailable"] .st-note')).toHaveText('needs you');
    await expect(page.locator('.station[data-stage="Downloading"] .st-note')).toHaveText('1 stalled');
    await expect(page.locator('#dash .dash-track')).toHaveAttribute('data-state', 'ok');
    await expect(page.locator('#dash .dash-health')).toHaveText(/stack up/);
    await expect(page.locator('.station[data-stage="Available"]')).toHaveAttribute('data-kind', 'ok');
    await expect(page.locator('.station[data-stage="Unavailable"]')).toHaveAttribute('data-kind', 'danger');
    await expect(page.locator('#dash .dash-host')).toContainText('fakehost');
    await expect(page.locator('#dash .dash-vpn')).toHaveText('no VPN');
  });

  test('Needs attention lists every kind with a word, facts and one primary action', async ({ page }) => {
    const items = page.locator('#attention .attn:not(.attn-src)');
    await expect(items).toHaveCount(5);
    await expect(items.locator('.attn-word')).toContainText(['STALLED', 'IMPORT', 'INDEXER', 'UNAVAILABLE', 'REQUEST']);
    await expect(page.locator('#attention [data-count]')).toHaveText('5');
    const stalled = items.first();
    await expect(stalled).toHaveClass(/attn-danger/);
    await expect(stalled.locator('.attn-title')).toHaveText('Blade Runner 2049 — stalled');
    await expect(stalled.locator('.attn-detail')).toContainText('dead swarm');
    await expect(stalled.locator('.btn-primary')).toHaveText('Blocklist & retry');
    await expect(page.locator('#attention .attn-src')).toHaveCount(0);
    await expect(page.locator('#attention .state-empty')).toHaveCount(0);
    await expect(page.locator('#quicklinks a')).toHaveText(['Jellyfin ↗', 'Jellyseerr ↗']);   // the two apps a household opens, one tap from the top
    await expect(page.locator('#quicklinks a').first()).toHaveAttribute('href', /:8096$/);
  });

  test('Live: torrents with progress and reasons, throughput, Now playing', async ({ page }) => {
    const tors = page.locator('#live .tor');
    await expect(tors).toHaveCount(4);
    await expect(tors.first().locator('.pbar')).toHaveAttribute('aria-valuenow', '42');
    await expect(tors.first().locator('.tor-why')).toContainText('dead swarm');
    await expect(tors.first().getByRole('button', { name: 'Reannounce' })).toBeVisible();
    await expect(page.locator('#live [data-alt]')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('.nowplaying .np')).toContainText(['Arrival']);
    await expect(page.locator('.nowplaying .pill')).toHaveText(/Transcode/);
    await expect(page.locator('#spark')).toHaveAttribute('aria-label', /Download .* upload .* two minutes/);
  });

  test('Library: grouped by stage (Available split into movies and shows), filtered by text, dropdown and station', async ({ page }) => {
    await expect(page.locator('#library .stage')).toHaveCount(5);
    await expect(page.locator('#library .stage-head .stage-toggle > span:nth-child(2)')).toHaveText(['Unavailable', 'Downloading', 'Waiting', 'Available movies', 'Available shows']);
    // while anything is not Available, both Available groups start collapsed (DASHBOARD.md)
    const movies = page.locator('#library .stage[data-sec="Available-movie"] .stage-toggle'), shows = page.locator('#library .stage[data-sec="Available-tv"] .stage-toggle');
    await expect(movies).toHaveAttribute('aria-expanded', 'false');
    await expect(shows).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#library .item')).toHaveCount(4);
    await movies.click();
    await expect(movies).toHaveAttribute('aria-expanded', 'true');
    await expect(shows).toHaveAttribute('aria-expanded', 'false');   // each half folds on its own
    await expect(page.locator('#library .item')).toHaveCount(5);
    await expect(page.locator('#library .stage[data-sec="Available-movie"] .item .m')).toContainText('5.3 GB · 1 h 56 min');   // size and duration
    await shows.click();
    await expect(page.locator('#library .item')).toHaveCount(6);
    await expect(page.locator('#library .stage[data-sec="Available-tv"] .item .m')).toContainText('20.0 GB · 55 min/ep · 8 h 15 min on disk');
    await page.locator('#filter').fill('expanse');
    await expect(page.locator('#library .item')).toHaveCount(1);
    await expect(page.locator('#library .item .title')).toHaveText('The Expanse');
    await page.locator('#filter').fill('');
    await page.locator('#fstage').selectOption('Available');
    await expect(page.locator('#library .item')).toHaveCount(2);
    await expect(page).toHaveURL(/#library\?stage=Available/);
    await page.locator('.station[data-stage="Unavailable"]').click();
    await expect(page.locator('#fstage')).toHaveValue('Unavailable');
    await expect(page.locator('#library .item .title')).toHaveText(['Coherence']);
    await expect(page.locator('.station[data-stage="Unavailable"]')).toHaveAttribute('aria-pressed', 'true');
  });

  test('Reference: every configured app with its version and a link', async ({ page }) => {
    const apps = page.locator('#reference .app');
    await expect(apps).toHaveCount(8);
    await expect(apps.locator('.app-name')).toHaveText(['Jellyfin', 'Jellyseerr', 'Radarr', 'Sonarr', 'Prowlarr', 'qBittorrent', 'Bazarr', 'ntfy']);
    await expect(apps.nth(2)).toContainText('5.0.0-fake');
    await expect(apps.nth(1).locator('.pill')).toHaveText(/update available/);
    await expect(apps.first().locator('.app-name')).toHaveAttribute('href', /:8096$/);
  });

  test('section navigation moves the hash and the current marker', async ({ page }) => {
    await expect(page.locator('.secnav a')).toHaveText(['Attention', 'Live', 'Library', 'Reference', 'Settings']);   // the order of the page
    await page.locator('.secnav a[href="#library"]').click();
    await expect(page).toHaveURL(/#library$/);
    await expect(page.locator('.secnav a[href="#library"]')).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('.secnav a[href="#attention"]')).toHaveAttribute('aria-current', 'false');
  });

  test('the command palette opens with Ctrl+K, filters and jumps', async ({ page }) => {
    await page.keyboard.press('Control+k');
    const input = page.getByRole('searchbox', { name: 'Command palette' });
    await expect(input).toBeFocused();
    await input.fill('refer');
    await expect(page.locator('.pal-item')).toHaveCount(1);
    await expect(page.locator('.pal-item').first()).toContainText('Reference');
    await input.press('Enter');
    await expect(page).toHaveURL(/#reference$/);
    await expect(page.locator('dialog.dlg')).toHaveCount(0);
  });

  test('theme and density are switchable and persist across reloads', async ({ page }) => {
    await page.locator('#theme').click();
    await expect(page.locator('#theme')).toHaveText('Theme: dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.locator('#density').click();
    await expect(page.locator('#density')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    await page.reload(); await loaded(page);
    await expect(page.locator('#theme')).toHaveText('Theme: dark');
    await expect(page.locator('#density')).toHaveText('Compact');
  });

  test('the refresh control re-scans and confirms', async ({ page }) => {
    await page.locator('#refresh-now').click();
    await expect(page.locator('.toast')).toHaveText('Refreshed');
  });
});
