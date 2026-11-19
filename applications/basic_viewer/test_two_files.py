import base64
import hashlib
import importlib.util
import math
import json
from pathlib import Path
import sqlite3
import struct
import unittest
from unittest.mock import patch
import zlib

from engine import Store, connection, length_cells
from prepare_test_overlay import prepare_pair
import test_engine


def decode_grid(data: dict, role: str) -> tuple:
    return struct.unpack("<1048576d", zlib.decompress(base64.b64decode(data["packed"][role])))


class TwoFileTests(unittest.TestCase):
    def setUp(self):
        fixture = test_engine.EngineTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.legacy = fixture.path
        self.paths = []
        with sqlite3.connect(self.legacy) as original:
            original.row_factory = sqlite3.Row
            for role in ("base", "comparison"):
                path = self.legacy.parent / f"{role}.sqlite"
                self.paths.append(path)
                with sqlite3.connect(path) as db:
                    db.executescript("""
                        PRAGMA user_version=4;
                        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                        CREATE TABLE images(id INTEGER PRIMARY KEY,name TEXT,pno INTEGER,seq INTEGER,ms TEXT,
                            chip_x INTEGER,chip_y INTEGER,xrel REAL,yrel REAL,jpeg BLOB,geometry BLOB);
                        CREATE UNIQUE INDEX pairing ON images(pno,seq,ms,chip_x,chip_y);
                        CREATE TABLE patterns(image_id INTEGER,ordinal INTEGER,layout_x REAL,layout_y REAL,
                            px REAL,py REAL,dx REAL,dy REAL,magnitude REAL,direction REAL,
                            available INTEGER,valid INTEGER,contour_index INTEGER,raw TEXT,
                            PRIMARY KEY(image_id,ordinal));
                        CREATE TABLE contour_data(image_id INTEGER PRIMARY KEY,width INTEGER,height INTEGER,
                            nm_x REAL,nm_y REAL,design_x REAL,design_y REAL,offsets BLOB,points BLOB,
                            density_offsets BLOB,density_cells BLOB,density_lengths BLOB);
                    """)
                    for local, entry in enumerate(original.execute("SELECT * FROM images WHERE role=? ORDER BY id", (role,)), 1):
                        db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (local, *(entry[k] for k in ("name", "pno", "seq", "ms", "chip_x", "chip_y",
                                                               "xrel", "yrel", "jpeg", "geometry"))))
                        geometry = json.loads(zlib.decompress(entry["geometry"]))
                        points, offsets, starts, cells, lengths = [], [0], [0], [], []
                        extent = max(geometry["width"] * geometry["nm_x"], geometry["height"] * geometry["nm_y"]) / 2
                        anchor = (geometry["width"] / 2, geometry["height"] / 2)
                        for polygon in geometry["contour"]:
                            points.extend(value for point in polygon for value in point)
                            offsets.append(len(points) // 2)
                            for cell, length in sorted(length_cells(polygon, geometry, anchor, extent).items()):
                                cells.append(cell)
                                lengths.append(length)
                            starts.append(len(cells))
                        db.execute("INSERT INTO contour_data VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                            local, geometry["width"], geometry["height"], geometry["nm_x"], geometry["nm_y"],
                            *anchor,
                            struct.pack(f"<{len(offsets)}I", *offsets),
                            struct.pack(f"<{len(points)}d", *points),
                            struct.pack(f"<{len(starts)}I", *starts),
                            struct.pack(f"<{len(cells)}I", *cells), struct.pack(f"<{len(lengths)}f", *lengths),
                        ))
                        for row in original.execute("SELECT * FROM patterns WHERE image_id=?", (entry["id"],)):
                            raw = dict(json.loads(row["raw"]), XINDEX=str(entry["chip_x"]), YINDEX=str(entry["chip_y"]))
                            db.execute("INSERT INTO patterns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                       (local, *tuple(row)[1:-1], None, json.dumps(raw)))
                    manifest = dict(format="plugin-viewer-sqlite", format_version=4, dataset_id=role, csv_sha256="fixture",
                                    images=db.execute("SELECT COUNT(*) FROM images").fetchone()[0],
                                    patterns=db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0])
                    for key, value in dict(manifest=manifest, layout=dict(info={"fixture": True}, cells=[])).items():
                        db.execute("INSERT INTO metadata VALUES(?,?)", (key, json.dumps(value)))
        self.store = Store(*self.paths)

    def area_fixture(self, identical=False):
        for role, path in enumerate(self.paths):
            x = 10 if not role else 13 if identical else 15
            y = 10 + role * 2
            points = [(x, y), (x + 4, y), (x + 4, y + 4), (x, y + 4)]
            with sqlite3.connect(path) as db:
                db.execute("UPDATE contour_data SET nm_x=2,nm_y=3")
                db.execute("UPDATE patterns SET contour_index=0 WHERE image_id=1 AND ordinal=0")
                db.execute("UPDATE patterns SET px=10+ordinal*10+?,py=10+? WHERE image_id=1", (role * 3, role * 2))
                db.execute("UPDATE contour_data SET offsets=?,points=? WHERE image_id=1",
                           (struct.pack("<2I", 0, 4), struct.pack("<8d", *(v for p in points for v in p))))

    def area_second_pair(self):
        for role, path in enumerate(self.paths):
            with sqlite3.connect(path) as db:
                if role:
                    db.execute("INSERT INTO images SELECT 2,name,pno,seq,ms,9,chip_y,xrel,yrel,jpeg,geometry FROM images WHERE id=1")
                db.execute("DELETE FROM patterns WHERE image_id=2")
                db.execute("INSERT INTO patterns SELECT 2,ordinal,layout_x,layout_y,px,py,dx,dy,magnitude,direction,"
                           "available,valid,contour_index,raw FROM patterns WHERE image_id=1")
                db.execute("DELETE FROM contour_data WHERE image_id=2")
                db.execute("INSERT INTO contour_data SELECT 2,width*2,height,nm_x,nm_y,design_x,design_y,offsets,points,"
                           "density_offsets,density_cells,density_lengths FROM contour_data WHERE image_id=1")
                db.execute("UPDATE metadata SET value=json_set(value,'$.images',(SELECT COUNT(*) FROM images),"
                           "'$.patterns',(SELECT COUNT(*) FROM patterns)) WHERE key='manifest'")

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_spawned_workers_match_serial_roi_exclusions_zero_and_shared_extent(self):
        import numpy as np
        from multiprocessing import active_children

        self.area_fixture()
        self.area_second_pair()
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE patterns SET contour_index=NULL WHERE image_id=2")
        before = [hashlib.sha256(path.read_bytes()).digest() for path in self.paths]
        children = {child.pid for child in active_children()}
        for roi in (None, [9, 9, 11, 11], [0, 0, 1, 1]):
            with self.subTest(roi=roi):
                params = dict(scope="selection", filters=dict(pno=2, roi=roi))
                with patch("engine._AREA_PARALLEL_PATTERNS", 10 ** 9):
                    serial = self.store.area_difference(params)
                with patch("engine._AREA_PARALLEL_PATTERNS", 0):
                    parallel = self.store.area_difference(params)
                for key in ("scope", "measure", "bins", "extent_nm", "pairs", "selected_pairs",
                            "compared", "excluded", "mean_area_nm2", "bounds"):
                    self.assertEqual(serial[key], parallel[key], key)
                self.assertEqual(parallel["selected_pairs"], 2)
                self.assertEqual(parallel["pairs"], int(roi != [0, 0, 1, 1]))
                for kind in ("inward", "outward"):
                    grids = [np.frombuffer(zlib.decompress(base64.b64decode(data["packed"][kind])), dtype="<f8")
                             for data in (serial, parallel)]
                    np.testing.assert_allclose(*grids, rtol=1e-10, atol=1e-10)
                    self.assertAlmostEqual(float(grids[1].sum()), parallel["mean_area_nm2"][kind])
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).digest() for path in self.paths])
        self.assertEqual({child.pid for child in active_children()}, children)

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_worker_failure_propagates_and_does_not_leave_children(self):
        from multiprocessing import active_children

        self.area_fixture()
        self.area_second_pair()
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE contour_data SET offsets=? WHERE image_id=2", (b"bad",))
        children = {child.pid for child in active_children()}
        with patch("engine._AREA_PARALLEL_PATTERNS", 0), self.assertRaisesRegex(ValueError, "Invalid compact contour data"):
            self.store.area_difference(dict(scope="selection", filters=dict(pno=2)))
        self.assertEqual({child.pid for child in active_children()}, children)

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_uses_image_alignment_and_conserves_mean_cell_areas(self):
        self.area_fixture()
        # A second matched image has no contour mappings, so it must not dilute the mean.
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("INSERT INTO images SELECT 2,name,pno,seq,ms,9,chip_y,xrel,yrel,jpeg,geometry FROM images WHERE id=1")
            db.execute("INSERT INTO contour_data SELECT 2,width,height,nm_x,nm_y,design_x,design_y,offsets,points,"
                       "density_offsets,density_cells,density_lengths FROM contour_data WHERE image_id=1")
            db.execute("INSERT INTO patterns SELECT 2,ordinal,layout_x,layout_y,px,py,dx,dy,magnitude,direction,"
                       "available,valid,NULL,raw FROM patterns WHERE image_id=1")
        before = [hashlib.sha256(p.read_bytes()).digest() for p in self.paths]
        image = self.store.area_difference(dict(scope="image", id=1))
        self.assertEqual(image["shift"], self.store.image(1)["shift"])
        self.assertEqual(image["shift"], [-3, -2])
        self.assertEqual(len(image["patterns"]), 1)
        self.assertEqual(len(image["issues"]), 3)
        pattern = image["patterns"][0]
        self.assertEqual(pattern["pid"], "21")
        self.assertEqual([pattern[k]["area_nm2"] for k in ("inward", "outward")], [48, 48])
        self.assertEqual(min(x for ring in pattern["inward"]["rings"] for x, y in ring), 10)
        result = self.store.area_difference(dict(scope="selection", filters=dict(pno=2)))
        self.assertEqual((result["pairs"], result["selected_pairs"], result["compared"]), (1, 2, 1))
        self.assertEqual(result["excluded"], {"missing contour": 7})
        for kind in ("inward", "outward"):
            self.assertAlmostEqual(math.fsum(decode_grid(result, kind)), 48)
            self.assertAlmostEqual(result["mean_area_nm2"][kind], 48)
        empty = self.store.area_difference(dict(scope="selection", filters=dict(pno=2, roi=[0, 0, 1, 1])))
        self.assertEqual((empty["pairs"], empty["compared"]), (0, 0))
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).digest() for p in self.paths])

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_identical_is_valid_zero_and_calibration_must_match(self):
        self.area_fixture(identical=True)
        data = self.store.area_difference(dict(scope="selection", filters=dict(pno=2)))
        self.assertEqual((data["pairs"], data["compared"]), (1, 1))
        for kind in ("inward", "outward"):
            self.assertEqual(data["mean_area_nm2"][kind], 0)
            self.assertFalse(any(decode_grid(data, kind)))
        pid = self.store.area_difference(dict(scope="pid", pid="21", filters=dict(pno=2)))
        self.assertEqual((pid["pairs"], pid["compared"]), (1, 1))
        self.assertEqual(pid["bounds"], [-1, -1, 1, 1])
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE contour_data SET nm_x=4")
        with self.assertRaisesRegex(ValueError, "equal Base/Comp pixel calibration"):
            self.store.area_difference(dict(scope="image", id=1))
        with self.assertRaisesRegex(ValueError, "both Base and Comp"):
            Store(self.paths[0]).area_difference(dict(scope="image", id=1))

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_excludes_bad_coordinates_without_losing_valid_patterns(self):
        for role in (0, 1):
            for value in (-1, 2000, math.nan, math.inf):
                with self.subTest(role=role, coordinate=value):
                    self.area_fixture()
                    for index, path in enumerate(self.paths):
                        with sqlite3.connect(path) as db:
                            points = db.execute("SELECT points FROM contour_data WHERE image_id=1").fetchone()[0]
                            invalid = list(struct.unpack("<8d", points))
                            if index == role:
                                invalid[0] = value
                            db.execute("UPDATE contour_data SET offsets=?,points=? WHERE image_id=1",
                                       (struct.pack("<3I", 0, 4, 8), struct.pack("<8d", *invalid) + points))
                            db.execute("UPDATE patterns SET contour_index=1 WHERE image_id=1 AND ordinal=1")
                    image = self.store.area_difference(dict(scope="image", id=1))
                    self.assertEqual([p["pid"] for p in image["patterns"]], ["22"])
                    reason = "Contour extends outside its declared image frame"
                    self.assertIn(dict(pid="21", reason=reason), image["issues"])
                    for kind in ("inward", "outward"):
                        self.assertEqual(image["patterns"][0][kind]["area_nm2"], 48)
                    result = self.store.area_difference(dict(scope="selection", filters=dict(pno=2)))
                    self.assertEqual((result["pairs"], result["compared"]), (1, 1))
                    self.assertEqual(result["excluded"], {reason: 1, "missing contour": 2})
                    for kind in ("inward", "outward"):
                        self.assertAlmostEqual(math.fsum(decode_grid(result, kind)), 48)
                        self.assertEqual(result["mean_area_nm2"][kind], 48)
                    pid = self.store.area_difference(dict(scope="pid", pid="21", filters=dict(pno=2)))
                    self.assertEqual((pid["pairs"], pid["compared"]), (0, 0))
                    self.assertEqual(pid["excluded"], {reason: 1})

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_parallel_skips_image_pair_with_only_out_of_frame_contours(self):
        self.area_fixture()
        self.area_second_pair()
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE contour_data SET points=? WHERE image_id=2",
                       (struct.pack("<8d", -1, 12, 19, 12, 19, 16, 15, 16),))
        params = dict(scope="selection", filters=dict(pno=2))
        with patch("engine._AREA_PARALLEL_PATTERNS", 10 ** 9):
            serial = self.store.area_difference(params)
        with patch("engine._AREA_PARALLEL_PATTERNS", 0):
            parallel = self.store.area_difference(params)
        self.assertEqual(serial, parallel)
        self.assertEqual((parallel["pairs"], parallel["selected_pairs"], parallel["compared"]), (1, 2, 1))
        self.assertEqual(parallel["excluded"],
                         {"missing contour": 6, "Contour extends outside its declared image frame": 1})
        for kind in ("inward", "outward"):
            self.assertAlmostEqual(math.fsum(decode_grid(parallel, kind)), 48)
            self.assertEqual(parallel["mean_area_nm2"][kind], 48)

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_excludes_ambiguous_mappings_and_invalid_geometry_explicitly(self):
        self.area_fixture()
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("UPDATE patterns SET contour_index=0 WHERE image_id=1 AND ordinal=1")
        data = self.store.area_difference(dict(scope="image", id=1))
        self.assertEqual(data["patterns"], [])
        self.assertIn("ambiguous contour mapping", [item["reason"] for item in data["issues"]])
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("UPDATE patterns SET contour_index=NULL WHERE image_id=1 AND ordinal=1")
            db.execute("UPDATE contour_data SET points=? WHERE image_id=1",
                       (struct.pack("<8d", 10, 10, 14, 14, 10, 14, 14, 10),))
        data = self.store.area_difference(dict(scope="image", id=1))
        self.assertEqual(data["patterns"], [])
        self.assertTrue(any("simple non-zero region" in item["reason"] for item in data["issues"]))
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE patterns SET raw=json_set(raw,'$.PID','21') WHERE image_id=1 AND ordinal=1")
        data = self.store.area_difference(dict(scope="image", id=1))
        self.assertIn("missing or ambiguous Comp PID", [item["reason"] for item in data["issues"]])

    @unittest.skipUnless(importlib.util.find_spec("shapely"), "Area geometry requires Shapely and NumPy")
    def test_area_scopes_preserve_shift_and_use_base_design_or_image_origins(self):
        self.area_fixture()
        with sqlite3.connect(self.paths[0]) as db:
            geometry = json.loads(zlib.decompress(db.execute("SELECT geometry FROM images WHERE id=1").fetchone()[0]))
            geometry["design"] = [[[8, 8], [16, 8], [16, 16], [8, 16]]]
            db.execute("UPDATE images SET geometry=? WHERE id=1", (zlib.compress(json.dumps(geometry).encode()),))
        image = self.store.area_difference(dict(scope="image", id=1, measure="area",
                                               filters=dict(roi=[0, 0, 15, 15])))
        self.assertEqual(image["compared"], 1)
        self.assertEqual(image["bounds"], [-4, -6, 8, 6])
        self.assertEqual([image["patterns"][0][kind]["area_nm2"] for kind in ("inward", "outward")], [48, 48])
        selected = self.store.area_difference(dict(scope="image", id=1, measure="area", pids=["21"],
                                                  filters=dict(roi=[100, 100, 200, 200])))
        self.assertEqual(selected["patterns"], image["patterns"], "Clicked PIDs replace ROI")
        empty = self.store.area_difference(dict(scope="image", id=1, measure="area", pids=[]))
        self.assertEqual(empty["patterns"], [])
        self.assertEqual(empty["bounds"], [-1, -1, 1, 1])
        pid = self.store.area_difference(dict(scope="pid", pid="21", filters=dict(pno=2)))
        self.assertEqual((pid["pairs"], pid["compared"]), (1, 1))
        self.assertEqual(pid["bounds"], [-492, 726, -480, 738])
        for kind in ("inward", "outward"):
            self.assertAlmostEqual(math.fsum(decode_grid(pid, kind)), 48)
        excluded = self.store.area_difference(dict(scope="pid", pid="22", filters=dict(pno=2)))
        self.assertEqual((excluded["pairs"], excluded["compared"]), (0, 0))
        self.assertEqual(excluded["excluded"], {"missing contour": 1})
        with self.assertRaisesRegex(ValueError, "Select a PID"):
            self.store.area_difference(dict(scope="pid", filters=dict(pno=2)))
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            self.store.area_difference(dict(scope="image", id=1, measure="area", pids=[None]))

    def test_colliding_local_ids_and_readonly(self):
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths]
        index = self.store.index()
        self.assertEqual(len({entry["id"] for entry in index["images"]}), 3)
        base = next(e for e in index["images"] if e["role"] == "base" and e["chip_x"] == 8)
        image = self.store.image(base["id"])
        self.assertEqual(image["matched"], 4)
        self.assertEqual([len(image["vectors"][r]) for r in ("base", "comparison")], [2, 2])
        self.assertEqual(self.store.statistics(dict(pno=2))["table"],
                         Store(self.legacy, allow_legacy=True).statistics(dict(pno=2))["table"])
        with connection(self.store.path) as db:
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                db.execute("DELETE FROM base_data.images")
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths])

    def test_contour_overlay_uses_saved_mapping_and_scope_specific_alignment(self):
        for path in self.paths:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE patterns SET contour_index=0")
                for identity, content in db.execute("SELECT id,geometry FROM images").fetchall():
                    geometry = json.loads(zlib.decompress(content))
                    geometry["design"] = [
                        [[x - 1.75, y - 1.5], [x + 2.25, y - 1.5],
                         [x + 2.25, y + 2.5], [x - 1.75, y + 2.5]]
                        for x, y in db.execute("SELECT px,py FROM patterns WHERE image_id=?", (identity,))
                    ]
                    db.execute("UPDATE images SET geometry=? WHERE id=?",
                               (zlib.compress(json.dumps(geometry).encode()), identity))
        base = next(entry for entry in self.store.index()["images"]
                    if entry["role"] == "base" and entry["chip_x"] == 8)
        filters = dict(pno=2, chips=[[8, 7]], roi=[0, 0, 50, 50])
        image = self.store.contour_overlay(dict(scope="image", id=base["id"], filters=filters))
        self.assertEqual(image["pairs"], 1)
        self.assertEqual(len(image["contours"]["base"]), 3)
        self.assertEqual(len(image["contours"]["comparison"]), 3)
        self.assertEqual(image["contours"]["base"][0]["points"][0], [-10.25, 10.5])

        with patch("engine.zlib.decompress", side_effect=AssertionError("Full geometry must not be decoded")):
            pid = self.store.contour_overlay(dict(scope="pid", pid="21", filters=filters))
            density = self.store.contour_overlay(dict(scope="selection", filters=filters))
        self.assertEqual((pid["pid"], pid["pairs"]), ("21", 1))
        self.assertEqual([len(pid["packed"][role]["lengths"]) for role in ("base", "comparison")], [1, 1])
        decoded = list(struct.iter_unpack("<dd", zlib.decompress(base64.b64decode(pid["packed"]["base"]["points"]))))
        self.assertEqual(decoded, [(-256, 256), (-255, 256), (-255, 255)])
        self.assertEqual(pid["bounds"], [
            min(x for x, y in decoded), min(y for x, y in decoded),
            max(x for x, y in decoded), max(y for x, y in decoded),
        ])

        self.assertEqual((density["pairs"], density["mapped"]), (1, {"base": 3, "comparison": 3}))
        self.assertEqual(density["packed"]["base"], density["packed"]["comparison"])
        self.assertGreater(sum(decode_grid(density, "base")), 0)

    def test_this_image_aligns_true_design_centers_and_selects_roi_or_pids(self):
        for path in self.paths:
            with sqlite3.connect(path) as db:
                geometry = json.loads(zlib.decompress(
                    db.execute("SELECT geometry FROM images WHERE id=1").fetchone()[0]))
                geometry["design"] = [
                    [[x - 1.75, 8.5], [x + 2.25, 8.5], [x + 2.25, 12.5], [x - 1.75, 12.5]]
                    for x in (10, 100)
                ]
                # The frame boundary must not be used as a pattern design center.
                geometry["design"].append([[-1, -1], [513, -1], [513, 513], [-1, 513]])
                db.execute("UPDATE images SET geometry=? WHERE id=1", (zlib.compress(json.dumps(geometry).encode()),))
                db.execute("UPDATE patterns SET px=CASE ordinal WHEN 0 THEN 10 ELSE 100 END,py=10,"
                           "contour_index=CASE ordinal WHEN 0 THEN 0 WHEN 1 THEN 1 ELSE NULL END WHERE image_id=1")
                points = [(x, y) for center in (10.75, 100.75)
                          for x, y in ((center - 1, 9.5), (center + 1, 9.5),
                                       (center + 1, 11.5), (center - 1, 11.5))]
                db.execute("UPDATE contour_data SET nm_x=2,nm_y=3,offsets=?,points=?,density_offsets=?,"
                           "density_cells=x'',density_lengths=x'' WHERE image_id=1", (
                    struct.pack("<3I", 0, 4, 8), struct.pack("<16d", *(v for p in points for v in p)),
                    struct.pack("<3I", 0, 0, 0)))
        args = dict(scope="image", id=1, filters=dict(pno=2))
        all_patterns = self.store.contour_overlay(args)
        shapes = all_patterns["contours"]["base"]
        self.assertEqual(len(shapes), 2)
        self.assertEqual(shapes[0]["points"], [[-1, 3], [3, 3], [3, -3], [-1, -3]])
        self.assertEqual(shapes[0]["points"], shapes[1]["points"])
        self.assertEqual(all_patterns["bounds"], [-1, -3, 3, 3])
        roi = self.store.contour_overlay({**args, "filters": dict(pno=2, roi=[0, 0, 50, 50])})
        self.assertEqual([p["pid"] for p in roi["contours"]["base"]], ["21"])
        clicked = self.store.contour_overlay({**args, "pids": ["22"], "filters": dict(pno=2, roi=[0, 0, 50, 50])})
        self.assertEqual([p["pid"] for p in clicked["contours"]["base"]], ["22"])
        empty = self.store.contour_overlay({**args, "pids": []})
        self.assertEqual(empty["bounds"], [-1, -1, 1, 1])
        # PID is optional: ROI contour targets still include patterns without one.
        for path in self.paths:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE patterns SET raw=json_remove(raw,'$.PID') WHERE image_id=1 AND ordinal=0")
        image = self.store.image(1)
        self.assertNotIn([10, 10], [[label["x"], label["y"]] for label in image["labels"]])
        self.assertEqual([[p["pid"], p["x"], p["y"]] for p in image["contour_patterns"]],
                         [[None, 10, 10], ["22", 100, 10]])
        no_pid = self.store.contour_overlay({**args, "filters": dict(pno=2, roi=[0, 0, 50, 50])})
        self.assertEqual([p["pid"] for p in no_pid["contours"]["base"]], [None])
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("UPDATE images SET geometry=? WHERE id=1", (
                zlib.compress(json.dumps(dict(width=512, height=512, design=[])).encode()),))
        with self.assertRaisesRegex(ValueError, "No unambiguous design pattern"):
            self.store.contour_overlay(args)

    def test_length_cells_split_segments_at_every_cell_boundary(self):
        geometry = dict(nm_x=1, nm_y=1)
        # 4 x 4 cells of 1 nm over [-2, 2] nm. Source y=+0.5 px is 0.5 nm below the origin -> row 2.
        # Each two-point polygon is closed, so every cell receives the segment twice.
        cells = length_cells([(-1.5, 0.5), (1.5, 0.5)], geometry, (0, 0), 2, bins=4)
        self.assertEqual(sorted(cells), [8, 9, 10, 11])
        for cell, expected in {8: 1, 9: 2, 10: 2, 11: 1}.items():
            self.assertAlmostEqual(cells[cell], expected)
        # Passing exactly through a cell corner splits the length evenly between the two cells.
        diagonal = length_cells([(-1, -1), (1, 1)], geometry, (0, 0), 2, bins=4)
        self.assertEqual(sorted(diagonal), [5, 10])
        self.assertAlmostEqual(diagonal[5], 2 * math.sqrt(2))
        self.assertAlmostEqual(diagonal[10], 2 * math.sqrt(2))
        clipped = length_cells([(1, 0.5), (3, 0.5)], geometry, (0, 0), 2, bins=4)
        self.assertEqual(clipped, {11: 2})

    def test_selection_density_is_exact_contour_length_and_uses_stored_cells(self):
        for path in self.paths:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE patterns SET contour_index=0")
        args = dict(scope="selection", bins=1024, filters=dict(pno=2))
        with patch.object(Store, "_contour_points", side_effect=AssertionError("Stored cells must be reused")):
            data = self.store.contour_overlay(args)
        grid = decode_grid(data, "base")
        # Four mapped patterns of the source triangle (0,0),(1,0),(1,1) at 1 nm/px, one image pair.
        self.assertAlmostEqual(sum(grid), 4 * (2 + math.sqrt(2)), places=5)
        # Cells 0,1 (bottom edge), 2,1026 (right edge) and 1025,0 (diagonal through cell corners).
        self.assertEqual([cell for cell, value in enumerate(grid) if value], [0, 1, 2, 1025, 1026])
        self.assertAlmostEqual(grid[0], 4 * (0.5 + math.sqrt(2) / 2), places=5)
        with sqlite3.connect(self.paths[0]) as db:
            # Valid layout, but a cell beyond the 1024 x 1024 grid must still be rejected.
            for image, cells in db.execute("SELECT image_id,density_cells FROM contour_data").fetchall():
                db.execute("UPDATE contour_data SET density_cells=? WHERE image_id=?",
                           (struct.pack("<I", 1024 * 1024) + cells[4:], image))
        with self.assertRaisesRegex(ValueError, "Invalid contour density"):
            self.store.contour_overlay(args)
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("UPDATE contour_data SET density_cells=x'01'")
        with self.assertRaisesRegex(ValueError, "Invalid contour density"):
            self.store.contour_overlay(args)

    def test_common_grid_recomputes_smaller_image_frames_exactly(self):
        for path in self.paths:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE patterns SET contour_index=0")
        # A larger Comparison frame forces exact recomputation for the smaller Base frame.
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE contour_data SET nm_x=2,nm_y=2")
        data = self.store.contour_overlay(dict(scope="selection", filters=dict(pno=2)))
        self.assertEqual(data["extent_nm"], 512)
        base = decode_grid(data, "base")
        self.assertAlmostEqual(sum(base), 4 * (2 + math.sqrt(2)))
        # 1 nm cells over [-512, 512] nm: the 1 nm triangle at Base x,y = -256 nm stays in row/col 256,
        # and its right edge on the col 257 boundary is counted in col 257.
        self.assertEqual([cell for cell, value in enumerate(base) if value], [256 * 1024 + 256, 256 * 1024 + 257])
        self.assertAlmostEqual(base[256 * 1024 + 257], 4)

    def test_selection_density_origin_is_each_design_layout_center(self):
        for path in self.paths:
            with sqlite3.connect(path) as db:
                db.execute("UPDATE patterns SET contour_index=0")
        # A larger Comparison frame makes the engine recompute Base cells from the design origin.
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE contour_data SET nm_x=2,nm_y=2")
        args = dict(scope="selection", bins=1024, filters=dict(pno=2))
        centered = decode_grid(self.store.contour_overlay(args), "base")
        for path in self.paths:
            with sqlite3.connect(path) as db:
                # One source pixel left and up of the 512-pixel image center (= one 1 nm cell).
                db.execute("UPDATE contour_data SET design_x=255,design_y=255")
        moved = decode_grid(self.store.contour_overlay(args), "base")
        shifted = [0.0] * len(centered)
        for cell, value in enumerate(centered):
            if value:
                shifted[cell + 1024 + 1] = value
        for cell, value in enumerate(moved):
            self.assertAlmostEqual(value, shifted[cell], places=5)
        image = self.store.image(1)
        self.assertEqual(image["reference_frames"]["base"]["design_center"], [255, 255])
        with self.assertRaisesRegex(ValueError, "must be 1024"):
            self.store.contour_overlay(dict(scope="selection", bins=2048, filters=dict(pno=2)))

    def test_missing_compact_contours_reported(self):
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("DROP TABLE contour_data")
        with self.assertRaisesRegex(ValueError, "contour_data.*rebuild"):
            Store(*self.paths)

    def test_damaged_compact_contours_reported(self):
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("UPDATE contour_data SET offsets=x'01'")
        with self.assertRaisesRegex(ValueError, "Invalid compact contour"):
            self.store.contour_overlay(dict(scope="selection", filters=dict(pno=2)))

    def test_base_only_session_uses_every_selected_base_image(self):
        single = Store(self.paths[0], None)
        self.assertTrue(single.single)
        self.assertFalse(self.store.single)
        self.assertFalse(Store(self.legacy, allow_legacy=True).single)
        metadata = single.index()["metadata"]
        self.assertEqual((metadata["single"], metadata["datasets"]), (True, {"base": "base"}))
        result = single.statistics(dict(pno=2))
        pair = self.store.statistics(dict(pno=2))
        self.assertEqual((result["single"], result["paired_images"], result["images"]), (True, 0, 2))
        self.assertEqual(result["counts"]["base"]["unpaired"], 0)
        n = {(row["role"], row["metric"]): row["n"] for row in result["table"]}
        self.assertEqual((n["base", "dx"], n["comparison", "dx"]), (4, 0))
        # Pair mode only counts the matched image, so the Base cohort is smaller there.
        self.assertLess(next(r["n"] for r in pair["table"] if r["role"] == "base" and r["metric"] == "dx"), 4)
        offset = {row["pid"]: row for row in result["offset_by_pid"]["table"]}
        self.assertEqual({row["role"] for row in offset.values()}, {"base"})
        self.assertEqual((offset["21"]["n"], offset["21"]["x_mean"], offset["21"]["magnitude_mean"]), (2, 3.0, 5.0))
        self.assertEqual((offset["23"]["n"], offset["23"]["excluded"], offset["23"]["x_mean"]), (0, 2, None))
        self.assertEqual({row["role"] for row in pair["offset_by_pid"]["table"]}, {"base", "comparison"})
        with self.assertRaisesRegex(ValueError, "requires both Base and Comp"):
            single.ppe_values(dict(pno=2))
        image = single.image(next(e["id"] for e in single.index()["images"] if e["chip_x"] == 8))
        self.assertEqual((image["notice"], image["vectors"]["comparison"]), ("", []))
        density = single.contour_overlay(dict(scope="selection", filters=dict(pno=2)))
        self.assertEqual((density["pairs"], density["mapped"]["comparison"]), (2, 0))

    def test_same_file_and_reversed_roles(self):
        for paths in (self.paths[::-1], [self.paths[0], self.paths[0]]):
            store = Store(*paths)
            result = store.statistics(dict(pno=2))
            self.assertEqual(result["paired_images"], 1 if paths[0] != paths[1] else 2)
            self.assertEqual(store.index()["metadata"]["datasets"]["base"], "comparison" if paths[0] != paths[1] else "base")

    def test_invalid_or_incomplete_file_rejected(self):
        damaged = self.paths[0].parent / "invalid.sqlite"
        damaged.write_text("not SQLite")
        with self.assertRaises(sqlite3.DatabaseError):
            Store(damaged, self.paths[1])
        with self.assertRaises(ValueError):
            Store(self.legacy, self.paths[1])
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("DELETE FROM patterns WHERE ordinal=0")
        with self.assertRaisesRegex(ValueError, "measurement count"):
            Store(*self.paths)

    def test_legacy_requires_explicit_opt_in(self):
        invalid = self.paths[0].parent / "invalid-v1.sqlite"
        with sqlite3.connect(invalid) as db:
            db.execute("PRAGMA user_version=1")
        for path in (invalid, self.legacy):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "version 4; rebuild"):
                    Store(path)
        legacy = Store(self.legacy, allow_legacy=True)
        self.assertFalse(legacy.single)
        self.assertEqual(legacy.statistics(dict(pno=2))["paired_images"], 1)

    def test_older_builder_files_rejected(self):
        for version in (2, 3):
            with self.subTest(version=version):
                with sqlite3.connect(self.paths[1]) as db:
                    db.execute(f"PRAGMA user_version={version}")
                with self.assertRaisesRegex(ValueError, "version 4; rebuild"):
                    Store(*self.paths)

    def test_missing_manifest_key_rejected_before_use(self):
        with sqlite3.connect(self.paths[1]) as db:
            manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
            del manifest["csv_sha256"]
            db.execute("UPDATE metadata SET value=? WHERE key='manifest'", (json.dumps(manifest),))
        with self.assertRaisesRegex(ValueError, "source CSV hash"):
            Store(*self.paths)
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE metadata SET value=? WHERE key='manifest'",
                       (json.dumps(dict(manifest, csv_sha256="fixture")),))
            db.execute("INSERT INTO metadata VALUES('test_chip_override','[]')")
        with self.assertRaises(AttributeError):
            Store(*self.paths)

    def test_test_only_provenance_visible_for_either_role(self):
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("INSERT INTO metadata VALUES('test_chip_override',?)",
                       (json.dumps(dict(warning="TEST ONLY", initial_image_name="sample-1.png")),))
        meta = Store(*self.paths).index()["metadata"]
        self.assertIn("TEST ONLY", meta["test_chip_override"]["warning"])
        self.assertIn("base", meta["test_chip_override"]["datasets"])

    def test_base_only_preserves_test_chip_provenance(self):
        override = dict(warning="TEST ONLY", initial_image_name="sample-1.png", marker="test")
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("INSERT INTO metadata VALUES('test_chip_override',?)", (json.dumps(override),))
        meta = Store(self.paths[0]).index()["metadata"]
        provenance = meta["test_chip_override"]
        self.assertEqual(provenance["datasets"], {"base": override})
        self.assertIn("synthetic chip coordinates", provenance["warning"])
        self.assertIn("TEST ONLY", provenance["warning"])
        self.assertNotIn("initial_image_id", provenance)

    def test_isolated_independent_test_chip_copy(self):
        with sqlite3.connect(self.paths[0]) as db:
            db.execute("DELETE FROM images WHERE chip_x=9")
            db.execute("DELETE FROM patterns WHERE image_id=2")
            db.execute("DELETE FROM contour_data WHERE image_id=2")
            manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
            manifest.update(images=1, patterns=4)
            db.execute("UPDATE metadata SET value=? WHERE key='manifest'", (json.dumps(manifest),))
        with sqlite3.connect(self.paths[1]) as db:
            db.execute("UPDATE images SET chip_x=6,chip_y=4")
            for ordinal, content in db.execute("SELECT ordinal,raw FROM patterns").fetchall():
                raw = dict(json.loads(content), XINDEX="6", YINDEX="4")
                db.execute("UPDATE patterns SET raw=? WHERE ordinal=?", (json.dumps(raw), ordinal))
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths]
        output = self.paths[0].parent / "test.sqlite"
        report = prepare_pair(*self.paths, output)
        self.assertIn("TEST ONLY", report["warning"])
        store = Store(self.paths[0], output)
        image = store.image(1)
        self.assertEqual(image["matched"], 4)
        self.assertEqual(image["rows"]["comparison"][0]["TEST_ORIGINAL_XINDEX"], "6")
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths])
        repeat = output.with_name("repeat.sqlite")
        with self.assertRaisesRegex(ValueError, "already synthetic"):
            prepare_pair(self.paths[0], output, repeat)
        self.assertFalse(repeat.exists())

    def test_unicode_and_spaces_in_dataset_path(self):
        renamed = self.paths[1].with_name("data # 日本語.sqlite")
        self.paths[1].rename(renamed)
        self.assertEqual(Store(self.paths[0], renamed).statistics(dict(pno=2))["paired_images"], 1)


if __name__ == "__main__":
    unittest.main()
import base64
