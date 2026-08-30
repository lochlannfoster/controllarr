# Installing

`./install.sh` asks where your apps are, writes its own config, generates a small compose override for how this machine reaches them, and starts one container. Re-running it is the normal way to change an answer: previous answers are the defaults and API keys are kept. Everything is logged to `install-<timestamp>.log`.

Related: [CONFIGURATION.md](CONFIGURATION.md) (every file and value) · [DASHBOARD.md](DASHBOARD.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## Requirements

- Linux with **Docker Engine** and the **`docker compose` plugin**; `docker info` must work without `sudo`.
- `python3` and `curl` on the host.
- An \*arr stack already running somewhere this machine can reach.

## Before you start: the API keys

Each \*arr app has one, in **Settings ▸ General ▸ Security ▸ API Key**: Radarr, Sonarr, Prowlarr and Bazarr. Jellyfin's is **Dashboard ▸ API Keys** (add one for Controllarr); Jellyseerr's is **Settings ▸ General ▸ API Key**. qBittorrent has no key — it takes its Web UI username and password. ntfy needs neither.

Nothing is stored twice: if Controllarr can read your apps' config directory it reads the keys live instead, and you can say so at the last prompt.

## The prompts

| Section | Prompt | Notes |
|---|---|---|
| Basics | Address people use to reach this box · timezone · port · state directory · admin password | The address only shapes the links Controllarr shows. A blank password means **no login at all** — every visitor is an admin, which is only sane on a trusted LAN. |
| Networking | `host` or a Docker `network` | `host` shares this machine's network, so `localhost:7878` works — the usual answer when your apps publish ports here. `network` joins an existing Docker network and reaches apps by container name. |
| Your apps | For each: host (blank = not installed), port, API key | An app you skip is **absent**, not broken: its Settings group is hidden and nothing reports it as failed. |
| Download client | username, password · the release threshold your arrs use · download ceiling | Only asked when you configure one. The threshold is Radarr and Sonarr's own per-indexer setting; Controllarr reads the same number back so it can say why a title is stuck. The ceiling caps concurrent downloads. |
| Extras | Media path for free space · your apps' config directory · mount the Docker socket read-only? | All optional. The socket adds container state, memory and last log lines. |

## What it does, in order

1. Checks Docker, Compose and `python3`.
2. Writes `.env` (compose values) and `<state directory>/controllarr.env` (mode 600 — addresses, keys, live knobs).
3. Generates `docker-compose.override.yml`: the networking choice and the optional read-only mounts.
4. `docker compose up -d`, then waits for `/health` to answer.
5. Prints the URL, the apps it connected to, and where its state lives.

Your apps are **not** reconfigured at any point. Controllarr writes to them only when you press a control in Settings.

## After it finishes

Open `http://<address>:<port>` and sign in as `admin`. The Dash should be green and Needs attention should say *Nothing needs you*. If a section is empty, check that app's row in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Upgrading

```bash
git pull
docker compose --env-file .env restart controllarr
```

`app/` is mounted read-only into the container, so a pull plus a restart is the whole upgrade. Re-run `./install.sh` instead when the compose file or your answers changed.
