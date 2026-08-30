// axe-core (WCAG 2.0/2.1 A + AA) on the major surfaces, in both themes, with the drawer and a dialog open.
import AxeBuilder from '@axe-core/playwright';
import { test, expect, loaded } from './fixtures.mjs';

// Every rule on, everywhere (the Library row's clickable part is its own element now, so nothing interactive nests).
const KNOWN = [];

async function axe(page, name) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).disableRules(KNOWN).analyze();
  const summary = r.violations.map(v => `${v.id} [${v.impact}] ×${v.nodes.length} — ${v.help} — e.g. ${v.nodes[0].target.join(' ')}`);
  expect(summary, `${name}: axe violations`).toEqual([]);
}

test.describe('accessibility', () => {
  test('sign-in page', async ({ page }) => { await page.goto('/login'); await axe(page, 'login'); });

  test('dashboard, dark and light', async ({ page, login }) => {
    await login(); await page.goto('/'); await loaded(page);
    await axe(page, 'dashboard dark');
    await page.evaluate(() => localStorage.setItem('mc-theme', 'light'));
    await page.reload(); await loaded(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await axe(page, 'dashboard light');
  });

  test('an expanded show, the drawer and a confirmation dialog', async ({ page, login }) => {
    await login(); await page.goto('/'); await loaded(page);
    await page.locator('.item[data-id="12"] .info').click();
    await expect(page.locator('.item[data-id="12"] .expand .season')).toHaveCount(2);
    await axe(page, 'library row expanded (tv)');
    await page.locator('.item[data-id="12"] .openx').click();
    await expect(page.locator('dialog.drawer #dw-title')).toHaveText(/The Expanse/);
    await expect(page.locator('dialog.drawer #dwseasons .season')).toHaveCount(2);
    await axe(page, 'drawer (tv)');
    await page.keyboard.press('Escape');
    await page.locator('[data-global="qall_pause"]').click();
    await expect(page.locator('dialog.dlg #dlg-title')).toHaveText('Pause all');
    await axe(page, 'confirm dialog');
  });

  test('settings', async ({ page, login }) => {
    await login(); await page.goto('/settings');
    await expect(page.locator('#pane .pane-head')).toBeVisible();
    await axe(page, 'settings');
  });
});
