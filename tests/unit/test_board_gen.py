"""board_gen.generate() is pure given its accessors: feed it the fake dataset in-process and check the
seven-stage classification the Dash, the Library and the attention list are built from."""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import board_gen, fake_stack


def accessors(d):
    """arr()/H() closures that answer from a dataset the way the fake servers would."""
    def arr(app, path, method="GET", data=None):
        items = d["movies"] if app == "radarr" else d["series"]
        p = path.split("?")[0]
        if p in ("/movie", "/series"): return 200, items
        if p == "/queue": return 200, {"records": d["queue"][app]}
        if p == "/release":
            q = dict(x.split("=") for x in path.split("?")[1].split("&"))
            k = f"radarr:{q.get('movieId')}" if app == "radarr" else f"sonarr:{q.get('seriesId')}:{q.get('seasonNumber')}"
            return 200, d["releases"].get(k, [])
        if p == "/health": return 200, d["health"][app]
        if p == "/history": return 200, {"records": [{"date": "2026-08-29T10:00:00Z", "sourceTitle": "X"}]}
        return 404, None
    def H(method, url, headers=None, data=None, timeout=10, expect_json=True):
        return 200, {"results": d["requests"]}
    return arr, H


class Classification(unittest.TestCase):
    def setUp(self):
        self.d = fake_stack.build_data("default"); self.cfg = tempfile.mkdtemp(prefix="mc-bg-")
        os.makedirs(os.path.join(self.cfg, "jellyseerr")); json.dump({"main": {"apiKey": "k"}}, open(os.path.join(self.cfg, "jellyseerr", "settings.json"), "w"))
    def gen(self, d=None):
        arr, H = accessors(d or self.d)
        st, cache = board_gen.generate(arr, None, H, "http://js", self.cfg, min_seeders=5, cache={}, data_dir=self.cfg)
        return st, {i["title"]: i for i in st["items"]}
    def test_default_dataset_covers_the_stages(self):
        st, by = self.gen()
        self.assertEqual(st["summary"], {"Unavailable": 1, "Searching": 0, "Downloading": 2, "Importing": 0, "Partial": 0, "Waiting": 1, "Available": 2})
        self.assertEqual(by["Arrival"]["stage"], "Available"); self.assertEqual(by["Severance"]["detail"], "9/9")
        self.assertEqual(by["Blade Runner 2049"]["stage"], "Downloading"); self.assertEqual(by["Blade Runner 2049"]["hashes"], [fake_stack.STALLED_HASH])
        self.assertEqual(by["Blade Runner 2049"]["detail"], "42% · 01:20:00")
        self.assertEqual(by["Coherence"]["stage"], "Unavailable"); self.assertEqual(by["Coherence"]["reason"], "No torrents found")
        self.assertEqual(by["Dune Part Three"]["stage"], "Waiting"); self.assertEqual(by["Coherence"]["who"], "sam")   # request map by tmdbId
        self.assertEqual(st["searches"], 1)   # only Coherence needed a release search
        self.assertEqual([i["stage"] for i in st["items"]][:2], ["Available", "Available"])   # ORDER sorts Available first
    def test_queue_state_import_and_rejections(self):
        d = self.d
        d["queue"]["radarr"][0]["trackedDownloadState"] = "importPending"
        d["queue"]["sonarr"] = []                                   # The Expanse leaves the queue: has files, missing some -> search
        st, by = self.gen(d)
        self.assertEqual(by["Blade Runner 2049"]["stage"], "Importing")
        self.assertEqual(by["The Expanse"]["stage"], "Partial"); self.assertIn("S2", by["The Expanse"]["reason"])   # 'existing file' verdict never means Available with gaps
    def test_low_seed_and_size_verdicts(self):
        d = self.d
        d["movies"][2]["id"] = 3
        d["releases"]["radarr:3"] = [{"rejected": True, "seeders": 2, "rejections": ["Not enough seeders"]}]
        st, by = self.gen(d); self.assertEqual(by["Coherence"]["reason"], "Only low-seed (max 2)")
        d["releases"]["radarr:3"] = [{"rejected": True, "seeders": 9, "rejections": ["Size 30 GB is larger than maximum allowed"]}]
        st, by = self.gen(d); self.assertIn("too big for the size limit", by["Coherence"]["reason"])
        d["releases"]["radarr:3"] = [{"rejected": False, "seeders": 9, "rejections": []}]
        st, by = self.gen(d); self.assertEqual(by["Coherence"]["stage"], "Searching")
    def test_search_budget_and_cache(self):
        d = self.d
        for n in range(10):
            d["movies"].append({"id": 100 + n, "tmdbId": 1000 + n, "title": f"M{n}", "year": 2000, "monitored": True, "hasFile": False, "isAvailable": True, "images": []})
        arr, H = accessors(d)
        st, cache = board_gen.generate(arr, None, H, "http://js", self.cfg, cache={})
        self.assertEqual(st["searches"], board_gen.SEARCH_BUDGET)
        st2, cache = board_gen.generate(arr, None, H, "http://js", self.cfg, cache=cache)
        self.assertLessEqual(st2["searches"], board_gen.SEARCH_BUDGET); self.assertGreater(len(cache), board_gen.SEARCH_BUDGET)
    def test_first_missing_season(self):
        s = fake_stack.build_data()["series"][1]
        self.assertEqual(board_gen.first_missing_season(s), 2)
        s["seasons"][1]["monitored"] = False; self.assertEqual(board_gen.first_missing_season(s), 1)
        self.assertEqual(board_gen.first_missing_season({}), 1)
        self.assertEqual(board_gen.STAGES, ["Unavailable", "Searching", "Downloading", "Importing", "Partial", "Waiting", "Available"])


if __name__ == "__main__":
    unittest.main()
