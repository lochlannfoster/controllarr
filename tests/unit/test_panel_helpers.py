"""Pure helpers of app/controllarr.py: the auth/redirect guards, the asset versioning, the download
wording, and the users/roles store. No network: the module is imported against a temp dir."""
import json, os, re, stat, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness

TMP = tempfile.mkdtemp(prefix="mc-unit-")
app = harness.load_panel_module(TMP)


class SafeNext(unittest.TestCase):
    def test_local_paths_are_kept(self):
        for p in ("/", "/?item=movie:12", "/settings", "/#library?stage=Partial"):
            self.assertEqual(app._safe_next(p), p)
    def test_everything_else_lands_on_the_root(self):
        for p in ("", None, "//evil.example", "http://evil.example/", "\\\\x", "/api/board", "/static/app.js", "/img/poster/movie/1",
                  "/login", "/logout", "/health", "/" + "a" * 600, "settings"):
            self.assertEqual(app._safe_next(p), "/", p)


class AssetVersioning(unittest.TestCase):
    def test_bare_relative_imports_get_the_version(self):
        v = app.asset_ver().encode()
        src = b"import { h } from './modules/dom.js';\nimport x from \"../up.js\";\nconst m = import('./lazy.js');\n"
        out = app._version_imports(src)
        self.assertIn(b"'./modules/dom.js?v=" + v + b"'", out)
        self.assertIn(b'"../up.js?v=' + v + b'"', out)
        self.assertIn(b"import('./lazy.js?v=" + v + b"')", out)
    def test_other_specifiers_are_left_alone(self):
        for src in (b"import x from 'dom';", b"import x from './a.js?v=1';", b"import x from '/static/a.js';", b"import x from './a';"):
            self.assertEqual(app._version_imports(src), src, src)
    def test_version_is_stable_and_short(self):
        self.assertEqual(app.asset_ver(), app.asset_ver()); self.assertTrue(re.fullmatch(r"[0-9a-f]{8}", app.asset_ver()))


class StaticFiles(unittest.TestCase):
    def test_allowlisted_file_is_served_with_its_type(self):
        data, ctype = app.static_file("app.js"); self.assertTrue(data.startswith(b"//")); self.assertTrue(ctype.startswith("text/javascript"))
        self.assertEqual(app.static_file("fonts/ibm-plex-mono-latin-400.woff2")[1], "font/woff2")
    def test_escapes_and_unknown_types_are_refused(self):
        for rel in ("../controllarr.py", "..%2Fcontrollarr.py", "/etc/passwd", "fonts\\x.woff2", "", "missing.js", "index.html", "../scripts/static/app.js"):
            self.assertIsNone(app.static_file(rel), rel)


class Passwords(unittest.TestCase):
    def test_roundtrip_and_rejection(self):
        salt, h = app.hash_pw("s3cret")
        self.assertTrue(app.verify_pw("s3cret", salt, h)); self.assertFalse(app.verify_pw("S3cret", salt, h)); self.assertFalse(app.verify_pw("s3cret", "zz", h))
        self.assertNotEqual(app.hash_pw("s3cret")[0], salt)


class TorrentWording(unittest.TestCase):
    def test_eta(self):
        self.assertEqual(app._eta(None), "∞"); self.assertEqual(app._eta(8640000), "∞"); self.assertEqual(app._eta(300), "5m"); self.assertEqual(app._eta(3660), "1h01m")
    def test_why(self):
        self.assertIn("cap", app._why({"state": "queuedDL", "progress": 0.1}))
        self.assertIn("metadata", app._why({"state": "metaDL", "progress": 0}))
        self.assertIn("dead swarm", app._why({"state": "stalledDL", "progress": 0.4, "dlspeed": 0, "num_seeds": 0, "num_leechs": 3, "num_complete": 0, "availability": 0.4}))
        self.assertIn("none reachable", app._why({"state": "stalledDL", "progress": 0.4, "dlspeed": 0, "num_seeds": 0, "num_leechs": 0, "num_complete": 5, "availability": 0}))
        self.assertIn("only 60%", app._why({"state": "stalledDL", "progress": 0.4, "dlspeed": 0, "num_seeds": 1, "num_leechs": 0, "num_complete": 5, "availability": 0.6}))
        self.assertEqual(app._why({"state": "stalledDL", "progress": 1.0}), "")
        self.assertEqual(app._why({"state": "downloading", "progress": 0.4, "dlspeed": 5000}), "")
    def test_torrent_label_from_the_queue_or_the_release_name(self):
        eps = [{"id": 1, "season": 2, "ep": 3, "title": "Doors"}]
        self.assertEqual(app._torrent_label("whatever", eps, "tv"), ("S02E03 · Doors", 2))
        pack = [{"id": i, "season": 2, "ep": i, "title": ""} for i in range(1, 14)]
        self.assertEqual(app._torrent_label("x", pack, "tv"), ("S02 · E01–E13 (13 episodes)", 2))
        # imported and seeding: not in the queue any more, the name still says which episode
        self.assertEqual(app._torrent_label("Futurama S08E03 How the West Was 1010001 1080p HULU WEB-DL", [], ""), ("S08E03", 8))
        self.assertEqual(app._torrent_label("Futurama.S08E10.All.The.Way.Down.1080p.DSNP.WEB-DL", [], "tv"), ("S08E10", 8))
        self.assertEqual(app._torrent_label("Some.Show.3x07.720p", [], ""), ("S03E07", 3))
        self.assertEqual(app._torrent_label("The Young Ones S01 And 2 (1982) DVDrip", [], "tv"), ("S01 (season pack)", 1))
        self.assertEqual(app._torrent_label("9 To 5 (1980) [1080p] [BluRay]", [], ""), ("", None))
        self.assertEqual(app._torrent_label("Arrival.2016.1080p", [], "movie"), ("", None))
    def test_derive_title(self):
        self.assertEqual(app._derive_title("The.Expanse.S02E01.1080p.WEB"), "The Expanse")
        self.assertEqual(app._derive_title("Arrival.2016.1080p.BluRay"), "Arrival")
        self.assertEqual(app._derive_title("Some.Show.Season.2.COMPLETE"), "Some Show")
        self.assertEqual(app._derive_title(""), "Other")
    def test_query_and_bool(self):
        self.assertEqual(app._q("/api/x?kind=movie&id=3"), {"kind": "movie", "id": "3"}); self.assertEqual(app._q("/api/x"), {})
        for v in (True, "True", "on", "1", "yes"): self.assertTrue(app._bool(v))
        for v in (False, "false", "off", "0", "", None): self.assertFalse(app._bool(v))
    def test_download_cap_comes_from_app_env(self):
        self.assertEqual(app.MAX_ACTIVE_DL_CAP, 2)


class UsersAndRoles(unittest.TestCase):
    def setUp(self):
        for f in ("users.json", "users.json.bad"):
            try: os.unlink(os.path.join(app.SB_DIR, f))
            except FileNotFoundError: pass
    def test_first_run_seeds_admin_with_a_private_file(self):
        d = app.load_users()
        self.assertEqual(d["users"]["admin"]["role"], "admin")
        self.assertEqual(stat.S_IMODE(os.stat(app.USERS_FILE).st_mode), 0o600)
        self.assertEqual(app.authenticate("admin", harness.PASSWORD), "admin"); self.assertIsNone(app.authenticate("admin", "nope"))
        self.assertEqual(app.authenticate("", harness.PASSWORD), "admin")   # blank username = admin
    def test_corrupt_store_is_kept_aside_not_overwritten(self):
        with open(app.USERS_FILE, "w") as f: f.write("{not json")
        d = app.load_users()
        self.assertIn("admin", d["users"]); self.assertTrue(os.path.exists(app.USERS_FILE + ".bad"))
    def test_user_lifecycle_and_guards(self):
        self.assertEqual(app.save_user({"username": "bad name", "password": "x", "role": "user"}), (False, "Invalid username"))
        self.assertEqual(app.save_user({"username": "sam", "role": "user"})[0], False)            # new user needs a password
        self.assertEqual(app.save_user({"username": "sam", "password": "pw", "role": "root"}), (False, "Invalid role"))
        self.assertEqual(app.save_user({"username": "sam", "password": "pw", "role": "user"}), (True, "Added sam"))
        self.assertEqual(app.authenticate("sam", "pw"), "user")
        self.assertEqual(app.save_user({"username": "sam", "password": "pw2"}), (True, "Updated sam"))   # password reset keeps the role
        self.assertEqual(app.authenticate("sam", "pw2"), "user")
        self.assertEqual(app.save_user({"username": "admin", "role": "user"}), (False, "Can't demote the last admin"))
        self.assertEqual(app.delete_user("admin"), (False, "Can't remove the last admin"))
        self.assertEqual(app.delete_user("ghost"), (False, "No such user"))
        self.assertEqual([u["username"] for u in app.list_users()], ["admin", "sam"])
        self.assertEqual(app.delete_user("sam"), (True, "Removed sam"))
    def test_role_capabilities_gate_actions(self):
        sess = {"user": "sam", "role": "user"}
        self.assertFalse(app._can(sess, "can_purge")); self.assertTrue(app._can({"role": "admin"}, "can_purge")); self.assertFalse(app._can(None, "can_purge"))
        self.assertEqual(app.save_role({"role": "admin"}), (False, "Only the 'user' role is editable"))
        self.assertEqual(app.save_role({"role": "user", "can_purge": True})[0], True)
        self.assertTrue(app._can(sess, "can_purge")); self.assertFalse(app._can(sess, "can_remove"))
        self.assertEqual(set(app.role_caps("admin")), set(app.CONFIGURABLE_CAPS))
    def test_every_privileged_action_names_a_configurable_cap(self):
        for action, cap in app._CAP_FOR.items(): self.assertIn(cap, app.CONFIGURABLE_CAPS, action)
    def test_sessions_file_is_private(self):
        app.SESSIONS["tok"] = {"user": "admin", "role": "admin", "exp": 4102444800}; app._save_sessions()
        self.assertEqual(stat.S_IMODE(os.stat(app.SESSIONS_FILE).st_mode), 0o600)
        self.assertIn("tok", json.load(open(app.SESSIONS_FILE)))


if __name__ == "__main__":
    unittest.main()
