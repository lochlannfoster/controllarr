# Configuration reference

Two files, one directory. The installer writes both; the Settings page changes live values inside your apps.

Related: [INSTALL.md](INSTALL.md) (the prompts) · [DASHBOARD.md ▸ Settings](DASHBOARD.md#settings)

## The files

| File | Where | Contains | Mode |
|---|---|---|---|
| `.env` | install dir | Compose values only: `STACK_NAME`, `TZ`, `CONTROLLARR_DATA`, `CONTROLLARR_PORT`, `CONTROLLARR_REFRESH`, and the answers the installer needs to regenerate the override. | 644 |
| `controllarr.env` | the state directory | Where each app lives and how to authenticate: `SERVICES`, `<APP>_HOST`, `<APP>_PORT`, `<APP>_APIKEY`, `QBIT_USER`/`QBIT_PASS`, `CONTROLLARR_PASSWORD`, `SERVER_HOST`, and the optional `CONFIG_DIR`, `MEDIA_DIR`, `DOCKER_SOCK`. **The only copy of your keys.** | 600 |
| `docker-compose.override.yml` | install dir | The networking choice and the optional read-only mounts. Regenerated on every run — never edit. | 644 |
| `users.json`, `sessions.json`, `settings.local`, `cache/` | the state directory | Controllarr's own: accounts (PBKDF2-SHA256, 200 000 iterations), logins for 30 days, the values Settings saves for itself, and proxied posters. | 600 / 600 / 600 / — |

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
| `MIN_SEEDERS` · `MAX_ACTIVE_DL_CAP` | Radarr and Sonarr's own per-indexer release threshold, mirrored here so Controllarr can explain a stuck title · the ceiling it enforces on concurrent downloads. | `5` · `2` |

Precedence: the process environment beats `settings.local`, which beats `controllarr.env`. Your apps hold the live quality and size values — Settings reads them back from the apps, never from a file here.

## Changing things later

- **Anything the installer asked:** re-run `./install.sh`.
- **The admin password:** Settings ▸ Users & roles (`CONTROLLARR_PASSWORD` only ever seeds the first account).
- **Sign everyone out:** delete `sessions.json` from the state directory and restart.
- **Locked out:** delete `users.json` and restart — `admin` is reseeded from `CONTROLLARR_PASSWORD`.
