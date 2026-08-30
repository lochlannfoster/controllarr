import { test, expect } from './fixtures.mjs';

const field = (page, label) => page.locator('.frow', { has: page.locator('.flabel', { hasText: label }) }).locator('input, select').first();

test.describe('settings', () => {
  test.beforeEach(async ({ page, login, control }) => { await control({ clear_calls: true }); await login(); await page.goto('/settings#downloads'); await expect(page.locator('#pane .pane-head')).toBeVisible(); });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('groups load the live values and say whether the app is reachable', async ({ page }) => {
    await expect(page.locator('#grouplist a')).toHaveCount(13);   // 11 groups + Quality & size's three sub-groups, minus the parent label
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Downloads');
    await expect(page.locator('#pane .reach .pill')).toHaveText(/reachable/);
    await expect(field(page, 'Upload limit')).toHaveValue('2');
    await expect(field(page, 'Active downloads')).toHaveAttribute('max', '2');
    await expect(page.locator('#applybar')).toBeHidden();
    await page.locator('#grouplist a[href="#radarr"]').click();
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Quality & size — Movies (Radarr)');
    await expect(field(page, 'Audio language')).toHaveValue('Original');
    await page.locator('#grouplist a[href="#users"]').click();
    await expect(page.locator('#pane')).toContainText('admin');
  });

  test('edits collect as a diff and nothing is written until Apply', async ({ page, control }) => {
    await field(page, 'Download limit').fill('5');
    await expect(page.locator('#applybar')).toBeVisible();
    await expect(page.locator('#applydiff')).toContainText('Download limit');
    await expect(page.locator('#applydiff')).toContainText('5');
    expect((await control()).calls.filter(c => /setPreferences/.test(c[2]))).toEqual([]);
    await page.locator('#apply').click();
    await expect(page.locator('.toast-ok')).toHaveText('qBittorrent settings saved');
    await expect(page.locator('#applybar')).toBeHidden();
    await expect(page.locator('dialog.dlg')).toHaveCount(0);           // a successful Apply must not ask "Discard unsaved changes?"
    await expect(field(page, 'Download limit')).toHaveValue('5');       // re-read from the app
    await expect.poll(async () => (await control()).calls.filter(c => /setPreferences/.test(c[2])).map(c => JSON.parse(c[3].json).dl_limit)).toEqual([5 * 1048576]);
  });

  test('Discard drops the pending edits', async ({ page }) => {
    await field(page, 'Upload limit').fill('9');
    await expect(page.locator('#applybar')).toBeVisible();
    await page.locator('#discard').click();
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Discard unsaved changes?');
    await dlg.getByRole('button', { name: 'Discard' }).click();
    await expect(page.locator('#applybar')).toBeHidden();
    await expect(field(page, 'Upload limit')).toHaveValue('2');
  });

  test('the guide is previewed in full before anything is written, then applied and rolled back', async ({ page, control }) => {
    await page.goto('/settings#trash');
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Quality & size — TRaSH Guides');
    await expect(page.locator('#pane')).toContainText('shipped with the panel');   // vendored: nothing was fetched to draw this
    const movies = page.locator('.fset', { hasText: 'Movies (Radarr)' });
    await movies.locator('select').selectOption('HD Bluray + WEB');
    const apply = movies.getByRole('button', { name: 'Apply' });
    await expect(apply).toBeDisabled();                                            // you cannot apply what you have not read

    await movies.getByRole('button', { name: 'Preview changes' }).click();
    await expect(movies.locator('.tdiff-sum')).toContainText('is created');
    await expect(movies.locator('.tdiff-sum')).toContainText('custom formats');
    await expect(movies.locator('.tdiff')).toContainText('size limits');
    expect((await control()).calls.filter(c => c[0] === 'radarr' && c[1] !== 'GET')).toEqual([]);   // a preview writes nothing
    await movies.locator('details', { hasText: 'format scores' }).locator('summary').click();
    await expect(movies.locator('details[open] .logtable tbody tr').first()).toBeVisible();

    await expect(apply).toBeEnabled();
    await apply.click();
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Apply HD Bluray + WEB to Radarr');
    await expect(dlg).toContainText('snapshot');
    await dlg.getByRole('button', { name: 'Apply' }).click();
    await expect(page.locator('.toast-ok')).toContainText('HD Bluray + WEB applied to Radarr');
    await expect.poll(async () => (await control()).calls.some(c => c[0] === 'radarr' && c[1] === 'POST' && /customformat/.test(c[2]))).toBe(true);

    await page.locator('#grouplist a[href="#backup"]').click();
    await expect(page.locator('#pane')).toContainText('taken before radarr ▸ HD Bluray + WEB');
    await page.getByRole('button', { name: 'Roll back' }).click();
    await expect(dlg.locator('#dlg-title')).toHaveText('Roll back the last sync');
    await dlg.getByRole('button', { name: 'Roll back' }).click();
    await expect(page.locator('.toast-ok').last()).toContainText('Rolled back');
  });

  test('the action log lists every write, filters it, and offers nothing that changes it', async ({ page }) => {
    const r = await page.request.post('/api/action', { data: { action: 'retry', kind: 'movie', id: 2 } });
    expect(r.ok(), 'the write the log should record').toBeTruthy();
    await page.locator('#grouplist a[href="#log"]').click();
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Action log');
    const rows = page.locator('.logtable tbody tr');
    await expect(rows.first()).toContainText('retry');
    await expect(rows.first()).toContainText('movie:2');            // the real target, never a pseudonym
    await expect(rows.first().locator('.pill')).toContainText('ok');   // the pill is a glyph and a word
    await expect(page.locator('#pane .fhelp')).toContainText('newest 2000 entries');
    const n = await rows.count();
    await page.locator('#log-action').selectOption('retry');
    await expect(rows.first()).toContainText('retry');
    expect(await rows.count()).toBeLessThanOrEqual(n);
    await page.locator('#log-user').selectOption('admin');
    await expect(rows.first()).toContainText('admin');
    // read-only: nothing here undoes, edits or clears an entry
    await expect(page.locator('#pane button')).toHaveText(['Refresh']);
    await expect(page.locator('#pane input, #pane textarea')).toHaveCount(0);
  });

  test('the notification test button reports the result', async ({ page, control }) => {
    await page.locator('#grouplist a[href="#notifications"]').click();
    await page.getByRole('button', { name: 'Send a test notification' }).click();
    await expect(page.locator('.toast-ok')).toHaveText('Test notification sent to admin');
    await expect.poll(async () => (await control()).calls.some(c => c[0] === 'ntfy' && c[2] === '/admin')).toBe(true);
  });
});
