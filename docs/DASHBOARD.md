# The panel

What every part of Controllarr shows and does. It is one page plus a Settings page; you never need another tab to run the stack day to day.

- **URL:** `http://<server-ip>:3002` (`CONTROLLARR_PORT` moves the host port). Two pages: the dashboard (`/`) and **Settings** (`/settings`, admin only). `/dashboard` → `/#live`, `/docs` → `/#reference`, `/?item=movie:123` opens a title's controls (ntfy links use this).
- **Data:** the library is rescanned every `CONTROLLARR_REFRESH` s (default 15; ≤ 6 indexer searches per pass, verdicts cached 3 h). Sections poll on their own clocks — attention 10 s, live 5 s while something moves / 30 s idle, system 10 s, library 15 s, reference 5 min — pause while the tab is hidden or titles are selected (never a first load), back off when a source fails, and refresh on focus. Until the first scan finishes the attention list shows a skeleton, not a guess.
- **Every control explains itself:** hover on a desktop; on a phone **long-press** it, or tap **?** in the header to enter help mode, where every tap shows the explanation instead of acting (tap ? again to leave).
- **Nothing leaves the LAN:** fonts, icons and posters are served by the panel; API keys never reach the browser.
- **It knows what you run:** an app you did not configure is absent, not "down" — Downloads says *no client*, its Settings group is not listed, nothing is reported as failed. Without a Docker socket the container table is simply not offered.

Related: [INSTALL.md](INSTALL.md) · [CONFIGURATION.md](CONFIGURATION.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## The page, top to bottom

**The Dash** — one compact bar. Left: a health pill (*stack up* / *N containers down* / *VPN down* / *VPN orphaned*) and the seven pipeline stations, *Unavailable · Searching · Downloading · Importing · Partial · Waiting · Available*, each a coloured dot, its short name and count, plus a word where something needs you (*needs you*, *1 stalled*, *incomplete*); click one to filter the library. Centre: the server's name and address. Right: the data age, and — when your download client runs inside a [gluetun](https://github.com/qdm12/gluetun) container — that tunnel's state, exit IP and forwarded port.

**System** — a headline pill (*all fine*, or the worst figures in colour: *disk filling up (8 GB free) · RAM tight*) and one line of host figures — CPU, IO wait, load, RAM, swap, media disk, temperature, uptime — each with a coloured word that says what it means (*fine · busy · maxed out*, *filling up · nearly full*, …) that expands (remembered per browser) into every container with its state, CPU, memory, **what it is doing now** (running arr commands, Jellyfin scheduled tasks, Jellyseerr jobs, Bazarr tasks, the download client's counts, the library scan) and its **last log line**. All of it read through the read-only Docker socket, when you mounted one. Two figures are judged carefully: **swap** by traffic, not occupancy — pages the kernel parked there while RAM was plentiful are harmless and read *parked*; only pages actually moving in and out earn *swapping* (amber) or *thrashing* (red) — and **disk** from 80 %, with the GB left in the headline because one film is 2–8 GB.

**Needs attention** — only actionable problems, each with one primary action; *Nothing needs you* is the good state. Two shortcuts sit in its heading: **Jellyfin ↗** and **Jellyseerr ↗**, the two apps a household opens. In severity order: a tunnel down or its containers orphaned (gluetun installs only); a container stopped, unhealthy or missing (its last log line is in System, `docker logs <name>` has the rest); a stalled download with the reason (*dead swarm — N peers, 0 seeds*, *reported but none reachable*, …) and **Blocklist & retry** / **Remove** / *Reannounce*; an import problem; indexer or FlareSolverr health; disk ≥ 80 / 90 / 95 %; a pending Jellyseerr request (**Approve** / *Decline*); a title unavailable for N days (**Search again**). A source that does not answer gets its own row instead of vanishing.

**Live** — the throughput meter (↓/↑, sparkline, connection state, DHT), **Pause all** (confirmed with real counts), **Resume all**, **Alt-speed**, and **Tune…** (admins: the presets, see [Settings](#settings)). Then every torrent, **labelled by episode** — `S10E03 · title` rather than ten rows all called *Futurama* — with queue number, state, progress, speeds, ETA, seeds/peers, ratio, size, the amber reason when it is not moving, and **Pause / Resume / Recheck / Reannounce / Top / Bottom / Force / Remove / Purge**. *Remove* takes the torrent out of qBittorrent and **keeps the files**; *Purge* deletes the torrent **and its files** — a movie is purged from the whole stack, a TV download also untracks the episodes it carried, and if that was the last of the show the show goes too ([purge](#purging)). Torrents of one title fold into a **group** — collapsed by default to one header with the aggregate (done / downloading, progress, speed, size) that acts on all of them at once (*Pause all*, *Resume all*, *Top*, *Bottom*, *Force all*, *Remove all*, *Purge all*); the caret reveals the episode rows. **Now playing** lists Jellyfin sessions with Direct Play / Direct Stream / Transcode and the reasons.

**Library** — every monitored title by stage; *Available* is two groups, **Available movies** and **Available shows**, each with its own fold, sort and count. While anything is not Available both start collapsed. Filter by text or stage, sort each group. Each row: checkbox, stage, poster, title, subtitle tag, the live download line or the stage detail, **size on disk**, **duration** (a movie's runtime; a show's minutes per episode and the hours on disk), reason, requester. **Tap a movie** to open its controls; **tap a show** to expand it, in one box under the title, into its episode list — `S01E01`, `S01E02`, … under thin season headers that carry the season's episode count and **size**; every tracked season open; a header's caret folds it; tap the title again to collapse everything. Each episode shows its **file size** and a **subs / no subs** word (Bazarr's verdict; nothing until Bazarr has seen the file):

- **Tick episodes** (or a season header, which ticks its episodes; or the show's own checkbox, which ticks every episode — then untick the ones to leave out). A **toolbar appears above the list** for exactly the ticked episodes: **Search**, **Subs** (one episode: pick from Bazarr's candidates; several: Bazarr searches every provider), **Track** / **Untrack**, and for their torrents **Top / Bottom / Pause / Resume / Force / Remove** (keeps files), then **Delete files** and **Purge** (files + torrents, then untracked), **Clear**.
- A season header also carries its own *tracked* switch (ticking it makes Sonarr search the season).
- Each episode's **›** opens the **episode dialog** — the same controls for that one episode plus the torrent behind it.
- **›** on the show's row opens the **title controls** (the drawer), which carries the same episode list under Monitoring.

**Multi-select bar** — tick whole titles (a show whose every episode is ticked counts as ticked): **Search…** (one), **Retry**, **Refresh**, **Monitor**, **Unmonitor**, **Blocklist**, **Quality** (one), **Top**, **Bottom**, **Pause**, **Resume**, **Purge** (the whole title), **Clear**. Polling pauses while anything is ticked.

**Reference** — every configured app with its version and a link, and the glossary.

**Header** — section links, **?** help mode, **⌘K / Ctrl-K** palette (sections, apps, titles, *Pause all*, *RSS sync*, *Test all indexers*, *Scan Jellyfin library*…), theme, density, **Incognito** ([below](#incognito)), **↻** rescan, log out.

## Title controls (the drawer)

Full-screen on a phone; focus-trapped; `Esc` closes and returns focus.

- **Quality & size** — profile; movies: minimum availability; TV: series type; root folder (`can_change_root`, files are not moved).
- **Monitoring** — the monitor toggle; TV: **Monitor all + search** and the same episode list (tick boxes, toolbar) as the library.
- **Subtitles** — status, **Fetch subs**, **Manual search…** (movies; per episode for TV), *Subs preferences* (admin).
- **Library** — season selector, **Search…** (the release picker: quality, size, seeders, rejection, **Grab** needs `can_grab`), **Auto-search**, **Refresh**, **Blocklist & retry**, **Purge** (everything, everywhere: [purge](#purging)).
- **Torrents** — every torrent of the title, downloading **or seeding** (the arr's history finds the ones that left the queue), each with state, progress, queue position, speeds, seeds, ratio, ETA, the reason, all eight controls and per-torrent ↓/↑ caps.

## Incognito

**Incognito** in the header draws every title, poster, requester and file name as a made-up one — *Amber
Lantern 47*, *Quiet Otter* — so you can screenshot the panel or share your screen without showing your
library. It is remembered per browser, like density, and applies to the dashboard.

Only what is *drawn* changes. Each pseudonym is a hash of the item's own id, so one title reads the same on
every refresh and in every section — the Library row, its torrent in **Live**, the row in **Needs attention**
and the confirmation dialog all say the same made-up name, and a sequence of screenshots stays coherent. The
page keeps the real values underneath, so:

- the **filter box** and the **palette** still match what you actually own — type *Expanse*, the row appears
  under its pseudonym;
- every control does exactly what it did: the id in each action is untouched, and so is the panel's own log
  line, which keeps the **real** target (an audit trail of pseudonyms is not an audit trail);
- a confirmation still says what it will do with **real counts** — *deletes 3 episode files on disk and
  removes 2 torrents* — it just does not name the thing.

What it covers: titles, years and posters; episode and release names; requesters and Jellyfin viewers with
their devices; the container **log lines** in System (that is where a file name usually turns up, and half a
path is still a leak, so the whole line is held back). What it does **not** cover: your server's name and
address in the Dash, container and app names, the wording of an app's own health message, and the Settings
page. It is a screenshot shield, not access control — whoever has the browser, or the API behind it, still
has the real data.

## Confirmations and logging

Anything destructive opens a dialog whose text **the server writes with the real counts** (with [incognito](#incognito) on it writes the same counts and leaves the names out) — *Purge season 2 of The Expanse: deletes 3 episode files on disk and removes 2 torrents with their data… That is the last of the show: the show itself is removed from Sonarr, Jellyseerr, Bazarr and Jellyfin as well.* — and the toast afterwards says what happened. Every write is one line in the container log (`docker logs controllarr`: user, role, action, target, result, duration).

## Settings

Admin only, grouped by what you control. Each group loads the app's **current** values; edits collect in the bar at the bottom as a diff and nothing is written until **Apply**. An unreachable app cannot be changed; the group of an optional part that is not installed is not listed.

| Group | Knobs | Writes to |
|---|---|---|
| **Presets** | one-click tuning, also in Live's **Tune…** menu. *Right now:* **Everything paused** (stop every download and seed), **Upload off** (no seeding, uploads capped to a trickle), **Balanced** (unlimited down, 1 MB/s up, seed to ratio 2, alt-speed and schedule off, all resumed), **Overclock** (no limits, more seed slots, seed forever, all resumed — the download cap still holds), **Off-peak only** (alternative limits 01–08 h). *What to look for:* **4K quality** (40/120 MB/min, x265 welcome, 3 seeders), **1080p balanced** (20/50, 5 seeders — the installer's default), **Data-saver** (8/20, x264 first, unknown quality allowed). A preset overlays a few values on the current settings; fine-tune in the groups below afterwards. | qBittorrent, Radarr, Sonarr |
| **Downloads** | download / upload limits, alternative limits + schedule, active downloads (clamped to `MAX_ACTIVE_DL_CAP`) and uploads, seed after complete, stop at ratio, remove once imported; the listen port (read-only, from a gluetun tunnel when there is one); Pause all / Resume all / alt-speed / RSS sync | qBittorrent (+ the arrs' *remove completed*) |
| **Quality & size → Movies / TV** | preferred + maximum MB/min, the per-indexer release threshold, audio language, unknown quality, prefer h264, propers, rename, hardlinks, recycle bin, minimum free space | Radarr / Sonarr; `SIZE_CAP_MBPM` and `MIN_SEEDERS` into `settings.local` |
| **Indexers** | per-indexer enable / test, **Test all**, sync to the arrs, FlareSolverr status | Prowlarr |
| **Subtitles** | languages, HI / forced, scores, adaptive search, upgrades, embedded options, providers | Bazarr (a refused save is reported as such — Bazarr 1.6 rejects a profile without `audio_only_include`); `SUBTITLE_LANGS` / `HEARING_IMPAIRED` into `settings.local` |
| **Requests** | default profile and root folder for new movie / series requests | Jellyseerr (a refused write is reported — its API rejects a body carrying `id`) |
| **Media server** | Jellyfin key present?; **Scan library now** | Jellyfin |
| **Notifications** | ntfy URL, topics, quiet hours, **Send a test notification** | `settings.local` (and whatever else on your box reads it) |
| **Users & roles** | accounts (add, change password, remove — never the last admin); what standard users may do | `users.json` |
| **Backup & config** | last backup age; save / load a settings snapshot; **Restore installer defaults** | all of the above |

**Roles.** Standard users can search, retry, monitor, change quality, fetch subtitles and pause / resume / reorder torrents. Eight grantable permissions: `can_purge` (every purge: title, torrent, season, episode), `can_delete_files`, `can_import`, `can_remove` (remove torrents, blocklist), `can_change_root`, `can_grab`, `can_control_client` (pause/resume all, alt-speed, caps, force-start, RSS sync, indexer tests, Jellyfin scan), `can_manage_requests`. The UI hides what a role cannot do; the server refuses it regardless (`403 Not permitted`).

## Login and sessions

- `CONTROLLARR_PASSWORD` at install turns auth on and seeds `admin` into `users.json`; from then on `users.json` is the only truth. **Blank = no login and every visitor is an admin** — fine only on a trusted LAN. Never expose `:3002` to the internet — use a private overlay network such as [Tailscale](https://tailscale.com) for remote access.
- Sessions persist for 30 days in `sessions.json` (cookie `HttpOnly` + `SameSite=Lax`; JSON endpoints require `Content-Type: application/json`), so restarts log nobody out. Deep links survive the login page (`?next=`, local paths only). `/health` is the only unauthenticated route.
- Locked out: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#signing-in).

## Phone, keyboard, themes

360 px and up: one column, the Dash scrolls sideways, dialogs go full-screen, every target ≥ 44 px, inputs 16 px. Everything is reachable by Tab with a visible focus ring; `Enter` opens a row; `Esc` closes. Dark by default, light by system preference or choice, both WCAG AA; status is never colour alone; motion is off under *prefers-reduced-motion*. *Compact* density halves row padding (targets stay 44 px on touch).

## Purging

A purge removes a title from every app at once: each of its downloads (running or seeding — the arr's own history remembers the ones that left the queue) with its data, the title and its files from Radarr or Sonarr, its request and media record from Jellyseerr, then Bazarr and Jellyfin are asked to rescan so it disappears there too. A purge below the title — a season, ticked episodes, one download — deletes those files and untracks those episodes; when that leaves a show with nothing on disk and nothing tracked, the show goes too, so no empty title lingers. The confirmation says exactly this, with real counts, before anything happens.

## Files

Everything Controllarr writes — `users.json`, `sessions.json`, `settings.local`, `cache/` and `controllarr.env` — lives in its own state directory ([CONFIGURATION.md](CONFIGURATION.md)). Your apps' config, your media and the Docker socket are mounted read-only when at all. Colours, type and spacing are the tokens in `app/static/tokens.css`.
