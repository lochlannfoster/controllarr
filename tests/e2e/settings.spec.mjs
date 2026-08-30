import { test, expect } from './fixtures.mjs';

const field = (page, label) => page.locator('.frow', { has: page.locator('.flabel', { hasText: label }) }).locator('input, select').first();

test.describe('settings', () => {
  test.beforeEach(async ({ page, login, control }) => { await control({ clear_calls: true }); await login(); await page.goto('/settings#downloads'); await expect(page.locator('#pane .pane-head')).toBeVisible(); });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('groups load the live values and say whether the app is reachable', async ({ page }) => {
    await expect(page.locator('#grouplist a')).toHaveCount(11);   // 10 groups + Quality & size's two sub-groups, minus the parent label
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

  test('the notification test button reports the result', async ({ page, control }) => {
    await page.locator('#grouplist a[href="#notifications"]').click();
    await page.getByRole('button', { name: 'Send a test notification' }).click();
    await expect(page.locator('.toast-ok')).toHaveText('Test notification sent to admin');
    await expect.poll(async () => (await control()).calls.some(c => c[0] === 'ntfy' && c[2] === '/admin')).toBe(true);
  });
});
