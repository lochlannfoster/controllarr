"""panel_data: the per-source cache semantics every section's ok/age_s/err rests on, the VPN
namespace check (the branch covered by a unit fake), the container roll-up
and the consequence text a confirmation dialog shows."""
import json, os, sys, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import panel_data


class SourcesCache(unittest.TestCase):
    def test_good_results_are_cached_and_failures_keep_the_last_good_value(self):
        S = panel_data.Sources(); calls = []
        def ok(): calls.append(1); return {"v": 1}
        with mock.patch("panel_data.time.time", return_value=1000.0):
            data, m = S.get("k", 30, ok); self.assertEqual((data, m), ({"v": 1}, {"ok": True, "age_s": 0, "err": None}))
            data, m = S.get("k", 30, ok); self.assertEqual(len(calls), 1)                              # within TTL: no call
        def boom(): raise RuntimeError("radarr unreachable")
        with mock.patch("panel_data.time.time", return_value=1040.0):
            data, m = S.get("k", 30, boom)
            self.assertEqual(data, {"v": 1}); self.assertEqual(m, {"ok": False, "age_s": 40, "err": "RuntimeError: radarr unreachable"})
        with mock.patch("panel_data.time.time", return_value=1045.0):
            data, m = S.get("k", 30, boom); self.assertFalse(m["ok"])                                # failure cached for fail_ttl
        with mock.patch("panel_data.time.time", return_value=1051.0):
            data, m = S.get("k", 30, ok); self.assertEqual((m["ok"], m["age_s"], len(calls)), (True, 0, 2))   # retried after fail_ttl
    def test_first_failure_has_no_age(self):
        S = panel_data.Sources()
        def boom(): raise ValueError("x" * 300)
        data, m = S.get("k", 30, boom)
        self.assertIsNone(data); self.assertIsNone(m["age_s"]); self.assertLessEqual(len(m["err"]), 140)
        S.invalidate("k"); self.assertEqual(S._c, {})


def make_panel(docker_raw=None, torrents=None, item_detail=None, expected=("radarr", "sonarr"), qbit_host="qbittorrent", docker=True):
    return panel_data.Panel(arr=lambda *a, **k: (200, []), prowlarr=lambda *a, **k: (200, []), js=lambda *a, **k: (200, {}), http=lambda *a, **k: (200, {}),
                            docker_raw=docker_raw or (lambda p: b"[]"), torrents=torrents or (lambda: []), transfer=lambda: {}, status=lambda: {},
                            bazarr_get=lambda p: None, env={"SERVER_HOST": "h"}, config_dir="/nonexistent", apikey=lambda a: "", arr_base=lambda a: "http://x",
                            expected=list(expected), services=list(expected), docker=docker, qbit_host=qbit_host)


class VpnNamespaceCheck(unittest.TestCase):
    def docker(self, gluetun_started, dep_started, mode="container:gluetunid", health="healthy"):
        def raw(path):
            if path == "/containers/gluetun/json":
                return json.dumps({"Id": "gluetunid", "State": {"Running": True, "Health": {"Status": health}, "StartedAt": gluetun_started}}).encode()
            if path.endswith("/json"):
                return json.dumps({"Id": "dep", "State": {"Running": True, "StartedAt": dep_started}, "HostConfig": {"NetworkMode": mode}}).encode()
            if "logs" in path: return b"ip 1.2.3.4\nport forwarded 51820\n"
            return b"[]"
        return raw
    def test_disabled_when_qbittorrent_is_not_behind_gluetun(self):
        self.assertEqual(make_panel().vpn(), ({"enabled": False}, {"ok": True, "age_s": 0, "err": None}))
    def test_disabled_without_a_docker_socket(self):
        """No socket configured: Controllarr does not claim a VPN it cannot see, and reports no failure either."""
        p = make_panel(docker_raw=self.docker("2026-08-29T10:00:00Z", "2026-08-29T09:00:00Z"), qbit_host="gluetun", docker=False)
        self.assertEqual(p.vpn(), ({"enabled": False}, {"ok": True, "age_s": 0, "err": None}))
    def test_dependent_started_before_gluetun_is_orphaned(self):
        p = make_panel(docker_raw=self.docker("2026-08-29T10:00:00Z", "2026-08-29T09:00:00Z"), qbit_host="gluetun")
        v, m = p.vpn(); self.assertTrue(m["ok"]); self.assertTrue(v["up"]); self.assertEqual(v["orphaned"], ["qbittorrent", "prowlarr", "flaresolverr"])
    def test_dependent_started_after_gluetun_is_fine(self):
        p = make_panel(docker_raw=self.docker("2026-08-29T10:00:00Z", "2026-08-29T10:05:00Z"), qbit_host="gluetun")
        v, m = p.vpn(); self.assertEqual(v["orphaned"], [])
    def test_unhealthy_tunnel_is_down_and_a_dead_socket_is_reported(self):
        p = make_panel(docker_raw=self.docker("2026-08-29T10:00:00Z", "2026-08-29T10:05:00Z", health="unhealthy"), qbit_host="gluetun")
        self.assertFalse(p.vpn()[0]["up"])
        def dead(path): raise OSError("No such file or directory")
        v, m = make_panel(docker_raw=dead, qbit_host="gluetun").vpn(); self.assertIsNone(v); self.assertFalse(m["ok"]); self.assertIn("OSError", m["err"])


class ServicesRollup(unittest.TestCase):
    def test_missing_and_unhealthy_containers(self):
        raw = lambda p: json.dumps([{"Id": "a" * 40, "Names": ["/radarr"], "State": "running", "Status": "Up 1 hour (unhealthy)"}]).encode()
        svcs, m = make_panel(docker_raw=raw).services()
        self.assertEqual([(s["name"], s["state"], s["health"]) for s in svcs], [("radarr", "running", "unhealthy"), ("sonarr", "missing", "")])
        self.assertEqual(svcs[0]["id"], "a" * 12)


class ConsequenceText(unittest.TestCase):
    def test_purge_and_pause_all_name_real_counts(self):
        tors = [{"name": "A", "state": "downloading"}, {"name": "B", "state": "stalledUP"}]
        p = make_panel(torrents=lambda: tors)
        detail = lambda kind, aid: {"torrents": [{"name": "Blade.Runner.2049.2017.1080p.BluRay.x264-GROUP.very.long.release.name.indeed"}], "sizeOnDisk": 5.3e9}
        t, txt = p.consequence({"action": "purge", "kind": "movie", "id": 2, "title": "Blade Runner 2049", "tmdbId": 102}, detail)
        self.assertEqual(t, "Purge Blade Runner 2049"); self.assertIn("5.3 GB", txt); self.assertIn("removes 1 torrent from qBittorrent and the Jellyseerr request", txt); self.assertIn("Radarr", txt)
        t, txt = p.consequence({"action": "blocklist_retry", "kind": "movie", "id": 2, "title": "X"}, detail); self.assertIn("…", txt)
        t, txt = p.consequence({"action": "qall_pause"}, detail); self.assertEqual(t, "Pause all"); self.assertIn("Stops 2 torrents — 1 downloading: A", txt)
        t, txt = p.consequence({"action": "t_delete", "name": "N", "deleteFiles": True}, detail); self.assertIn("deleted too", txt)
        self.assertEqual(p.consequence({"action": "unknown"}, detail), ("unknown", ""))

    def test_incognito_drops_the_names_the_server_adds_and_keeps_every_count(self):
        """The page in incognito hands in a pseudonym; what the server must not do is add a real name of its own."""
        tors = [{"name": "Arrival.2016.1080p.WEB-DL", "group": "Arrival", "state": "downloading"}, {"name": "B", "state": "stalledUP"}]
        p = make_panel(torrents=lambda: tors)
        detail = lambda kind, aid: {"torrents": [{"name": "Blade.Runner.2049.2017.1080p.BluRay.x264-GROUP"}], "sizeOnDisk": 5.3e9}
        t, txt = p.consequence({"action": "blocklist_retry", "kind": "movie", "id": 2, "title": "Quiet Otter 41", "incognito": "1"}, detail)
        self.assertEqual(t, "Blocklist & retry Quiet Otter 41")
        self.assertNotIn("Blade", txt); self.assertIn("the current download", txt); self.assertIn("removes 1 torrent", txt)
        t, txt = p.consequence({"action": "qall_pause", "incognito": "1"}, detail)
        self.assertNotIn("Arrival", txt); self.assertIn("Stops 2 torrents — 1 downloading. Seeding stops", txt)
        # off (the default), both still name what they are about
        self.assertIn("Blade", p.consequence({"action": "blocklist_retry", "kind": "movie", "id": 2, "title": "X"}, detail)[1])
        self.assertIn("Arrival", p.consequence({"action": "qall_pause"}, detail)[1])


class AttentionSubjects(unittest.TestCase):
    def test_every_real_name_in_an_item_is_listed_for_the_incognito_pass(self):
        """Incognito substitutes in the browser; the server's only job is to say which words are a real name."""
        s = panel_data.Panel._subject
        self.assertEqual(s("The Expanse", "tv:12"), {"text": "The Expanse", "key": "tv:12", "who": False})
        self.assertEqual(s("sam", who=True), {"text": "sam", "key": "sam", "who": True})


if __name__ == "__main__":
    unittest.main()
