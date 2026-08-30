# Roadmap

What Controllarr intends to build next, why each item earns its place, and — as firmly — what it will never
build. Nothing here is promised in a release; the order is a dependency order, not a schedule. Each item
records the seam it touches and the thing most likely to go wrong, so the decision does not have to be
rediscovered when someone picks it up.

Related: [DEVELOPMENT.md](DEVELOPMENT.md) (how the code is put together) · [DASHBOARD.md](DASHBOARD.md) (what exists today)

## The line that decides

Controllarr is a **control surface, not an automation platform**. It shows what needs a person and gives that
person the button. Nothing happens that nobody pressed: no scheduled deletion, no rule engine acting on the
library overnight, no background tagger. Every idea below was judged against that sentence before anything
else, and several good ideas were rejected by it (see [Not building](#not-building)).

The neighbours that *do* automate — [Cleanuparr](https://github.com/Cleanuparr/Cleanuparr),
[Janitorr](https://github.com/Schaka/janitorr), [Maintainerr](https://github.com/Maintainerr/Maintainerr),
[Recyclarr](https://github.com/recyclarr/recyclarr) — run alongside Controllarr without conflict. Duplicating
them would cost the one property that makes this panel safe to hand to a household.

## Order

Dependency first, then cheapest to riskiest.

| # | Item | Done | Why here | Main seam |
|---|---|---|---|---|
| 1 | [Incognito mode](#1-incognito-mode) | ☑ | small, self-contained; unblocks honest screenshots for every later item | the render layer |
| 2 | [Secrets at rest](#2-secrets-at-rest) | ☑ | fix the credential choke point *before* four more credentials arrive | `services.load_env`, `services.apikey` |
| 3 | [The action log](#3-the-action-log) | ☐ | defines the event record that notifications will subscribe to | `do_action` |
| 4 | [Notification channels](#4-notification-channels) | ☐ | needs 2 (tokens) and 3 (events) | `ntfy_test`, `NTFY_URL` |
| 5 | [Calendar](#5-calendar) | ☐ | independent; the panel's missing axis | `panel_data`, a new source |
| 6 | [TRaSH Guides sync](#6-trash-guides-sync) | ☐ | largest, and the only one that rewrites hand-tuned profiles | `settings_ops` |

## 1. Incognito mode

**Built.** It is the header's **Incognito** switch — [DASHBOARD.md ▸ Incognito](DASHBOARD.md#incognito) says what
it covers and what it deliberately does not; [DEVELOPMENT.md §3.1](DEVELOPMENT.md#31-incognito-the-render-layer)
is the seam. The rest of this section is the reasoning it was built from.

**What.** A toggle that replaces every title, poster, requester and file name in the rendered page with a
neutral placeholder, so the panel can be screenshotted or screen-shared without exposing a library.
[arr-dashboard](https://github.com/Kha-kis/arr-dashboard) disguises items as "Linux ISO downloads"; the idea
is theirs and it is a good one.

**Why.** Today the only safe way to produce a screenshot is to boot `tests/fake_stack.py`. That is right for
the README and useless when someone wants to show a real problem in a support thread.

**Seam.** A flag on the session, not a build of the page. The substitution belongs at the point a row is
rendered, so the underlying id still reaches the action unchanged. Derive each pseudonym deterministically
from the id (a hash into an adjective–noun pair) so rows stay distinguishable across a refresh and a
screenshot sequence stays coherent.

**Watch.** The redaction must cover server-written text too. `/api/consequence` composes confirmation copy
with real titles and counts — *"deletes 3 episode files… that is the last of the show"* — and would leak the
name of the thing about to be purged. The `do_action` log line must keep the real target: an audit trail of
pseudonyms is not an audit trail.

## 2. Secrets at rest

**Built, as the third option below: the encryption was judged theatre and the plaintext path hardened instead.**
[CONFIGURATION.md ▸ Your keys](CONFIGURATION.md#your-keys) states what that does and does not protect against;
`services.py` holds the seam. Two things settled it. The panel starts unattended, so the passphrase would have
to sit on the same disk as the ciphertext and be readable by the same process. And with `CONFIG_DIR` set the
panel holds no copy of the key at all — it reads Radarr's own cleartext `config.xml`, which is not ours to
change, so encrypting our copy would leave the original untouched next to it. **A passphrase typed at start is
still the one thing that would genuinely help, and it is still the owner's trade to make** — it costs an
unattended restart, and it would only cover a stack whose config tree the panel cannot see. The rest of this
section is the reasoning it was built from.

**What.** API keys stop living in cleartext in `CONTROLLARR_ENV`, and are decrypted at boot instead.

**Why.** arr-dashboard encrypts every stored key with AES-256-GCM under a `secrets.json` it owns. Ours is a
`chmod`-protected env file — defensible on a single-owner LAN box, and the first objection anyone raises.
Every later item adds another credential (a Discord webhook, a Telegram token, an SMTP password, a GitHub
token for TRaSH), so the seam is worth settling before they arrive rather than after.

**Seam.** `services.load_env` and `services.apikey` are the only readers in the codebase. One place.

**Decide this before writing anything.** The standard library has no symmetric cipher, and "stdlib only, no
build step, one container" is not a detail of this project — it is the project. Three honest options:

- **Take the dependency** (`cryptography`). Correct crypto, and the end of the property above.
- **Ship a pure-Python ChaCha20-Poly1305** (~120 lines, RFC 8439). Runs once at boot over a handful of keys,
  so its speed is irrelevant — but hand-rolled crypto earns its place only with the RFC's own test vectors in
  `tests/unit` and a clear-eyed review, and it is still hand-rolled crypto.
- **Decide the encryption is theatre.** A key stored on the same disk as the ciphertext, readable by the same
  process, defends against approximately nothing an attacker with that disk cannot already do. Harden what
  exists instead: 0600 through `_own_like_dir`, a startup refusal on a world-readable config, keys never
  echoed into a response or a log line, and a documented threat model saying what this does and does not
  protect against.

A passphrase supplied at start would be genuinely stronger and costs an unattended restart. That trade is the
owner's to make, not the implementer's.

## 3. The action log

**What.** `do_action` already writes exactly one line per write — user, role, action, target, result, duration.
Tee it into a capped file the panel can show under Settings, filterable by user and action.

**Why.** Today that record lives in `docker logs controllarr` and disappears when the container is recreated.
Since Controllarr's destructive actions cascade across the whole stack, "who purged that, and when" deserves
an answer that survives a `compose up`.

**Seam.** `do_action` is the single funnel every write passes through; nothing else needs to know.

**Watch.** Cap it as a ring (a few thousand entries) so it cannot fill a disk the panel also monitors. Chown
it like every other panel-written file. It must never record an API key, a session cookie or a password. It
is read-only in the UI — there is no undo, and offering one would be a lie.

## 4. Notification channels

**What.** Generalise the single ntfy send into a small adapter set — Discord, Telegram, Gotify, SMTP — behind
a per-channel × per-event subscription grid, with delivery results visible.

**Why.** ntfy is excellent and it is the only thing the panel speaks. arr-dashboard ships eight channels
against twelve-plus event types; most of that is a webhook and a payload shape, and the events we would fire
on (stalled download, container down, disk threshold, pending request, tunnel down) are already computed for
**Needs attention**.

**Seam.** `ntfy_test` and the `NTFY_URL` handling in `settings.local`. Subscribe channels to the events from
item 3 plus the attention conditions — notify on *state*, not on every action, or a bulk purge becomes forty
messages.

**Watch.** Delivery must never block or fail a write: dispatch after `do_action` returns, and a dead channel
is a logged delivery failure, not a failed purge. More seriously, this is the **first thing in Controllarr
that sends data to a host outside the LAN**. The README's "nothing loaded from outside your network" is
currently about inbound loading; an outbound push to Discord carrying your titles is a real change in
posture. Keep it opt-in, per channel, and say so plainly in the docs rather than quietly widening the claim.

## 5. Calendar

**What.** What is due and when — upcoming episodes, film release dates — as a view.

**Why.** Every station on the Dash answers "what is true now". The panel has no time dimension at all, and
it is the one omission a person notices in the first hour.

**Seam.** Radarr and Sonarr both expose `/calendar`; a new source in `panel_data` with a long TTL, reported
per-source like every other.

**Watch.** It is a **view, not a station**. The Dash's seven stations describe the pipeline and must not grow
an eighth. Posters are already cached and served locally — the calendar must not start reaching out to TMDB
for art.

## 6. TRaSH Guides sync

**What.** Fetch the [TRaSH Guides](https://trash-guides.info) custom formats, scores and quality profiles;
show a real diff against what Radarr and Sonarr currently hold; apply on a press; roll back.

**Why.** Presets tune *behaviour* — speeds, seeding, size ceilings. TRaSH defines *quality correctness*, which
is the part people get wrong and cannot easily tell they have got wrong. `settings_ops._cf_ids` and
`settings_ops._format_scores` already create and score custom formats, so half the write path exists.

**Seam.** `settings_ops` stays the single writer of app settings. The Settings **Backup & config** group
already saves and loads a settings snapshot — the rollback is that snapshot taken automatically before an
apply, not a second mechanism invented alongside it.

**Watch.** This is the largest item and the only one that rewrites profiles a user may have hand-tuned, so
preview-then-apply is not a nicety. Recyclarr and Profilarr already do this well from a config file; our
reason to build it at all is that a person sees the diff and presses the button — if it ends up as a
scheduled sync, we have built a worse Recyclarr and broken [the line](#the-line-that-decides). It also pulls
from GitHub, which would be the first runtime fetch off the LAN: vendor a profile set, refresh only on
request, never at boot.

## Not building

Each of these exists in arr-dashboard and each is a deliberate no.

| Rejected | Because |
|---|---|
| Rule-based library cleanup (20+ conditions, approval queue) | a scheduled deletion engine is exactly the thing the panel promises not to be; Maintainerr and Janitorr do it |
| Auto-tagger (50+ criteria, webhook-driven) | background writes to your library with no person in the loop |
| Automated hunting (scheduled missing/upgrade searches) | Controllarr already searches ≤ 6 times per scan to *classify*; scheduling grabs is a different promise |
| Queue cleaner with a strike system | we surface the stalled download **with its cause** and one button; the automation belongs in Cleanuparr |
| Multi-instance aggregation | a fleet console is a different product, and it would cost the depth that justifies this one |
| Lidarr, Readarr, Plex, Emby, Tautulli | breadth over depth; Bazarr and qBittorrent are where our leverage is |
| TMDB discovery and browsing | Jellyseerr is already the request surface, and it is linked from the header |
| Any indexer, tracker or source list | never, in code, fixtures, docs or defaults |

## Where these came from

A survey of the field in August 2026. Nothing above is original to us except the judgement about which parts
belong in a control surface.

| Project | Overlap | What it taught us |
|---|---|---|
| [arr-dashboard](https://github.com/Kha-kis/arr-dashboard) | closest by feature count | items 1, 2, 4 and 6; also that a fleet console and a cockpit are different products |
| Prismarr | closest by philosophy | single container, embedded store, no external dependencies — the same instinct as ours |
| [qBitrr](https://github.com/Feramance/qBitrr) | the torrent layer | the one project that takes qBittorrent as seriously as we do |
| [Reiverr](https://github.com/aleksilassila/reiverr) | Jellyfin + arr | a media browser, not a control panel — a useful boundary |
| [Recyclarr](https://github.com/recyclarr/recyclarr) | TRaSH | the reference implementation of item 6, config-file shaped |
| [Cleanuparr](https://github.com/Cleanuparr/Cleanuparr) | queue health | the automation we deliberately do not duplicate |
| [awesome-arr](https://github.com/Ravencentric/awesome-arr) | the index | where to check before assuming something is unbuilt |

Controllarr is GPL-3.0 and arr-dashboard is MIT, so their code may be borrowed here; the reverse is not true.
Borrow ideas by preference and attribute either way.
