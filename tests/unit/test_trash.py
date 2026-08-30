"""The TRaSH Guides seam: the vendored data, the profile the arrs are asked to hold, and the diff a person
reads before pressing Apply. Nothing here talks to a real app — the arr accessor is a dictionary."""
import copy, json, os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
os.environ.setdefault("CONTROLLARR_ENV", "/nonexistent/controllarr.env")
import settings_ops, trash

QUALITIES = [{"id": 0, "name": "Unknown"}, {"id": 1, "name": "SDTV"}, {"id": 4, "name": "HDTV-720p"},
             {"id": 3, "name": "WEBDL-1080p"}, {"id": 15, "name": "WEBRip-1080p"}, {"id": 7, "name": "Bluray-1080p"},
             {"id": 30, "name": "Remux-1080p"}, {"id": 25, "name": "BR-DISK"}]


def fake_arr(profiles=None, formats=None, defs=None):
    """An arr as a dictionary: GETs read it, PUTs and POSTs write it, and `calls` is the ordered call log."""
    state = {"profiles": copy.deepcopy(profiles if profiles is not None else
                                      [{"id": 1, "name": "Any", "cutoff": 7, "upgradeAllowed": True, "minFormatScore": 0,
                                        "cutoffFormatScore": 10000, "minUpgradeFormatScore": 1, "formatItems": [],
                                        "language": {"id": -2, "name": "Original"},
                                        "items": [{"quality": dict(q), "items": [], "allowed": q["name"] == "Bluray-1080p"} for q in QUALITIES]}]),
             "formats": copy.deepcopy(formats or []),
             "defs": copy.deepcopy(defs if defs is not None else
                                   [{"id": n + 1, "quality": dict(q), "minSize": 0, "preferredSize": 20, "maxSize": 50}
                                    for n, q in enumerate(QUALITIES)])}
    calls = []

    def arr(app, path, method="GET", data=None):
        calls.append((method, path))
        if path == "/qualityprofile/schema":
            return 200, {"items": [{"quality": dict(q), "items": [], "allowed": False} for q in QUALITIES]}
        if path == "/qualityprofile":
            if method == "POST":
                p = dict(data); p["id"] = max([x["id"] for x in state["profiles"]] or [0]) + 1
                state["profiles"].append(p); return 201, p
            return 200, state["profiles"]
        if path.startswith("/qualityprofile/"):
            pid = int(path.rsplit("/", 1)[1])
            for n, p in enumerate(state["profiles"]):
                if p["id"] == pid: state["profiles"][n] = dict(data); return 202, data
            return 404, {}
        if path == "/customformat":
            if method == "POST":
                c = dict(data); c["id"] = max([x["id"] for x in state["formats"]] or [100]) + 1
                state["formats"].append(c); return 201, c
            return 200, state["formats"]
        if path.startswith("/customformat/"):
            cid = int(path.rsplit("/", 1)[1])
            for n, c in enumerate(state["formats"]):
                if c["id"] == cid: state["formats"][n] = dict(data, id=cid); return 202, data
            return 404, {}
        if path == "/qualitydefinition": return 200, state["defs"]
        if path.startswith("/qualitydefinition/"):
            qid = int(path.rsplit("/", 1)[1])
            for n, q in enumerate(state["defs"]):
                if q["id"] == qid: state["defs"][n] = dict(data); return 202, data
            return 404, {}
        if path == "/language": return 200, [{"id": -2, "name": "Original"}, {"id": 1, "name": "English"}]
        return 200, None
    return arr, state, calls


class VendoredData(unittest.TestCase):
    def test_both_apps_ship_profiles_formats_and_sizes_with_the_licence(self):
        for app in ("radarr", "sonarr"):
            d = trash.load(app)
            self.assertTrue(d["profiles"], app); self.assertTrue(d["formats"], app)
            self.assertIn("default", d["sizes"], app)
            for p in d["profiles"]:
                self.assertTrue(p["items"], p["name"])
                for name, tid in p["formats"].items():
                    self.assertIn(tid, d["formats"], f"{app} {p['name']} {name}")
        with open(os.path.join(trash.VENDOR_DIR, "LICENSE")) as f:
            self.assertIn("Copyright (c) 2021 TRaSH", f.read())

    def test_descriptions_carry_no_markup(self):
        """TRaSH writes its descriptions with <br> in them; the panel renders text, never markup."""
        for app in ("radarr", "sonarr"):
            for p in trash.load(app)["profiles"]:
                self.assertNotIn("<", p["desc"] if "desc" in p else p["description"], p["name"])


class BuildProfile(unittest.TestCase):
    def test_items_come_back_worst_first_with_groups_and_nothing_dropped(self):
        tp = trash.profile("radarr", "HD Bluray + WEB")
        items = trash.build_items(tp, {q["name"]: q for q in QUALITIES})
        flat = trash._flatten(items)
        self.assertEqual(set(flat), {q["name"] for q in QUALITIES})       # a quality the guide does not name still appears
        self.assertEqual(items[0].get("quality", {}).get("name"), "Unknown")   # worst first, as the arrs list them
        group = next(i for i in items if i.get("name") == "WEB 1080p")
        self.assertGreaterEqual(group["id"], 1000)                        # above every real quality id
        self.assertEqual([q["quality"]["name"] for q in group["items"]], ["WEBDL-1080p", "WEBRip-1080p"])
        self.assertEqual(trash._cutoff_id(tp, items), 7)                  # "Bluray-1080p"

    def test_a_cutoff_that_names_a_group_resolves_to_the_group(self):
        tp = trash.profile("sonarr", "WEB-1080p")
        items = trash.build_items(tp, {q["name"]: q for q in QUALITIES})
        cid = trash._cutoff_id(tp, items)
        self.assertEqual(next(i["name"] for i in items if i.get("id") == cid), "WEB 1080p")


class Plan(unittest.TestCase):
    def test_a_stock_app_is_a_creation_with_every_format_to_make(self):
        arr, state, calls = fake_arr()
        pl = trash.plan("radarr", "HD Bluray + WEB", arr, "Any")
        self.assertFalse(pl["exists"])                              # the app has no profile of that name
        self.assertFalse(pl["empty"])
        self.assertEqual(len(pl["formats"]["create"]), pl["formats"]["total"])
        self.assertFalse(pl["formats"]["update"])
        self.assertFalse(pl["qualities"])                           # a creation is not two dozen quality changes
        self.assertIn("Bluray-1080p", pl["allowed"])
        self.assertTrue(pl["sizes"])                                # the per-quality limits replace the old MB/min cap
        self.assertEqual(pl["default_profile"], {"current": "Any", "to": "HD Bluray + WEB", "change": True})
        self.assertFalse([c for c in calls if c[0] != "GET"], "a plan writes nothing")

    def test_a_format_whose_regex_moved_on_is_an_update_not_a_duplicate(self):
        tp = trash.profile("radarr", "HD Bluray + WEB")
        name, tid = sorted(tp["formats"].items())[0]
        stale = trash.format_body(trash.load("radarr")["formats"][tid])
        stale["specifications"][0]["fields"] = [{"name": "value", "value": "something the guide no longer says"}]
        arr, state, calls = fake_arr(formats=[dict(stale, id=101)])
        pl = trash.plan("radarr", "HD Bluray + WEB", arr, "Any")
        self.assertEqual(pl["formats"]["update"], [name])
        self.assertNotIn(name, pl["formats"]["create"])

    def test_applying_twice_is_a_no_op_the_second_time(self):
        arr, state, calls = fake_arr()
        pl = trash.plan("radarr", "HD Bluray + WEB", arr, "Any")
        self.assertEqual(settings_ops.apply_trash(pl, arr), [])
        again = trash.plan("radarr", "HD Bluray + WEB", arr, "HD Bluray + WEB")
        self.assertTrue(again["exists"])
        self.assertTrue(again["empty"], {k: again[k] for k in ("formats", "scores", "qualities", "fields", "sizes")})


class Apply(unittest.TestCase):
    def test_formats_then_the_profile_then_the_sizes(self):
        arr, state, calls = fake_arr()
        pl = trash.plan("radarr", "HD Bluray + WEB", arr, "Any")
        calls.clear()
        self.assertEqual(settings_ops.apply_trash(pl, arr), [])
        writes = [c for c in calls if c[0] in ("POST", "PUT")]
        kinds = [c[1].split("/")[1] for c in writes]
        self.assertEqual(kinds[:len(pl["formats"]["create"])], ["customformat"] * len(pl["formats"]["create"]))
        self.assertEqual(kinds[len(pl["formats"]["create"])], "qualityprofile")
        self.assertTrue(all(k == "qualitydefinition" for k in kinds[len(pl["formats"]["create"]) + 1:]))

    def test_the_profile_the_app_ends_up_with_is_the_guides(self):
        arr, state, calls = fake_arr()
        settings_ops.apply_trash(trash.plan("radarr", "HD Bluray + WEB", arr, "Any"), arr)
        p = next(x for x in state["profiles"] if x["name"] == "HD Bluray + WEB")
        allowed = {n for n, a in trash._allowed(p["items"]).items() if a}
        self.assertIn("Bluray-1080p", allowed); self.assertNotIn("BR-DISK", allowed)
        self.assertEqual(p["cutoff"], 7); self.assertEqual(p["language"], {"id": -2, "name": "Original"})
        scores = {i["name"]: i["score"] for i in p["formatItems"]}
        self.assertEqual(scores["HD Bluray Tier 01"], 1800)     # the guide's score, not a number of ours
        # a format this profile does not want is scored 0 rather than left at whatever put it there
        self.assertEqual(scores.get("Original-language", 0), 0)
        sizes = {d["quality"]["name"]: (d["minSize"], d["preferredSize"], d["maxSize"]) for d in state["defs"]}
        self.assertNotEqual(sizes["Bluray-1080p"], (0, 20, 50))     # the one MB/min figure for everything is gone
        self.assertNotEqual(sizes["Bluray-1080p"][0], sizes["HDTV-720p"][0])   # a floor per quality, which is the point
        # and the guide sets no ceiling: a release is ranked by score, not refused for being big
        self.assertGreaterEqual(sizes["Bluray-1080p"][2], 1000)

    def test_a_format_the_guide_changed_is_updated_in_place_so_profiles_follow(self):
        tp = trash.profile("radarr", "HD Bluray + WEB")
        name, tid = sorted(tp["formats"].items())[0]
        stale = trash.format_body(trash.load("radarr")["formats"][tid])
        stale["specifications"][0]["fields"] = [{"name": "value", "value": "stale"}]
        arr, state, calls = fake_arr(formats=[dict(stale, id=101)])
        settings_ops.apply_trash(trash.plan("radarr", "HD Bluray + WEB", arr, "Any"), arr)
        self.assertEqual(len([c for c in state["formats"] if c["name"] == name]), 1)   # updated, never duplicated
        self.assertEqual(next(c for c in state["formats"] if c["name"] == name)["id"], 101)
        self.assertNotIn("stale", json.dumps(state["formats"]))


class SnapshotAndRollback(unittest.TestCase):
    def test_a_snapshot_taken_first_puts_everything_back(self):
        arr, state, calls = fake_arr()
        before = settings_ops.arr_state(arr, "radarr")
        settings_ops.apply_trash(trash.plan("radarr", "HD Bluray + WEB", arr, "Any"), arr)
        self.assertNotEqual(settings_ops.arr_state(arr, "radarr")["quality_definitions"], before["quality_definitions"])
        errs, extra = settings_ops.apply_arr_state(before, arr, "radarr")
        self.assertEqual(errs, [])
        after = settings_ops.arr_state(arr, "radarr")
        self.assertEqual(after["quality_definitions"], before["quality_definitions"])
        self.assertEqual([p for p in after["profiles"] if p["name"] == "Any"],
                         [p for p in before["profiles"] if p["name"] == "Any"])

    def test_a_profile_the_sync_created_is_left_alone_and_named(self):
        """Deleting a profile titles are already on is not a rollback, it is a second accident."""
        arr, state, calls = fake_arr()
        before = settings_ops.arr_state(arr, "radarr")
        settings_ops.apply_trash(trash.plan("radarr", "HD Bluray + WEB", arr, "Any"), arr)
        errs, extra = settings_ops.apply_arr_state(before, arr, "radarr")
        self.assertEqual(extra, ["HD Bluray + WEB"])
        self.assertTrue(any(p["name"] == "HD Bluray + WEB" for p in state["profiles"]))

    def test_the_state_is_keyed_by_name_so_it_travels(self):
        arr, state, calls = fake_arr(formats=[{"id": 55, "name": "BR-DISK", "specifications": []}])
        state["profiles"][0]["formatItems"] = [{"format": 55, "score": -900}]
        snap = settings_ops.arr_state(arr, "radarr")
        self.assertEqual(snap["profiles"][0]["formats"], {"BR-DISK": -900})
        self.assertEqual(snap["profiles"][0]["cutoff"], "Bluray-1080p")
        self.assertNotIn("55", json.dumps(snap["profiles"][0]["formats"]))


if __name__ == "__main__":
    unittest.main()
