# Configuration reference

Two files, one directory. The installer writes both; the Settings page changes live values inside your apps.

Related: [INSTALL.md](INSTALL.md) (the prompts) · [DASHBOARD.md ▸ Settings](DASHBOARD.md#settings)

## The files

| File | Where | Contains | Mode |
|---|---|---|---|
| `.env` | install dir | Compose values only: `STACK_NAME`, `TZ`, `CONTROLLARR_DATA`, `CONTROLLARR_PORT`, `CONTROLLARR_REFRESH`, and the answers the installer needs to regenerate the override. | 644 |
| `controllarr.env` | the state directory | Where each app lives and how to authenticate: `SERVICES`, `<APP>_HOST`, `<APP>_PORT`, `<APP>_APIKEY`, `QBIT_USER`/`QBIT_PASS`, `CONTROLLARR_PASSWORD`, `SERVER_HOST`, and the optional `CONFIG_DIR`, `MEDIA_DIR`, `DOCKER_SOCK`. **The only copy of your keys.** | 600 |
| `docker-compose.override.yml` | install dir | The networking choice and the optional read-only mounts. Regenerated on every run — never edit. | 644 |
| `users.json`, `sessions.json`, `settings.local`, `actions.log`, `rollback.json`, `cache/` | the state directory | Controllarr's own: accounts (PBKDF2-SHA256, 200 000 iterations), logins for 30 days, the values Settings saves for itself, the [action log](DASHBOARD.md#settings), the settings snapshot taken before the last [TRaSH sync](DASHBOARD.md#trash-guides) and proxied posters. | 600 / 600 / 600 / 600 / 600 / — |
| `trash-guides/` | the state directory | Present only after **Refresh the guide**: the re-fetched quality data, which then wins over the copy vendored in `app/trash-guides/`. Safe to delete — the vendored one takes over again. | — |

**The state directory is the only thing worth backing up.** Everything else is either regenerated or lives in your apps.

## Your keys

`controllarr.env` holds them in plain text, and the panel **will not start** if anyone but its owner can read
or write that file:

```
controllarr: refusing to start — /srv/controllarr/controllarr.env is mode 0644 and holds your API keys, so
             anyone with an account on this box can read them.
             fix it with:  chmod 600 /srv/controllarr/controllarr.env
```

Re-running `./install.sh` also puts it right, and creates the file at 600 rather than tightening it a moment
later. A group permission (640) is not refused — sharing a group with the user your apps run as is a
legitimate way to set this up — but it means anyone in that group can read your keys.

**They are not encrypted, and that is a decision rather than an omission.** The panel starts on its own when
the box boots, so it has to be able to unscramble them with nobody there to help — which means the password
for the scrambling would have to sit on the same disk, in a file the same process can read. Anyone who can
read the keys could read that too, and unscramble them in one more step. Encrypting here would look like
protection without being any.

It is also usually not the only copy. With `CONFIG_DIR` set, the panel does not store your Radarr key at
all — it reads Radarr's own `config.xml`, which Radarr keeps in cleartext, in a file the panel must be able
to read and which is not Controllarr's to change.

So what you actually get:

- **Nothing but its owner reads the file.** Enforced at startup, above, and by the installer.
- **No key ever leaves the panel.** Every response body, log line and error message is scrubbed of every
  secret the panel holds on its way out, so a key cannot reach your browser, `docker logs controllarr`, or a
  screenshot of either — whatever composed the text. Keys travel to your apps in a request header, never in a
  URL, so they stay out of the apps' access logs too. A value counts as a secret when its name contains
  `APIKEY`, `API_KEY`, `PASSWORD`, `PASS`, `TOKEN` or `SECRET` — so name a new credential accordingly and it is
  covered — and when it is at least twelve characters long, below which replacing it would mangle ordinary text.
- **The settings snapshot is safe to share.** `Settings ▸ Backup & config` leaves the ntfy URL out of the
  export, because a token can be part of it.

What this does **not** protect against, plainly: anyone who is root on the box, anyone who can read the state
directory as its owner, and anyone holding a backup of it or of the disk. If your threat model includes those,
the answer is not a cipher on the same disk — it is full-disk encryption, or not putting the panel there.

## Values

| Value | Meaning | Default |
|---|---|---|
| `SERVICES` | Comma-separated list of the apps this install connects to. A service that is not listed is absent: its Settings group is hidden, its sources are not polled, and nothing reports it as failed. | written by the installer |
| `<APP>_HOST` · `<APP>_PORT` | Where each app is, from Controllarr's point of view — a container name on a shared network, or a host address. `QBIT_*` for qBittorrent. | — |
| `<APP>_APIKEY` | The app's API key. Ignored when `CONFIG_DIR` is set and the key can be read from the app's own `config.xml`. | — |
| `CONFIG_DIR` | Your apps' config tree, mounted read-only, so keys are read live instead of copied. | unset |
| `MEDIA_DIR` | The library path, mounted read-only, so Controllarr can report its free space. | unset |
| `DOCKER_SOCK` | The Docker socket, mounted read-only, for container state, memory and last log lines. Empty = the feature is not offered at all. | `/var/run/docker.sock` |
| `SERVER_HOST` | The address in the links Controllarr shows. | detected |
| `CONTROLLARR_PASSWORD` | Seeds the first `admin` account. **Blank = no login**, every visitor an admin. Change it later in Settings ▸ Users & roles. | asked once |
| `CONTROLLARR_PORT` · `CONTROLLARR_REFRESH` | Host port · seconds between library scans (each pass costs a few searches in Radarr/Sonarr). | `3002` · `15` |
| `DEFAULT_PROFILE_RADARR` · `DEFAULT_PROFILE_SONARR` | The quality profile Controllarr gives a title it adds or adopts, as that profile's id. Set it in Settings ▸ Quality & size, which stores it by name. Unset, Controllarr uses the app's first profile — on a stock install that is *Any*, which allows SD and DVD rips. | unset |
| `MIN_SEEDERS` · `MAX_ACTIVE_DL_CAP` | Radarr and Sonarr's own per-indexer release threshold, mirrored here so Controllarr can explain a stuck title · the ceiling it enforces on concurrent downloads. | `5` · `2` |
| `TRASH_PROFILE_RADARR` · `TRASH_PROFILE_SONARR` | The [TRaSH profile](DASHBOARD.md#trash-guides) last applied to each app, by name, so Settings can show it. Written by an apply; changing it by hand syncs nothing. | unset |

Precedence: the process environment beats `settings.local`, which beats `controllarr.env`. Your apps hold the live quality and size values — Settings reads them back from the apps, never from a file here.

## The quality guide

Controllarr ships a compiled copy of the [TRaSH Guides](https://trash-guides.info) quality data in
`app/trash-guides/`: one JSON file per app, holding the guide's quality profiles, the custom formats those
profiles score, and the size limit per quality. It was compiled from
[TRaSH-Guides/Guides](https://github.com/TRaSH-Guides/Guides) `docs/json/radarr/` and `docs/json/sonarr/` at
commit `a63c1d05510d` on 31 August 2026, by `python3 app/trash.py vendor` — which is also what **Refresh the
guide** runs, into the state directory instead. The localised (German, French) profiles and the SQP series are
left out; everything else is carried, with exactly the custom formats it scores.

That data is MIT licensed, Copyright (c) 2021 TRaSH; the notice ships with it as `app/trash-guides/LICENSE`.
Controllarr itself is GPL-3.0, which may include it on those terms.

**This is the one thing that reaches outside your network**, and only when somebody presses *Refresh the
guide*: never at boot, never on a timer. Everything else the panel loads — fonts, icons, posters — is
vendored or proxied from your own apps. What a sync then writes is [DASHBOARD.md ▸ TRaSH Guides](DASHBOARD.md#trash-guides).

## Changing things later

- **Anything the installer asked:** re-run `./install.sh`.
- **The admin password:** Settings ▸ Users & roles (`CONTROLLARR_PASSWORD` only ever seeds the first account).
- **Sign everyone out:** delete `sessions.json` from the state directory and restart.
- **Locked out:** delete `users.json` and restart — `admin` is reseeded from `CONTROLLARR_PASSWORD`.
