// Every control behind a title: the drawer, the season tree, the episode dialog and the episode toolbar.
//
// Same contract as controls-dash.spec.mjs — press the control, assert the request that reached the app
// behind it. These are the controls that write to Radarr, Sonarr and Bazarr per title, so each assertion
// names the id it must carry: acting on the wrong title is the failure that matters here.
import { test, expect, loaded } from './fixtures.mjs';

const MOVIE = '.item[data-kind="movie"][data-id="2"]';   // Blade Runner 2049
const SHOW = '.item[data-kind="tv"][data-id="12"]';      // The Expanse
const STALLED = 'aa11bb22cc33dd44ee55ff6677889900aabbccdd';
const S02E02 = '1122334455667788990011223344556677889900';

const isWrite = c => c[1] !== 'GET' && !/auth\/login/.test(c[2]);
const qbit = (path, body) => c => c[0] === 'qbittorrent' && c[1] === 'POST' && c[2] === `/api/v2/${path}` &&
  (!body || Object.entries(body).every(([k, v]) => c[3]?.[k] === v));
const cmd = (app, name) => c => c[0] === app && c[1] === 'POST' && /\/command$/.test(c[2]) && c[3]?.name === name;

async function recorded(control, pred, what) {
  await expect.poll(async () => (await control()).calls.filter(pred).length, { message: what, timeout: 10000 }).toBeGreaterThan(0);
  return (await control()).calls.filter(pred);
}
async function noWrites(control, what) {
  await new Promise(r => setTimeout(r, 500));
  expect((await control()).calls.filter(isWrite), what).toEqual([]);
}
async function confirmWith(page, verb, { cancel = false } = {}) {
  const dlg = page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') }).last();
  await dlg.waitFor();
  await dlg.getByRole('button', { name: cancel ? 'Cancel' : verb, exact: true }).click();
  await expect(page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') })).toHaveCount(0);
}

test.describe('every control behind a title', () => {
  test.beforeEach(async ({ page, login, control }) => {
    await control({ reset: true });
    await login();
    // Resetting the fake restores its data; the panel is still holding the board it read during the last
    // test, so a title that test unmonitored or purged would still be missing from this one's library.
    await page.request.post('/api/refresh', { data: {} });
    await page.goto('/');
    await loaded(page);
    await page.locator(MOVIE).waitFor();
    await control({ clear_calls: true });
  });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  // The drawer draws its head before its sections, so #dw-title is not enough to click by: wait for a
  // button that only the loaded body carries.
  const openDrawer = async (page, sel) => {
    await page.locator(sel + ' .openx').click();
    const dw = page.locator('dialog.drawer');
    await dw.locator('#dw-title').waitFor();
    await dw.getByRole('button', { name: 'Auto-search' }).waitFor();
    return dw;
  };
  const reopen = async (page, control, sel) => {
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog.drawer')).toHaveCount(0);
    await control({ clear_calls: true });
    return openDrawer(page, sel);
  };
  /** Run a drawer control and wait for the refresh it triggers to land, which is the ordinary sequence.
   *  Closing the drawer mid-refresh is its own case, asserted at the end of this file. */
  const settled = async (page, fn) => {
    const landed = page.waitForResponse(r => /\/api\/item/.test(r.url()), { timeout: 8000 }).catch(() => null);
    await fn();
    await landed;
  };

  test("the drawer's selects write the title back to Radarr, each changing one field", async ({ page, control }) => {
    let dw = await openDrawer(page, MOVIE);
    await settled(page, () => dw.locator('#dw-profileId').selectOption({ label: 'HD-1080p' }));
    let put = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && c[2] === '/api/v3/movie/2', 'the quality profile is written');
    expect(put.at(-1)[3].qualityProfileId).toBe(2);

    dw = await reopen(page, control, MOVIE);
    await settled(page, () => dw.locator('#dw-value').selectOption('inCinemas'));
    put = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && c[2] === '/api/v3/movie/2', 'minimum availability is written');
    expect(put.at(-1)[3].minimumAvailability).toBe('inCinemas');

    dw = await reopen(page, control, MOVIE);
    await settled(page, () => dw.locator('#dw-path').selectOption({ index: 0 }));
    // moveFiles=false: changing the root folder must not start shifting files around behind the user.
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && c[2] === '/api/v3/movie/2?moveFiles=false', 'the root folder is written, without moving files');
  });

  test("the drawer's monitor toggle flips exactly that title", async ({ page, control }) => {
    const dw = await openDrawer(page, MOVIE);
    await dw.getByRole('button', { name: /Monitored|Not monitored/ }).click();
    const put = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && c[2] === '/api/v3/movie/2', 'monitoring is written');
    expect(put.at(-1)[3].monitored, 'it was monitored, so the toggle unmonitors it').toBe(false);
  });

  test("the drawer's search, refresh and blocklist reach the right arr command", async ({ page, control }) => {
    let dw = await openDrawer(page, MOVIE);
    await settled(page, () => dw.getByRole('button', { name: 'Auto-search' }).click());
    expect((await recorded(control, cmd('radarr', 'MoviesSearch'), 'Auto-search searches'))[0][3].movieIds).toEqual([2]);

    dw = await reopen(page, control, MOVIE);
    await settled(page, () => dw.getByRole('button', { name: 'Refresh', exact: true }).click());
    expect((await recorded(control, cmd('radarr', 'RefreshMovie'), 'Refresh re-scans'))[0][3].movieIds).toEqual([2]);

    dw = await reopen(page, control, MOVIE);
    await dw.getByRole('button', { name: 'Blocklist & retry' }).click();
    await confirmWith(page, 'Blocklist');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'DELETE' && /queue\/501\?removeFromClient=true&blocklist=true/.test(c[2]), 'the release is blocklisted');
    await recorded(control, cmd('radarr', 'MoviesSearch'), 'and another is searched for');
  });

  test('the subtitle controls go to Bazarr, and only for this title', async ({ page, control }) => {
    let dw = await openDrawer(page, MOVIE);
    await settled(page, () => dw.getByRole('button', { name: 'Fetch subs' }).click());
    await recorded(control, c => c[0] === 'bazarr' && c[1] === 'POST' && c[3]?.taskid === 'wanted_search_missing_subtitles_movies', 'Fetch subs runs the movie task');

    dw = await reopen(page, control, MOVIE);
    await dw.getByRole('button', { name: 'Manual search…' }).click();
    await recorded(control, c => c[0] === 'bazarr' && /providers\/movies\?radarrid=2/.test(c[2]), 'Manual search lists this film\'s candidates');

    // A show asks for the series task instead — the same button, a different call.
    await page.keyboard.press('Escape');
    await control({ clear_calls: true });
    dw = await openDrawer(page, SHOW);
    await settled(page, () => dw.getByRole('button', { name: 'Fetch subs' }).click());
    await recorded(control, c => c[0] === 'bazarr' && c[3]?.taskid === 'wanted_search_missing_subtitles_series', 'a show runs the series task');
  });

  test("the drawer's torrent block acts on that title's torrent, caps included", async ({ page, control }) => {
    test.slow();
    let dw = await openDrawer(page, MOVIE);
    for (const [label, path] of [['Pause', 'torrents/stop'], ['Resume', 'torrents/start'],
      ['Recheck', 'torrents/recheck'], ['Reannounce', 'torrents/reannounce'],
      ['Top', 'torrents/topPrio'], ['Bottom', 'torrents/bottomPrio']]) {
      await control({ clear_calls: true });
      await dw.locator('.tor-inline').getByRole('button', { name: label, exact: true }).click();
      await recorded(control, qbit(path, { hashes: STALLED }), `the drawer's ${label} reaches this torrent`);
    }
    await control({ clear_calls: true });
    await dw.getByRole('button', { name: 'Force start' }).click();
    await recorded(control, qbit('torrents/setForceStart', { hashes: STALLED, value: 'true' }), 'Force start forces it');

    // The caps are MB/s in the panel and bytes/s in qBittorrent; the conversion is the thing worth pinning.
    await control({ clear_calls: true });
    await dw.locator('input[aria-label="Download cap, MB/s"]').fill('2.5');
    await dw.locator('input[aria-label="Download cap, MB/s"]').blur();
    expect((await recorded(control, qbit('torrents/setDownloadLimit'), '2.5 MB/s becomes bytes/s'))[0][3].limit).toBe('2500000');

    await control({ clear_calls: true });
    await dw.locator('input[aria-label="Upload cap, MB/s"]').fill('1');
    await dw.locator('input[aria-label="Upload cap, MB/s"]').blur();
    expect((await recorded(control, qbit('torrents/setUploadLimit'), '1 MB/s becomes bytes/s'))[0][3].limit).toBe('1000000');
  });

  test("a show's drawer offers what a film's cannot: series type, monitor-all, and a season to search", async ({ page, control }) => {
    let dw = await openDrawer(page, SHOW);
    await settled(page, () => dw.locator('#dw-value').selectOption('anime'));
    let put = await recorded(control, c => c[0] === 'sonarr' && c[1] === 'PUT' && c[2] === '/api/v3/series/12', 'the series type is written');
    expect(put.at(-1)[3].seriesType).toBe('anime');

    dw = await reopen(page, control, SHOW);
    await settled(page, () => dw.getByRole('button', { name: 'Monitor all + search' }).click());
    const mon = await recorded(control, c => c[0] === 'sonarr' && c[1] === 'PUT' && c[2] === '/api/v3/episode/monitor', 'every episode is monitored');
    expect(mon[0][3].monitored).toBe(true);
    expect(mon[0][3].episodeIds, 'all thirteen of them, both seasons').toHaveLength(13);
    await recorded(control, cmd('sonarr', 'MissingEpisodeSearch'), 'and the gaps are searched for');

    dw = await reopen(page, control, SHOW);
    await dw.locator('#dwseason').selectOption('2');
    await dw.getByRole('button', { name: 'Search…', exact: true }).click();
    const picker = page.locator('dialog.dlg').last();
    await picker.getByRole('button', { name: 'Grab' }).first().click();
    await recorded(control, c => c[0] === 'sonarr' && /release\?seriesId=12&seasonNumber=2/.test(c[2]), 'the picker asks for that season');
    expect((await recorded(control, c => c[0] === 'sonarr' && c[1] === 'POST' && c[2] === '/api/v3/release', 'and Grab sends that release'))[0][3])
      .toMatchObject({ guid: 'g-2', indexerId: 1 });
  });

  // ---- the season tree and what hangs off it
  const expand = async page => {
    if (!(await page.locator(SHOW + ' .expand').count())) {
      await page.locator(SHOW + ' .info').click();
      await page.locator(SHOW + ' .expand .season-row').first().waitFor();
    }
    return page.locator(SHOW + ' .expand');
  };

  test('folding a season is browser-side; ticking "tracked" writes the season and searches it', async ({ page, control }) => {
    const tree = await expand(page);
    await tree.locator('.scaret').first().click();
    await tree.getByRole('button', { name: 'Season 1' }).click();
    await noWrites(control, 'a caret and a season name only fold the list');

    await tree.locator('input[aria-label="Track Season 2"]').uncheck();
    const put = await recorded(control, c => c[0] === 'sonarr' && c[1] === 'PUT' && c[2] === '/api/v3/series/12', 'the season is written back on the series');
    expect(put.at(-1)[3].seasons.find(s => s.seasonNumber === 2).monitored, 'season 2 is no longer monitored').toBe(false);
  });

  test('the episode dialog acts on that one episode', async ({ page, control }) => {
    const tree = await expand(page);
    await tree.locator('button[aria-label="Controls for S01E01"]').click();
    const dlg = page.locator('dialog.dlg').first();
    await dlg.getByRole('button', { name: 'Search', exact: true }).waitFor();

    await dlg.getByRole('button', { name: 'Search', exact: true }).click();
    expect((await recorded(control, cmd('sonarr', 'EpisodeSearch'), 'Search searches that episode'))[0][3].episodeIds).toEqual([1101]);

    await control({ clear_calls: true });
    await dlg.locator('input[type=checkbox]').first().uncheck();
    const mon = await recorded(control, c => c[0] === 'sonarr' && c[2] === '/api/v3/episode/monitor', 'Track untracks it');
    expect(mon.at(-1)[3]).toMatchObject({ episodeIds: [1101], monitored: false });

    await control({ clear_calls: true });
    await dlg.getByRole('button', { name: 'Subtitles…' }).click();
    await recorded(control, c => c[0] === 'bazarr' && /providers\/episodes\?episodeid=1101/.test(c[2]), 'Subtitles lists that episode\'s candidates');
  });

  test('the episode dialog deletes a file, and purges the episode through the whole stack', async ({ page, control }) => {
    let tree = await expand(page);
    await tree.locator('button[aria-label="Controls for S01E01"]').click();
    let dlg = page.locator('dialog.dlg').first();
    await dlg.getByRole('button', { name: 'Delete file' }).click();
    await confirmWith(page, 'Delete');
    await recorded(control, c => c[0] === 'sonarr' && c[1] === 'DELETE' && c[2] === '/api/v3/episodefile/7101', 'the file for that episode is deleted');

    await control({ reset: true });
    await page.request.post('/api/refresh', { data: {} });
    await page.reload();
    await loaded(page);
    await control({ clear_calls: true });

    tree = await expand(page);
    await tree.locator('button[aria-label="Controls for S01E01"]').click();
    dlg = page.locator('dialog.dlg').first();
    await dlg.getByRole('button', { name: 'Purge', exact: true }).click();
    await confirmWith(page, 'Purge');
    await recorded(control, c => c[0] === 'sonarr' && c[1] === 'DELETE' && c[2] === '/api/v3/episodefile/7101', 'purge deletes the file');
    const mon = await recorded(control, c => c[0] === 'sonarr' && c[2] === '/api/v3/episode/monitor', 'and stops tracking the episode');
    expect(mon.at(-1)[3]).toMatchObject({ episodeIds: [1101], monitored: false });
    await recorded(control, c => c[0] === 'bazarr' && c[3]?.taskid === 'update_series', 'and tells Bazarr');
    await recorded(control, c => c[0] === 'jellyfin' && c[2] === '/Library/Refresh', 'and asks Jellyfin to re-scan');
  });

  test('the episode toolbar acts on the ticked episodes; its torrent half needs a torrent', async ({ page, control }) => {
    test.slow();
    const tree = await expand(page);
    // S01E01 is on disk with nothing downloading, so every torrent control is disabled rather than silently
    // doing nothing — the state a user can see is the state the server would enforce.
    await tree.locator('input[aria-label="Tick S01E01"]').check();
    const bar = tree.locator('.ep-bar');
    for (const l of ['Top', 'Bottom', 'Pause', 'Resume', 'Force', 'Remove'])
      await expect(bar.getByRole('button', { name: l, exact: true }), `${l} is disabled without a torrent`).toBeDisabled();
    await expect(bar.getByRole('button', { name: 'Delete files', exact: true }), 'but its file can be deleted').toBeEnabled();

    await bar.getByRole('button', { name: 'Search', exact: true }).click();
    expect((await recorded(control, cmd('sonarr', 'EpisodeSearch'), 'Search searches the ticked episode'))[0][3].episodeIds).toEqual([1101]);

    await control({ clear_calls: true });
    await tree.locator('input[aria-label="Tick S01E01"]').uncheck();
    // S02E02 is downloading, so the torrent half comes alive and the file half goes quiet.
    await tree.locator('input[aria-label="Tick S02E02"]').check();
    await expect(bar.getByRole('button', { name: 'Delete files', exact: true }), 'nothing on disk to delete').toBeDisabled();
    for (const [label, path] of [['Top', 'torrents/topPrio'], ['Pause', 'torrents/stop'], ['Resume', 'torrents/start']]) {
      await control({ clear_calls: true });
      await bar.getByRole('button', { name: label, exact: true }).click();
      await recorded(control, qbit(path, { hashes: S02E02 }), `toolbar ${label} reaches that episode's torrent`);
    }
  });

  test('closing the drawer while its refresh is in flight leaves no error behind', async ({ page, errors }) => {
    errors.allow.push(/\/api\/item/);   // the refresh is aborted on purpose below; its failure is the setup
    // refreshDrawer's guard runs before its await, and the drawer's close handler nulls dwEl; without the
    // second guard in its catch, a fetch that fails after the close threw an uncaught TypeError. The
    // errors fixture fails this test on any page error, which is the whole assertion.
    await page.locator(MOVIE + ' .openx').click();
    const dw = page.locator('dialog.drawer');
    await dw.getByRole('button', { name: 'Auto-search' }).waitFor();
    await page.route('**/api/item*', async route => { await new Promise(r => setTimeout(r, 2000)); await route.abort(); });
    await dw.getByRole('button', { name: 'Auto-search' }).click();
    await page.waitForTimeout(300);
    await page.keyboard.press('Escape');
    await expect(dw).toHaveCount(0);
    await page.waitForTimeout(3000);   // the aborted fetch rejects into refreshDrawer's catch in here
  });
});
