# Troubleshooting

Symptom → one check → one fix. Controllarr only ever reads your apps until you press a control, so most problems here are about reaching them.

Related: [INSTALL.md](INSTALL.md) · [CONFIGURATION.md](CONFIGURATION.md) · [DASHBOARD.md](DASHBOARD.md)

## First look

```bash
docker logs --tail 50 controllarr          # what it is failing to reach, one line per write
docker compose --env-file .env ps          # is it even up
tail -n 50 "$(ls -t install-*.log | head -1)"
```

Every failing source names itself in the section it belongs to — Controllarr never silently hides an app that stopped answering.

## Reaching your apps

| Symptom | Check | Fix |
|---|---|---|
| A section says an app *didn't answer* | `docker exec controllarr wget -qO- http://<host>:<port>/ping` (arrs) | Wrong address for Controllarr's point of view. With `host` networking use `localhost`; on a Docker network use the **container name**, not `localhost` — inside a container that means the container itself. Re-run `./install.sh`. |
| Everything unreachable, `network` mode | `docker network inspect <name>` lists both Controllarr and your apps | They must share a network. Re-run and pick the one your apps are on, or choose `host`. |
| An app answers but every call is refused | its API key | Copy it again from the app's Settings ▸ General ▸ Security and re-run `./install.sh` (a blank answer keeps the old one). |
| qBittorrent unreachable, everything else fine | its Web UI login | qBittorrent uses a username and password, not a key. Also check *Bypass authentication for clients on localhost* is off or your host is allowed. |
| Jellyfin's *Now playing* says the key is missing | Dashboard ▸ API Keys | Add a key for Controllarr and re-run the installer. |
| Downloads or Indexers missing from Settings | that app is not in `SERVICES` | Intended — you said you do not run it. Re-run `./install.sh` and give it an address. |

## The panel itself

| Symptom | Check | Fix |
|---|---|---|
| **Locked out** | `users.json` exists; the install password only seeded it | Delete `users.json` from the state directory and restart: `admin` is reseeded from `CONTROLLARR_PASSWORD`. Other accounts are lost. |
| **No login, everyone is an admin** | `CONTROLLARR_PASSWORD` is empty | Re-run `./install.sh` and set one. Until then treat the URL as public to your LAN. |
| **Sign everyone out** | sessions live 30 days on disk | Delete `sessions.json` and restart. |
| **Page is blank or the container restarts** | `docker logs controllarr` | Usually the config file is unreadable; re-run `./install.sh`. |
| **Refuses to start: *mode 0644 and holds your API keys*** | anyone with an account on the box could read your keys | Do what it says — `chmod 600 <state directory>/controllarr.env` — or re-run `./install.sh`, which repairs it. Why it is a refusal and not a warning: [CONFIGURATION.md ▸ Your keys](CONFIGURATION.md#your-keys). |
| **Changed a value but nothing happened** | `controllarr.env` is read once at start | `docker compose --env-file .env restart controllarr`. |
| **Old CSS or JS after a `git pull`** | assets are versioned per request | Restart the container; no hard reload needed. |
| **No container table in System** | you declined the Docker socket, or it is not mounted | Re-run `./install.sh` and say yes. Everything else works without it. |
| **Who did what?** | — | `docker logs controllarr --since 1h \| grep action=` — one line per write: user, role, action, target, result, duration. |

## What the library is telling you

| Symptom | Why | Fix |
|---|---|---|
| **Unavailable — No torrents found** | your indexers returned nothing for it | Check the indexers in Prowlarr (or in the arr itself); a Cloudflare-fronted one needs FlareSolverr. |
| **Unavailable — Only low-seed (max N)** | every candidate is below the release threshold your arrs are set to | Lower it in Settings ▸ Quality & size, or open the title and **Search…** to grab one by hand. Verdicts are cached three hours; **Refresh** re-checks. |
| **Rejected: too big / quality not allowed** | the app's own size limits or the title's quality profile | Put the title on a roomier profile (its row's quality chip), or apply a wider [TRaSH profile](DASHBOARD.md#trash-guides) — the size limits are per quality and come from the guide. |
| **A download is stalled** | the amber reason on the row says which (*dead swarm*, *none reachable*, *only N %*, *queued behind the cap*) | **Blocklist & retry**, or leave it — your stack's own cleanup will get to it. |
| **A show is Partial with files** | intended: any missing episode in a tracked season | Expand it, tick the episodes, **Search** or **Untrack**. |
| **A purged title is still in Jellyfin or Bazarr** | both are asked to rescan on a purge; a scan takes a moment | Wait a minute; Settings ▸ Media server ▸ **Scan library now**. |
| **System says *swap swapping* or *thrashing*** | pages are moving in and out — the box is short of RAM (what merely *sits* in swap reads *parked* and is harmless) | Find the hog in the container table; add RAM. |

## Still stuck

Open an issue with `docker logs --tail 100 controllarr`, the output of `docker compose --env-file .env config`, and which apps you configured. Never paste `controllarr.env` — it holds your keys.
