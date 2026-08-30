import { test, expect, loaded } from './fixtures.mjs';

test.describe('actions', () => {
  test.beforeEach(async ({ page, login, control }) => { await control({ clear_calls: true }); await login(); await page.goto('/'); await loaded(page); });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('selecting a title shows the bulk bar; Retry asks Radarr to search and toasts', async ({ page, control }) => {
    await page.locator('.item[data-id="2"] .sel').check();
    await expect(page.locator('#selbar')).toBeVisible();
    await expect(page.locator('#seln')).toHaveText('1');
    await expect(page.locator('#holdnote')).not.toBeEmpty();
    await page.locator('#selbar').getByRole('button', { name: 'Retry' }).click();
    await expect(page.locator('.toast-ok')).toHaveText('Retry — 1 title');   // the bulk bar summarises; the server's own message is checked in tests/api
    await expect.poll(async () => (await control()).calls.filter(c => c[0] === 'radarr' && c[1] === 'POST' && c[3]?.name === 'MoviesSearch').map(c => c[3].movieIds)).toEqual([[2]]);
  });

  test('bulk Purge confirms with the titles named and can be cancelled', async ({ page, control }) => {
    await page.locator('.item[data-id="2"] .sel').check();
    await page.locator('#selbar').getByRole('button', { name: 'Purge' }).click();
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Purge 1 title');
    await expect(dlg.locator('p')).toContainText("Blade Runner 2049. Can't be undone.");
    await expect(dlg.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await expect(dlg.getByRole('button', { name: 'Purge' })).toHaveClass(/btn-danger/);
    await dlg.getByRole('button', { name: 'Cancel' }).click();
    await expect(dlg).toHaveCount(0);
    const calls = (await control()).calls;
    expect(calls.filter(c => c[1] === 'DELETE' || /torrents\/delete/.test(c[2]))).toEqual([]);
  });

  test('a drawer action confirms with the server-written consequence (real counts)', async ({ page, control }) => {
    await page.locator('.item[data-id="2"]').click();
    const drawer = page.locator('dialog.drawer');
    await expect(drawer.locator('#dw-title')).toHaveText('Blade Runner 2049 (2017)');
    await drawer.locator('section.dwsec', { has: page.getByRole('heading', { name: 'Library' }) }).getByRole('button', { name: 'Purge' }).click();   // the title's Purge, not a torrent's
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Purge Blade Runner 2049');
    await expect(dlg.locator('p')).toContainText('removes 1 torrent from qBittorrent and the Jellyseerr request, and drops the title from Radarr');
    await dlg.getByRole('button', { name: 'Cancel' }).click();
    await expect(dlg).toHaveCount(0);
    expect((await control()).calls.filter(c => c[1] === 'DELETE' || /torrents\/delete/.test(c[2]))).toEqual([]);
  });

  test('Pause all confirms with real counts, then acts and reports', async ({ page, control }) => {
    await page.locator('[data-global="qall_pause"]').click();   // the global one (a torrent group has its own Pause all)
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Pause all');
    await expect(dlg.locator('p')).toContainText('Stops 4 torrents — 2 downloading: Blade Runner 2049, The Expanse');
    await dlg.getByRole('button', { name: 'Pause', exact: true }).click();
    await expect(page.locator('.toast-ok')).toHaveText('All paused');
    await expect.poll(async () => (await control()).calls.some(c => c[0] === 'qbittorrent' && /torrents\/stop/.test(c[2]) && c[3]?.hashes === 'all')).toBe(true);
  });

  test('the drawer opens from a row, is labelled, closes on Escape and returns focus', async ({ page }) => {
    const row = page.locator('.item[data-id="2"]');
    await row.click();
    const drawer = page.locator('dialog.drawer');
    await expect(drawer).toBeVisible();
    await expect(drawer).toHaveAttribute('aria-labelledby', 'dw-title');
    await expect(drawer.locator('#dw-title')).toHaveText('Blade Runner 2049 (2017)');
    await expect(drawer.getByRole('heading', { name: 'Quality & size' })).toBeVisible();
    await expect(drawer.locator('.tor-inline')).toHaveCount(1);
    await expect(page).toHaveURL(/item=movie(:|%3A)2/);
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    await expect(page).not.toHaveURL(/item=/);
    await expect(row.locator('.info')).toBeFocused();   // focus returns to the clickable part of the row
  });

  test('an attention item\'s Open lands in the drawer', async ({ page }) => {
    await page.locator('#attention .attn').first().getByRole('button', { name: 'Open' }).click();
    await expect(page.locator('dialog.drawer #dw-title')).toHaveText('Blade Runner 2049 (2017)');
  });

  test('a standard user sees no privileged controls and the server refuses them anyway', async ({ page, login, errors }) => {
    await page.request.post('/api/users', { data: { username: 'viewer', password: 'viewer-pw', role: 'user' } });
    await page.context().clearCookies();
    await login('viewer', 'viewer-pw');
    await page.goto('/'); await loaded(page);
    await expect(page.locator('.secnav a[data-admin]')).toBeHidden();
    await expect(page.locator('[data-global="qall_pause"]')).toBeHidden();
    await expect(page.locator('#import-btn')).toBeHidden();
    await page.locator('.item[data-id="2"] .sel').check();
    await expect(page.locator('#selbar [data-a="purge"]')).toBeHidden();
    await expect(page.locator('#selbar [data-a="retry"]')).toBeVisible();
    const stalled = page.locator('#attention .attn').first();
    await expect(stalled.getByRole('button', { name: 'Blocklist & retry' })).toBeHidden();
    await expect(stalled.getByRole('button', { name: 'Open' })).toBeVisible();
    errors.allow.push(/\/api\/action$/);
    const r = await page.request.post('/api/action', { data: { action: 'purge', kind: 'movie', id: 2 } });
    expect(r.status()).toBe(403);
    expect(await r.json()).toEqual({ ok: false, message: 'Not permitted (ask an admin)' });
  });
});
