"""services.load_env (Controllarr reads its whole configuration through it) and the settings_ops readers
that turn an app's live state into the Settings page's values."""
import io, json, os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
os.environ.setdefault("CONTROLLARR_ENV", "/nonexistent/controllarr.env")
import services, settings_ops


class LoadEnv(unittest.TestCase):
    def test_quoting_comments_and_the_local_overlay(self):
        root = tempfile.mkdtemp(prefix="mc-env-"); os.makedirs(os.path.join(root, "controllarr"))
        p = os.path.join(root, "app.env")
        with open(p, "w") as f: f.write(f"# comment\nCONFIG_DIR={root}\nQBIT_PASS='p a$s'\nEMPTY=\nPLAIN=x=y\n  INDENTED=1\nNOEQUALS\n")
        os.environ["CONTROLLARR_DIR"] = os.path.join(root, "controllarr")
        with open(os.path.join(root, "controllarr", "settings.local"), "w") as f: f.write("MIN_SEEDERS=9\nPLAIN=override\n")
        d = services.load_env(p)
        self.assertEqual(d["QBIT_PASS"], "p a$s"); self.assertEqual(d["EMPTY"], ""); self.assertEqual(d["PLAIN"], "override")
        self.assertEqual(d["MIN_SEEDERS"], "9"); self.assertEqual(d["INDENTED"], "1"); self.assertNotIn("NOEQUALS", d)
        self.assertEqual(services.load_env("/nonexistent"), {})
    def test_apikey_falls_back_to_env(self):
        with mock.patch.dict(services.E, {"RADARR_APIKEY": "from-env"}): self.assertEqual(services.apikey("radarr"), "from-env")


class ReadQbit(unittest.TestCase):
    def qbit(self, prefs):
        op = mock.Mock(); op.open = lambda url, timeout=None: io.BytesIO(json.dumps(prefs).encode())
        return lambda: (op, "http://q")
    def test_ratio_semantics_and_units(self):
        d = settings_ops.read_qbit(self.qbit({"dl_limit": 2 * 1048576, "max_ratio_enabled": True, "max_ratio": 0, "scheduler_enabled": True, "schedule_from_hour": 1, "schedule_to_hour": 6}))
        self.assertEqual(d["dl_limit"], 2.0); self.assertFalse(d["seed_after_complete"]); self.assertEqual(d["max_ratio"], 0)   # ratio 0 = don't seed
        self.assertEqual((d["scheduler_enabled"], d["sched_from"], d["sched_to"]), (True, 1, 6))
        d = settings_ops.read_qbit(self.qbit({"max_ratio_enabled": True, "max_ratio": 1.5})); self.assertTrue(d["seed_after_complete"]); self.assertEqual(d["max_ratio"], 1.5)
        d = settings_ops.read_qbit(lambda: (None, None)); self.assertEqual(d["max_active_downloads"], 3)   # unreachable: defaults, no crash


class ReadContent(unittest.TestCase):
    def test_radarr_values_are_derived_from_the_app_objects(self):
        answers = {"/qualitydefinition": [{"preferredSize": 20, "maxSize": 50}],
                   "/qualityprofile": [{"language": {"name": "Original"}, "formatItems": [{"format": 9, "score": -500}, {"format": 3, "score": -1000}], "items": [{"quality": {"name": "Unknown"}, "allowed": True}]}],
                   "/customformat": [{"id": 9, "name": "x265-HEVC"}, {"id": 3, "name": "Dubbed-penalty"}],   # the dub penalty is negative too and must not read as "prefer h264"
                   "/indexer": [{"fields": [{"name": "minimumSeeders", "value": 7}]}],
                   "/config/mediamanagement": {"downloadPropersAndRepacks": "preferAndUpgrade", "copyUsingHardlinks": False, "recycleBinCleanupDays": 3, "minimumFreeSpaceWhenImporting": 500},
                   "/config/naming": {"renameMovies": False}}
        arr = lambda app, path, method="GET", data=None: (200, answers.get(path))
        d = settings_ops.read_content(arr, "radarr")
        self.assertEqual(d, {"size_cap": 20, "size_max": 50, "audio_language": "Original", "allow_unknown": True, "prefer_h264": True, "min_seeders": 7,
                             "propers": True, "copy_hardlinks": False, "recycle_bin": "", "recycle_days": 3, "min_free_mb": 500, "rename": False})


if __name__ == "__main__":
    unittest.main()
