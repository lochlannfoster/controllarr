# Configuration reference

Two files, one directory. The installer writes both; the Settings page changes live values inside your apps.

Related: [INSTALL.md](INSTALL.md) (the prompts) · [DASHBOARD.md ▸ Settings](DASHBOARD.md#settings)

## The files

| File | Where | Contains | Mode |
|---|---|---|---|
| `.env` | install dir | Compose values only: `STACK_NAME`, `TZ`, `CONTROLLARR_DATA`, `CONTROLLARR_PORT`, `CONTROLLARR_REFRESH`, and the answers the installer needs to regenerate the override. | 644 |
| `controllarr.env` | the state directory | Where each app lives and how to authenticate: `SERVICES`, `<APP>_HOST`, `<APP>_PORT`, `<APP>_APIKEY`, `QBIT_USER`/`QBIT_PASS`, `CONTROLLARR_PASSWORD`, `SERVER_HOST`, and the optional `CONFIG_DIR`, `MEDIA_DIR`, `DOCKER_SOCK`. **The only copy of your keys.** | 600 |
| `docker-compose.override.yml` | install dir | The networking choice and the optional read-only mounts. Regenerated on every run — never edit. | 644 |
| `users.json`, `sessions.json`, `settings.local`, `cache/` | the state directory | Controllarr's own: accounts (PBKDF2-SHA256, 200 000 iterations), logins for 30 days, the values Settings saves for itself, and proxied posters. | 600 / 600 / 644 / — |

**The state directory is the only thing worth backing up.** Everything else is either regenerated or lives in your apps.

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
