"""HTTP-level behaviour of Controllarr against a fake *arr stack: the auth gate, the HTTP/1.1 invariants,
capability enforcement, the JSON contracts of every section, ETags, static caching, the source-failure
reporting, what an install without a given service does, and the action → app wiring (asserted on the
fake's call log)."""
import http.client, json, os, re, stat, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fake_stack, harness

H = None
def setUpModule():
    global H
    H = harness.Harness().start()
def tearDownModule():
    H.stop()


class AuthGate(unittest.TestCase):
    def test_health_is_the_only_public_route(self):
        self.assertEqual(H.get("/health")[0], 200)
        for path in ("/", "/settings", "/api/board", "/api/attention", "/api/me", "/img/poster/movie/1", "/dashboard", "/docs"):
            st, hd, raw = H.get(path)
            self.assertEqual(st, 302, path); self.assertTrue(hd["Location"].startswith("/login"), path); self.assertEqual(hd.get("Content-Length"), "0", path)
        self.assertEqual(H.get("/?item=movie:2")[1]["Location"], "/login?next=%2F%3Fitem%3Dmovie%3A2")
        self.assertEqual(H.get("/api/board")[1]["Location"], "/login")   # API paths are never a post-login destination
    def test_login_page_and_wrong_password(self):
        st, hd, raw = H.get("/login?next=%2Fsettings"); self.assertEqual(st, 200); self.assertIn(b'name="next" value="/settings"', raw); self.assertNotIn(b"__ERR__", raw)
        self.assertIn(b'value=""', H.get("/login?next=http://evil.example/")[2])
        self.assertIsNone(H.login("admin", "wrong")); self.assertIsNone(H.login("ghost", harness.PASSWORD))
        st, hd, raw = H.request("POST", "/login", body="username=admin&password=wrong", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 200); self.assertIn(b"Wrong username or password", raw)
    def test_login_sets_a_hardened_cookie_and_follows_next(self):
        st, hd, raw = H.request("POST", "/login", body="username=admin&password=" + harness.PASSWORD + "&next=%2F%3Fitem%3Dmovie%3A2", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual((st, hd["Location"]), (302, "/?item=movie:2")); ck = hd["Set-Cookie"]
        self.assertIn("HttpOnly", ck); self.assertIn("SameSite=Lax", ck); self.assertIn("Path=/", ck); self.assertTrue(ck.startswith("sb="))
        cookie = ck.split(";")[0]
        st, me = H.json("/api/me", cookie); self.assertEqual(me, {"user": "admin", "role": "admin", "caps": {c: True for c in me["caps"]}, "auth": True})
        st, hd, raw = H.get("/logout", cookie); self.assertEqual((st, hd["Location"]), (302, "/login")); self.assertIn("Max-Age=0", hd["Set-Cookie"])
        self.assertEqual(H.get("/api/me", cookie)[0], 302)
    def test_sessions_and_users_are_private_files(self):
        for f in ("sessions.json", "users.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(H.sb_dir, f)).st_mode), 0o600, f)
    def test_old_bookmarks_redirect(self):
        c = H.admin_cookie()
        self.assertEqual(H.get("/dashboard", c)[1]["Location"], "/#live"); self.assertEqual(H.get("/docs", c)[1]["Location"], "/#reference")


class Http11Invariants(unittest.TestCase):
    def test_every_response_carries_content_length_over_keepalive(self):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=10)
        cookie = H.admin_cookie()
        st, hd, raw = H.request("GET", "/dashboard", cookie=cookie, conn=conn); self.assertEqual((st, hd["Content-Length"]), (302, "0"))
        st, hd, raw = H.request("GET", "/api/me", cookie=cookie, conn=conn); self.assertEqual(st, 200)                        # same connection still usable
        st, hd, raw = H.request("GET", "/health", conn=conn); self.assertEqual((st, raw), (200, b"ok")); conn.close()
    def test_post_body_is_drained_before_an_early_return(self):
        """A 401/415 that left the body unread would be parsed as the next request on the same connection (a 501)."""
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=10)
        st, hd, raw = H.request("POST", "/api/action", body={"action": "retry"}, conn=conn); self.assertEqual(st, 401)      # no cookie
        st, hd, raw = H.request("GET", "/health", conn=conn); self.assertEqual((st, raw), (200, b"ok"))
        cookie = H.admin_cookie()
        st, hd, raw = H.request("POST", "/api/action", body="action=retry", headers={"Content-Type": "application/x-www-form-urlencoded"}, cookie=cookie, conn=conn)
        self.assertEqual(st, 415)                                                                                          # CSRF: JSON only
        st, hd, raw = H.request("GET", "/health", conn=conn); self.assertEqual((st, raw), (200, b"ok")); conn.close()
    def test_head_is_not_implemented(self):
        self.assertEqual(H.request("HEAD", "/health")[0], 501)
    def test_unknown_api_path_is_json_404_never_the_page(self):
        c = H.admin_cookie()
        st, j = H.json("/api/nope", c); self.assertEqual((st, j), (404, {"ok": False, "message": "not found"}))
        st, hd, raw = H.request("POST", "/api/nope", body={}, cookie=c); self.assertEqual(st, 404)
        st, hd, raw = H.get("/whatever", c); self.assertEqual(st, 200); self.assertIn(b"<title>Controllarr</title>", raw)   # non-API paths get the page


class StaticAssets(unittest.TestCase):
    def test_versioned_assets_are_immutable_and_etagged(self):
        st, hd, raw = H.get("/static/app.js?v=abc"); self.assertEqual(st, 200)
        self.assertEqual(hd["Cache-Control"], "public, max-age=31536000, immutable"); self.assertIn(b"./modules/dom.js?v=", raw)
        st2, hd2, raw2 = H.get("/static/app.js?v=abc", headers={"If-None-Match": hd["ETag"]}); self.assertEqual((st2, hd2["Content-Length"]), (304, "0"))
        self.assertEqual(H.get("/static/app.js")[1]["Cache-Control"], "no-cache")
        self.assertEqual(H.get("/static/fonts/ibm-plex-mono-latin-400.woff2")[1]["Cache-Control"], "public, max-age=31536000, immutable")
    def test_no_escape_no_listing(self):
        for p in ("/static/../controllarr.py", "/static/..%2Fcontrollarr.py", "/static/", "/static/nope.js", "/static/index.html"):
            self.assertEqual(H.get(p)[0], 404, p)
    def test_page_carries_the_version_and_the_client_config(self):
        c = H.admin_cookie(); st, hd, raw = H.get("/", c); page = raw.decode()
        self.assertNotIn("__VER__", page); self.assertNotIn("__CONFIG__", page); self.assertNotIn("__DOCS__", page)
        cfg = json.loads(page.split("window.MC = ", 1)[1].split(";</script>", 1)[0])
        self.assertEqual(cfg["role"], "admin"); self.assertEqual(cfg["stages"], fake_stack and ["Unavailable", "Searching", "Downloading", "Importing", "Partial", "Waiting", "Available"])
        self.assertFalse(cfg["vpn"]); self.assertEqual(cfg["maxActive"], 2); self.assertTrue(cfg["auth"]); self.assertEqual(cfg["hostname"], "fakehost")
        self.assertEqual(cfg["links"]["jellyfin"], "http://127.0.0.1:8096"); self.assertEqual(cfg["links"]["jellyseerr"], "http://127.0.0.1:5055")   # the shortcuts under Needs attention
        self.assertEqual(cfg["services"], fake_stack.SERVICES)   # what this install connects to: Settings hides the groups of a service you do not run


class Sections(unittest.TestCase):
    """Runs on its own fresh panel + reset data so no other class's writes or warm caches leak in."""
    @classmethod
    def setUpClass(cls): H.control(reset=True); cls.h = harness.Harness().start(); cls.c = cls.h.admin_cookie()
    @classmethod
    def tearDownClass(cls): cls.h.stop()
    def json(self, path): return self.h.json(path, self.c)
    def get(self, path, **kw): return self.h.get(path, self.c, **kw)
    def test_attention_lists_every_kind_the_fake_produces(self):
        st, j = self.json("/api/attention")
        kinds = {i["kind"] for i in j["items"]}; self.assertEqual(kinds, {"stalled", "import", "indexer", "unavailable", "request"})
        for k, m in j["sources"].items(): self.assertTrue(m["ok"], (k, m))
        stalled = next(i for i in j["items"] if i["kind"] == "stalled")
        self.assertEqual(stalled["title"], "Blade Runner 2049 — stalled"); self.assertIn("dead swarm", stalled["detail"])
        self.assertEqual([a["label"] for a in stalled["actions"]], ["Blocklist & retry", "Open", "Reannounce"]); self.assertEqual(stalled["actions"][0]["cap"], "can_remove")
        self.assertEqual([i["sev"] for i in j["items"]], sorted([i["sev"] for i in j["items"]], key={"danger": 0, "warn": 1, "info": 2}.get))
    def test_live_reference_and_board(self):
        st, live = self.json("/api/live")
        self.assertEqual([t["state"] for t in live["torrents"]], ["stalledDL", "uploading", "uploading", "downloading"]); self.assertEqual(live["transfer"]["dht"], 312)
        exp = [t for t in live["torrents"] if t["kind"] == "tv"]   # episode labels from the Sonarr queue: the record's episode object, else the release name
        self.assertEqual([(t["label"], t["season"], t["iid"], t["matched"]) for t in exp], [("S02E01", 2, 12, True), ("S02E02 · Doors & Corners", 2, 12, True)])
        self.assertEqual(live["sessions"][0]["method"], "Transcode"); self.assertEqual(live["vpn"], {"enabled": False})
        self.assertEqual(self.json("/api/flow")[0], 404)   # Flow is gone: an old tab polling it gets the JSON 404, never the page
        st, ref = self.json("/api/reference")
        self.assertEqual([a["name"] for a in ref["apps"]], ["Jellyfin", "Jellyseerr", "Radarr", "Sonarr", "Prowlarr", "qBittorrent", "Bazarr", "ntfy"])
        self.assertEqual(ref["apps"][2]["version"], "5.0.0-fake"); self.assertTrue(ref["jellyseerr_update"])
        st, board = self.json("/api/board")
        self.assertEqual(board["summary"]["Available"], 2); self.assertEqual(next(i for i in board["items"] if i["id"] == 2)["live"]["pct"], 42)
        self.assertEqual(next(i for i in board["items"] if i["title"] == "Arrival")["sub"], "ok")
    def test_board_etag_revalidates(self):
        st, hd, raw = self.get("/api/board"); etag = hd["ETag"]
        st, hd, raw = self.get("/api/board", headers={"If-None-Match": etag}); self.assertEqual((st, hd["Content-Length"]), (304, "0"))
        self.assertEqual(hd["Cache-Control"], "private, no-cache")
    def test_drawer_data(self):
        st, d = self.json("/api/item?kind=movie&id=2")
        self.assertEqual((d["title"], d["stage"], len(d["torrents"]), [p["name"] for p in d["profiles"]]), ("Blade Runner 2049", "Downloading", 1, ["Any", "HD-1080p"]))
        st, d = self.json("/api/item?kind=tv&id=12"); self.assertEqual([s["season"] for s in d["seasons"]], [1, 2]); self.assertEqual(d["sub_missing"], 1)
        self.assertEqual(self.json("/api/item?kind=movie&id=999")[1], {})
        st, d = self.json("/api/item?kind=movie&id=1"); self.assertEqual([t["name"] for t in d["torrents"]], ["Arrival.2016.1080p.WEB-DL"])   # seeding, out of the queue: found through Radarr's history
        st, eps = self.json("/api/episodes?seriesId=12"); self.assertEqual(len(eps), 13)
        st, rel = self.json("/api/releases?kind=movie&id=2"); self.assertEqual(rel[0]["quality"], "Remux-2160p"); self.assertTrue(rel[0]["rejected"])
        st, rel = self.json("/api/releases?kind=tv&id=12"); self.assertEqual(rel[0]["title"], "The.Expanse.S02.1080p")   # first missing season
        st, cq = self.json("/api/consequence?action=purge&kind=movie&id=2&title=Blade+Runner+2049&tmdbId=102")
        self.assertEqual(cq["title"], "Purge Blade Runner 2049"); self.assertIn("removes 1 torrent from qBittorrent and the Jellyseerr request", cq["text"])
        st, hd, raw = self.get("/img/poster/movie/2?size=250"); self.assertEqual((st, hd["Content-Type"]), (200, "image/png"))
        self.assertEqual(self.get("/img/poster/movie/2?size=999")[0], 404); self.assertEqual(self.get("/img/poster/book/2")[0], 404)
    def test_series_tree_maps_episodes_to_their_torrents(self):
        st, t = self.json("/api/series-tree?seriesId=12")
        self.assertEqual([s["season"] for s in t["seasons"]], [1, 2]); self.assertEqual(t["seasons"][1]["hashes"], sorted([fake_stack.IMPORT_HASH, fake_stack.EP2_HASH]))
        by = {e["id"]: e for e in t["episodes"]}; self.assertEqual(len(by), 13)
        self.assertEqual(by[1201]["torrent"]["hash"], fake_stack.IMPORT_HASH); self.assertEqual((by[1202]["torrent"]["progress"], by[1202]["torrent"]["state"]), (30, "downloading")); self.assertIsNone(by[1203]["torrent"])
        # per episode: the file's size and Bazarr's subtitle verdict; per season: the size on disk; the show's runtime
        self.assertEqual((by[1101]["size"], by[1101]["sub"]), (3_000_000_000, True)); self.assertIs(by[1110]["sub"], False); self.assertEqual((by[1201]["size"], by[1201]["sub"]), (0, None))
        self.assertEqual((t["seasons"][0]["size"], t["seasons"][1]["size"], t["runtime"]), (30_000_000_000, 0, 42))
        st, board = self.json("/api/board"); ex = next(i for i in board["items"] if i["id"] == 12 and i["kind"] == "tv")
        self.assertEqual((ex["runtime"], ex["have"], ex["total"]), (42, 10, 23)); self.assertEqual(next(i for i in board["items"] if i["title"] == "Arrival")["runtime"], 116)
        self.assertEqual(self.json("/api/series-tree?seriesId=999")[1], {}); self.assertEqual(self.json("/api/series-tree")[1], {})
    def test_system_monitor_has_the_host_every_container_and_their_tasks(self):
        st, j = self.json("/api/system")
        H = j["host"]; self.assertIn("mem_total", H); self.assertTrue(H["mem_total"] > 0); self.assertEqual(len(H["load"]), 3); self.assertIn("disk_pct", H["disk"])
        rows = {r["name"]: r for r in j["containers"]}; self.assertEqual(set(rows), set(fake_stack.EXPECTED))
        self.assertEqual(rows["radarr"]["task"], "RSS sync, searching"); self.assertEqual(rows["prowlarr"]["task"], "syncing indexers")
        self.assertEqual(rows["jellyfin"]["task"], "Scan Media Library 42 %"); self.assertEqual(rows["jellyseerr"]["task"], "Download Sync"); self.assertEqual(rows["bazarr"]["task"], "Search for Missing Movies Subtitles")
        self.assertEqual(rows["qbittorrent"]["task"], "2 downloading · 2 seeding"); self.assertTrue(rows["controllarr"]["task"].startswith("library scan"))
        self.assertEqual(rows["radarr"]["log"], "[Info] radarr is running"); self.assertEqual(rows["radarr"]["mem_mb"], 136)   # usage minus inactive file cache
        self.assertTrue(all(m["ok"] for m in j["sources"].values()), j["sources"])
        st, j2 = self.json("/api/system"); self.assertIsInstance(j2["host"]["cpu_pct"], int)   # a delta needs two reads
    def test_granular_purge_consequences_carry_real_counts(self):
        st, cq = self.json(f"/api/consequence?action=t_purge&hash={fake_stack.EP2_HASH}&name=The+Expanse+S02E02")
        self.assertEqual(cq["title"], "Purge The Expanse S02E02"); self.assertIn("S02E02 (1 episode)", cq["text"]); self.assertIn("unmonitored", cq["text"])
        st, cq = self.json(f"/api/consequence?action=t_purge&hash={fake_stack.STALLED_HASH}"); self.assertIn("the whole movie", cq["text"])
        st, cq = self.json("/api/consequence?action=season_purge&kind=tv&id=12&season=2&title=The+Expanse")
        self.assertEqual(cq["title"], "Purge season 2 of The Expanse"); self.assertIn("removes 2 torrents", cq["text"]); self.assertIn("3 episodes affected", cq["text"])
        st, cq = self.json("/api/consequence?action=episode_purge&kind=tv&id=12&episodeIds=1201&title=The+Expanse")
        self.assertEqual(cq["title"], "Purge S02E01 of The Expanse"); self.assertIn("1 torrent", cq["text"])
        st, cq = self.json("/api/consequence?action=config_preset&name=Overclock"); self.assertEqual(cq["title"], "Apply preset Overclock"); self.assertIn("No speed limits", cq["text"])
    def test_incognito_redacts_the_confirmation_but_never_the_log_line(self):
        """A page drawing pseudonyms hands one in; the server must not put a real name back — and the write
        that follows is still logged against the real target (docs/DASHBOARD.md, Incognito)."""
        st, cq = self.json("/api/consequence?action=blocklist_retry&kind=movie&id=2&title=Quiet+Otter+41&incognito=1")
        self.assertEqual(cq["title"], "Blocklist & retry Quiet Otter 41")
        self.assertNotIn("Blade", cq["text"]); self.assertIn("the current download", cq["text"]); self.assertIn("torrent", cq["text"])
        st, cq = self.json("/api/consequence?action=qall_pause&incognito=1")
        self.assertNotIn("Arrival", cq["text"]); self.assertNotIn("Blade", cq["text"])
        self.assertIn("Stops 4 torrents", cq["text"])                                       # every count survives
        st, cq = self.json("/api/consequence?action=qall_pause"); self.assertIn("Blade", cq["text"])   # switched off it names them
        st, j = self.h.post("/api/action", {"action": "retry", "kind": "movie", "id": 2, "title": "Quiet Otter 41"}, self.c)
        self.assertTrue(j["ok"], j)
        self.assertIn("action=retry target=movie:2", self.h.panel_log())
    def test_attention_items_name_the_words_incognito_has_to_replace(self):
        """The substitution happens in the browser; the server only says which words are a real name."""
        st, j = self.json("/api/attention")
        subj = {i["kind"]: i.get("subjects") for i in j["items"]}
        self.assertEqual(subj["stalled"], [{"text": "Blade Runner 2049", "key": "movie:2", "who": False}])
        self.assertEqual(subj["import"], [{"text": "The.Expanse.S02E01.1080p", "key": "tv:12", "who": False}])
        self.assertEqual(subj["request"], [{"text": "sam", "key": "sam", "who": True}])
        self.assertEqual(subj["unavailable"], [{"text": "Coherence", "key": "movie:3", "who": False},
                                               {"text": "sam", "key": "sam", "who": True}])   # its requester too
        for kind, subs in subj.items():                      # every listed word really is in the item it belongs to
            i = next(x for x in j["items"] if x["kind"] == kind)
            for sub in subs or []:
                self.assertIn(sub["text"], " ".join([i["title"], i.get("detail") or ""] + (i.get("facts") or [])), kind)


class SecretsAtRest(unittest.TestCase):
    """docs/CONFIGURATION.md: the keys are not encrypted and the reasoning is written down. The two things
    that ARE promised — the file is the owner's alone, and no key leaves the panel — are asserted here."""
    def test_the_panel_refuses_to_start_on_a_world_readable_config(self):
        import subprocess, tempfile
        root = tempfile.mkdtemp(prefix="mc-perm-"); cfg = os.path.join(root, "app.env")
        with open(cfg, "w") as f: f.write("RADARR_APIKEY=0123456789abcdef0123\n")
        os.chmod(cfg, 0o644)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CONTROLLARR_ENV": cfg, "CONTROLLARR_DIR": root,
               "CONTROLLARR_PORT": str(harness.free_port()), "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, os.path.join(harness.APP, "controllarr.py")], env=env, cwd=root,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("refusing to start", r.stdout); self.assertIn("0644", r.stdout)
        self.assertIn(f"chmod 600 {cfg}", r.stdout)                       # it says exactly how to fix it
        self.assertNotIn("0123456789abcdef0123", r.stdout + r.stderr)     # and never echoes the key it is protecting
    def test_no_endpoint_and_no_log_line_carries_a_key(self):
        c = H.admin_cookie()
        paths = ["/", "/settings", "/api/board", "/api/attention", "/api/live", "/api/system", "/api/reference",
                 "/api/me", "/api/users", "/api/roles", "/api/log", "/api/config/export", "/api/config/presets",
                 "/api/item?kind=movie&id=2", "/api/series-tree?seriesId=12", "/api/episodes?seriesId=12",
                 "/api/rootfolders?kind=movie", "/api/qualityprofiles?kind=movie", "/api/releases?kind=movie&id=2",
                 "/api/sub-search?kind=movie&id=1", "/api/consequence?action=purge&kind=movie&id=2",
                 "/api/set/radarr", "/api/set/qbit", "/api/set/bazarr", "/api/set/notify", "/api/set/jellyfin"]
        seen = []
        for p in paths:
            body = H.get(p, c)[2].decode(errors="replace")
            for name, key in fake_stack.KEYS.items():
                if key in body: seen.append((p, name))
            if fake_stack.QBIT_PASS in body: seen.append((p, "qbit password"))
        self.assertEqual(seen, [])
        log = H.panel_log()
        self.assertEqual([k for k in list(fake_stack.KEYS.values()) + [fake_stack.QBIT_PASS] if k in log], [])
        # and the guarantee has teeth: ask the panel to echo a key straight back into a response body
        key = fake_stack.KEYS["radarr"]
        st, cq = H.json("/api/consequence?action=t_delete&name=" + key, c)
        self.assertIn("Removes *** from qBittorrent", cq["text"]); self.assertNotIn(key, json.dumps(cq))
    def test_the_overrides_file_the_panel_writes_is_private(self):
        """settings.local carries the ntfy URL, which can carry a token."""
        st, j = H.post("/api/set/notify", {"quiet_start": "1", "quiet_end": "8", "topic_media": "media",
                                           "topic_admin": "admin", "ntfy_url": "http://ntfy.example/tkn_secret"}, H.admin_cookie())
        self.assertTrue(j.get("ok"), j)
        local = os.path.join(H.sb_dir, "settings.local")
        self.assertEqual(stat.S_IMODE(os.stat(local).st_mode), 0o600)
        st, j = H.json("/api/config/export", H.admin_cookie())            # a snapshot is made to be shared
        self.assertNotIn("ntfy_url", j.get("notify", {}))
        self.assertNotIn("tkn_secret", json.dumps(j))


class SourceFailures(unittest.TestCase):
    def test_a_dead_service_is_reported_not_hidden(self):
        """Fresh panel so no cached value masks the outage; the section keeps working and names the source."""
        H.control(down=["prowlarr", "jellyfin", "docker"])
        try:
            with harness.Harness() as h2:
                c = h2.admin_cookie()
                st, j = h2.json("/api/attention", c)
                self.assertFalse(j["sources"]["prowlarr_health"]["ok"]); self.assertIn("unreachable", j["sources"]["prowlarr_health"]["err"])
                self.assertFalse(j["sources"]["services"]["ok"]); self.assertTrue(j["sources"]["queue"]["ok"])   # the rest of the section still works
                st, live = h2.json("/api/live", c); self.assertFalse(live["sources"]["jellyfin"]["ok"]); self.assertIsNone(live["sessions"])
                st, ref = h2.json("/api/reference", c); self.assertFalse(ref["sources"]["services"]["ok"]); self.assertEqual(len(ref["apps"]), 8)   # no Docker view: every configured app listed
        finally:
            H.control(up=["prowlarr", "jellyfin", "docker"])
    def test_container_down_scenario(self):
        H.control(scenario="container_down")
        try:
            with harness.Harness() as h2:
                st, j = h2.json("/api/attention", h2.admin_cookie())
                svc = next(i for i in j["items"] if i["kind"] == "container"); self.assertEqual((svc["title"], svc["sev"]), ("radarr has stopped", "danger"))
                st, ref = h2.json("/api/reference", h2.admin_cookie()); self.assertEqual(next(a for a in ref["apps"] if a["name"] == "Radarr")["state"], "exited")
        finally:
            H.control(reset=True)
    def test_backup_stale_scenario(self):
        with harness.Harness(backups_enabled=True) as h2:
            st, j = h2.json("/api/attention", h2.admin_cookie())
            self.assertIn("No recent config backup", [i["title"] for i in j["items"]])


class WhatThisInstallRuns(unittest.TestCase):
    """Controllarr attaches to whatever *arr stack it is given. A service that is not configured is absent —
    hidden, not broken — and without a Docker socket it simply does not report on containers."""
    def test_a_service_you_do_not_run_is_absent_not_broken(self):
        with harness.Harness(extra_env={"SERVICES": "radarr,sonarr,bazarr,jellyfin,jellyseerr"}) as h:   # no qBittorrent, no Prowlarr
            c = h.admin_cookie()
            page = h.get("/", c)[2].decode()
            self.assertEqual(json.loads(page.split("window.MC = ", 1)[1].split(";</script>", 1)[0])["services"], ["radarr", "sonarr", "bazarr", "jellyfin", "jellyseerr"])
            st, live = h.json("/api/live", c)
            self.assertEqual((live["torrents"], live["transfer"]), ([], None)); self.assertNotIn("qbit", live["sources"])
            st, att = h.json("/api/attention", c)
            self.assertNotIn("qbit", att["sources"]); self.assertNotIn("prowlarr_health", att["sources"]); self.assertNotIn("flaresolverr", att["sources"])
            self.assertEqual([i for i in att["items"] if i["kind"] == "stalled"], [])   # no download client: nothing to be stalled
            # Radarr's OWN indexer health warning still belongs on the list — it is the arr talking, not Prowlarr —
            # but without Prowlarr there is nothing to offer "Test all indexers" against
            idx = [i for i in att["items"] if i["kind"] == "indexer"]
            self.assertEqual(len(idx), 1); self.assertTrue(idx[0]["title"].startswith("radarr:")); self.assertEqual(idx[0]["actions"], [])
            st, ref = h.json("/api/reference", c)
            self.assertEqual([a["name"] for a in ref["apps"]], ["Jellyfin", "Jellyseerr", "Radarr", "Sonarr", "Bazarr"])   # no qBittorrent, Prowlarr or ntfy
            self.assertTrue(all(m["ok"] for m in ref["sources"].values()), ref["sources"])
    def test_without_a_docker_socket_containers_are_simply_not_offered(self):
        with harness.Harness(extra_env={"DOCKER_SOCK": ""}) as h:
            c = h.admin_cookie()
            st, sysj = h.json("/api/system", c)
            self.assertEqual(sysj["containers"], []); self.assertNotIn("docker", sysj["sources"])
            self.assertIsInstance(sysj["host"]["mem_total"], int)                      # the host figures still come from /proc
            st, att = h.json("/api/attention", c)
            self.assertNotIn("services", att["sources"])                                   # nothing was asked of Docker…
            self.assertEqual(att["sources"]["vpn"], {"ok": True, "age_s": 0, "err": None})   # …and no source is reported as failing
            self.assertEqual([i for i in att["items"] if i["kind"] in ("container", "vpn", "orphaned")], [])
            self.assertTrue(all(m["ok"] for m in att["sources"].values()), att["sources"])
            st, ref = h.json("/api/reference", c); self.assertEqual(len(ref["apps"]), 8)   # every configured app, no container state


class Actions(unittest.TestCase):
    """Writes: own panel, own copy of the data (reset afterwards) so the read-only classes never see them."""
    @classmethod
    def setUpClass(cls):
        H.control(reset=True); cls.h = harness.Harness().start(); cls.h.add_user("viewer", "viewer-pw")
        cls.admin = cls.h.admin_cookie(); cls.viewer = cls.h.login("viewer", "viewer-pw"); assert cls.viewer
    @classmethod
    def tearDownClass(cls): cls.h.stop(); H.control(reset=True)
    def setUp(self): self.h.control(clear_calls=True)
    def act(self, body, cookie=None): return self.h.post("/api/action", body, cookie or self.admin)
    def test_privileged_actions_need_a_capability(self):
        for action in ("purge", "t_delete", "t_purge", "season_purge", "episode_purge", "episode_delete_files", "alt_set", "qall_pause", "req_approve", "import_library", "blocklist_retry"):
            st, j = self.act({"action": action, "kind": "movie", "id": 2, "hash": "deadbeef", "reqId": 31}, self.viewer)
            self.assertEqual((st, j), (403, {"ok": False, "message": "Not permitted (ask an admin)"}), action)
        self.assertEqual(self.h.calls("radarr", "POST"), [])                                              # nothing reached a backend
        st, j = self.act({"action": "retry", "kind": "movie", "id": 2}, self.viewer); self.assertEqual((st, j["ok"]), (200, True))   # ordinary action is fine
    def test_admin_routes_are_gated(self):
        for path in ("/api/users", "/api/roles", "/api/set/qbit", "/api/config/export", "/api/config/presets",
                     "/api/trash", "/api/trash/plan?app=radarr&name=WEB%201080p"): self.assertEqual(self.h.get(path, self.viewer)[0], 403, path)
        for path in ("/api/users", "/api/roles", "/api/set/qbit", "/api/config/defaults", "/api/prowlarr", "/api/ntfy-test",
                     "/api/trash/apply", "/api/trash/rollback", "/api/trash/refresh"):
            self.assertEqual(self.h.post(path, {}, self.viewer)[0], 403, path)
        self.assertEqual(self.h.get("/settings", self.viewer)[1]["Location"], "/"); self.assertEqual(self.h.get("/settings", self.admin)[0], 200)
        st, me = self.h.json("/api/me", self.viewer); self.assertEqual(me["role"], "user"); self.assertFalse(any(me["caps"].values()))
    def test_actions_reach_the_right_backend(self):
        st, j = self.act({"action": "retry", "kind": "movie", "id": 2}); self.assertEqual(j, {"ok": True, "message": "Search triggered"})
        self.assertEqual(self.h.calls("radarr", "POST", "MoviesSearch")[0][3]["movieIds"], [2])
        st, j = self.act({"action": "refresh", "kind": "tv", "id": 12}); self.assertEqual(self.h.calls("sonarr", "POST", "RefreshSeries")[0][3]["seriesId"], 12)
        st, j = self.act({"action": "t_reannounce", "hash": fake_stack.STALLED_HASH}); self.assertEqual(j["message"], "Reannounced to trackers")
        self.assertEqual(self.h.calls("qbittorrent", "POST", "torrents/reannounce")[0][3]["hashes"], fake_stack.STALLED_HASH)
        st, j = self.act({"action": "qall_pause"}); self.assertEqual(j["message"], "All paused"); self.assertEqual(self.h.calls("qbittorrent", "POST", "torrents/stop")[0][3]["hashes"], "all")
        st, j = self.act({"action": "req_approve", "reqId": 32}); self.assertTrue(j["ok"]); self.assertTrue(self.h.calls("jellyseerr", "POST", "/request/32/approve"))
        st, j = self.act({"action": "indexers_test_all"}); self.assertEqual(j["message"], "Tested 1 indexers — all passed")
        st, j = self.act({"action": "jf_scan"}); self.assertEqual(j["message"], "Jellyfin library scan started"); self.assertTrue(self.h.calls("jellyfin", "POST", "/Library/Refresh"))
        st, j = self.act({"action": "t_pause"}); self.assertEqual((st, j["message"]), (400, "no hash"))
        st, j = self.act({"action": "t_pause", "hash": "not-a-hash"}); self.assertEqual((st, j["message"]), (400, "no hash"))              # validated, never forwarded
        both = fake_stack.IMPORT_HASH + "|" + fake_stack.EP2_HASH                                                                        # a group / a season is one call
        st, j = self.act({"action": "t_top", "hash": both.upper()}); self.assertTrue(j["ok"]); self.assertEqual(self.h.calls("qbittorrent", "POST", "torrents/topPrio")[0][3]["hashes"], both)
        st, j = self.act({"action": "season_search", "kind": "tv", "id": 12, "season": 2}); self.assertEqual(j["message"], "Searching season 2")
        st, j = self.act({"action": "episode_search", "kind": "tv", "id": 12, "episodeIds": [1201, 1202]}); self.assertEqual(j["message"], "Searching 2 episodes")   # the toolbar: one command for the ticked set
        self.assertEqual(self.h.calls("sonarr", "POST", "EpisodeSearch")[-1][3]["episodeIds"], [1201, 1202])
        st, j = self.act({"action": "episode_monitor", "kind": "tv", "id": 12, "episodeIds": "1201,1203", "monitored": False}); self.assertEqual(j["message"], "2 episodes untracked")
        self.assertEqual(self.h.calls("sonarr", "PUT", "/episode/monitor")[-1][3], {"episodeIds": [1201, 1203], "monitored": False})
        st, j = self.act({"action": "episode_delete_files", "kind": "tv", "id": 12, "episodeFileIds": "77,78"}); self.assertEqual(j["message"], "2 episode files deleted")
        self.assertEqual([c[2] for c in self.h.calls("sonarr", "DELETE") if "/episodefile/" in c[2]], ["/api/v3/episodefile/77", "/api/v3/episodefile/78"])
        st, j = self.act({"action": "episode_search", "kind": "tv", "id": 12, "episodeIds": []}); self.assertEqual((st, j["message"]), (400, "no episodes"))
        self.assertEqual(self.h.calls("sonarr", "POST", "SeasonSearch")[0][3]["seasonNumber"], 2)
        st, j = self.act({"action": "alt_set", "value": False}); self.assertEqual(j["message"], "Alt-speed off"); self.assertEqual(self.h.calls("qbittorrent", "POST", "toggleSpeedLimitsMode"), [])   # already off: no toggle
        st, j = self.act({"action": "alt_set", "value": True}); self.assertEqual(j["message"], "Alt-speed on"); self.assertEqual(len(self.h.calls("qbittorrent", "POST", "toggleSpeedLimitsMode")), 1)
        st, j = self.act({"action": "alt_set", "value": False}); self.assertEqual(len(self.h.calls("qbittorrent", "POST", "toggleSpeedLimitsMode")), 2)
        st, j = self.act({"action": "nonsense", "kind": "movie", "id": 2}); self.assertEqual(st, 400)
        self.assertIn("action user=admin role=admin action=retry target=movie:2 result=ok", self.h.panel_log())
    def test_a_dead_backend_does_not_crash_the_dispatcher(self):
        self.h.control(down=["radarr"])
        try:
            st, j = self.act({"action": "retry", "kind": "movie", "id": 2}); self.assertEqual((st, j["ok"]), (400, False)); self.assertIn("refused", j["message"])
        finally: self.h.control(up=["radarr"])
    def test_settings_roundtrip_and_users(self):
        h = self.h
        st, q = h.json("/api/set/qbit", self.admin); self.assertEqual(q["up_limit"], 2.0)
        st, j = h.post("/api/set/qbit", dict(q, dl_limit=5, max_active_downloads=9), self.admin); self.assertEqual(j["message"], "qBittorrent settings saved")
        sent = json.loads(h.calls("qbittorrent", "POST", "setPreferences")[0][3]["json"])
        self.assertEqual(sent["dl_limit"], 5 * 1048576); self.assertEqual(sent["max_active_downloads"], 2)      # clamped to MAX_ACTIVE_DL_CAP
        st, b = h.json("/api/set/bazarr", self.admin); self.assertEqual(b["enabled_providers"], ["opensubtitlescom"])
        st, j = h.post("/api/set/bazarr", dict(b, enabled_providers=["opensubtitlescom", "subdl"], subtitle_langs="en,fr"), self.admin); self.assertEqual((st, j["message"]), (200, "Bazarr settings saved"))
        sent = h.calls("bazarr", "POST", "/api/system/settings")[-1][3]
        self.assertEqual(sent["settings-general-enabled_providers"], ["opensubtitlescom", "subdl"])   # one form field per provider
        prof = json.loads(sent["languages-profiles"])[0]["items"]; self.assertEqual([i["language"] for i in prof], ["en", "fr"]); self.assertTrue(all(i["audio_only_include"] == "False" for i in prof))
        self.assertEqual(sent["settings-general-adaptive_searching"], "false"); self.assertEqual(sent["settings-general-upgrade_subs"], "true")   # lowercase, or Bazarr keeps a string
        h.control(down=["bazarr"])
        st, j = h.post("/api/set/bazarr", b, self.admin); self.assertEqual(st, 400); self.assertIn("Bazarr refused", j["message"])   # a failure is reported, never "saved"
        h.control(up=["bazarr"])
        st, j = h.post("/api/set/notify", {"quiet_start": "1", "quiet_end": "6", "topic_media": "m", "topic_admin": "a", "ntfy_url": ""}, self.admin); self.assertTrue(j["ok"])
        with open(os.path.join(h.sb_dir, "settings.local")) as f: self.assertIn("NOTIFY_QUIET_START=1", f.read())
        st, j = h.post("/api/ntfy-test", {}, self.admin); self.assertEqual(j["message"], "Test notification sent to a"); self.assertTrue(h.calls("ntfy", "POST", "/a"))
        st, j = h.post("/api/roles", {"role": "user", "can_purge": True}, self.admin); self.assertTrue(j["ok"])
        st, me = h.json("/api/me", self.viewer); self.assertTrue(me["caps"]["can_purge"]); self.assertFalse(me["caps"]["can_remove"])
        h.post("/api/roles", {"role": "user"}, self.admin)
        st, j = h.post("/api/users/delete", {"username": "admin"}, self.admin); self.assertEqual(j["message"], "Can't remove the last admin")
        st, ex = h.json("/api/config/export", self.admin); self.assertEqual(set(ex), {"radarr", "sonarr", "qbit", "bazarr", "notify", "arr"}); self.assertNotIn("cap", ex["qbit"])
        self.assertEqual(set(ex["arr"]), {"radarr", "sonarr"}); self.assertTrue(ex["arr"]["radarr"]["quality_definitions"])   # a snapshot that cannot undo a sync is not a snapshot
        st, ps = h.json("/api/config/presets", self.admin)
        self.assertEqual([p["name"] for p in ps], ["Everything paused", "Upload off", "Balanced", "Overclock", "Off-peak only"])
        self.assertTrue(all(p["desc"] and p["group"] == "throughput" for p in ps))   # quality is the guide's, and is applied from a diff, not a description
        st, j = h.post("/api/config/preset", {"name": "Off-peak only"}, self.admin); self.assertTrue(j["ok"]); self.assertIn("Off-peak only applied", j["message"])
        h.control(clear_calls=True)
        st, j = h.post("/api/config/preset", {"name": "Everything paused"}, self.admin); self.assertTrue(j["ok"]); self.assertIn("All paused", j["message"])
        self.assertEqual(h.calls("qbittorrent", "POST", "torrents/stop")[0][3]["hashes"], "all")
        st, j = h.post("/api/config/preset", {"name": "Overclock"}, self.admin); self.assertTrue(j["ok"])
        sent = json.loads(h.calls("qbittorrent", "POST", "setPreferences")[-1][3]["json"])
        self.assertEqual((sent["up_limit"], sent["max_active_downloads"], sent["max_active_uploads"], sent["scheduler_enabled"]), (0, 2, 8, False))   # the download cap still holds
        self.assertTrue(h.calls("qbittorrent", "POST", "torrents/start"))
        self.assertEqual(h.post("/api/config/preset", {"name": "nope"}, self.admin)[0], 400)
        self.assertEqual(h.post("/api/refresh", {}, self.viewer), (200, {"ok": True}))


class ActionLog(unittest.TestCase):
    """The durable record behind Settings > Action log: what a write puts in it, who may read it, and that
    it survives the container being recreated (docs/DASHBOARD.md, Settings)."""
    @classmethod
    def setUpClass(cls):
        H.control(reset=True); cls.h = harness.Harness().start(); cls.h.add_user("viewer", "viewer-pw")
        cls.admin = cls.h.admin_cookie(); cls.viewer = cls.h.login("viewer", "viewer-pw"); assert cls.viewer
    @classmethod
    def tearDownClass(cls): cls.h.stop(); H.control(reset=True)
    def log(self, query="", cookie=None): return self.h.json("/api/log" + query, cookie or self.admin)[1]

    def test_a_write_lands_as_one_entry_naming_its_real_target(self):
        st, j = self.h.post("/api/action", {"action": "retry", "kind": "movie", "id": 2}, self.admin); self.assertTrue(j["ok"], j)
        d = self.log("?action=retry")
        self.assertEqual(d["cap"], 2000)                                                     # the number docs/DASHBOARD.md states
        e = d["entries"][0]
        self.assertEqual(sorted(e), sorted(["ts", "type", "user", "role", "action", "target", "result", "ms", "msg"]))
        self.assertEqual((e["type"], e["user"], e["role"], e["action"], e["target"], e["result"], e["msg"]),
                         ("action", "admin", "admin", "retry", "movie:2", "ok", "Search triggered"))
        self.assertIsInstance(e["ms"], int); self.assertLessEqual(abs(e["ts"] - time.time()), 120)
        st, j = self.h.post("/api/action", {"action": "nonsense", "kind": "movie", "id": 2}, self.admin)
        self.assertEqual((st, self.log("?action=nonsense")["entries"][0]["result"]), (400, "fail"))   # a refusal is part of the record

    def test_an_account_change_is_the_same_record_under_its_own_type(self):
        st, j = self.h.post("/api/users", {"username": "logged", "password": "logged-pw", "role": "user"}, self.admin); self.assertTrue(j["ok"], j)
        e = self.log("?action=user_save")["entries"][0]
        self.assertEqual((e["type"], e["action"], e["target"], e["result"], e["ms"]), ("account", "user_save", "logged", "ok", None))
        self.h.post("/api/users/delete", {"username": "logged"}, self.admin)
        self.assertEqual(self.log("?action=delete")["entries"][0]["target"], "logged")

    def test_filters_come_from_the_whole_ring(self):
        st, j = self.h.post("/api/action", {"action": "retry", "kind": "movie", "id": 2}, self.viewer); self.assertTrue(j["ok"], j)
        d = self.log()
        self.assertIn("viewer", d["users"]); self.assertIn("admin", d["users"]); self.assertIn("retry", d["actions"])
        self.assertEqual({e["user"] for e in self.log("?user=viewer")["entries"]}, {"viewer"})
        self.assertEqual(len(self.log("?limit=1")["entries"]), 1)
        self.assertEqual(self.log("?limit=1")["total"], d["total"])                           # the ring is not what was shown
        self.assertEqual(self.log("?user=nobody")["entries"], [])

    def test_only_an_admin_reads_it_and_nobody_writes_it(self):
        self.assertEqual(self.h.get("/api/log", self.viewer)[0], 403)                          # Settings is admin-only; so is its record
        self.assertEqual(self.h.get("/api/log")[0], 302)                                       # signed out: the login page
        self.assertEqual(self.h.post("/api/log", {}, self.admin)[0], 404)                      # read-only: there is no writing route

    def test_the_file_is_private_and_outlives_the_process(self):
        self.h.post("/api/action", {"action": "retry", "kind": "movie", "id": 2}, self.admin)
        path = os.path.join(self.h.sb_dir, "actions.log")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        before = self.log()["total"]
        self.h.restart()                                                                       # as recreating the container would
        self.assertEqual(self.log("", self.h.admin_cookie())["total"], before)

    def test_no_key_reaches_the_record(self):
        key = fake_stack.KEYS["radarr"]
        self.h.post("/api/action", {"action": "t_delete", "hash": key, "name": key}, self.admin)
        body = self.h.get("/api/log", self.admin)[2].decode(errors="replace")
        self.assertNotIn(key, body)
        with open(os.path.join(self.h.sb_dir, "actions.log")) as f: self.assertNotIn(key, f.read())


class GranularPurges(unittest.TestCase):
    """Torrent-, season- and episode-level purges against a fresh copy of the data; every backend write is asserted."""
    def setUp(self): H.control(reset=True); self.h = harness.Harness().start(); self.c = self.h.admin_cookie(); self.h.control(clear_calls=True)
    def tearDown(self): self.h.stop(); H.control(reset=True)
    def act(self, body): return self.h.post("/api/action", body, self.c)
    def test_torrent_purge_of_an_episode_download(self):
        st, j = self.act({"action": "t_purge", "hash": fake_stack.EP2_HASH}); self.assertEqual(j, {"ok": True, "message": "Purged 1 torrent, 1 episode unmonitored"})
        d = self.h.calls("qbittorrent", "POST", "torrents/delete")[0][3]; self.assertEqual((d["hashes"], d["deleteFiles"]), (fake_stack.EP2_HASH, "true"))
        self.assertEqual(self.h.calls("sonarr", "PUT", "/episode/monitor")[0][3], {"episodeIds": [1202], "monitored": False})
        self.assertTrue(self.h.calls("sonarr", "DELETE", "/queue/602")); self.assertEqual(self.h.calls("sonarr", "DELETE", "/series/12"), [])   # the show stays
        st, live = self.h.json("/api/live", self.c); self.assertNotIn(fake_stack.EP2_HASH, [t["hash"] for t in live["torrents"]])
    def test_torrent_purge_of_a_movie_download_purges_the_movie(self):
        st, j = self.act({"action": "t_purge", "hash": fake_stack.STALLED_HASH}); self.assertEqual(j["message"], "Purged 1 torrent")
        self.assertTrue(self.h.calls("radarr", "DELETE", "/movie/2")); self.assertTrue(self.h.calls("qbittorrent", "POST", "torrents/delete"))
        self.assertTrue(self.h.calls("jellyseerr", "GET", "/request")); self.assertEqual(self.h.calls("jellyseerr", "DELETE"), [])   # looked for its request; the fake has none for tmdb 102
    def test_a_title_purge_reaches_the_whole_stack(self):
        """Arrival is on disk and its torrent is seeding — long gone from Radarr's queue. The purge must still take the torrent
        (Radarr's history remembers it), then tell Bazarr and Jellyfin so the title vanishes everywhere at once."""
        st, cq = self.h.json("/api/consequence?action=purge&kind=movie&id=1&title=Arrival&tmdbId=101", self.c); self.assertIn("removes 1 torrent", cq["text"]); self.assertIn("Jellyfin", cq["text"])
        st, j = self.act({"action": "purge", "kind": "movie", "id": 1, "title": "Arrival", "tmdbId": 101})
        self.assertEqual((st, j["ok"]), (200, True)); self.assertIn("1 torrent", j["message"]); self.assertIn("Jellyfin", j["message"])
        d = self.h.calls("qbittorrent", "POST", "torrents/delete")[0][3]; self.assertEqual((d["hashes"], d["deleteFiles"]), ("ffee0011223344556677889900aabbccddeeff00", "true"))
        self.assertTrue(self.h.calls("radarr", "DELETE", "/movie/1?deleteFiles=true"))
        self.assertEqual(self.h.calls("bazarr", "POST", "/api/system/tasks")[0][3]["taskid"], "update_movies"); self.assertTrue(self.h.calls("jellyfin", "POST", "/Library/Refresh"))
    def test_purging_the_last_of_a_show_removes_the_show(self):
        """Severance has one episode on disk. Purging it leaves nothing on disk and nothing tracked, so the show itself goes —
        otherwise it would sit in the Library as an empty title reading \"Searching\"."""
        st, cq = self.h.json("/api/consequence?action=episode_purge&kind=tv&id=11&episodeIds=1501&title=Severance", self.c); self.assertIn("last of the show", cq["text"])
        st, cq = self.h.json("/api/consequence?action=episode_purge&kind=tv&id=12&episodeIds=1201&title=The+Expanse", self.c); self.assertNotIn("last of the show", cq["text"])   # S01 stays
        st, j = self.act({"action": "episode_purge", "kind": "tv", "id": 11, "episodeIds": "1501"})
        self.assertEqual(j["message"], "Purged 1 episode — 0 torrents, 1 file deleted, unmonitored; that was the last of the show, which is gone from the stack")
        self.assertTrue(self.h.calls("sonarr", "DELETE", "/episodefile/7501")); self.assertTrue(self.h.calls("sonarr", "DELETE", "/series/11?deleteFiles=true"))
        self.assertEqual(self.h.calls("bazarr", "POST", "/api/system/tasks")[-1][3]["taskid"], "update_series"); self.assertTrue(self.h.calls("jellyfin", "POST", "/Library/Refresh"))
        board = self.h.refresh_board(self.c); self.assertNotIn("Severance", [i["title"] for i in board["items"]])
    def test_season_purge(self):
        st, j = self.act({"action": "season_purge", "kind": "tv", "id": 12, "season": 2}); self.assertEqual(j["message"], "Season 2 purged — 2 torrents, 0 files, 3 episodes unmonitored")
        d = self.h.calls("qbittorrent", "POST", "torrents/delete")[0][3]; self.assertEqual(set(d["hashes"].split("|")), {fake_stack.IMPORT_HASH, fake_stack.EP2_HASH})
        self.assertEqual(sorted(self.h.calls("sonarr", "PUT", "/episode/monitor")[0][3]["episodeIds"]), [1201, 1202, 1203])
        put = self.h.calls("sonarr", "PUT", "/series/12")[0][3]; self.assertFalse(next(s for s in put["seasons"] if s["seasonNumber"] == 2)["monitored"]); self.assertTrue(put["seasons"][0]["monitored"])
    def test_episode_purge_takes_the_pack_it_is_in(self):
        st, j = self.act({"action": "episode_purge", "kind": "tv", "id": 12, "episodeIds": "1201"}); self.assertEqual(j["message"], "Purged 1 episode — 1 torrent, 0 files deleted, unmonitored")
        self.assertEqual(self.h.calls("qbittorrent", "POST", "torrents/delete")[0][3]["hashes"], fake_stack.IMPORT_HASH)
        self.assertEqual(self.h.calls("sonarr", "PUT", "/episode/monitor")[0][3]["episodeIds"], [1201]); self.assertEqual(self.h.calls("sonarr", "PUT", "/series/12"), [])   # season stays tracked
        st, j = self.act({"action": "episode_purge", "kind": "tv", "id": 12, "episodeIds": []}); self.assertEqual((st, j["message"]), (400, "no episodes"))


if __name__ == "__main__":
    unittest.main()


class SessionFollowsTheAccount(unittest.TestCase):
    """A cookie lasts 30 days. Without a check against the account store on every request, a user deleted in
    Settings keeps their access until it expires, and a demoted admin stays an admin on the device they were
    already signed in on (docs/DASHBOARD.md ▸ Settings ▸ Users & roles)."""
    def setUp(self): self.admin = H.admin_cookie()

    def test_deleting_a_user_ends_their_open_session(self):
        H.add_user("shortlived", "shortlived-pw")
        c = H.login("shortlived", "shortlived-pw"); self.assertTrue(c)
        st, me = H.json("/api/me", c); self.assertEqual(me["user"], "shortlived")
        st, j = H.post("/api/users/delete", {"username": "shortlived"}, self.admin); self.assertTrue(j["ok"], j)
        self.assertEqual(H.get("/api/me", c)[0], 302)                      # the cookie is no longer a session
        self.assertEqual(H.post("/api/action", {"action": "retry", "kind": "movie", "id": 2}, c)[0], 401)

    def test_a_demotion_reaches_a_session_already_open(self):
        H.add_user("wasadmin", "wasadmin-pw", role="admin")
        c = H.login("wasadmin", "wasadmin-pw"); self.assertTrue(c)
        self.assertEqual(H.json("/api/me", c)[1]["role"], "admin")
        self.assertEqual(H.get("/api/users", c)[0], 200)                   # an admin-only route, while still an admin
        st, j = H.post("/api/users", {"username": "wasadmin", "role": "user"}, self.admin); self.assertTrue(j["ok"], j)
        st, me = H.json("/api/me", c)
        self.assertEqual(me["role"], "user")
        self.assertEqual(H.get("/api/users", c)[0], 403)                   # and no longer
        H.post("/api/users/delete", {"username": "wasadmin"}, self.admin)


class TheProfileTitlesAreAddedWith(unittest.TestCase):
    """The arrs list profiles in id order, so "the first one" is "Any" on a stock install — it allows SDTV and
    DVD, and an SD rip is then a legal grab for everything the panel adds (docs/CONFIGURATION.md ▸ Settings)."""
    def setUp(self): self.admin = H.admin_cookie()

    def test_the_setting_round_trips_by_name(self):
        st, d = H.json("/api/set/sonarr", self.admin)
        self.assertEqual(d["profiles"], ["Any", "HD-1080p"])
        st, j = H.post("/api/set/sonarr", dict(d, default_profile="HD-1080p"), self.admin); self.assertTrue(j["ok"], j)
        with open(os.path.join(H.sb_dir, "settings.local")) as f: self.assertIn("DEFAULT_PROFILE_SONARR=2", f.read())
        st, d = H.json("/api/set/sonarr", self.admin); self.assertEqual(d["default_profile"], "HD-1080p")
        st, j = H.post("/api/set/sonarr", dict(d, default_profile="Any"), self.admin); self.assertTrue(j["ok"], j)
        st, d = H.json("/api/set/sonarr", self.admin); self.assertEqual(d["default_profile"], "Any")
        st, j = H.post("/api/set/sonarr", dict(d, default_profile="No Such Profile"), self.admin)
        self.assertTrue(j["ok"], j)                                        # a name the arr does not have leaves it alone
        self.assertEqual(H.json("/api/set/sonarr", self.admin)[1]["default_profile"], "Any")

    def test_a_title_added_here_gets_that_profile(self):
        d = H.json("/api/set/sonarr", self.admin)[1]
        H.post("/api/set/sonarr", dict(d, default_profile="HD-1080p"), self.admin)
        self.addCleanup(H.post, "/api/set/sonarr", dict(d, default_profile="Any"), self.admin)
        H.control(clear_calls=True)
        st, j = H.post("/api/action", {"action": "add", "kind": "tv", "title": "Something New", "tvdbId": 424242}, self.admin)
        self.assertTrue(j["ok"], j)
        posted = [b for _, m, p, b in H.calls("sonarr", "POST") if p.endswith("/series")]
        self.assertEqual([b["qualityProfileId"] for b in posted], [2])

    def test_the_profile_list_is_not_part_of_a_config_snapshot(self):
        st, ex = H.json("/api/config/export", self.admin)
        self.assertNotIn("profiles", ex["sonarr"]); self.assertIn("default_profile", ex["sonarr"])


class AdoptionSkipsAnEmptiedFolder(unittest.TestCase):
    """A purge deletes files, not directories. Adopting the folder it left behind re-adds the very title that
    was purged, with nothing in it (docs/DASHBOARD.md ▸ Library ▸ Import existing files)."""
    def test_only_the_folder_with_media_is_added(self):
        admin = H.admin_cookie(); H.control(clear_calls=True)
        st, j = H.post("/api/action", {"action": "import_library"}, admin); self.assertTrue(j["ok"], j)
        self.assertIn("'skipped': 1", j["message"])
        for app, key in (("sonarr", "/series"), ("radarr", "/movie")):
            posted = [b for _, m, p, b in H.calls(app, "POST") if p.endswith(key)]
            self.assertEqual([b.get("path", "").rsplit("/", 1)[-1] for b in posted], [fake_stack.ADOPT_NAME], app)


class AnEpisodeRowSaysWhatTheFileIs(unittest.TestCase):
    """The codec is the reason a file plays or transcodes, and the reason a grab was the wrong one. Sonarr reads
    media info on a scan, so a file it has not scanned yet has a quality name and nothing else — the row shows
    that difference rather than hiding it (docs/DASHBOARD.md ▸ Library)."""
    def test_a_scanned_file_carries_its_codecs(self):
        st, d = H.json("/api/series-tree?seriesId=12", H.admin_cookie())
        e = next(e for e in d["episodes"] if e["hasFile"])
        self.assertEqual(e["file"], {"size": 3_000_000_000, "quality": "Bluray-1080p", "video": "x264",
                                     "audio": "AC3", "channels": "5.1", "resolution": "1920x1080"})
        self.assertEqual(e["size"], 3_000_000_000)                         # unchanged: the size still stands alone

    def test_an_unscanned_file_carries_the_quality_alone(self):
        st, d = H.json("/api/series-tree?seriesId=11", H.admin_cookie())
        e = next(e for e in d["episodes"] if e["hasFile"])
        self.assertEqual(e["file"]["quality"], "DVD")
        self.assertEqual((e["file"]["video"], e["file"]["audio"], e["file"]["resolution"]), ("", "", ""))

    def test_an_episode_with_no_file_has_no_facts(self):
        st, d = H.json("/api/series-tree?seriesId=12", H.admin_cookie())
        self.assertTrue(all(e["file"] is None for e in d["episodes"] if not e["hasFile"]))


class TheGuideOwnsQualityNotThePanel(unittest.TestCase):
    """Custom formats, their scores, which qualities a profile allows and the per-quality size limits come from
    TRaSH Guides. The panel previews the difference, writes it on a press, and can put it back
    (docs/DASHBOARD.md \u25b8 Settings). Two things follow and are asserted here: a save of the Movies or TV group
    must not touch any of that, and the apply must take a snapshot before it writes."""
    @classmethod
    def setUpClass(cls):
        # its own panel and a clean fake: a sync rewrites profiles and every size limit, which no other test
        # in this module should have to expect
        H.control(reset=True); cls.h = harness.Harness().start(); cls.admin = cls.h.admin_cookie()
    @classmethod
    def tearDownClass(cls): cls.h.stop(); H.control(reset=True)

    def test_saving_the_tv_group_writes_no_format_no_profile_and_no_size(self):
        d = self.h.json("/api/set/sonarr", self.admin)[1]
        for gone in ("size_cap", "size_max", "prefer_h264", "reject_legacy", "allow_unknown"): self.assertNotIn(gone, d)
        self.h.control(clear_calls=True)
        st, j = self.h.post("/api/set/sonarr", dict(d, min_seeders=6), self.admin); self.assertTrue(j["ok"], j)
        self.assertFalse(self.h.calls("sonarr", "POST", "/customformat"))
        self.assertFalse(self.h.calls("sonarr", "PUT", "/qualitydefinition/"))
        self.assertFalse(self.h.calls("sonarr", "PUT", "/qualityprofile/"))
        self.assertTrue(self.h.calls("sonarr", "PUT", "/indexer/"))                    # the seeder threshold is still ours

    def test_the_audio_language_is_offered_for_films_only(self):
        """Sonarr v4 has no per-profile language; it used to be faked with a custom format that is the guide's now."""
        self.assertIn("audio_language", self.h.json("/api/set/radarr", self.admin)[1])
        self.assertNotIn("audio_language", self.h.json("/api/set/sonarr", self.admin)[1])

    def test_the_guide_is_vendored_and_never_fetched_to_show_it(self):
        st, t = self.h.json("/api/trash", self.admin)
        self.assertTrue(t["version"]["vendored"]); self.assertTrue(t["version"]["profiles"])
        self.assertIn("HD Bluray + WEB", [p["name"] for p in t["apps"]["radarr"]["profiles"]])
        self.assertTrue(all(p["desc"] for p in t["apps"]["sonarr"]["profiles"]))

    def test_a_preview_writes_nothing_and_names_every_change(self):
        self.h.control(clear_calls=True)
        # a profile no other test in this class touches, so this one does not depend on running first
        st, pl = self.h.json("/api/trash/plan?app=radarr&name=UHD%20Bluray%20%2B%20WEB", self.admin)
        self.assertFalse(pl["empty"]); self.assertFalse(pl["exists"])
        self.assertTrue(pl["formats"]["create"]); self.assertTrue(pl["sizes"])
        self.assertTrue(pl["default_profile"]["change"])
        for method in ("POST", "PUT"):
            self.assertFalse(self.h.calls("radarr", method, "/customformat"), method)
            self.assertFalse(self.h.calls("radarr", method, "/qualityprofile"), method)
            self.assertFalse(self.h.calls("radarr", method, "/qualitydefinition"), method)

    def test_apply_snapshots_first_then_writes_formats_profile_sizes_in_that_order(self):
        st, pl = self.h.json("/api/trash/plan?app=radarr&name=HD%20Bluray%20%2B%20WEB", self.admin)
        n_create = len(pl["formats"]["create"])
        self.h.control(clear_calls=True)
        st, j = self.h.post("/api/trash/apply", {"app": "radarr", "name": "HD Bluray + WEB"}, self.admin)
        self.assertTrue(j["ok"], j)
        self.assertTrue(os.path.exists(os.path.join(self.h.sb_dir, "rollback.json")), "no snapshot was taken before the write")
        kinds = [mt.group(1) for _, m, p, b in self.h.calls("radarr") if m in ("POST", "PUT")
                 for mt in [re.search(r"/(customformat|qualityprofile|qualitydefinition)", p)] if mt]
        self.assertEqual(kinds[:n_create], ["customformat"] * n_create)          # a profile can only score a format that exists
        self.assertEqual(kinds[n_create], "qualityprofile")
        self.assertTrue(all(k == "qualitydefinition" for k in kinds[n_create + 1:]), kinds)
        # the panel keeps one quality setting: which profile a title added here goes on
        with open(os.path.join(self.h.sb_dir, "settings.local")) as f: loc = f.read()
        self.assertIn("TRASH_PROFILE_RADARR=HD Bluray + WEB", loc); self.assertIn("DEFAULT_PROFILE_RADARR=", loc)
        st, t = self.h.json("/api/trash", self.admin)
        self.assertEqual(t["apps"]["radarr"]["default_profile"], "HD Bluray + WEB")
        self.assertTrue(t["rollback"]["taken"]); self.assertEqual(t["rollback"]["before"], "radarr \u25b8 HD Bluray + WEB")
        st, again = self.h.json("/api/trash/plan?app=radarr&name=HD%20Bluray%20%2B%20WEB", self.admin)
        self.assertTrue(again["empty"], again)                                    # applying twice changes nothing

    def test_rolling_back_restores_the_profiles_and_the_size_limits(self):
        st, ex0 = self.h.json("/api/config/export", self.admin)
        before = {p["name"]: p for p in ex0["arr"]["sonarr"]["profiles"]}
        st, j = self.h.post("/api/trash/apply", {"app": "sonarr", "name": "WEB-1080p"}, self.admin); self.assertTrue(j["ok"], j)
        st, ex1 = self.h.json("/api/config/export", self.admin)
        self.assertNotEqual(ex1["arr"]["sonarr"]["quality_definitions"], ex0["arr"]["sonarr"]["quality_definitions"])
        st, j = self.h.post("/api/trash/rollback", {}, self.admin); self.assertTrue(j["ok"], j)
        st, ex2 = self.h.json("/api/config/export", self.admin)
        self.assertEqual(ex2["arr"]["sonarr"]["quality_definitions"], ex0["arr"]["sonarr"]["quality_definitions"])
        after = {p["name"]: p for p in ex2["arr"]["sonarr"]["profiles"]}
        for name, p in before.items(): self.assertEqual(after[name], p, name)
        # the profile the sync created is left in place, not deleted out from under the titles now on it
        self.assertIn("WEB-1080p", after); self.assertNotIn("WEB-1080p", before)


class EveryRowSaysWhichProfileItIsOn(unittest.TestCase):
    """The profile decides what a title may grab, so it belongs on the title, not two clicks deep in Settings."""
    def test_the_board_carries_the_profile_name_per_title(self):
        st, b = H.json("/api/board", H.admin_cookie())
        # a title Jellyseerr has requested but no arr holds yet is a synthesised row with no profile to name
        rows = [i for i in b["items"] if isinstance(i.get("id"), int)]
        self.assertTrue(rows)
        self.assertTrue(all("profile" in i for i in rows), [i["title"] for i in rows if "profile" not in i])
        self.assertIn("Any", {i["profile"] for i in rows})
