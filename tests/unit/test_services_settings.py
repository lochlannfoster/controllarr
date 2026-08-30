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


class SecretsAtRest(unittest.TestCase):
    """The panel does not encrypt its keys and says why (services.py). What it does instead is enforced here:
    the file is the owner's alone, and no secret survives a trip out of the panel."""
    def test_a_world_readable_config_is_the_one_thing_that_stops_the_panel(self):
        root = tempfile.mkdtemp(prefix="mc-perm-"); p = os.path.join(root, "app.env")
        with open(p, "w") as f: f.write("RADARR_APIKEY=0123456789abcdef\n")
        os.chmod(p, 0o600); self.assertEqual(services.config_problems(p), [])
        os.chmod(p, 0o640); self.assertEqual(services.config_problems(p), [])          # a shared group is a choice, not a fault
        os.chmod(p, 0o644); self.assertEqual(services.config_problems(p), [(p, 0o644)])
        os.chmod(p, 0o602); self.assertEqual(services.config_problems(p), [(p, 0o602)])   # writable by anyone is worse
        self.assertEqual(services.config_problems("/nonexistent/app.env"), [])          # nothing to judge yet
    def test_redact_replaces_every_secret_and_nothing_else(self):
        key = "radarr-test-key-0123"
        with mock.patch.dict(services.E, {"RADARR_APIKEY": key, "QBIT_PASS": "a-long-enough-pass", "QBIT_USER": "admin",
                                          "SERVER_HOST": "nas.example"}, clear=True):
            services._known["ts"] = 0.0                                                # the cache must not hide a rotation
            self.assertIn(key, services.secrets())
            self.assertEqual(services.redact(f"GET /movie failed for {key}"), "GET /movie failed for ***")
            self.assertEqual(services.redact("a-long-enough-pass"), "***")
            self.assertEqual(services.redact("connecting to nas.example as admin"), "connecting to nas.example as admin")
            self.assertEqual(services.redact(RuntimeError(f"401 with {key}")), "401 with ***")   # an exception, not just a string
    def test_a_short_value_is_left_alone_so_output_is_not_mangled(self):
        with mock.patch.dict(services.E, {"QBIT_PASS": "abc"}, clear=True):
            services._known["ts"] = 0.0
            self.assertNotIn("abc", services.secrets())
            self.assertEqual(services.redact("abcdef — 3 abc torrents"), "abcdef — 3 abc torrents")
    def test_a_key_from_another_app_s_own_file_can_be_registered(self):
        services.add_secret("bazarr-key-registered"); services.add_secret("short")
        self.assertEqual(services.redact("bazarr-key-registered"), "***")
        self.assertEqual(services.redact("short"), "short")


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
        answers = {"/qualityprofile": [{"language": {"name": "Original"}, "formatItems": [{"format": 9, "score": -500}], "items": []}],
                   "/indexer": [{"fields": [{"name": "minimumSeeders", "value": 7}]}],
                   "/config/mediamanagement": {"downloadPropersAndRepacks": "preferAndUpgrade", "copyUsingHardlinks": False, "recycleBinCleanupDays": 3, "minimumFreeSpaceWhenImporting": 500},
                   "/config/naming": {"renameMovies": False}}
        arr = lambda app, path, method="GET", data=None: (200, answers.get(path))
        d = settings_ops.read_content(arr, "radarr")
        self.assertEqual(d, {"audio_language": "Original", "min_seeders": 7, "propers": True, "copy_hardlinks": False,
                             "recycle_bin": "", "recycle_days": 3, "min_free_mb": 500, "rename": False})

    def test_quality_is_not_read_or_written_here_any_more(self):
        """Sizes, format scores and which qualities a profile allows belong to the guide (app/trash.py +
        settings_ops.apply_trash). A save of the Movies group must not so much as look at them, or it would
        quietly undo a sync every time somebody changed the recycle-bin path."""
        answers = {"/qualityprofile": [{"id": 1, "language": {"id": -1, "name": "Any"}, "formatItems": [], "items": []}],
                   "/indexer": [], "/config/mediamanagement": {}, "/config/naming": {}}
        seen = []
        def arr(app, path, method="GET", data=None):
            seen.append((method, path)); return (200, answers.get(path))
        d = settings_ops.read_content(arr, "radarr")
        for gone in ("size_cap", "size_max", "prefer_h264", "reject_legacy", "allow_unknown"): self.assertNotIn(gone, d)
        self.assertNotIn(("GET", "/qualitydefinition"), seen)
        seen.clear()
        settings_ops.apply_content({"audio_language": "Any", "min_seeders": 5, "propers": True}, arr, apps=("radarr",))
        self.assertFalse([x for x in seen if x[1].startswith("/qualitydefinition")], seen)
        self.assertFalse([x for x in seen if x[1].startswith("/customformat")], seen)

    def test_sonarr_has_no_audio_language_to_read_or_write(self):
        """Sonarr v4 has no per-profile language field. It used to be faked with a custom format; that format is
        the guide's now, so the setting is offered for films only rather than written into TV's profiles."""
        seen = []
        def arr(app, path, method="GET", data=None):
            seen.append((method, path)); return (200, {"/indexer": [], "/config/mediamanagement": {}, "/config/naming": {}}.get(path))
        self.assertNotIn("audio_language", settings_ops.read_content(arr, "sonarr"))
        seen.clear()
        settings_ops.apply_content({"audio_language": "Original", "min_seeders": 5}, arr, apps=("sonarr",))
        self.assertFalse([x for x in seen if x[0] == "PUT" and x[1].startswith("/qualityprofile")], seen)


if __name__ == "__main__":
    unittest.main()
