// The granular controls: the Settings link, per-episode torrent labels and groups in Live, the inline season tree and
// the episode dialog, the system monitor, presets, and the Subtitles providers apply flow.
import { test, expect, loaded } from './fixtures.mjs';

test.describe('granular controls', () => {
  test.beforeEach(async ({ page, login, control }) => { await control({ clear_calls: true }); await login(); await page.goto('/'); await loaded(page); });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('the Settings link in the header is a page, not a section', async ({ page }) => {
    await page.locator('.secnav a[href="/settings"]').click();
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Presets');
  });

  test('Live labels each episode and folds a show into a group with controls for all of it', async ({ page }) => {
    const group = page.locator('#live .tor-group');
    await expect(group).toHaveCount(1);
    await expect(group.locator('.tg-head .tor-title')).toContainText('The Expanse');
    await expect(group.locator('.tg-head .tor-title')).toContainText('2 torrents · S02');
    await expect(group.locator('.tg-rows')).toBeHidden();                       // collapsed by default
    await expect(group.locator('.tg-agg')).toContainText('1/2 done · 1 downloading');
    await group.locator('.tg-caret').click();
    await expect(group.locator('.tg-rows')).toBeVisible();
    await expect(group.locator('.tor .tor-title b')).toHaveText(['S02E01', 'S02E02 · Doors & Corners']);
    await expect(page.locator('#live > .live-grid .tor:not(.tor-group .tor)').first().getByRole('button', { name: 'Purge' })).toBeVisible();
    await group.getByRole('button', { name: 'Purge all' }).click();
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Purge The Expanse S02');
    await expect(dlg.locator('p')).toContainText('S02E01 (1 episode)');
    await expect(dlg.locator('p')).toContainText('S02E02 (1 episode)');
    await dlg.getByRole('button', { name: 'Cancel' }).click();
    await group.getByRole('button', { name: 'Pause all' }).click();
    await expect(page.locator('.toast-ok')).toHaveText('Paused');
  });

  test('a show expands into its episode list; ticking episodes brings up the toolbar; › opens the episode dialog', async ({ page, control }) => {
    const row = page.locator('.item[data-id="12"]');
    await row.locator('.info').click();
    await expect(row.locator('.info')).toHaveAttribute('aria-expanded', 'true');
    const seasons = row.locator('.expand .season');
    await expect(seasons).toHaveCount(2);
    await expect(seasons.nth(1)).toContainText('Season 2');
    await expect(seasons.nth(1)).toContainText('2 downloading');
    await expect(seasons.first()).toContainText('10/10 eps · 30.0 GB');                 // a season's size on its header
    const s1 = seasons.first().locator('.ep');
    await expect(s1.first().locator('.ep-size')).toHaveText('3.0 GB');                 // and per episode: size…
    await expect(s1.first().locator('.pill').last()).toHaveText(/subs/);               // …and whether it has subtitles
    await expect(s1.nth(9).locator('.pill').last()).toHaveText(/no subs/);
    const eps = seasons.nth(1).locator('.ep');                                   // every tracked season opens with its episodes
    await expect(seasons.nth(1).locator('.scaret')).toHaveAttribute('aria-expanded', 'true');
    await expect(eps).toHaveCount(3);
    await expect(eps.first()).toContainText('S02E01');
    await expect(eps.nth(1).locator('.pill')).toHaveText(/30 %/);
    await expect(row.locator('.ep-bar')).toBeHidden();                           // nothing ticked: no toolbar
    await eps.first().locator('.ep-sel').check();
    await eps.nth(1).locator('.ep-sel').check();
    const bar = row.locator('.ep-bar');
    await expect(bar).toBeVisible();
    await expect(bar.locator('.ep-bar-n')).toHaveText('2 episodes');
    await expect(row.locator('.sel')).toHaveJSProperty('indeterminate', true);   // some, not all: the show's own box shows it
    await bar.getByRole('button', { name: 'Search' }).click();
    await expect(page.locator('.toast-ok')).toHaveText('Searching 2 episodes');
    await expect.poll(async () => (await control()).calls.filter(c => c[0] === 'sonarr' && c[3]?.name === 'EpisodeSearch').map(c => c[3].episodeIds)).toEqual([[1201, 1202]]);
    await bar.getByRole('button', { name: 'Purge' }).click();
    const confirm2 = page.locator('dialog.dlg').last();
    await expect(confirm2.locator('#dlg-title')).toHaveText('Purge S02 E01–E02 of The Expanse');
    await confirm2.getByRole('button', { name: 'Cancel' }).click();
    await row.locator('.sel').check();                                           // the show's box ticks every episode
    await expect(bar.locator('.ep-bar-n')).toHaveText('13 episodes');
    await eps.nth(2).locator('.ep-sel').uncheck();                               // …and one can be left out again
    await expect(bar.locator('.ep-bar-n')).toHaveText('12 episodes');
    await expect(row.locator('.sel')).toHaveJSProperty('indeterminate', true);
    await bar.getByRole('button', { name: 'Clear' }).click();
    await expect(bar).toBeHidden();
    await eps.first().locator('.ep-x').click();
    const dlg = page.locator('dialog.dlg').last();
    await expect(dlg.locator('h2')).toHaveText('The Expanse — S02E01 Episode 1');
    await expect(dlg.getByRole('heading', { name: 'Torrent' })).toBeVisible();
    await dlg.getByRole('button', { name: 'Search' }).click();
    await expect(page.locator('.toast-ok').last()).toHaveText('Searching 1 episode');
    await dlg.locator('section.dwsec', { has: page.getByRole('heading', { name: 'Episode' }) }).getByRole('button', { name: 'Purge' }).click();   // the episode's Purge, not its torrent's
    const confirm = page.locator('dialog.dlg').last();
    await expect(confirm.locator('#dlg-title')).toHaveText('Purge S02E01 of The Expanse');
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await page.keyboard.press('Escape');
    await row.locator('.openx').click();
    await expect(page.locator('dialog.drawer #dw-title')).toHaveText(/^The Expanse/);
    await expect(page.locator('dialog.drawer #dwseasons .season')).toHaveCount(2);
  });

  test('the system monitor: a compact line that expands to every container with its task and last log line', async ({ page }) => {
    await expect(page.locator('#system .sys-chip')).toHaveCount(await page.locator('#system .sys-chip').count());
    await expect(page.locator('#system .sys-chip').first()).toContainText('CPU');
    await expect(page.locator('#system .sys-chip').first().locator('.sys-word')).toHaveText(/fine|busy|maxed out/);
    await expect(page.locator('#system .sys-headline')).toBeVisible();
    await expect(page.locator('#system .sys-line')).toContainText('RAM');
    await expect(page.locator('#system .sys-line')).toContainText('disk');
    await expect(page.locator('#sys-detail')).toBeHidden();
    await page.locator('.sys-toggle').click();
    await expect(page.locator('#sys-detail')).toBeVisible();
    await expect(page.locator('.sys-table tbody tr')).toHaveCount(11);
    const radarr = page.locator('.sys-table tbody tr', { hasText: 'radarr' });
    await expect(radarr.locator('.sys-task')).toHaveText('RSS sync, searching');
    await expect(radarr.locator('.sys-log')).toHaveText('[Info] radarr is running');
    await expect(page.locator('.sys-table tbody tr', { hasText: 'qbittorrent' }).locator('.sys-task')).toHaveText('2 downloading · 2 seeding');
    await page.reload(); await loaded(page);
    await expect(page.locator('#sys-detail')).toBeVisible();   // remembered
  });

  test('Tune applies a preset from the dashboard after naming what it does', async ({ page, control }) => {
    await expect(page.locator('#tune optgroup')).toHaveCount(2);
    await page.locator('#tune').selectOption('Everything paused');
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Apply preset Everything paused');
    await expect(dlg.locator('p')).toContainText('Stops every download');
    await dlg.getByRole('button', { name: 'Apply' }).click();
    await expect(page.locator('.toast-ok')).toContainText('Everything paused applied');
    await expect.poll(async () => (await control()).calls.some(c => c[0] === 'qbittorrent' && /torrents\/stop/.test(c[2]) && c[3]?.hashes === 'all')).toBe(true);
  });
});

test.describe('settings, granular', () => {
  test.beforeEach(async ({ page, login, control }) => { await control({ clear_calls: true }); await login(); });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('Presets is the first group: every preset with a description and an Apply that confirms', async ({ page, control }) => {
    await page.goto('/settings');
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Presets');
    await expect(page.locator('.preset')).toHaveCount(8);
    await expect(page.locator('.preset').first()).toContainText('Everything paused');
    await page.locator('.preset', { hasText: /^Balanced/ }).getByRole('button', { name: 'Apply' }).click();   // not "1080p balanced"
    const dlg = page.locator('dialog.dlg');
    await expect(dlg.locator('#dlg-title')).toHaveText('Apply preset Balanced');
    await dlg.getByRole('button', { name: 'Apply' }).click();
    await expect(page.locator('.toast-ok')).toContainText('Balanced applied');
    await expect.poll(async () => (await control()).calls.filter(c => /setPreferences/.test(c[2])).map(c => JSON.parse(c[3].json).up_limit)).toEqual([1048576]);
  });

  test('ticking a subtitle provider and applying it never asks about unapplied changes', async ({ page, control }) => {
    await page.goto('/settings#subtitles');
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Subtitles');
    await page.locator('.provgrid label', { hasText: 'subdl' }).locator('input').check();
    await expect(page.locator('#applybar')).toBeVisible();
    await expect(page.locator('#applydiff')).toContainText('enabled_providers');
    await page.locator('#apply').click();
    await expect(page.locator('.toast-ok')).toHaveText('Bazarr settings saved');
    await expect(page.locator('dialog.dlg')).toHaveCount(0);
    await expect(page.locator('#applybar')).toBeHidden();
    await expect.poll(async () => (await control()).calls.some(c => c[0] === 'bazarr' && c[1] === 'POST' && c[2] === '/api/system/settings')).toBe(true);
    await page.locator('#grouplist a[href="#downloads"]').click();
    await expect(page.locator('#pane .pane-head h2')).toHaveText('Downloads');   // and leaving the group is silent too
    await expect(page.locator('dialog.dlg')).toHaveCount(0);
  });
});
