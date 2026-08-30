"""library_import: which folders adoption is allowed to take. A purge deletes files, not directories, so an
emptied folder outlives the title it held; adopting one re-adds the very thing that was purged, with nothing
in it (docs/DASHBOARD.md ▸ Library, docs/DEVELOPMENT.md §2.1)."""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import library_import

FULL, EMPTY = "/data/media/tv/Has Media", "/data/media/tv/Emptied By A Purge"


class FakeArr:
    """The injected arr(app, path, method, data) -> (status, body), recording what it was asked to add.
    `probe` is what /manualimport answers: "real" scans the folder, "broken" fails the way an old arr would."""
    def __init__(self, probe="real"):
        self.probe = probe; self.added = []; self.calls = []

    def __call__(self, app, path, method="GET", data=None):
        self.calls.append((app, path, method))
        if path.startswith("/manualimport"):
            if self.probe == "broken": return 404, {"message": "not found"}
            return 200, ([{"path": FULL + "/thefile.mkv"}] if "Has%20Media" in path else [])
        if path == "/qualityprofile": return 200, [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}]
        if path == "/rootfolder":
            return 200, [{"path": "/data/media/tv", "unmappedFolders": [{"name": "Has Media", "path": FULL},
                                                                       {"name": "Emptied By A Purge", "path": EMPTY}]}]
        if path in ("/movie", "/series") and method == "GET": return 200, []
        if path.startswith("/movie/lookup") or path.startswith("/series/lookup"):
            term = path.split("term=", 1)[1]                      # a distinct id per folder, or the second dedupes away
            n = abs(hash(term)) % 100000
            return 200, [{"title": "Has Media" if "Has%20Media" in term else "Emptied By A Purge", "tmdbId": n, "tvdbId": n}]
        if path in ("/movie", "/series") and method == "POST":
            self.added.append((app, data)); return 201, dict(data or {}, id=9)
        return 200, {}


class Adoption(unittest.TestCase):
    def test_an_emptied_folder_is_not_re_adopted(self):
        arr = FakeArr()
        out = library_import.import_existing(arr)
        self.assertEqual([a[1]["title"] for a in arr.added], ["Has Media", "Has Media"])   # radarr + sonarr
        self.assertEqual(out["series"], {"added": 1, "failed": 0, "skipped": 1})
        self.assertEqual(out["movies"], {"added": 1, "failed": 0, "skipped": 1})
        self.assertNotIn(EMPTY, [a[1].get("path") for a in arr.added])

    def test_an_unanswerable_probe_still_adopts(self):
        """A broken or unsupported probe must not turn adoption into a silent no-op."""
        arr = FakeArr(probe="broken")
        out = library_import.import_existing(arr)
        self.assertEqual(out["series"]["added"], 2)
        self.assertEqual(out["series"]["skipped"], 0)

    def test_the_given_profile_is_used_not_the_first_one(self):
        arr = FakeArr()
        library_import.import_existing(arr, profiles={"radarr": 4, "sonarr": 4})
        self.assertEqual({a[1]["qualityProfileId"] for a in arr.added}, {4})

    def test_no_profile_given_falls_back_to_the_arr_s_first(self):
        arr = FakeArr()
        library_import.import_existing(arr)
        self.assertEqual({a[1]["qualityProfileId"] for a in arr.added}, {1})


if __name__ == "__main__":
    unittest.main()
