// Runs in the `phone` project only (Pixel 7 emulation, 360 px wide, touch): the phone floor: 44 px targets, 16 px inputs, nothing scrolling sideways.
import { test, expect, loaded } from './fixtures.mjs';

// Two things must hold on a phone: nothing wider than the page, AND the layout viewport is the device width. When a
// nowrap row cannot shrink, mobile Chromium widens the layout viewport to fit it (innerWidth 743 on a 360 px device) and
// the page renders zoomed out — scrollWidth then equals innerWidth and the old check passed anyway.
const noSidewaysScroll = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.body.scrollWidth <= window.innerWidth && window.innerWidth <= Math.round(window.visualViewport.width) + 1);

test.describe('phone', () => {
  test('no page scrolls sideways at 360 px', async ({ page, login }) => {
    await page.goto('/login');
    expect(await noSidewaysScroll(page), '/login').toBe(true);
    await login();
    await page.goto('/'); await loaded(page);
    expect(await noSidewaysScroll(page), '/').toBe(true);
    await page.goto('/settings');
    await expect(page.locator('#pane .pane-head')).toBeVisible();
    expect(await noSidewaysScroll(page), '/settings').toBe(true);
  });

  test('every control is at least 44 px on a touch screen, including in compact density', async ({ page, login }) => {
    await login(); await page.goto('/'); await loaded(page);
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), 'the emulated device reports a coarse pointer').toBe(true);
    const check = async () => {
      const short = await page.evaluate(() => {
        const sel = 'header .btn, header .secnav a, #dash .station, #attention .btn, #live .btn, #library .lib-tools > *';
        return [...document.querySelectorAll(sel)].filter(el => el.offsetParent !== null).map(el => [el.textContent.trim().slice(0, 20), el.getBoundingClientRect().height]).filter(([, h]) => h < 44);
      });
      expect(short, 'controls shorter than 44 px').toEqual([]);
    };
    await check();
    await page.locator('#density').click();
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    await check();
    const fontSize = await page.locator('#filter').evaluate(el => parseFloat(getComputedStyle(el).fontSize));
    expect(fontSize, 'inputs stay 16 px (no iOS zoom)').toBeGreaterThanOrEqual(16);
  });

  test('tooltips on a phone: help mode explains a control instead of running it, a long-press shows the same', async ({ page, login }) => {
    await login(); await page.goto('/'); await loaded(page);
    await page.locator('#help').tap();
    await expect(page.locator('#help')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#tip-pop')).toContainText('Help mode');
    await page.locator('#refresh-now').tap();
    await expect(page.locator('#tip-pop')).toHaveText('Re-scan the library now instead of waiting for the next automatic refresh');
    await expect(page.locator('.toast')).toHaveCount(0);            // explained, not run
    await page.locator('#help').tap();
    await expect(page.locator('#help')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('#tip-pop')).toHaveCount(0);
    const btn = page.locator('#live [data-global="qall_resume"]');
    await btn.dispatchEvent('pointerdown', { pointerType: 'touch', isPrimary: true, bubbles: true });
    await expect(page.locator('#tip-pop')).toHaveText('Start every stopped torrent again', { timeout: 3000 });
    await btn.dispatchEvent('pointerup', { pointerType: 'touch', isPrimary: true, bubbles: true });
    await page.keyboard.press('Escape');
    await expect(page.locator('#tip-pop')).toHaveCount(0);
  });

  test('the drawer and dialogs take the whole screen', async ({ page, login }) => {
    await login(); await page.goto('/'); await loaded(page);
    await page.locator('.item[data-id="2"]').click();
    const drawer = page.locator('dialog.drawer');
    await expect(drawer.locator('#dw-title')).toBeVisible();
    const box = await drawer.boundingBox();
    const vw = await page.evaluate(() => window.innerWidth);
    expect(box.width).toBeGreaterThanOrEqual(vw - 2);
    expect(await noSidewaysScroll(page)).toBe(true);
  });
});
