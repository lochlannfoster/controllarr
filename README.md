# Controllarr

A unified control panel for the \*arr stack you already run. It shows what needs manual intervention, what is flowing as expected, and every control to fix it — without opening Radarr, Sonarr, Bazarr, Jellyfin, or a shell.

Controllarr installs nothing else and changes nothing you have not asked it to: it reads your apps over their own APIs and, when you press a control, calls the same API you would
have clicked in their UI.

## Quick start

Linux with Docker and the compose plugin, `python3`, `curl`.

```bash
git clone https://github.com/lochlannfoster/controllarr.git
cd controllarr
./install.sh
```

It asks where each app lives and for its API key, writes its own config, and starts one container. Re-run it to change an answer; `./uninstall.sh` removes it. Nothing else on the
machine is touched.

## What it is for

Controllarr is a client for software you already run. It reads your apps over their own APIs and, when you press a control, calls the API their own UI would have called. It installs none of them and reconfigures nothing you have not asked it to.

It ships **no indexers, no sources and no lists of either**, and it never adds one. Which indexers your \*arr apps search, and which download client they hand a release to, are configured in those apps by you — Controllarr only shows you the result and gives you the buttons.

## What it connects to

Every one is optional — Controllarr shows what you have and stays quiet about the rest.

| App | Default port | What Controllarr does with it |
|---|---|---|
| [Radarr](https://radarr.video) | 7878 | movies: stage, quality, search, subtitles, files |
| [Sonarr](https://sonarr.tv) | 8989 | shows: seasons, episodes, gaps, files |
| [Bazarr](https://www.bazarr.media) | 6767 | subtitle status per title and per episode, manual search |
| [Jellyfin](https://jellyfin.org) | 8096 | who is watching, direct play or transcode, library scans |
| [Jellyseerr](https://github.com/fallenbagel/jellyseerr) | 5055 | pending requests, approve or decline |
| [Prowlarr](https://prowlarr.com) | 9696 | indexer health, test and sync |
| [qBittorrent](https://www.qbittorrent.org) | 8080 | the download queue, speeds and per-item controls |
| [ntfy](https://ntfy.sh) | 8090 | push notifications |

With a read-only Docker socket it also shows each container's state, memory and last log line.

## What it looks like

- **The Dash** — one bar: pipeline stations with counts, the server, and what is unhealthy.
- **System** — CPU, IO wait, load, RAM, swap, disk and temperature, each with a word that says what it means; expands to every container.
- **Needs attention** — only actionable problems, each with one primary action.
- **Downloads** — what the client is doing, per item, with the reason when nothing moves.
- **Library** — every tracked title by stage; a show expands into its episodes with sizes
  and subtitle status; tick some and act on exactly those.
- **Settings** — quality, size, languages, subtitles, request defaults, users and roles, written straight into the apps.

<table>
  <tr>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/01-dashboard.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/01-dashboard.png" alt="Dashboard — the Dash, System, and only what needs a person" width="240"></a><br><sub><b>Dashboard</b> — the Dash, System, and only what needs a person</sub></td>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/02-downloads.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/02-downloads.png" alt="Downloads — per item, with the reason when nothing moves" width="240"></a><br><sub><b>Downloads</b> — per item, with the reason when nothing moves</sub></td>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/03-library.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/03-library.png" alt="Library — every tracked title, grouped by stage" width="240"></a><br><sub><b>Library</b> — every tracked title, grouped by stage</sub></td>
  </tr>
  <tr>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/04-episodes.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/04-episodes.png" alt="Episodes — a show opens into seasons, sizes and subtitle status" width="240"></a><br><sub><b>Episodes</b> — a show opens into seasons, sizes and subtitle status</sub></td>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/05-episode-actions.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/05-episode-actions.png" alt="Episode actions — tick some and act on exactly those" width="240"></a><br><sub><b>Episode actions</b> — tick some and act on exactly those</sub></td>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/06-episode-controls.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/06-episode-controls.png" alt="Episode controls — one episode and the torrent behind it" width="240"></a><br><sub><b>Episode controls</b> — one episode and the torrent behind it</sub></td>
  </tr>
  <tr>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/07-title-controls.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/07-title-controls.png" alt="Title controls — quality, monitoring, subtitles, files, torrents" width="240"></a><br><sub><b>Title controls</b> — quality, monitoring, subtitles, files, torrents</sub></td>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/08-settings-presets.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/08-settings-presets.png" alt="Presets — one click, written into the apps" width="240"></a><br><sub><b>Presets</b> — one click, written into the apps</sub></td>
    <td><a href="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/09-settings-downloads.png" target="_blank" rel="noopener noreferrer"><img src="https://raw.githubusercontent.com/lochlannfoster/controllarr-assets/main/09-settings-downloads.png" alt="Settings — speeds, queue and seeding, per group" width="240"></a><br><sub><b>Settings</b> — speeds, queue and seeding, per group</sub></td>
  </tr>
</table>

<sub>Click any shot for the full-size image. They are captured against the test stack in
<code>tests/fake_stack.py</code>: the films and shows are real, everything else — users, hosts, paths,
sizes and states — is invented. No screenshot here shows anybody's library.</sub>

Stdlib Python and vanilla ES modules: no build step, no framework, no telemetry, nothing
loaded from outside your network.

## Documentation

[Install](docs/INSTALL.md) · [Configuration](docs/CONFIGURATION.md) · [The panel](docs/DASHBOARD.md) ·
[Troubleshooting](docs/TROUBLESHOOTING.md) · [Development](docs/DEVELOPMENT.md) · [Roadmap](docs/ROADMAP.md)

## Licence

GPL-3.0, the same licence as Radarr, Sonarr, Prowlarr and Bazarr. Free and open-source, forever.
