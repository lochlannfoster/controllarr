// Every control on /settings, pressed, with the call it makes to the app behind it.
//
// The settings page writes more per press than anything on the dashboard — a group's Apply sends the
// whole group — so each test here says which app received what. Two things are asserted as carefully as
// the writes: that Preview and the snapshot export write nothing at all, and that a group with no pending
// change offers no Apply.
import { test, expect } from './fixtures.mjs';

const isWrite = c => c[1] !== 'GET' && !/auth\/login/.test(c[2]);
const qbit = path => c => c[0] === 'qbittorrent' && c[1] === 'POST' && c[2] === `/api/v2/${path}`;
const cmd = (app, name) => c => c[0] === app && c[1] === 'POST' && /\/command$/.test(c[2]) && c[3]?.name === name;

async function recorded(control, pred, what) {
  await expect.poll(async () => (await control()).calls.filter(pred).length, { message: what, timeout: 8000 }).toBeGreaterThan(0);
  return (await control()).calls.filter(pred);
}
async function noWrites(control, what) {
  await new Promise(r => setTimeout(r, 600));
  expect((await control()).calls.filter(isWrite), what).toEqual([]);
}
async function confirmWith(page, verb, { cancel = false } = {}) {
  const dlg = page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') }).last();
  await dlg.waitFor();
  await dlg.getByRole('button', { name: cancel ? 'Cancel' : verb, exact: true }).click();
  await expect(page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') })).toHaveCount(0);
}

test.describe('every control in Settings', () => {
  // A full load per group: a pending diff in one group makes the next navigation ask about discarding it,
  // and an unanswered dialog would silently swallow every later click.
  let go;
  test.beforeEach(async ({ page, login, control }) => {
    await control({ reset: true });
    await login();
    go = async group => {
      // goto() between two fragments of one URL is a same-document navigation, so it would leave the
      // previous group's pending diff — and the dialog that guards it — in place. reload() forces a
      // fresh document, which is the only way each group starts from the values the apps actually hold.
      await page.goto('/settings#' + group);
      await page.reload();
      await page.locator('#pane').waitFor();
      await expect(page.locator('#pane .skel')).toHaveCount(0);
      await expect(page.locator('dialog[open]'), 'no dialog is left over from the group before').toHaveCount(0);
      await control({ clear_calls: true });
    };
  });
  test.afterEach(async ({ control }) => { await control({ reset: true }); });

  test('each preset writes what its description promises', async ({ page, control }) => {
    test.slow();
    await go('presets');
    await page.locator('.preset').filter({ hasText: 'Everything paused' }).getByRole('button', { name: 'Apply' }).click();
    await confirmWith(page, 'Apply');
    await recorded(control, c => qbit('torrents/stop')(c) && c[3]?.hashes === 'all', 'Everything paused stops every torrent');

    await go('presets');
    await page.locator('.preset').filter({ hasText: 'Upload off' }).getByRole('button', { name: 'Apply' }).click();
    await confirmWith(page, 'Apply');
    let prefs = await recorded(control, qbit('app/setPreferences'), 'Upload off writes preferences');
    let json = JSON.parse(prefs.at(-1)[3].json);
    expect(json.max_active_uploads, 'no seeding slots').toBe(0);
    expect(json.up_limit, 'upload trickles').toBe(52428);

    await go('presets');
    await page.locator('.preset').filter({ hasText: 'Off-peak only' }).getByRole('button', { name: 'Apply' }).click();
    await confirmWith(page, 'Apply');
    prefs = await recorded(control, qbit('app/setPreferences'), 'Off-peak writes preferences');
    json = JSON.parse(prefs.at(-1)[3].json);
    expect(json.scheduler_enabled, 'the schedule is on').toBe(true);
    expect([json.schedule_from_hour, json.schedule_to_hour], 'from 01:00 to 08:00').toEqual([1, 8]);

    await go('presets');
    await page.locator('.preset').filter({ hasText: 'Overclock' }).getByRole('button', { name: 'Apply' }).click();
    await confirmWith(page, 'Apply');
    prefs = await recorded(control, qbit('app/setPreferences'), 'Overclock writes preferences');
    json = JSON.parse(prefs.at(-1)[3].json);
    expect(json.up_limit, 'no upload cap').toBe(0);
    expect(json.max_active_downloads, 'but the download cap still applies — a single disk saturates').toBe(2);
    await recorded(control, qbit('torrents/start'), 'and everything is resumed');
  });

  test('Downloads: nothing is written until Apply, and Discard puts the value back', async ({ page, control }) => {
    await go('downloads');
    await page.locator('#f-up_limit').fill('3');
    await page.locator('#f-up_limit').blur();
    await expect(page.locator('#applybar')).toBeVisible();
    await noWrites(control, 'an edited field is a pending diff, not a write');

    await page.locator('#apply').click();
    const prefs = await recorded(control, qbit('app/setPreferences'), 'Apply writes the group to qBittorrent');
    expect(JSON.parse(prefs.at(-1)[3].json).up_limit, '3 MB/s in bytes').toBe(3145728);
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /downloadclient/.test(c[2]), 'and the arrs are told about the client');

    await go('downloads');
    const before = await page.locator('#f-up_limit').inputValue();
    await page.locator('#f-up_limit').fill('9');
    await page.locator('#f-up_limit').blur();
    await page.locator('#discard').click();
    // Discard routes through the same guard that catches leaving a group with unsaved changes, so it asks.
    await confirmWith(page, 'Discard');
    await expect(page.locator('#applybar')).toBeHidden();
    await expect(page.locator('#f-up_limit')).toHaveValue(before);
    await noWrites(control, 'a discarded edit reaches no app');
  });

  test("Downloads' client buttons act on qBittorrent and the arrs at once", async ({ page, control }) => {
    await go('downloads');
    await page.getByRole('button', { name: 'Pause all', exact: true }).click();
    await confirmWith(page, 'Pause');
    await recorded(control, c => qbit('torrents/stop')(c) && c[3]?.hashes === 'all', 'Pause all stops everything');

    await go('downloads');
    await page.getByRole('button', { name: 'Resume all', exact: true }).click();
    await recorded(control, c => qbit('torrents/start')(c) && c[3]?.hashes === 'all', 'Resume all starts everything');

    await go('downloads');
    await page.getByRole('button', { name: 'Toggle alt-speed', exact: true }).click();
    await recorded(control, qbit('transfer/toggleSpeedLimitsMode'), 'the alternative limits are toggled');

    await go('downloads');
    await page.getByRole('button', { name: 'RSS sync', exact: true }).click();
    await recorded(control, cmd('radarr', 'RssSync'), 'RSS sync runs on Radarr');
    await recorded(control, cmd('sonarr', 'RssSync'), 'and on Sonarr');
  });

  test('Indexers: enable, disable, test one, test all, and push the list to the arrs', async ({ page, control }) => {
    // Address each indexer by name: the button says Enable or Disable depending on the state it is in,
    // so an ordinal would follow whichever one the previous step happened to flip.
    const indexer = name => page.locator('.frow').filter({ hasText: name });
    await go('indexers');
    await indexer('Indexer A').getByRole('button', { name: 'Disable', exact: true }).click();
    let put = await recorded(control, c => c[0] === 'prowlarr' && c[1] === 'PUT' && /indexer\/1$/.test(c[2]), 'the enabled indexer is disabled');
    expect(put.at(-1)[3].enable).toBe(false);

    await go('indexers');
    await indexer('Indexer B').getByRole('button', { name: 'Enable', exact: true }).click();
    put = await recorded(control, c => c[0] === 'prowlarr' && c[1] === 'PUT' && /indexer\/2$/.test(c[2]), 'the disabled one is enabled');
    expect(put.at(-1)[3].enable).toBe(true);

    await go('indexers');
    await indexer('Indexer A').getByRole('button', { name: 'Test', exact: true }).click();
    await recorded(control, c => c[0] === 'prowlarr' && c[1] === 'POST' && c[2] === '/api/v1/indexer/test', 'Test tests that indexer');

    // Test all only tests what is enabled. The fixture ships Indexer B disabled, so on a clean dataset
    // that is Indexer A alone.
    await control({ reset: true });
    await go('indexers');
    await page.getByRole('button', { name: 'Test all', exact: true }).click();
    const tests = await recorded(control, c => c[0] === 'prowlarr' && c[2] === '/api/v1/indexer/test', 'Test all tests the enabled indexers');
    expect(tests.map(t => t[3].name), 'the disabled indexer is left alone').toEqual(['Indexer A']);

    await go('indexers');
    await page.getByRole('button', { name: 'Sync to Radarr / Sonarr' }).click();
    await recorded(control, cmd('prowlarr', 'ApplicationIndexerSync'), 'the list is pushed to the arrs');
  });

  test('Media server and Notifications reach Jellyfin and ntfy', async ({ page, control }) => {
    await go('media');
    await page.getByRole('button', { name: 'Scan library now' }).click();
    await recorded(control, c => c[0] === 'jellyfin' && c[2] === '/Library/Refresh', 'Jellyfin is asked to re-scan');

    await go('notifications');
    await page.getByRole('button', { name: 'Send a test notification' }).click();
    const sent = await recorded(control, c => c[0] === 'ntfy' && c[1] === 'POST', 'a test notification is published');
    expect(sent[0][2], 'to the admin topic').toBe('/admin');

    // The quiet hours live in the panel's own settings.local — saving them must reach no app at all.
    await go('notifications');
    await page.locator('#f-quiet_start').fill('1');
    await page.locator('#f-quiet_start').blur();
    await page.locator('#apply').click();
    await noWrites(control, 'notification preferences are the panel\'s own, not an app\'s');
  });

  test('Movies and TV write media management, the seeder threshold and the audio language', async ({ page, control }) => {
    test.slow();
    await go('radarr');
    await page.locator('#f-recycle_bin').fill('/data/recycle');
    await page.locator('#f-recycle_bin').blur();
    await page.locator('#apply').click();
    const mm = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /config\/mediamanagement/.test(c[2]), 'the recycle bin is written');
    expect(mm.at(-1)[3].recycleBin).toBe('/data/recycle');

    await go('sonarr');
    await page.locator('#f-min_seeders').fill('9');
    await page.locator('#f-min_seeders').blur();
    await page.locator('#apply').click();
    const idx = await recorded(control, c => c[0] === 'sonarr' && c[1] === 'PUT' && /indexer\/1$/.test(c[2]), "Sonarr's indexer gets the threshold");
    expect(idx.at(-1)[3].fields.find(f => f.name === 'minimumSeeders').value).toBe(9);
    // The threshold is stack-wide, so Prowlarr's app profile is written too.
    const app = await recorded(control, c => c[0] === 'prowlarr' && c[1] === 'PUT' && /appprofile/.test(c[2]), "and so does Prowlarr's app profile");
    expect(app.at(-1)[3].minimumSeeders).toBe(9);

    await go('radarr');
    await page.locator('#f-audio_language').selectOption({ label: 'English' });
    await page.locator('#apply').click();
    // Radarr keeps the language on the profile, so every profile is rewritten; Sonarr v4 has no such field
    // and the setting is not offered for TV at all.
    const profiles = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /qualityprofile\/\d+$/.test(c[2]), 'the audio language goes on the profiles');
    expect(new Set(profiles.map(p => p[3].id)), 'on all of them').toEqual(new Set([1, 2]));
    await expect(page.locator('#f-audio_language'), 'and TV is not offered it').toHaveCount(1);
    await go('sonarr');
    await expect(page.locator('#f-audio_language')).toHaveCount(0);
  });

  test('Subtitles goes to Bazarr; Requests goes to Jellyseerr', async ({ page, control }) => {
    await go('subtitles');
    await page.locator('.provgrid input[type=checkbox]').first().click();
    await page.locator('#apply').click();
    await recorded(control, c => c[0] === 'bazarr' && c[1] === 'POST' && c[2] === '/api/system/settings', 'the subtitle settings are written to Bazarr');

    await go('requests');
    await page.locator('#js-movie-p').selectOption({ label: 'HD-1080p' });
    await page.locator('#apply').click();
    const put = await recorded(control, c => c[0] === 'jellyseerr' && c[1] === 'PUT' && /settings\/radarr/.test(c[2]), "Jellyseerr's movie default is written");
    expect(put.at(-1)[3].activeProfileId).toBe(2);
    // The name goes with the id, so a snapshot still means the same thing on another box.
    expect(put.at(-1)[3].activeProfileName).toBe('HD-1080p');
    expect(put.at(-1)[3].id, "and never the id Jellyseerr marks read-only").toBeUndefined();

    // Only one root folder exists for TV, so picking it is not a change and there is nothing to apply.
    await go('requests');
    await expect(page.locator('#js-series-r option')).toHaveCount(1);
    await expect(page.locator('#applybar')).toBeHidden();
  });

  test('TRaSH: Preview writes nothing; Apply writes formats, then the profile, then the size limits', async ({ page, control }) => {
    test.slow();
    await go('trash');
    const sec = page.locator('.fset, .tsec').filter({ has: page.locator('#trash-radarr') });
    await sec.getByRole('button', { name: 'Preview changes' }).click();
    await expect(sec.getByRole('button', { name: 'Apply', exact: true })).toBeEnabled();
    await noWrites(control, 'a preview is GETs only — nothing is written until Apply');

    await sec.getByRole('button', { name: 'Apply', exact: true }).click();
    await confirmWith(page, 'Apply');
    const calls = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /qualitydefinition/.test(c[2]), 'the size limits are written');
    const all = (await control()).calls;
    const firstFormat = all.findIndex(c => c[0] === 'radarr' && c[1] === 'POST' && /customformat/.test(c[2]));
    const profile = all.findIndex(c => c[0] === 'radarr' && c[1] === 'POST' && c[2] === '/api/v3/qualityprofile');
    const firstSize = all.findIndex(c => c[0] === 'radarr' && c[1] === 'PUT' && /qualitydefinition/.test(c[2]));
    expect(firstFormat, 'the custom formats are created first').toBeGreaterThan(-1);
    expect(profile, 'then the profile that scores them').toBeGreaterThan(firstFormat);
    expect(firstSize, 'then the global size limits').toBeGreaterThan(profile);
    // TRaSH sets a floor per quality and no usable ceiling: a big release is then ranked by score, not refused.
    expect(calls.every(c => c[3].minSize > 0), 'every quality gets a minimum size').toBe(true);
  });

  test('TRaSH: rolling back restores the whole snapshot, not only the quality profiles', async ({ page, control }) => {
    test.slow();
    await go('trash');
    const sec = page.locator('.fset, .tsec').filter({ has: page.locator('#trash-radarr') });
    await sec.getByRole('button', { name: 'Preview changes' }).click();
    await sec.getByRole('button', { name: 'Apply', exact: true }).click();
    await confirmWith(page, 'Apply');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'POST' && c[2] === '/api/v3/qualityprofile', 'the sync ran');

    await go('backup');
    await page.getByRole('button', { name: 'Roll back' }).click();
    // The confirmation has to say what the roll back really does, because it does more than quality:
    // a setting changed after the sync is undone with it.
    const dlg = page.locator('dialog.dlg').filter({ has: page.locator('#dlg-title') }).last();
    await expect(dlg.locator('p')).toContainText('Downloads, Movies, TV, Subtitles and Notifications');
    await expect(dlg.locator('p')).toContainText('Anything you changed after that sync is undone too');
    await confirmWith(page, 'Roll back');
    // The quality half: profiles and size limits go back to what the snapshot held.
    const defs = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /qualitydefinition/.test(c[2]), 'the size limits are restored');
    expect(defs.at(-1)[3].maxSize, 'back to the pre-sync ceiling').toBe(50);
    const profiles = await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /qualityprofile\/\d+$/.test(c[2]), 'the profiles are restored');
    // Each profile gets its own pre-sync scores back: the format HD-1080p already scored keeps its 100,
    // and every format the sync created stays on the profile at 0 rather than being deleted.
    const hd = profiles.filter(p => p[3].id === 2).at(-1)[3].formatItems;
    expect(hd.find(f => f.format === 7)?.score, 'the format the profile already scored is back to 100').toBe(100);
    expect(hd.filter(f => f.format !== 7).every(f => f.score === 0), 'formats the sync added stay, scored 0').toBe(true);
    // The rest of the snapshot goes back too — this is one mechanism, not a quality-only undo. A download
    // limit or a subtitle provider changed since the sync is reverted with it.
    await recorded(control, qbit('app/setPreferences'), 'and so do the qBittorrent preferences');
    await recorded(control, c => c[0] === 'bazarr' && c[1] === 'POST' && c[2] === '/api/system/settings', 'and the Bazarr settings');
  });

  test('Users & roles: add, permit, repassword, remove — and the last admin stays', async ({ page, control, errors, login }) => {
    test.slow();
    errors.allow.push(/\/login$/);              // the old password is tried on purpose below
    errors.allow.push(/\/api\/users\/delete$/);   // so is removing the last admin, which must be refused
    // The role grant is panel state that outlives this test, and another spec asserts a standard user
    // cannot purge; put back whatever was there.
    const roleBefore = (await (await page.request.get('/api/roles')).json()).user;
    // Accounts outlive a test run, and other specs add their own; start from a known state for this one.
    await page.request.post('/api/users/delete', { data: { username: 'probe' } });
    await go('users');
    await page.locator('input[aria-label="New username"]').fill('probe');
    await page.locator('input[aria-label="New password"]').fill('probe-pw-123');
    await page.getByRole('button', { name: 'Add user' }).click();
    await expect.poll(async () => (await (await page.request.get('/api/users')).json()).map(u => u.username))
      .toContain('probe');
    await noWrites(control, 'an account is the panel\'s own, so no app is told');

    await page.request.post('/api/roles', { data: { role: 'user', can_purge: false } });
    await go('users');
    await page.locator('input[data-cap="can_purge"]').check();
    await page.getByRole('button', { name: 'Save permissions' }).click();
    await expect.poll(async () => (await (await page.request.get('/api/roles')).json()).user.can_purge, { message: 'the grant is saved' }).toBe(true);

    await go('users');
    const row = page.locator('.frow').filter({ has: page.locator('.flabel b', { hasText: /^probe$/ }) });
    await row.getByRole('button', { name: 'Change password' }).click();
    const dlg = page.locator('dialog.dlg').last();
    await dlg.locator('input[aria-label="New password"]').fill('another-pw-123');
    await dlg.locator('input[aria-label="Repeat password"]').fill('another-pw-123');
    await dlg.getByRole('button', { name: 'Change password' }).click();
    await expect(dlg).toHaveCount(0);
    expect((await page.request.post('/login', { form: { username: 'probe', password: 'probe-pw-123', next: '' }, maxRedirects: 0 })).status(),
      'the old password no longer signs in').toBe(200);
    expect((await page.request.post('/login', { form: { username: 'probe', password: 'another-pw-123', next: '' }, maxRedirects: 0 })).status(),
      'the new one does').toBe(302);
    await login();   // that last sign-in replaced this context's cookie with probe's; Settings is admin-only

    await go('users');
    await page.locator('.frow').filter({ has: page.locator('.flabel b', { hasText: /^probe$/ }) })
      .getByRole('button', { name: 'Remove' }).click();
    await confirmWith(page, 'Remove');
    await expect.poll(async () => (await (await page.request.get('/api/users')).json()).map(u => u.username),
      { message: 'the account is gone' }).not.toContain('probe');

    await go('users');
    await page.locator('.frow').filter({ has: page.locator('.flabel b', { hasText: /^admin$/ }) })
      .getByRole('button', { name: 'Remove' }).click();
    await confirmWith(page, 'Remove');
    await expect(page.locator('.toast')).toHaveText("Can't remove the last admin");
    expect((await (await page.request.get('/api/users')).json()).map(u => u.username), 'the admin is still there').toContain('admin');

    await page.request.post('/api/roles', { data: { role: 'user', ...roleBefore } });
  });

  test('the action log records the writes, filters them, and offers nothing that changes them', async ({ page, control }) => {
    await go('downloads');
    await page.getByRole('button', { name: 'RSS sync', exact: true }).click();
    await recorded(control, cmd('radarr', 'RssSync'), 'a write to record');

    await go('log');
    const rows = page.locator('#pane tbody tr');
    await expect(rows.first()).toBeVisible();
    await expect(page.locator('#log-action option')).toContainText(['Every action']);
    await expect(page.locator('#pane').getByRole('button', { name: /Clear|Undo|Delete|Edit/ }), 'read-only: nothing here changes the log').toHaveCount(0);

    const all = await rows.count();
    await page.locator('#log-action').selectOption('rss_sync');
    await expect.poll(async () => rows.count(), { message: 'filtering narrows the list' }).toBeLessThan(all);
    await expect(rows.first()).toContainText('rss_sync');
    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(rows.first()).toBeVisible();
  });

  test('Backup: a snapshot only reads; restoring defaults rewrites every group', async ({ page, control }) => {
    test.slow();
    await go('backup');
    await page.getByRole('button', { name: 'Save snapshot (.json)' }).click();
    await expect(page.locator('.toast-ok')).toHaveText('Snapshot downloaded');
    await noWrites(control, 'exporting a snapshot writes nothing anywhere');

    await go('backup');
    await page.getByRole('button', { name: 'Restore installer defaults' }).click();
    await confirmWith(page, 'Restore');
    await recorded(control, qbit('app/setPreferences'), 'the client is put back to the installer values');
    await recorded(control, c => c[0] === 'radarr' && c[1] === 'PUT' && /config\/mediamanagement/.test(c[2]), 'and so is Radarr');
    await recorded(control, c => c[0] === 'sonarr' && c[1] === 'PUT' && /config\/mediamanagement/.test(c[2]), 'and Sonarr');
    await recorded(control, c => c[0] === 'bazarr' && c[1] === 'POST' && c[2] === '/api/system/settings', 'and Bazarr');
  });
});
