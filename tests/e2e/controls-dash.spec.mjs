// Every control on the dashboard, pressed, with the call it makes to the app behind it.
//
// The other dashboard specs check what a screen says; this one checks what a button *does*: for each
// control it clears the fake stack's call log, presses the control, and asserts the exact request that
// reached Radarr, Sonarr, Bazarr, Jellyfin, Jellyseerr, Prowlarr or qBittorrent. A control that is only
// meant to change the browser (theme, density, a filter) is asserted to reach no backend at all — that
// half matters just as much, because a "view" control that quietly writes is the worse bug.
import { test, expect, loaded } from './fixtures.mjs';

const MOVIE = '.item[data-kind="movie"][data-id="2"]';   // Blade Runner 2049 — downloading, stalled, one torrent
const SHOW = '.item[data-kind="tv"][data-id="12"]';      // The Expanse — S01 on disk, S02 missing, two torrents
const STALLED = 'aa11bb22cc33dd44ee55ff6677889900aabbccdd';
const S02E01 = '0011223344556677889900aabbccddeeff001122';
const S02E02 = '1122334455667788990011223344556677889900';

// qBittorrent's session login is a POST the panel makes on its own; it is not something a button did.
const isWrite = c => c[1] !== 'GET' && !/auth\/login/.test(c[2]);
const qbit = (path, body) => c => c[0] === 'qbittorrent' && c[1] === 'POST' && c[2] === `/api/v2/${path}` &&
  (!body || Object.entries(body).every(([k, v]) => c[3]?.[k] === v));
const cmd = (app, name) => c => c[0] === app && c[1] === 'POST' && /\/command$/.test(c[2]) && c[3]?.name === name;

/** Wait until the fake stack has recorded a call matching `pred`, then hand back every match. */
async function recorded(control, pred, what) {
  await expect.poll(async () => (await control()).calls.filter(pred).length, { message: what, timeout: 10000 }).toBeGreaterThan(0);
  return (await control()).calls.filter(pred);
}
/** Assert no button-driven write reached any backend. */
async function noWrites(control, what) {
  await new Promise(r => setTimeout(r, 500));
  expect((await control()).calls.filter(isWrite), what).toEqual([]);
}
/** The confirmation is built after /api/consequence answers, and the episode dialog is a dialog.dlg too:
 *  the confirm is the last dialog carrying #dlg-title, never simply "the dialog". */
async function confirmWith(page, verb, { cancel = false } = {}) {
  const dlg = page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') }).last();
  await dlg.waitFor();
  await dlg.getByRole('button', { name: cancel ? 'Cancel' : verb, exact: true }).click();
  await expect(page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') })).toHaveCount(0);
}

/** Reset the fake stack and reload, so a test that changed a title (unmonitoring it moves it off the
 *  board) starts its next family of controls against the dataset every other test sees. */
async function fresh(page, control) {
  await control({ reset: true });
  // Resetting the fake restores its data, but the panel is still holding the board it read a moment ago;
  // /api/refresh is what the header's re-scan control posts, and it drops that cache.
  await page.request.post('/api/refresh', { data: {} });
  await page.reload();
  await loaded(page);
  await page.locator('#live .tor').first().waitFor();
  await page.locator(MOVIE).waitFor();
  await page.locator(`#live .torrents > article.tor[data-key="${STALLED}"]`).waitFor();
  await control({ clear_calls: true });
}

test.describe('every control on the dashboard', () => {
  test.beforeEach(async ({ page, login, control }) => {
    await control({ reset: true });
    await login();
    // See fresh(): the fake's data is back, but the panel's own board cache is not, and a title the last
    // test unmonitored or purged would still be missing from this one's library.
    await page.request.post('/api/refresh', { data: {} });
    await page.goto('/');
    await loaded(page);
    await page.locator('#live .tor').first().waitFor();
    await page.locator('#attention .attn').first().waitFor();
    await control({ clear_calls: true });
  });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('the header tools change this browser and nothing else', async ({ page, control }) => {
    await page.locator('#theme').click();
    await expect(page.locator('#theme')).toHaveText('Theme: dark');
    await page.locator('#density').click();
    await expect(page.locator('#density')).toHaveText('Compact');
    await page.locator('#incognito').click();
    await expect(page.locator('#incognito')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#incognito').click();
    await page.locator('#refresh-now').click();
    await noWrites(control, 'theme, density, incognito and re-scan write to no app');
  });

  test('help mode explains a control instead of running it', async ({ page, control }) => {
    await page.locator('#help').click();
    await expect(page.locator('#help')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('[data-global="qall_pause"]').click();
    await noWrites(control, 'in help mode a press explains, it does not act');
    await page.locator('#help').click();
    await expect(page.locator('#help')).toHaveAttribute('aria-pressed', 'false');
  });

  test('the Dash stations filter the library and ask nothing of any app', async ({ page, control }) => {
    const station = page.locator('.station[data-stage="Downloading"]');
    await station.click();
    await expect(station).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#library .item:visible')).toHaveCount(2);   // Blade Runner 2049 and The Expanse
    await station.click();
    await expect(station).toHaveAttribute('aria-pressed', 'false');
    await noWrites(control, 'a station is a filter, not an action');
  });

  test('the client-wide controls reach qBittorrent, and Cancel reaches nothing', async ({ page, control }) => {
    await page.locator('[data-global="qall_pause"]').click();
    await confirmWith(page, 'Pause', { cancel: true });
    await noWrites(control, 'cancelling Pause all stops every torrent from being stopped');

    await page.locator('[data-global="qall_pause"]').click();
    await confirmWith(page, 'Pause');
    await recorded(control, qbit('torrents/stop', { hashes: 'all' }), 'Pause all stops every torrent');

    await control({ clear_calls: true });
    await page.locator('[data-global="qall_resume"]').click();
    await recorded(control, qbit('torrents/start', { hashes: 'all' }), 'Resume all starts every torrent');

    await control({ clear_calls: true });
    await page.locator('[data-global="alt_toggle"]').click();
    await recorded(control, qbit('transfer/toggleSpeedLimitsMode'), 'Alt-speed toggles the alternative limits');
  });

  test('Tune applies a preset to qBittorrent and to both arrs', async ({ page, control }) => {
    await page.locator('#tune').selectOption('Balanced');
    await confirmWith(page, 'Apply');
    const prefs = await recorded(control, qbit('app/setPreferences'), 'Balanced writes qBittorrent preferences');
    expect(JSON.parse(prefs[0][3].json).up_limit, 'Balanced caps upload at 1 MB/s').toBe(1048576);
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /downloadclient/.test(c[2]), 'and tells Radarr about the client');
    await recorded(control, qbit('torrents/start', { hashes: 'all' }), 'and resumes everything');
  });

  test('each button on a torrent row acts on that torrent alone', async ({ page, control }) => {
    test.slow();
    const row = page.locator(`#live .torrents > article.tor[data-key="${STALLED}"]`);
    for (const [label, path] of [['Pause', 'torrents/stop'], ['Resume', 'torrents/start'],
      ['Recheck', 'torrents/recheck'], ['Reannounce', 'torrents/reannounce']]) {
      await control({ clear_calls: true });
      await row.getByRole('button', { name: label, exact: true }).click();
      await recorded(control, qbit(path, { hashes: STALLED }), `${label} sends ${path} for this hash only`);
    }
    // Top and Bottom also pin (or unpin) the torrent, so the queue optimiser leaves a hand-placed one alone.
    await control({ clear_calls: true });
    await row.getByRole('button', { name: 'Top', exact: true }).click();
    await recorded(control, qbit('torrents/topPrio', { hashes: STALLED }), 'Top moves it to the front');
    await recorded(control, qbit('torrents/addTags', { hashes: STALLED, tags: 'pinned' }), 'and pins it');

    await control({ clear_calls: true });
    await row.getByRole('button', { name: 'Bottom', exact: true }).click();
    await recorded(control, qbit('torrents/bottomPrio', { hashes: STALLED }), 'Bottom moves it to the back');
    await recorded(control, qbit('torrents/removeTags', { hashes: STALLED, tags: 'pinned' }), 'and unpins it');

    await control({ clear_calls: true });
    await row.getByRole('button', { name: 'Force', exact: true }).click();
    await recorded(control, qbit('torrents/setForceStart', { hashes: STALLED, value: 'true' }), 'Force ignores the queue limits');
  });

  test('Remove keeps the files; Purge takes the title out of the whole stack', async ({ page, control }) => {
    const row = () => page.locator(`#live .torrents > article.tor[data-key="${STALLED}"]`);
    await row().getByRole('button', { name: 'Remove', exact: true }).click();
    await confirmWith(page, 'Remove');
    const del = await recorded(control, qbit('torrents/delete', { hashes: STALLED }), 'Remove deletes the torrent');
    expect(del[0][3].deleteFiles, 'Remove keeps the downloaded files').toBe('false');

    await fresh(page, control);

    await row().getByRole('button', { name: 'Purge', exact: true }).click();
    await confirmWith(page, 'Purge');
    const purge = await recorded(control, qbit('torrents/delete', { hashes: STALLED }), 'Purge deletes the torrent');
    expect(purge[0][3].deleteFiles, 'Purge deletes the files too').toBe('true');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'DELETE' && /\/movie\/2\?deleteFiles=true/.test(c[2]), 'and drops the film from Radarr');
    await recorded(control, c => c[0] === 'bazarr' && c[1] === 'POST' && c[3]?.taskid === 'update_movies', 'and tells Bazarr');
    await recorded(control, c => c[0] === 'jellyfin' && c[2] === '/Library/Refresh', 'and asks Jellyfin to re-scan');
  });

  test('a torrent group acts on every hash it holds, in one call', async ({ page, control }) => {
    const group = page.locator('#live section.tor-group').first();
    await group.locator('.tg-caret').click();
    await noWrites(control, 'expanding a group is not an action');
    const both = `${S02E01}|${S02E02}`;
    for (const [label, path] of [['Pause all', 'torrents/stop'], ['Resume all', 'torrents/start']]) {
      await control({ clear_calls: true });
      await group.locator('.tg-actions').getByRole('button', { name: label, exact: true }).click();
      await recorded(control, qbit(path, { hashes: both }), `group ${label} addresses both hashes at once`);
    }
    await control({ clear_calls: true });
    await group.locator('.tg-actions').getByRole('button', { name: 'Force all', exact: true }).click();
    await recorded(control, qbit('torrents/setForceStart', { hashes: both, value: 'true' }), 'Force all forces both');
  });

  test("each attention item's actions do what its label says", async ({ page, control }) => {
    const stalled = page.locator('#attention .attn').filter({ hasText: 'stalled' }).first();
    await stalled.getByRole('button', { name: 'Reannounce' }).click();
    await recorded(control, qbit('torrents/reannounce', { hashes: STALLED }), 'Reannounce asks the trackers again');

    await control({ clear_calls: true });
    await stalled.getByRole('button', { name: 'Blocklist & retry' }).click();
    await confirmWith(page, 'Blocklist');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'DELETE' && /queue\/501\?removeFromClient=true&blocklist=true/.test(c[2]), 'the release is blocklisted');
    await recorded(control, cmd('radarr', 'MoviesSearch'), 'and Radarr searches for another');

    await fresh(page, control);
    // "Indexers unavailable due to failures" also carries the word, so the unavailable *title* is named.
    await page.locator('#attention .attn').filter({ hasText: 'Coherence' }).getByRole('button', { name: 'Search again' }).click();
    const search = await recorded(control, cmd('radarr', 'MoviesSearch'), 'Search again searches for that title');
    expect(search[0][3].movieIds, 'and for that title only').toEqual([3]);

    await control({ clear_calls: true });
    const req = page.locator('#attention .attn').filter({ hasText: 'requested' }).first();
    await req.getByRole('button', { name: 'Approve' }).click();
    await recorded(control, c => c[0] === 'jellyseerr' && c[1] === 'POST' && c[2] === '/api/v1/request/31/approve', 'Approve approves that request');
  });

  test('a request can be declined, and the confirmation says so first', async ({ page, control }) => {
    const req = page.locator('#attention .attn').filter({ hasText: 'requested' }).first();
    await req.getByRole('button', { name: 'Decline' }).click();
    await confirmWith(page, 'Decline', { cancel: true });
    await noWrites(control, 'cancelling a decline declines nothing');
    await req.getByRole('button', { name: 'Decline' }).click();
    await confirmWith(page, 'Decline');
    await recorded(control, c => c[0] === 'jellyseerr' && c[2] === '/api/v1/request/31/decline', 'Decline declines that request');
  });

  test('the library tools filter and sort in the browser; only adoption talks to the arrs', async ({ page, control }) => {
    await page.locator('#filter').fill('coher');
    await expect(page.locator('#library .item:visible')).toHaveCount(1);
    await page.locator('#filter').fill('');
    await page.locator('#fstage').selectOption('Downloading');
    await expect(page).toHaveURL(/stage=Downloading/);
    await page.locator('#fstage').selectOption('');
    await page.locator('#library .stage-toggle').first().click();
    await page.locator('#library .ssort').first().selectOption({ index: 1 });
    await noWrites(control, 'filtering, sorting and folding a section are browser-side');

    await page.locator('#import-btn').click();
    await confirmWith(page, 'Import');
    // A folder that holds media is adopted; the empty one a purge left behind is probed and skipped.
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'POST' && c[2] === '/api/v3/movie' && c[3]?.title === 'Adoptable Title', 'the folder with media in it is adopted');
    const ghost = (await control()).calls.filter(c => c[1] === 'POST' && /\/(movie|series)$/.test(c[2]) && c[3]?.title === 'Purged Leftover');
    expect(ghost, 'the empty folder a purge left is not re-adopted').toEqual([]);
  });

  test('the bulk bar acts on the selection, one call per title', async ({ page, control }) => {
    test.slow();
    const bar = page.locator('#selbar');
    const pick = async () => { await page.locator(MOVIE + ' .sel').check(); await expect(bar).toBeVisible(); };
    for (const [label, pred, what] of [
      ['Retry', cmd('radarr', 'MoviesSearch'), 'Retry searches'],
      ['Refresh', cmd('radarr', 'RefreshMovie'), 'Refresh re-scans'],
    ]) {
      await control({ clear_calls: true });
      await pick();
      await bar.getByRole('button', { name: label, exact: true }).click();
      const seen = await recorded(control, pred, what);
      expect(seen[0][3].movieIds, `${label} names the selected title`).toEqual([2]);
    }
    for (const [label, monitored] of [['Monitor', true], ['Unmonitor', false]]) {
      await fresh(page, control);
      await pick();
      await bar.getByRole('button', { name: label, exact: true }).click();
      const put = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && c[2] === '/api/v3/movie/2', `${label} writes the title back`);
      expect(put.at(-1)[3].monitored, `${label} sets monitored ${monitored}`).toBe(monitored);
    }
    // Unmonitor left the title off the board; put the dataset back before the torrent half.
    await fresh(page, control);
    for (const [label, path] of [['Top', 'torrents/topPrio'], ['Bottom', 'torrents/bottomPrio'],
      ['Pause', 'torrents/stop'], ['Resume', 'torrents/start']]) {
      await control({ clear_calls: true });
      await pick();
      await bar.getByRole('button', { name: label, exact: true }).click();
      await recorded(control, qbit(path, { hashes: STALLED }), `bulk ${label} reaches the title's torrent`);
    }
  });

  test('the bulk bar Blocklist asks first, and names what it blocks', async ({ page, control }) => {
    const bar = page.locator('#selbar');
    await page.locator(MOVIE + ' .sel').check();
    await bar.getByRole('button', { name: 'Blocklist', exact: true }).click();
    const dlg = page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') }).last();
    await expect(dlg.locator('#dlg-title')).toHaveText('Blocklist 1 title');
    await expect(dlg.locator('p')).toContainText('never picked again');
    await expect(dlg.locator('p'), 'and says which title').toContainText('Blade Runner 2049');
    await confirmWith(page, 'Blocklist', { cancel: true });
    await noWrites(control, 'cancelling blocklists nothing');

    await bar.getByRole('button', { name: 'Blocklist', exact: true }).click();
    await confirmWith(page, 'Blocklist');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'DELETE' && /queue\/501\?removeFromClient=true&blocklist=true/.test(c[2]), 'confirming blocklists the release');
    await recorded(control, cmd('radarr', 'MoviesSearch'), 'and searches for another');
  });

  test('the bulk bar Purge names the titles first, and Clear only deselects', async ({ page, control }) => {
    const bar = page.locator('#selbar');
    await page.locator(MOVIE + ' .sel').check();
    await bar.getByRole('button', { name: 'Purge', exact: true }).click();
    await confirmWith(page, 'Purge', { cancel: true });
    await noWrites(control, 'a cancelled bulk purge deletes nothing');

    await bar.getByRole('button', { name: 'Purge', exact: true }).click();
    await confirmWith(page, 'Purge');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'DELETE' && /\/movie\/2\?deleteFiles=true/.test(c[2]), 'bulk Purge drops the title');

    await fresh(page, control);
    await page.locator(MOVIE + ' .sel').check();
    await bar.getByRole('button', { name: 'Clear', exact: true }).click();
    await expect(bar).toBeHidden();
    await noWrites(control, 'Clear deselects and nothing more');
  });

  test('the quality chip and the release picker write through the arr the title lives in', async ({ page, control }) => {
    await page.locator(MOVIE + ' .qchip').click();
    const picker = page.locator('dialog.dlg').last();
    await picker.locator('select').selectOption({ label: 'HD-1080p' });
    await picker.getByRole('button', { name: 'Set' }).click();
    const put = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && c[2] === '/api/v3/movie/2', 'the chip changes the profile for that title');
    expect(put.at(-1)[3].qualityProfileId, 'to the profile picked').toBe(2);

    await control({ clear_calls: true });
    await page.locator(MOVIE + ' .sel').check();
    await page.locator('#selbar').getByRole('button', { name: 'Search…', exact: true }).click();
    const rel = page.locator('dialog.dlg').last();
    await rel.getByRole('button', { name: 'Grab' }).first().click();
    const grab = await recorded(control, c => c[0] === 'radarr' && c[1] === 'POST' && c[2] === '/api/v3/release', 'Grab sends that exact release');
    expect(grab[0][3], 'with the guid and indexer the list showed').toMatchObject({ guid: 'g-1', indexerId: 1 });
  });
});
