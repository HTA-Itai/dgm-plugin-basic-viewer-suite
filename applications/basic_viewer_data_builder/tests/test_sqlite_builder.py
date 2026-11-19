import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import unittest

import test_builder
from sqlite_builder import build_sqlite, contour_index, length_cells
from builder import BuildConfig


class SQLiteBuilderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_builder.BuilderTests()
        self.fixture.setUp()
        self.source = self.fixture.source
        self.output = self.fixture.root / "sample.sqlite"
        self.msr = self.fixture.root / "sample.msr"
        self.msr.write_text(
            "~idw_info array : 0 : 1,1\n~idw_info pitch : 0 : 1000,1000\n"
            "~idw_info array : 1 : 1,1\n~idw_info chip_pitch : 1000,1000\n"
            "~idw_info map_origin : 0,0\n~idw_info map_offset : 0,0\n"
            "~idw_info chip_exist : 0 : 1\n"
        )
        self.rows = []
        for row in self.fixture.rows:
            name = row["IMAGENAME"].replace(".png", "-01MS.png")
            self.rows.append(dict(row, IMAGENAME=name, PNO="2",
                                  SEQUENCENO="1" if name.startswith("one") else "2",
                                  XINDEX="0", YINDEX="0", XREL="10", YREL="20"))
        for name in ("one", "two"):
            (self.source / "viewer" / f"{name}.ovlx").rename(self.source / "viewer" / f"{name}-01MS.ovlx")
        self.write_rows()

    def tearDown(self):
        self.fixture.tearDown()

    def write_rows(self):
        with (self.source / "report/result_Size_Area.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)

    def build(self):
        return build_sqlite(self.source, self.output, BuildConfig(), dataset_id="one-run", msr=self.msr)

    def prepare_plugin_input(self):
        root = self.fixture.root
        shutil.copytree(self.source, root / "input/result")
        shutil.copy(self.msr, root / "input/source.msr")
        (root / "config").mkdir()
        (root / "config/vector.yml").write_text("TargetCSV: result_Size_Area.csv\n")
        return root

    def test_roundtrip_role_free_quality_and_raw_assets(self):
        original = hashlib.sha256((self.source / "report/result_Size_Area.csv").read_bytes()).hexdigest()
        result = self.build()
        self.assertEqual((result["images"], result["patterns"]), (2, 3))
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertNotIn("role", [r[1] for r in db.execute("PRAGMA table_info(images)")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 6)
            rows = db.execute("SELECT available,valid,dx,raw FROM patterns ORDER BY image_id,ordinal").fetchall()
            self.assertEqual([r[:3] for r in rows], [(1, 1, 1.0), (1, 0, -11.0), (0, 0, None)])
            self.assertEqual(json.loads(rows[0][3])["Label"], "日本語")
            manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
            self.assertEqual(manifest["csv_sha256"], original)
        with self.assertRaises(FileExistsError):
            self.build()

    def test_contour_index_is_saved_per_pattern(self):
        positions = {"two": [(12, 12)], "one": [(2, 3), (8, 8)]}
        polygons = [
            "11.15,11.15 13.15,11.15 13.15,13.15 11.15,13.15",
            "1,2 3,2 3,4 1,4",
            "7,7 9,7 9,9 7,9",
        ]
        for name, image_positions in positions.items():
            rows = [row for row in self.rows if row["IMAGENAME"] == f"{name}-01MS.png"]
            for row, (x, y) in zip(rows, image_positions, strict=True):
                row["PixelX"], row["PixelY"] = str(x), str(y)
            self.fixture.write_ovlx(f"{name}-01MS", svg=(
                '<svg xmlns="http://www.w3.org/2000/svg"><g>'
                + "".join(f'<polygon points="{points}"/>' for points in polygons)
                + "</g></svg>"
            ).encode())
        self.write_rows()
        self.build()
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            indices = db.execute(
                "SELECT i.name,p.ordinal,p.contour_index FROM patterns p "
                "JOIN images i ON i.id=p.image_id ORDER BY i.name,p.ordinal"
            ).fetchall()
            frame = db.execute("SELECT offsets,points,density_offsets,density_cells,density_lengths,design_x,design_y "
                               "FROM contour_data WHERE image_id=1").fetchone()
            offsets = [value for value, in struct.iter_unpack("<I", frame[0])]
            points = list(struct.iter_unpack("<dd", frame[1]))
            self.assertEqual(offsets, [0, 4, 8, 12])
            self.assertEqual(points[offsets[1]:offsets[2]], [(1, 2), (3, 2), (3, 4), (1, 4)])
            # Density origin is the design layout center, not the 16-pixel image center.
            self.assertEqual(frame[5:], (10, 10))
            starts = [value for value, in struct.iter_unpack("<I", frame[2])]
            self.assertEqual(len(starts), len(offsets))
            cells = [value for value, in struct.iter_unpack("<I", frame[3])]
            lengths = [value for value, in struct.iter_unpack("<f", frame[4])]
            self.assertEqual(starts[-1], len(cells))
            self.assertEqual(len(cells), len(lengths))
            # The grid spans design center +/- 8 px; length outside it is dropped, not moved to the edge.
            # Polygon 1 keeps x=2..3 of the top/bottom edges and the whole x=3 edge: 4 px.
            for index, inside_px in enumerate((8, 4, 8)):
                self.assertAlmostEqual(sum(lengths[starts[index]:starts[index + 1]]),
                                       inside_px * 0.659, places=4)
        self.assertEqual(indices, [
            ("one-01MS.png", 0, 1),
            ("one-01MS.png", 1, 2),
            ("two-01MS.png", 0, 0),
        ])
        self.assertEqual(contour_index(2, 3, [[[1, 2], [3, 2], [3, 4], [1, 4]]]), 0)
        geometry = dict(nm_x=1, nm_y=1)
        # 4 x 4 cells of 1 nm; a closed two-point polygon counts each crossed length twice.
        self.assertEqual(length_cells([(1, 0.5), (3, 0.5)], geometry, (0, 0), 2, bins=4), {11: 2})
        with self.assertRaisesRegex(ValueError, "multiple contours"):
            contour_index(2, 3, [
                [[1, 2], [3, 2], [3, 4], [1, 4]],
                [[0, 1], [4, 1], [4, 5], [0, 5]],
            ])

    def test_identity_errors_publish_nothing(self):
        for key, value in (("SEQUENCENO", ""), ("XINDEX", "1"), ("PNO", "NaN")):
            with self.subTest(key=key):
                old = self.rows[0][key]
                self.rows[0][key] = value
                self.write_rows()
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())
                self.rows[0][key] = old

    def test_layout_center_requires_a_design_pattern(self):
        # Only the image-enclosing design frame remains; it is not a pattern.
        self.fixture.write_ovlx("one-01MS", design=(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            b'<polygon data-design-type="0" points="0,0 40,0 40,40 0,40"/></svg>'))
        with self.assertRaisesRegex(ValueError, "Missing design patterns"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_default_cli_and_plugin_trigger(self):
        app = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(app / "main.py"), "--source", str(self.source),
                        "--output", str(self.output), "--dataset-id", "cli", "--msr", str(self.msr)],
                       capture_output=True, text=True, check=True)
        self.assertTrue(self.output.is_file())
        root = self.prepare_plugin_input()
        (root / "input/msr").mkdir()
        names = ("20251024_001322_XP420400_EPE_RGDFA_6O", "ウェーハA")
        for name in names:
            with self.subTest(msr=name):
                trigger = dict(TargetData="result", Msr=name)
                if name == names[0]:
                    trigger["SourceMsr"] = "source.msr"
                else:
                    shutil.copy(self.msr, root / "input/msr" / name)
                (root / "trigger.json").write_text(json.dumps(trigger))
                subprocess.run([sys.executable, str(app / "main.py"), "trigger.json", "--data-only"], cwd=root,
                               capture_output=True, text=True, check=True)
                output = root / "output" / f"{name}.sqlite"
                self.assertTrue(output.is_file())
                with sqlite3.connect(output) as db:
                    manifest = json.loads(db.execute(
                        "SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
                self.assertEqual(manifest["dataset_id"], name)
        self.assertFalse((root / "output/dataset.sqlite").exists())
        output = root / "output" / f"{names[-1]}.sqlite"
        original = output.read_bytes()
        repeated = subprocess.run([sys.executable, str(app / "main.py"), "trigger.json", "--data-only"], cwd=root,
                                  capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(output.read_bytes(), original)

    def test_trigger_without_msr_keeps_legacy_output_name(self):
        root = self.prepare_plugin_input()
        (root / "trigger.json").write_text(json.dumps(dict(TargetData="result", SourceMsr="source.msr")))
        app = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(app / "main.py"), "trigger.json", "--data-only"], cwd=root,
                                capture_output=True, text=True, check=True)
        self.assertTrue((root / "output/dataset.sqlite").is_file())
        self.assertIn("Msr", result.stderr)
        with sqlite3.connect(root / "output/dataset.sqlite") as db:
            manifest = json.loads(db.execute(
                "SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        self.assertEqual(manifest["dataset_id"], "result")

    def test_trigger_rejects_msr_names_unsafe_for_windows(self):
        root = self.prepare_plugin_input()
        app = Path(__file__).resolve().parents[1]
        for name in ("../escape", "folder/name", r"folder\name", "bad:name", "bad*name",
                     "trailing.", "trailing ", "CON", "LPT1", "bad\nname", 42):
            with self.subTest(msr=name):
                (root / "trigger.json").write_text(json.dumps(
                    dict(TargetData="result", SourceMsr="source.msr", Msr=name)))
                result = subprocess.run([sys.executable, str(app / "main.py"), "trigger.json"], cwd=root,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Msr", result.stderr)
                self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
