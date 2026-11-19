import base64
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib

from engine import Store, summarize


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.sqlite"
        db = sqlite3.connect(self.path)
        db.executescript("""
            PRAGMA user_version=1;
            CREATE TABLE metadata(key TEXT,value TEXT);
            CREATE TABLE images(id INTEGER PRIMARY KEY,role TEXT,name TEXT,pno INTEGER,seq INTEGER,
                ms TEXT,chip_x INTEGER,chip_y INTEGER,xrel REAL,yrel REAL,jpeg BLOB,geometry BLOB);
            CREATE TABLE patterns(image_id INTEGER,ordinal INTEGER,layout_x REAL,layout_y REAL,
                px REAL,py REAL,dx REAL,dy REAL,magnitude REAL,direction REAL,
                available INTEGER,valid INTEGER,raw TEXT);
        """)
        geometry = zlib.compress(json.dumps(dict(width=512, height=512, nm_x=1, nm_y=1,
                                                  design=[], contour=[[[0, 0], [1, 0], [1, 1]]])).encode())
        for identity, role, chip in [(1, "base", 8), (2, "comparison", 8), (3, "base", 9)]:
            db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (identity, role, f"sample-{identity}.png", 2, 1, "01MS", chip, 7,
                        10, 20, b"jpeg", geometry))
            for ordinal, (px, dx, dy, available, valid) in enumerate([
                (10, 3, 4, True, True), (100, 0, 0, True, True),
                (20, None, None, False, False), (30, 20, 1, True, False),
            ]):
                magnitude = math.hypot(dx, dy) if available else None
                direction = math.degrees(math.atan2(dy, dx)) if magnitude else None
                raw = json.dumps(dict(PixelX=px, PixelY=10, PID=str(21 + ordinal)))
                db.execute("INSERT INTO patterns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    identity, ordinal, ordinal, ordinal, px+(1 if role == "comparison" else 0), 10,
                    dx, dy, magnitude, direction, available, valid, raw))
        db.commit()
        db.close()
        self.store = Store(self.path, allow_legacy=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_same_chip_both_vectors_and_translation(self):
        image = self.store.image(1)
        self.assertEqual(image["matched"], 4)
        self.assertEqual(image["shift"], [-1, 0])
        self.assertEqual([len(image["vectors"][role]) for role in ("base", "comparison")], [2, 2])
        self.assertEqual(image["vectors"]["base"][0]["start"], image["vectors"]["comparison"][0]["start"])
        self.assertEqual(image["vectors"]["base"][0]["delta"], [3, -4])

    def test_comparison_image_only_for_same_chip_pair(self):
        with sqlite3.connect(self.path) as db:
            name, jpeg = db.execute("SELECT name,jpeg FROM images WHERE id=2").fetchone()
        paired = self.store.comparison_image(1)
        self.assertEqual(paired["name"], name)
        self.assertEqual(base64.b64decode(paired["image"].split(",", 1)[1]), jpeg)
        self.assertEqual(self.store.comparison_image(3), {"id": 3, "name": None, "image": None})
        with self.assertRaisesRegex(ValueError, "Base image"):
            self.store.comparison_image(2)

    def test_reference_frames_and_design_keep_native_coordinates(self):
        design = [[[20, 30], [40, 30], [40, 50]]]
        with sqlite3.connect(self.path) as db:
            geometry = json.loads(zlib.decompress(db.execute(
                "SELECT geometry FROM images WHERE id=2").fetchone()[0]))
            geometry.update(width=1024, nm_x=2, nm_y=3, design=design)
            db.execute("UPDATE images SET geometry=? WHERE id=2", (zlib.compress(json.dumps(geometry).encode()),))
        image = self.store.image(1)
        self.assertEqual(image["shift"], [-1, 0])
        self.assertEqual(image["reference_frames"]["base"], dict(width=512, height=512, nm_x=1, nm_y=1))
        self.assertEqual(image["reference_frames"]["comparison"], dict(width=1024, height=512, nm_x=2, nm_y=3))
        self.assertEqual(image["comparison_design"], design, "The source-image PID shift must not enter reference design")
        self.assertEqual(image["design"], [])

    def test_different_chip_not_paired(self):
        image = self.store.image(3)
        self.assertEqual(image["vectors"]["comparison"], [])
        self.assertEqual(image["rows"]["comparison"], [])
        self.assertIn("same-chip", image["notice"])

    def test_common_roi_and_quality_counts(self):
        stats = self.store.statistics(dict(pno=2, chips=[[8, 7]], roi=[0, 0, 50, 50]))
        for role in ("base", "comparison"):
            dx = next(r for r in stats["table"] if r["role"] == role and r["metric"] == "dx")
            self.assertEqual((dx["n"], dx["missing"], dx["excluded"]), (1, 1, 1))
            self.assertEqual(dx["mean"], 3)
        self.assertEqual(stats["paired_images"], 1)

    def test_direction_removed_and_population_sigma(self):
        result = self.store.statistics(dict(pno=2, chips=[[8, 7]]))
        self.assertEqual(len(result["table"]), 6)
        self.assertFalse(any(r["metric"] == "direction" for r in result["table"]))
        self.assertNotIn("direction", self.store.image(1)["rows"]["base"][0])
        self.assertEqual(summarize([0, 2])["sigma"], 1)
        self.assertAlmostEqual(abs(summarize([179, -179], True)["mean"]), 180)

    def test_ms_filter_and_shared_maps(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE images SET ms='02MS' WHERE id=3")
        result = self.store.statistics(dict(pno=2, ms="01MS"))
        self.assertEqual(result["images"], 1)
        self.assertEqual({r["id"] for r in result["image_maps"]}, {1, 2})
        value = next(r for r in result["maps"] if r["role"] == "base")
        self.assertEqual((value["dx"], value["dy"], value["magnitude"], value["n"]), (1.5, 2, 2.5, 2))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE images SET chip_x=6 WHERE id=2")
        result = self.store.statistics(dict(pno=2, ms="01MS"))
        self.assertEqual(result["paired_images"], 0)
        self.assertEqual(next(r for r in result["table"] if r["role"] == "comparison")["n"], 0)
        self.assertEqual(result["maps"], [])
        self.assertEqual(result["image_maps"], [])
        self.assertEqual(result["counts"]["base"]["unpaired"], 1)
        self.assertEqual(result["counts"]["comparison"]["unpaired"], 1)
        for filters in [dict(pno=2, ms="unknown"), dict(pno=2, roi=[0, 0, float("inf"), 1])]:
            with self.assertRaises(ValueError):
                self.store.statistics(filters)

    def test_unpaired_base_excluded_everywhere(self):
        result = self.store.statistics(dict(pno=2))
        self.assertEqual(result["images"], 1)
        self.assertEqual(result["counts"]["base"], dict(selected=2, unpaired=1, counterpart_outside=0))
        self.assertEqual({row["id"] for row in result["image_maps"]}, {1, 2})
        for role in ("base", "comparison"):
            table = next(r for r in result["table"] if r["role"] == role and r["metric"] == "dx")
            maps = [r for r in result["maps"] if r["role"] == role]
            self.assertEqual(table["n"], sum(r["n"] for r in maps))
            self.assertEqual(table["n"], len(result["graph"][role]["dx"]))
            self.assertEqual(table["n"], sum(r["n"] for r in result["image_maps"] if r["role"] == role))
        self.assertEqual(next(e for e in self.store.index()["images"] if e["id"] == 1)["pair_id"], 2)

    def test_counterpart_outside_spatial_selection_excluded_both_sides(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE images SET xrel=100 WHERE id=2")
        result = self.store.statistics(dict(pno=2, chips=[[8, 7]], chipRect=[0, 0, 50, 50]))
        self.assertEqual(result["paired_images"], 0)
        self.assertEqual(result["counts"]["base"]["counterpart_outside"], 1)
        self.assertEqual(result["counts"]["base"]["unpaired"], 0)
        self.assertEqual(result["maps"], [])
        self.assertTrue(all(r["n"] == 0 for r in result["table"]))

    def test_source_roi_can_yield_different_valid_pattern_counts(self):
        result = self.store.statistics(dict(pno=2, chips=[[8, 7]], roi=[0, 0, 10, 50]))
        self.assertEqual(result["paired_images"], 1)
        self.assertEqual([r["n"] for r in result["image_maps"]], [1, 0])
        self.assertEqual([r["n"] for r in result["table"] if r["metric"] == "dx"], [1, 0])

    def test_empty_and_rect_validation(self):
        result = self.store.statistics(dict(pno=2, chipRect=[50, 50, 60, 60]))
        self.assertEqual(result["images"], 0)
        self.assertTrue(all(r["n"] == 0 and r["mean"] is None for r in result["table"]))
        for rect in ([1, 0, 0, 1], [float("nan"), 0, 1, 1], [1, 2]):
            with self.assertRaises(ValueError):
                self.store.statistics(dict(pno=2, roi=rect))

    def test_overlay_pairs_patterns_by_pid_not_layout(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE patterns SET layout_x=layout_x+5000 WHERE image_id=2")
        image = self.store.image(1)
        self.assertEqual((image["matched"], image["shift"]), (4, [-1, 0]))
        self.assertEqual(len(image["vectors"]["comparison"]), 2)
        self.assertEqual([label["pid"] for label in image["labels"]], ["21", "22", "23", "24"])

    def test_ppe_per_pid(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE patterns SET dx=1,dy=1 WHERE image_id=2 AND ordinal=0")
        ppe = self.store.statistics(dict(pno=2))["ppe"]
        self.assertEqual(ppe["pids"], ["21", "22", "23", "24"])
        rows = {row["pid"]: row for row in ppe["table"]}
        self.assertEqual(list(rows), ["All", "21", "22", "23", "24"])
        self.assertEqual((rows["21"]["n"], rows["21"]["x_mean"], rows["21"]["y_mean"]), (1, 2, 3))
        self.assertAlmostEqual(rows["21"]["magnitude_mean"], math.hypot(2, 3))
        self.assertEqual((rows["All"]["n"], rows["All"]["x_mean"], rows["All"]["x_sd"]), (2, 1, 1))
        self.assertNotIn("x_3sigma", rows["All"])
        self.assertEqual((rows["23"]["n"], rows["23"]["x_mean"], rows["23"]["excluded"]), (0, None, 1))
        self.assertEqual(ppe["values"]["21"], {"x": [2], "y": [3], "magnitude": [math.hypot(2, 3)]})

    def test_ppe_roi_uses_base_position_and_values_export(self):
        result = self.store.statistics(dict(pno=2, roi=[0, 0, 10, 50]))
        self.assertEqual([r["n"] for r in result["table"] if r["metric"] == "dx"], [1, 0])
        self.assertEqual(result["ppe"]["table"][0]["n"], 1)
        rows = self.store.ppe_values(dict(pno=2))
        self.assertEqual([r["pid"] for r in rows], ["21", "22", "23", "24"])
        self.assertEqual((rows[0]["ppe_x"], rows[0]["base_image"], rows[0]["comparison_image"]),
                         (0, "sample-1.png", "sample-2.png"))
        self.assertEqual((rows[2]["ppe_x"], rows[2]["base_valid"]), (None, False))

    def test_repeated_or_missing_pid_not_paired(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE patterns SET raw=json_set(raw,'$.PID','21') WHERE image_id=2 AND ordinal=1")
            db.execute("UPDATE patterns SET raw=json_remove(raw,'$.PID') WHERE image_id=2 AND ordinal=2")
        ppe = self.store.statistics(dict(pno=2))["ppe"]
        self.assertEqual(ppe["pids"], ["24"])
        self.assertEqual(ppe["unmatched"], dict(base=3, comparison=0))
        self.assertEqual(self.store.image(1)["matched"], 1)

    def test_ambiguous_pair_errors(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO images SELECT 4,role,name,pno,seq,ms,chip_x,chip_y,xrel,yrel,jpeg,geometry FROM images WHERE id=2")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.store.image(1)


if __name__ == "__main__":
    unittest.main()
