"""action_log: the bounded ring behind Settings ▸ Action log. Its two promises are that it cannot grow
without limit and that no secret can reach it (docs/DASHBOARD.md ▸ Settings, docs/DEVELOPMENT.md §2.1)."""
import io, json, os, stat, sys, tempfile, unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
os.environ.setdefault("CONTROLLARR_ENV", "/nonexistent/controllarr.env")
import action_log, services


def _write(n, action="retry", user="admin", ok=True, msg="done"):
    for i in range(n):
        with redirect_stdout(io.StringIO()):
            action_log.record("action", user, "admin", action, f"movie:{i}", ok, ms=i, msg=msg)


class Ring(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mc-log-")
        self.path = os.path.join(self.root, "actions.log")
        self.owned = []
        action_log.configure(self.path, self.owned.append)
    def tearDown(self):
        action_log.configure("", None)

    def test_an_entry_has_the_fixed_shape_and_the_line_still_reaches_stdout(self):
        out = io.StringIO()
        with redirect_stdout(out):
            e = action_log.record("action", "sam", "user", "purge", "movie:12", True, ms=41, msg="Purged Coherence")
        self.assertEqual(tuple(e), action_log.FIELDS)
        self.assertEqual((e["type"], e["user"], e["role"], e["action"], e["target"], e["result"], e["ms"]),
                         ("action", "sam", "user", "purge", "movie:12", "ok", 41))
        self.assertIn("action user=sam role=user action=purge target=movie:12 result=ok ms=41", out.getvalue())
        self.assertEqual(action_log.entries()[0], e)                      # printed and kept are the same entry
        with redirect_stdout(io.StringIO()):
            f = action_log.record("account", "admin", "admin", "user_save", "sam", False, msg="Invalid username")
        self.assertEqual((f["type"], f["result"], f["ms"]), ("account", "fail", None))
        self.assertEqual([x["action"] for x in action_log.entries()], ["user_save", "purge"])   # newest first

    def test_the_ring_is_bounded_and_keeps_the_newest(self):
        n = action_log.LOG_RING + action_log._SLACK + 5
        _write(n)
        with open(self.path) as f: kept = [json.loads(ln) for ln in f]
        self.assertLessEqual(len(kept), action_log.LOG_RING + action_log._SLACK)                # it can never grow without limit
        self.assertGreaterEqual(len(kept), action_log.LOG_RING)                                 # and a trim leaves a full ring
        self.assertEqual(kept[-1]["target"], f"movie:{n - 1}")                                  # the newest write is always there
        self.assertEqual(kept[0]["target"], f"movie:{n - len(kept)}")                           # the oldest fell off
        _write(action_log.LOG_RING)                                                             # and it stays bounded for ever
        with open(self.path) as f: self.assertLessEqual(sum(1 for _ in f), action_log.LOG_RING + action_log._SLACK)

    def test_the_file_is_private_and_owned_like_its_directory(self):
        _write(1)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(self.owned, [self.path])
        _write(action_log.LOG_RING + action_log._SLACK + 1)                                     # a rewrite must not lose either
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertIn(self.path, self.owned[1:])

    def test_no_secret_survives_into_the_file(self):
        key = "action-log-secret-0123"
        services.add_secret(key)
        try:
            with redirect_stdout(io.StringIO()):
                action_log.record("action", "admin", "admin", "grab", key, False, ms=1, msg=f"Indexer refused ({key})")
            with open(self.path) as f: raw = f.read()
            self.assertNotIn(key, raw)
            self.assertIn("***", raw)
        finally:
            services._extra.discard(key)

    def test_filters_and_a_torn_line_never_break_a_read(self):
        _write(2, action="retry", user="admin")
        _write(2, action="purge", user="sam")
        self.assertEqual([e["action"] for e in action_log.entries(user="sam")], ["purge", "purge"])
        self.assertEqual([e["user"] for e in action_log.entries(action="retry")], ["admin", "admin"])
        v = action_log.view(limit=1)
        self.assertEqual((len(v["entries"]), v["total"], v["cap"]), (1, 4, action_log.LOG_RING))
        self.assertEqual((v["users"], v["actions"]), (["admin", "sam"], ["purge", "retry"]))
        with open(self.path, "a") as f: f.write('{"ts": 1, "type": "act')                        # a crash mid-write
        self.assertEqual(action_log.view()["total"], 4)
        self.assertEqual(action_log.view(limit="nonsense")["total"], 4)

    def test_before_configure_it_prints_and_keeps_nothing(self):
        action_log.configure("", None)
        with redirect_stdout(io.StringIO()):
            action_log.record("action", "admin", "admin", "retry", "movie:1", True)
        self.assertEqual(action_log.entries(), [])
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
